"""Persisted first-pass ordering must break alphabet bias and respect deadlines."""

import asyncio
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from random_telegram_bot.catalogue_jobs import verify_batch
from random_telegram_bot.models import Base, Channel, ChannelCheck
from random_telegram_bot.suggestions import VerifiedChannel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_first_pass_uses_persistent_order_and_keeps_deadlines() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        visited: list[str] = []

        async def check(name: str) -> VerifiedChannel:
            visited.append(name)
            return VerifiedChannel(name)

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                for index, letter in enumerate("abcdefghijklmnopqrstuvwxyz", 1):
                    db.add(Channel(channel_id=index, channelnick=letter + "channel", status=False))
                    db.add(
                        ChannelCheck(
                            channel_id=index,
                            state="pending",
                            attempts=0,
                            next_check_at=0,
                            queue_order=27 - index,
                            import_sha256="a" * 64,
                            source_urls="",
                            attribution="",
                        )
                    )
                db.add(Channel(channel_id=99, channelnick="retrychannel", status=True))
                db.add(
                    ChannelCheck(
                        channel_id=99,
                        state="deferred",
                        attempts=2,
                        next_check_at=time.time() + 3600,
                        queue_order=0,
                        import_sha256="a" * 64,
                        source_urls="",
                        attribution="",
                    )
                )
            assert await verify_batch(engine, check, limit=3, delay=0) == {
                "valid": 3,
                "invalid": 0,
                "deferred": 0,
            }
            # A new invocation resumes the same stored order rather than reshuffling it.
            await verify_batch(engine, check, limit=2, delay=0)
            assert visited == [letter + "channel" for letter in "zyxwv"]
            async with sessions() as db:
                retry = await db.get(ChannelCheck, 99)
                assert retry is not None and retry.attempts == 2
                assert retry.state == "deferred" and retry.next_check_at > time.time()
                channel = await db.get(Channel, 99)
                assert channel is not None and channel.status
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_shuffle_migration_preserves_state_and_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "sqlite+aiosqlite:///" + (tmp_path / "shuffle.sqlite").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("WEBHOOK_SECRET", "x" * 32)
    config = Config("services/random_telegram_bot/alembic.ini")
    command.upgrade(config, "0007")

    async def seed() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                for identifier, state, attempts, deadline in (
                    (1, "pending", 0, 0),
                    (2, "pending", 0, 0),
                    (3, "deferred", 2, 1234567890.0),
                    (4, "valid", 1, 9999999999.0),
                ):
                    await connection.execute(
                        text(
                            "INSERT INTO channels "
                            "(channel_id,channelnick,status,likes,dislikes,avg_score) "
                            "VALUES (:id,:name,:status,0,0,0)"
                        ),
                        {
                            "id": identifier,
                            "name": f"channel{identifier}",
                            "status": state == "valid",
                        },
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO channel_checks (channel_id,state,attempts,next_check_at, "
                            "import_sha256,source_urls,attribution) "
                            "VALUES (:id,:state,:attempts,:due,'hash','','')"
                        ),
                        {"id": identifier, "state": state, "attempts": attempts, "due": deadline},
                    )
        finally:
            await engine.dispose()

    asyncio.run(seed())
    numbers = iter((200, 100))
    monkeypatch.setattr("secrets.randbits", lambda bits: next(numbers))
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    async def verify() -> None:
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine)() as db:
                rows = list(
                    await db.scalars(select(ChannelCheck).order_by(ChannelCheck.channel_id))
                )
                assert [r.queue_order for r in rows] == [200, 100, 0, 0]
                assert [(r.state, r.attempts, r.next_check_at) for r in rows] == [
                    ("pending", 0, 0),
                    ("pending", 0, 0),
                    ("deferred", 2, 1234567890.0),
                    ("valid", 1, 9999999999.0),
                ]
                assert list(
                    await db.scalars(select(Channel.status).order_by(Channel.channel_id))
                ) == [False, False, False, True]
        finally:
            await engine.dispose()

    asyncio.run(verify())
