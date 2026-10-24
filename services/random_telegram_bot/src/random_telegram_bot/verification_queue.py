"""Enroll every catalogue source in the existing durable recheck queue."""

import hashlib
from datetime import datetime
from typing import Literal

from sqlalchemy import Table, func, literal, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from random_telegram_bot.catalogue import normalize_channel
from random_telegram_bot.models import Channel, ChannelCheck
from random_telegram_bot.moderation import RECHECK_SECONDS

SOURCE = "Direct catalogue verification; not a CSV import"
SOURCE_HASH = hashlib.sha256(SOURCE.encode()).hexdigest()


async def record_check(
    db: AsyncSession,
    channel: Channel,
    state: Literal["valid", "invalid"],
    checked_at: datetime,
    *,
    reason: str | None = None,
) -> None:
    await db.flush()
    entry = await db.get(ChannelCheck, channel.channel_id, with_for_update=True)
    if entry is None:
        entry = ChannelCheck(
            channel_id=channel.channel_id,
            attempts=0,
            import_sha256=SOURCE_HASH,
            source_urls="https://t.me/" + normalize_channel(channel.channelnick),
            attribution=SOURCE,
        )
        db.add(entry)
    entry.state = state
    entry.attempts = (entry.attempts or 0) + 1
    entry.next_check_at = checked_at.timestamp() + RECHECK_SECONDS
    entry.last_error = reason


async def enqueue_untracked(engine: AsyncEngine) -> int:
    """Keep existing approval/status untouched; add missing queue receipts idempotently."""
    channels, checks = Channel.__table__, ChannelCheck.__table__
    assert isinstance(channels, Table) and isinstance(checks, Table)
    columns = [
        "channel_id",
        "state",
        "next_check_at",
        "attempts",
        "queue_order",
        "import_sha256",
        "source_urls",
        "attribution",
    ]
    mysql = engine.dialect.name == "mysql"
    order = func.floor(func.rand() * 2**53) if mysql else func.abs(func.random() % 2**53)
    rows = (
        select(
            channels.c.channel_id,
            literal("pending"),
            literal(0),
            literal(0),
            order,
            literal(SOURCE_HASH),
            literal(""),
            literal(SOURCE),
        )
        .select_from(channels.outerjoin(checks, checks.c.channel_id == channels.c.channel_id))
        .where(checks.c.channel_id.is_(None))
    )
    if mysql:
        statement = mysql_insert(checks).from_select(columns, rows)
        statement = statement.on_duplicate_key_update(channel_id=checks.c.channel_id)
    else:
        statement = sqlite_insert(checks).from_select(columns, rows)
        statement = statement.on_conflict_do_nothing(index_elements=["channel_id"])
    async with engine.begin() as connection:
        result = await connection.execute(statement)
        return max(0, result.rowcount)
