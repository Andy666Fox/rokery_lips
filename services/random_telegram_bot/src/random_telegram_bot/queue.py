"""MySQL inbox with leases, duplicate protection and per-chat ordering.

Telegram sends have at-least-once semantics: a crash after Telegram accepts a
message but before the completion commit may repeat that message. Database
effects and prepared replies are committed together and are not repeated.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendMessage
from aiogram.types import Update
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from random_telegram_bot.catalogue import Catalogue
from random_telegram_bot.handlers import prepare_actions
from random_telegram_bot.models import Inbox, MessageDelivery
from random_telegram_bot.suggestions import CheckChannel

logger = logging.getLogger(__name__)


async def claim(sessions: async_sessionmaker[AsyncSession]) -> int | None:
    now = time.time()
    earlier = aliased(Inbox)
    async with sessions.begin() as db:
        row = await db.scalar(
            select(Inbox)
            .where(
                Inbox.state.in_(("pending", "processing")),
                Inbox.next_attempt_at <= now,
                Inbox.lease_until <= now,
                ~exists(
                    select(earlier.sequence).where(
                        earlier.chat_id == Inbox.chat_id,
                        earlier.sequence < Inbox.sequence,
                        earlier.state.in_(("pending", "processing")),
                    )
                ),
            )
            .order_by(Inbox.sequence)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        row.state = "processing"
        row.lease_until = now + 120
        row.attempts += 1
        return row.sequence


async def deliver(bot: Bot, action: dict[str, Any]) -> None:
    data = dict(action)
    method = data.pop("method")
    if method == "sendMessage":
        await bot(SendMessage.model_validate(data))
    elif method == "answerCallbackQuery":
        try:
            await bot(AnswerCallbackQuery.model_validate(data))
        except TelegramBadRequest as error:
            if not any(
                reason in error.message.lower()
                for reason in ("query is too old", "query_id_invalid")
            ):
                raise
    elif method == "editMessageReplyMarkup":
        try:
            await bot(EditMessageReplyMarkup.model_validate(data))
        except TelegramBadRequest as error:
            if not any(
                reason in error.message.lower()
                for reason in ("message is not modified", "message to edit not found")
            ):
                raise
    else:
        raise ValueError("Unsupported durable reply method")


class Workers:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        catalogue: Catalogue,
        sender: Callable[[dict[str, Any]], Awaitable[None]],
        count: int = 4,
        *,
        check_channel: CheckChannel | None = None,
        admin_telegram_id: int = 0,
    ) -> None:
        self.sessions = sessions
        self.catalogue = catalogue
        self.sender = sender
        self.count = count
        self.check_channel = check_channel
        self.admin_telegram_id = admin_telegram_id
        self.wake = asyncio.Event()
        self.tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self.run(), name=f"webhook-worker-{i}") for i in range(self.count)
        ]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def process(self, sequence: int) -> None:
        # A persisted action list doubles as the receipt for all business effects.
        async with self.sessions.begin() as db:
            row = await db.get(Inbox, sequence, with_for_update=True)
            assert row is not None
            if row.actions is None:
                row.actions = await prepare_actions(
                    Update.model_validate(row.payload),
                    db,
                    self.catalogue,
                    self.check_channel,
                    admin_telegram_id=self.admin_telegram_id,
                )
            assert row.actions is not None
            actions = list(row.actions)
        for action in actions:
            await self.sender(action)
            if action["method"] == "sendMessage":
                # Record only successful sends, including actual repeats after a retry.
                async with self.sessions.begin() as db:
                    db.add(MessageDelivery())
        async with self.sessions.begin() as db:
            row = await db.get(Inbox, sequence, with_for_update=True)
            assert row is not None
            row.state = "done"
            row.completed_at = time.time()
            row.lease_until = 0
            row.last_error = None

    async def retry(self, sequence: int, error: Exception) -> None:
        async with self.sessions.begin() as db:
            row = await db.get(Inbox, sequence, with_for_update=True)
            assert row is not None
            permanent = isinstance(error, (TelegramForbiddenError, TelegramBadRequest))
            row.state = "dead" if permanent or row.attempts >= 8 else "pending"
            delay = min(60, 2**row.attempts)
            if isinstance(error, TelegramRetryAfter):
                delay = max(delay, error.retry_after)
            row.next_attempt_at = time.time() + delay
            row.lease_until = 0
            # Exception strings from HTTP clients may contain credentials or payloads.
            row.last_error = type(error).__name__
        logger.warning(
            "Webhook delivery failed: sequence=%s error=%s", sequence, type(error).__name__
        )

    async def run(self) -> None:
        next_cleanup = 0.0
        while True:
            try:
                self.wake.clear()
                sequence = await claim(self.sessions)
                if sequence is not None:
                    try:
                        await self.process(sequence)
                    except Exception as error:
                        await self.retry(sequence, error)
                    continue
                if time.monotonic() >= next_cleanup:
                    async with self.sessions.begin() as db:
                        await db.execute(
                            delete(Inbox).where(
                                Inbox.state == "done",
                                Inbox.completed_at < time.time() - 7 * 86400,
                            )
                        )
                        await db.execute(
                            delete(MessageDelivery).where(
                                MessageDelivery.delivered_at < time.time() - 7 * 86400
                            )
                        )
                    next_cleanup = time.monotonic() + 3600
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), timeout=0.25)
            except Exception as error:
                logger.error("Webhook worker recovered from %s", type(error).__name__)
                await asyncio.sleep(2)
