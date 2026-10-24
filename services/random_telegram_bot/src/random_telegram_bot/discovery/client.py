"""Observe discovery candidates through the shared Telegram HTTP and parser contracts."""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from random_telegram_bot.catalogue import normalize_channel
from random_telegram_bot.moderation import POLICY_VERSION, InvalidChannel, check_preview
from random_telegram_bot.telegram_web import Deferred, TelegramHTTP, parse_preview


@dataclass(slots=True)
class Observation:
    state: str
    metadata: dict[str, Any]


class Reader(TelegramHTTP):
    def __init__(self, client: httpx.AsyncClient, token: str, *, delay: float = 1.5) -> None:
        super().__init__(client, delay=delay)
        self.token = token

    async def chat(self, username: str) -> dict[str, Any]:
        if not self.token:
            raise ValueError(
                "DISCOVERY_BOT_TOKEN is required for identity checks; use a separate bot"
            )
        username = normalize_channel(username)
        status, _, text = await self.fetch(
            f"https://api.telegram.org/bot{self.token}/getChat", {"chat_id": "@" + username}
        )
        try:
            body = json.loads(text)
            result = body.get("result")
            if status != 200 or not body.get("ok") or not isinstance(result, dict):
                raise Deferred("chat_unavailable", 86400)
            if not isinstance(result.get("id"), int) or not isinstance(result.get("type"), str):
                raise Deferred("invalid_bot_response")
            return result
        except (ValueError, AttributeError):
            raise Deferred("invalid_bot_response") from None

    async def observe(self, username: str, *, now: float, max_age_days: int) -> Observation:
        chat = await self.chat(username)
        metadata: dict[str, Any] = {
            "telegram_id": chat["id"],
            "type": chat["type"],
            "title": str(chat.get("title", ""))[:255],
            "about": str(chat.get("description", ""))[:4000],
        }
        if chat["type"] != "channel":
            return Observation("not_channel", metadata)
        try:
            canonical = normalize_channel(str(chat.get("username", "")))
        except ValueError:
            return Observation("no_public_username", metadata)
        metadata["username"] = canonical
        aliases = {canonical, *(str(name).lower() for name in chat.get("active_usernames", []))}
        if username not in aliases:
            return Observation("identity_mismatch", metadata)
        observation = await self.preview(canonical, now=now, max_age_days=max_age_days)
        if (
            observation.metadata.get("telegram_id") is not None
            and observation.metadata["telegram_id"] != chat["id"]
        ):
            observation.state = "identity_mismatch"
        observation.metadata.update(metadata)
        observation.metadata["identity_verified"] = True
        observation.metadata["identity_source"] = "bot_api"
        if observation.state in {"preview_ready", "web_ready"}:
            observation.state = "ready"
        return observation

    async def preview(self, username: str, *, now: float, max_age_days: int) -> Observation:
        canonical = normalize_channel(username)
        metadata: dict[str, Any] = {
            "username": canonical,
            "identity_verified": False,
            "max_age_days": max_age_days,
        }
        status, _, document = await self.fetch(f"https://t.me/s/{canonical}")
        if status != 200:
            metadata["preview_http"] = status
            return Observation("activity_unknown", metadata)
        preview = parse_preview(document, canonical)
        metadata.update(
            {
                "post_count": preview.post_count,
                "sample": preview.sample,
                "links": sorted(preview.links),
                "title": preview.title,
                "about": preview.about,
                "subscriber_count": preview.subscriber_count,
                "subscriber_display": preview.subscriber_display,
            }
        )
        if not preview.dates:
            return Observation("activity_unknown", metadata)
        latest = max(preview.dates)
        metadata["latest_post"] = latest
        if latest > now + 86400:
            return Observation("invalid_post_date", metadata)
        if len(preview.peer_ids) > 1:
            return Observation("identity_mismatch", metadata)
        state = "preview_ready" if latest >= now - max_age_days * 86400 else "stale"
        if preview.subscriber_label and len(preview.peer_ids) == 1:
            peer = next(iter(preview.peer_ids))
            metadata.update(
                telegram_id=-1000000000000 + peer,
                type="channel",
                identity_verified=True,
                identity_source="web_preview",
            )
            if state == "preview_ready":
                state = "web_ready"
        try:
            await asyncio.to_thread(check_preview, preview, datetime.fromtimestamp(now, UTC))
        except InvalidChannel as error:
            metadata["moderation_reason"] = error.reason
            return Observation("moderation_rejected", metadata)
        metadata["moderation_version"] = POLICY_VERSION
        return Observation(state, metadata)
