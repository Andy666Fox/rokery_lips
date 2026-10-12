"""Shared parsing and bounded HTTP reads of public Telegram pages."""

import asyncio
import base64
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import override
from urllib.parse import parse_qs, urlsplit

import httpx

from random_telegram_bot.catalogue import normalize_channel

RESERVED = {"joinchat", "addlist", "share", "proxy", "socks", "contact", "login", "boost"}


def public_username(value: str) -> str | None:
    """Resolve public profile/post links, excluding invites, services and bot starts."""
    value = unescape(value).strip().rstrip(".,;!")
    if value.startswith("@"):
        try:
            return normalize_channel(value)
        except ValueError:
            return None
    if value.lower().startswith(("t.me/", "telegram.me/")):
        value = "https://" + value
    try:
        url = urlsplit(value)
        if url.username is not None or url.password is not None or url.port is not None:
            return None
    except ValueError:
        return None
    if url.scheme not in {"http", "https"} or url.hostname not in {"t.me", "telegram.me"}:
        return None
    query = parse_qs(url.query, keep_blank_values=True)
    if any(key in query for key in ("start", "startapp", "startgroup", "startchannel")):
        return None
    parts = url.path.strip("/").split("/")
    if parts[0] == "s":
        parts = parts[1:]
    if not parts or parts[0].lower() in RESERVED:
        return None
    if any(not part.isascii() or not part.isdigit() for part in parts[1:]):
        return None
    try:
        return normalize_channel(parts[0])
    except ValueError:
        return None


@dataclass(slots=True)
class Preview:
    dates: list[float] = field(default_factory=list)
    links: set[str] = field(default_factory=set)
    sample: str = ""
    post_count: int = 0
    title: str = ""
    about: str = ""
    peer_ids: set[int] = field(default_factory=set)
    subscriber_label: bool = False
    subscriber_display: str = ""
    subscriber_count: int | None = None


@dataclass(slots=True)
class Frame:
    tag: str
    message: bool
    text: bool
    widget: bool
    counter_type: bool = False
    counter_value: bool = False
    counter_root: bool = False


class PreviewParser(HTMLParser):
    """Read only the requested channel's message bodies, dates and forward links."""

    def __init__(self, username: str) -> None:
        super().__init__(convert_charrefs=True)
        self.username = username
        self.preview = Preview()
        self.stack: list[Frame] = []
        self.text: list[str] = []
        self.counter_type: list[str] = []
        self.counter_value: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "meta":
            if attr.get("property") == "og:title":
                self.preview.title = (attr.get("content") or "")[:255]
            elif attr.get("property") == "og:description":
                self.preview.about = (attr.get("content") or "")[:4000]
        parent = self.stack[-1] if self.stack else None
        parent_message = parent.message if parent else False
        parent_text = parent.text if parent else False
        classes = (attr.get("class") or "").split()
        own_post = (attr.get("data-post") or "").split("/")
        message = parent_message
        if "tgme_widget_message" in classes:
            message = (
                len(own_post) == 2
                and own_post[0].lower() == self.username
                and own_post[1].isascii()
                and own_post[1].isdigit()
            )
            if message:
                self.preview.post_count += 1
                raw_view = attr.get("data-view") or ""
                if raw_view and len(raw_view) < 1024:
                    try:
                        view = json.loads(
                            base64.urlsafe_b64decode(raw_view + "=" * (-len(raw_view) % 4))
                        )
                        peer = view.get("c") if isinstance(view, dict) else None
                        if (
                            type(peer) is int
                            and -1000000000000 < peer < 0
                            and view.get("p") == int(own_post[1])
                        ):
                            self.preview.peer_ids.add(peer)
                    except (ValueError, TypeError):
                        pass  # Missing/changed web metadata never establishes identity.
        text = message and (parent_text or "tgme_widget_message_text" in classes)
        if message and tag == "time" and attr.get("datetime") is not None:
            try:
                date = datetime.fromisoformat(str(attr["datetime"]).replace("Z", "+00:00"))
                if date.tzinfo is not None:
                    self.preview.dates.append(date.timestamp())
            except ValueError:
                pass  # Malformed markup provides no evidence of activity.
        if (
            message
            and tag == "a"
            and (name := public_username(attr.get("href") or "")) is not None
            and name != self.username
        ):
            self.preview.links.add(name)
        if tag in {"br", "hr"} and text:
            self.text.append("\n")
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            widget = "tgme_widget_message" in classes or bool(parent and parent.widget)
            root = not widget and "tgme_channel_info_counter" in classes
            info = root or any(frame.counter_root for frame in self.stack)
            if root:
                self.counter_type = []
                self.counter_value = []
            self.stack.append(
                Frame(
                    tag,
                    message,
                    text,
                    widget,
                    info and ("counter_type" in classes or bool(parent and parent.counter_type)),
                    info and ("counter_value" in classes or bool(parent and parent.counter_value)),
                    root,
                )
            )

    @override
    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                if any(frame.counter_root for frame in self.stack[index:]):
                    label = "".join(self.counter_type).strip().lower()
                    if label in {"subscriber", "subscribers"}:
                        display = "".join(self.counter_value).strip()
                        self.preview.subscriber_label = True
                        self.preview.subscriber_display = display
                        # Rounded K/M values cannot establish the <=1000 preference.
                        exact = re.fullmatch(
                            r"[0-9]+|[0-9]{1,3}(?:[ \u00a0\u202f,][0-9]{3})+", display
                        )
                        digits = re.sub(r"[ \u00a0\u202f,]", "", display)
                        self.preview.subscriber_count = (
                            int(digits) if exact and len(digits) <= 12 else None
                        )
                del self.stack[index:]
                break

    @override
    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1].text:
            self.text.append(data)
        if self.stack and self.stack[-1].counter_type:
            self.counter_type.append(data)
        if self.stack and self.stack[-1].counter_value:
            self.counter_value.append(data)


