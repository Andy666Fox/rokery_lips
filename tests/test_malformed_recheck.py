"""A malformed legacy channel name must not stop the re-verification batch (audit T2-03)."""

import asyncio

from random_telegram_bot.catalogue_jobs import verify_batch
from random_telegram_bot.models import Base, Channel, ChannelCheck
from random_telegram_bot.suggestions import VerifiedChannel
from random_telegram_bot.verification_queue import enqueue_untracked
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_malformed_name_is_rejected_and_batch_continues() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                db.add(Channel(channel_id=1, channelnick="bad!", status=True))
                db.add(Channel(channel_id=2, channelnick="@goodchannel", status=False))
            # Same path as the legacy backfill; then put the malformed row first.
            assert await enqueue_untracked(engine) == 2
            async with sessions.begin() as db:
                for entry in await db.scalars(select(ChannelCheck)):
                    entry.queue_order = entry.channel_id

            checked: list[str] = []

            async def check(name: str) -> VerifiedChannel:
                checked.append(name)
                return VerifiedChannel(name, 123, "123")

            assert await verify_batch(engine, check, delay=0) == {
                "valid": 1,
                "invalid": 1,
                "deferred": 0,
            }
            # No network check is attempted for a name that cannot be a username.
            assert checked == ["goodchannel"]
            async with sessions() as db:
                bad = await db.get(Channel, 1)
                bad_check = await db.get(ChannelCheck, 1)
                good = await db.get(Channel, 2)
                assert bad is not None and bad_check is not None and good is not None
                assert bad.status is False
                assert bad_check.state == "invalid"
                assert bad_check.last_error == "malformed_username"
                assert good.status is True
        finally:
            await engine.dispose()

    asyncio.run(scenario())
