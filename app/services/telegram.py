import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import BaseModel

logger = logging.getLogger(__name__)
_PHOTO_URL_PATTERN = re.compile(r"background-image:\s*url\(['\"]?([^'\")]+)")


class TelegramPost(BaseModel):
    """Safe representation of a public Telegram post."""

    text: str | None = None
    photo: str | None = None
    video: str | None = None
    link: str | None = None
    date: datetime | None = None


class TelegramUnavailableError(RuntimeError):
    """Raised when Telegram and the local cache cannot provide a post."""


class TelegramResponseError(ValueError):
    """Raised when Telegram returns an unsupported response."""


@dataclass(frozen=True, slots=True)
class _CachedPost:
    post: TelegramPost
    refreshed_at: float


def _safe_http_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        return None
    if url.scheme not in {"http", "https"} or not url.host:
        return None
    return str(url)


def _string_attribute(element: Tag | None, name: str) -> str | None:
    if element is None:
        return None
    value = element.get(name)
    return value if isinstance(value, str) else None


def _background_image_url(element: Tag | None) -> str | None:
    style = _string_attribute(element, "style")
    if style and (match := _PHOTO_URL_PATTERN.search(style)):
        return _safe_http_url(match.group(1))
    return None


def parse_latest_post(html: str) -> TelegramPost:
    """Parse the latest post without exposing Telegram-owned HTML."""

    soup = BeautifulSoup(html, "html.parser")
    messages = soup.select("div.tgme_widget_message_wrap")
    if not messages:
        raise TelegramResponseError("Telegram response did not contain any posts")

    message = messages[-1]
    text_element = message.select_one("div.tgme_widget_message_text")
    text = text_element.get_text(separator="\n", strip=True) if text_element else None

    photo = _background_image_url(message.select_one(".tgme_widget_message_photo_wrap"))
    if not photo:
        photo = _safe_http_url(
            _string_attribute(
                message.select_one(
                    ".tgme_widget_message_photo_wrap img, img.tgme_widget_message_photo"
                ),
                "src",
            )
        )

    # Telegram publishes GIF animations as looping MP4s, alongside a blurred duplicate.
    video_element = message.select_one("video.tgme_widget_message_video:not(.blured)")
    video = _safe_http_url(_string_attribute(video_element, "src"))
    if video_element is not None:
        video = video or _safe_http_url(
            _string_attribute(video_element.select_one("source"), "src")
        )
        photo = photo or _safe_http_url(_string_attribute(video_element, "poster"))
        photo = photo or _background_image_url(
            message.select_one(".tgme_widget_message_video_thumb")
        )

    link_element = message.select_one("a.tgme_widget_message_date")
    link = _safe_http_url(_string_attribute(link_element, "href"))

    date = None
    date_element = message.select_one("time")
    if date_value := _string_attribute(date_element, "datetime"):
        try:
            date = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("telegram response contained an invalid date: %s", date_value)

    if not any((text, photo, video, link)):
        raise TelegramResponseError("Latest Telegram post did not contain usable content")

    return TelegramPost(text=text, photo=photo, video=video, link=link, date=date)


class TelegramService:
    """Fetch and cache the latest public Telegram post."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        channel: str,
        cache_ttl: float,
        stale_ttl: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._channel = channel
        self._cache_ttl = cache_ttl
        self._stale_ttl = stale_ttl
        self._clock = clock
        self._cache: _CachedPost | None = None
        self._retry_after = 0.0
        self._refresh_lock = asyncio.Lock()

    async def latest_post(self) -> TelegramPost:
        now = self._clock()
        if self._is_fresh(now) or now < self._retry_after:
            return self._cached_or_unavailable(now)

        async with self._refresh_lock:
            now = self._clock()
            if self._is_fresh(now) or now < self._retry_after:
                return self._cached_or_unavailable(now)

            try:
                response = await self._client.get(f"https://t.me/s/{self._channel}")
                response.raise_for_status()
                post = parse_latest_post(response.text)
            except (httpx.HTTPError, TelegramResponseError) as error:
                logger.warning("telegram refresh failed: %s: %s", type(error).__name__, error)
                now = self._clock()
                self._retry_after = now + self._cache_ttl
                try:
                    return self._cached_or_unavailable(now)
                except TelegramUnavailableError as unavailable:
                    raise unavailable from error

            self._cache = _CachedPost(post=post, refreshed_at=self._clock())
            self._retry_after = 0.0
            return post

    def _is_fresh(self, now: float) -> bool:
        return self._cache is not None and now - self._cache.refreshed_at < self._cache_ttl

    def _is_usable_stale(self, now: float) -> bool:
        return self._cache is not None and now - self._cache.refreshed_at < self._stale_ttl

    def _cached_or_unavailable(self, now: float) -> TelegramPost:
        if self._is_usable_stale(now):
            return self._require_cache().post
        raise TelegramUnavailableError("Telegram is unavailable")

    def _require_cache(self) -> _CachedPost:
        if self._cache is None:
            raise AssertionError("cache must exist after a successful cache check")
        return self._cache
