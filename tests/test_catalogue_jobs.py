"""Protect import idempotence, legacy data and durable verification outcomes."""

import asyncio
import csv
import time
from pathlib import Path

from random_telegram_bot.catalogue_jobs import import_csv, verify_batch
from random_telegram_bot.models import Base, Channel, ChannelCheck, Rating
from random_telegram_bot.suggestions import InvalidChannel, ValidationUnavailable, VerifiedChannel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_import_resume_and_verification(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "channels.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["username", "source_urls", "attribution"])
            writer.writeheader()
            for name in ("existing", "validchan", "invalidchan", "retrychan"):
                writer.writerow(
                    {
                        "username": name,
                        "source_urls": "https://example.org/",
                        "attribution": "CC-BY",
                    }
                )
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                db.add(Channel(channel_id=7, channelnick="@Existing", status=True, likes=3))
                db.add(Rating(telegram_id=42, channel_id=7, score=1))
            result = await import_csv(engine, path)
            assert result["inserted_inactive"] == 3
            assert (await import_csv(engine, path))["inserted_inactive"] == 0
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(ChannelCheck)) == 3
                assert (
                    await db.scalar(
                        select(func.count()).select_from(Channel).where(Channel.status.is_(True))
                    )
                    == 1
                )

            async def check(name: str) -> VerifiedChannel:
                if name == "invalidchan":
                    raise InvalidChannel
                if name == "retrychan":
                    raise ValidationUnavailable(3600, stop=True)
                return VerifiedChannel(name, 123, "123")

            assert await verify_batch(engine, check, delay=0) == {
                "valid": 1,
                "invalid": 1,
                "deferred": 1,
            }
            async with sessions() as db:
                channels = {c.channelnick: c for c in await db.scalars(select(Channel))}
                assert channels["@Existing"].channel_id == 7 and channels["@Existing"].likes == 3
                assert channels["@Existing"].status
                assert (
                    channels["validchan"].status and channels["validchan"].subscriber_count == 123
                )
                assert not channels["invalidchan"].status
                assert not channels["retrychan"].status
                assert await db.get(Rating, (42, 7)) is not None
                entries = list(await db.scalars(select(ChannelCheck)))
                assert all(e.next_check_at > time.time() for e in entries)
            assert "cooldown" in await verify_batch(engine, check, delay=0)
            # Clear global cooldown and make a previously valid channel due.
            from random_telegram_bot.models import VerificationControl

            async with sessions.begin() as db:
                control = await db.get(VerificationControl, "telegram_web")
                assert control is not None
                control.not_before = 0
                entry = await db.scalar(select(ChannelCheck).where(ChannelCheck.state == "valid"))
                assert entry is not None
                entry.next_check_at = 0

            async def unavailable(name: str) -> VerifiedChannel:
                raise ValidationUnavailable

            assert (await verify_batch(engine, unavailable, delay=0))["deferred"] == 1
            async with sessions() as db:
                channel = await db.scalar(select(Channel).where(Channel.channelnick == "validchan"))
                assert channel is not None and channel.status
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_csv_does_not_partially_import(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "bad.csv"
        path.write_text(
            "username,source_urls,attribution\nvalidname,source,license\n../../bad,source,license\n",
            encoding="utf-8",
        )
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            import pytest

            with pytest.raises(ValueError, match="line 3"):
                await import_csv(engine, path)
            async with engine.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(Channel)) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