def parse_preview(document: str, username: str) -> Preview:
    parser = PreviewParser(username)
    parser.feed(document)
    parser.close()
    parser.preview.sample = " ".join(" ".join(parser.text).split())[-4000:]
    return parser.preview


MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = "SynchronisicaDiscoveryBot/1.0 (+https://dedvnutri.space/) httpx/0.28.1"


class Deferred(Exception):
    """A failed observation is retryable and must never deactivate a live channel."""

    def __init__(
        self, reason: str, retry_after: float = 3600, *, stop: bool = False, scope: str = "web"
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after
        self.stop = stop
        self.scope = scope


class TelegramHTTP:
    def __init__(self, client: httpx.AsyncClient, *, delay: float = 1.5) -> None:
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("Request delay must be finite and cannot be negative")
        self.client = client
        self.delay = delay
        self.last_request = 0.0
        self.requests = 0

    async def fetch(
        self, url: str, payload: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], str]:
        """Never follows redirects, and never includes credential-bearing URLs in errors."""
        await asyncio.sleep(max(0, self.last_request + self.delay - time.monotonic()))
        self.last_request = time.monotonic()
        self.requests += 1
        try:
            async with (
                asyncio.timeout(30),
                self.client.stream(
                    "POST" if payload is not None else "GET",
                    url,
                    json=payload,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=False,
                    timeout=15,
                ) as response,
            ):
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise Deferred("response_too_large", 86400)
                headers = dict(response.headers)
                if response.status_code == 429:
                    retry = 60.0
                    try:
                        raw_retry = headers.get("retry-after", "0")
                        try:
                            header_retry = float(raw_retry)
                        except ValueError:
                            header_retry = (
                                parsedate_to_datetime(raw_retry).timestamp() - time.time()
                            )
                        if math.isfinite(header_retry):
                            retry = max(retry, header_retry)
                    except (ValueError, TypeError, OverflowError):
                        pass
                    try:
                        body = json.loads(content)
                        body_retry = float(body.get("parameters", {}).get("retry_after", 0))
                        if math.isfinite(body_retry):
                            retry = max(retry, body_retry)
                    except (ValueError, TypeError, AttributeError):
                        pass
                    raise Deferred(
                        "rate_limited",
                        retry,
                        stop=True,
                        scope="bot" if payload is not None else "web",
                    )
                if response.status_code in {401, 403} and payload is not None:
                    raise Deferred("bot_access_denied", 86400, stop=True, scope="bot")
                if response.status_code >= 500:
                    raise Deferred("upstream_unavailable")
                return response.status_code, headers, content.decode("utf-8", errors="replace")
        except (httpx.HTTPError, TimeoutError) as error:
            raise Deferred(type(error).__name__) from None
