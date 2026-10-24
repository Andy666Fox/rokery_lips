"""Large catalogues and status changes must remain usable with a warm cache."""

import asyncio
from typing import Any

import pytest
from random_telegram_bot.catalogue import Catalogue
from random_telegram_bot.models import Base, Channel
from sqlalchemy import insert, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncResult,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def test_large_catalogue_and_retired_channels() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalogue = Catalogue(sessions)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.execute(
                    insert(Channel),
                    [{"channelnick": f"channel{i}", "status": True} for i in range(2501)]
                    + [
                        {"channelnick": "bad", "status": True},
                        {"channelnick": "inactive", "status": False},
                    ],
                )
            for _ in range(10):
                result = await catalogue.choose()
                assert result is not None and result[1].startswith("channel")
            async with sessions.begin() as db:
                await db.execute(update(Channel).values(status=False))
            assert await catalogue.choose() is None
        finally:
            await catalogue.stop()
            await engine.dispose()

    asyncio.run(scenario())


def test_refresh_failure_keeps_snapshot_and_recovers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalogue = Catalogue(sessions, ttl=0.01)
        retrying = asyncio.Event()
        release = asyncio.Event()
        original_stream = AsyncSession.stream
        attempts = 0

        async def unreliable_stream(
            self: AsyncSession, *args: Any, **kwargs: Any
        ) -> AsyncResult[Any]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise SQLAlchemyError("sensitive connection details")
            retrying.set()
            await release.wait()
            return await original_stream(self, *args, **kwargs)

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.execute(
                    insert(Channel), [{"channelnick": "channel_one", "status": True}]
                )
            await catalogue.start()
            async with sessions.begin() as db:
                db.add(Channel(channelnick="channel_two", status=True))
            monkeypatch.setattr(AsyncSession, "stream", unreliable_stream)
            await asyncio.wait_for(retrying.wait(), timeout=1)
            assert catalogue.running
            assert await catalogue.choose() == (1, "channel_one")
            assert "keeping snapshot and retrying" in caplog.text
            assert "sensitive connection details" not in caplog.text
            release.set()
            monkeypatch.setattr("random_telegram_bot.catalogue.secrets.choice", lambda ids: ids[-1])
            async with asyncio.timeout(1):
                while await catalogue.choose() != (2, "channel_two"):
                    await asyncio.sleep(0.01)
        finally:
            await catalogue.stop()
            await engine.dispose()

    asyncio.run(scenario())


def test_empty_catalogue_refreshes_without_request_traffic() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        catalogue = Catalogue(sessions, ttl=0.01)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await catalogue.start()
            assert await catalogue.choose() is None
            async with sessions.begin() as db:
                db.add(Channel(channelnick="newchannel", status=True))
            async with asyncio.timeout(1):
                while await catalogue.choose() is None:
                    await asyncio.sleep(0.01)
            assert await catalogue.choose() == (1, "newchannel")
        finally:
            await catalogue.stop()
            await engine.dispose()

    asyncio.run(scenario())


def test_startup_warms_once_for_concurrent_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        catalogue = Catalogue(sessions)
        original_stream = AsyncSession.stream
        loads = 0

        async def counted_stream(self: AsyncSession, *args: Any, **kwargs: Any) -> AsyncResult[Any]:
            nonlocal loads
            loads += 1
            return await original_stream(self, *args, **kwargs)

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            monkeypatch.setattr(AsyncSession, "stream", counted_stream)
            assert await asyncio.gather(*(catalogue.choose() for _ in range(5))) == [None] * 5
            assert loads == 1
            assert catalogue.running
        finally:
            await catalogue.stop()
            assert not catalogue.running
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan")])
def test_rejects_invalid_refresh_interval(interval: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        Catalogue(async_sessionmaker(), ttl=interval)


def test_slow_refresh_does_not_delay_warm_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        release = asyncio.Event()
        refreshing = asyncio.Event()
        catalogue = Catalogue(sessions, ttl=0.01)
        original_stream = AsyncSession.stream

        async def delayed_stream(self: AsyncSession, *args: Any, **kwargs: Any) -> AsyncResult[Any]:
            refreshing.set()
            await release.wait()
            return await original_stream(self, *args, **kwargs)

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.execute(
                    insert(Channel), [{"channelnick": "channel_one", "status": True}]
                )
            assert await catalogue.choose() is not None
            monkeypatch.setattr(AsyncSession, "stream", delayed_stream)
            await asyncio.wait_for(refreshing.wait(), timeout=1)
            assert await asyncio.wait_for(catalogue.choose(), timeout=0.5) is not None
        finally:
            await catalogue.stop()
            await engine.dispose()

    asyncio.run(scenario())
