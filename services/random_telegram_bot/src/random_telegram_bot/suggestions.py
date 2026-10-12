"""Telegram verification for user suggestions, independent of bulk discovery."""

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from random_telegram_bot.catalogue import normalize_channel
from random_telegram_bot.models import Channel, User
from random_telegram_bot.telegram_web import Deferred, TelegramHTTP, parse_preview


class InvalidChannel(Exception):
    """Telegram did not confirm a public broadcast channel."""


class ValidationUnavailable(Exception):
    """A temporary API failure must not approve or reject the candidate."""

    def __init__(self, retry_after: float = 300, stop: bool = False) -> None:
        self.retry_after = retry_after
        self.stop = stop
        super().__init__("telegram_temporarily_unavailable")


@dataclass(frozen=True, slots=True)
class VerifiedChannel:
    username: str
    subscriber_count: int | None = None
    subscriber_display: str = ""
    last_post_at: datetime | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


type CheckChannel = Callable[[str], Awaitable[VerifiedChannel]]


class ChannelValidator:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.reader = TelegramHTTP(client, delay=0)
        self.not_before = 0.0

    async def verify(self, username: str) -> VerifiedChannel:
        if time.monotonic() < self.not_before:
            raise ValidationUnavailable(self.not_before - time.monotonic(), stop=True)
        name = normalize_channel(username)
        try:
            status, _, document = await self.reader.fetch(f"https://t.me/s/{name}")
        except Deferred as error:
            if error.stop:
                self.not_before = time.monotonic() + error.retry_after
            raise ValidationUnavailable(error.retry_after, error.stop) from None
        if status in {301, 302, 303, 307, 308, 404, 410}:
            raise InvalidChannel
        if status != 200:
            raise ValidationUnavailable
        preview = parse_preview(document, name)
        if not preview.subscriber_label:
            raise InvalidChannel
        if len(preview.subscriber_display) > 32 or (
            preview.subscriber_count is None
            and not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?[KMB]", preview.subscriber_display, re.I)
        ):
            raise ValidationUnavailable
        checked = datetime.now(UTC)
        dates = [date for date in preview.dates if date <= checked.timestamp() + 86400]
        return VerifiedChannel(
            name,
            preview.subscriber_count,
            preview.subscriber_display,
            datetime.fromtimestamp(max(dates), UTC) if dates else None,
            checked,
        )


async def existing_channel(db: AsyncSession, username: str) -> Channel | None:
    # Imported legacy rows can still contain @name or public URL forms.
    forms = [
        form + suffix
        for form in (
            username,
            "@" + username,
            "https://t.me/" + username,
            "http://t.me/" + username,
        )
        for suffix in ("", "/")
    ]
    return await db.scalar(
        select(Channel)
        .where(func.lower(func.trim(Channel.channelnick)).in_(forms))
        .order_by(Channel.status.desc(), Channel.channel_id)
        .with_for_update()
    )


async def accept_suggestion(
    db: AsyncSession, user: User, username: str, check: CheckChannel | None
) -> str:
    user.awaiting_suggestion = True
    try:
        if check is None:
            raise ValidationUnavailable
        verified = await check(username)
    except InvalidChannel:
        return (
            "Не удалось подтвердить публичный канал. "
            "Проверьте адрес и отправьте его снова. Отмена: /cancel."
        )
    except ValidationUnavailable:
        return (
            "Проверка Telegram временно недоступна. Отправьте канал ещё раз позже. Отмена: /cancel."
        )
    canonical = await existing_channel(db, verified.username)
    if canonical is not None:
        if canonical.status:
            response = "Этот канал уже есть в каталоге."
        else:
            canonical.status = True
            response = "Канал проверен и подтверждён в каталоге. Спасибо!"
    else:
        canonical = Channel(channelnick=verified.username, status=True, added_at=datetime.now(UTC))
        db.add(canonical)
        user.suggested_channels += 1
        response = "Публичный канал проверен и добавлен в каталог. Спасибо!"
    apply_verification(canonical, verified)
    user.awaiting_suggestion = False
    return response


def apply_verification(channel: Channel, verified: VerifiedChannel) -> None:
    """One owner for UTC storage and Telegram preview metadata."""
    channel.subscriber_count = verified.subscriber_count
    channel.subscriber_display = verified.subscriber_display
    channel.last_post_at = (
        verified.last_post_at.astimezone(UTC).replace(tzinfo=None)
        if verified.last_post_at
        else None
    )
    channel.last_checked_at = verified.checked_at.astimezone(UTC).replace(tzinfo=None)
