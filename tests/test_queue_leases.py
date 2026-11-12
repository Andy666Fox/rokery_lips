"""A reclaimed inbox entry must reject its previous worker's state changes."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from random_telegram_bot import queue
from random_telegram_bot.catalogue import Catalogue
from random_telegram_bot.models import Base, Inbox
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def test_reclaimed_job_rejects_stale_process_and_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        clock = SimpleNamespace(value=1794432065.125)
        monkeypatch.setattr(queue, "time", SimpleNamespace(time=lambda: clock.value))
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        sent = []

        async def send(action: dict[str, Any]) -> None:
            sent.append(action)

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                db.add(
                    Inbox(
                        sequence=1,
                        update_id=1,
                        chat_id=42,
                        payload={},
                        actions=[{"method": "sendMessage", "chat_id": 42, "text": "test"}],
                    )
                )
            workers = queue.Workers(sessions, Catalogue(sessions), send)
            old = await queue.claim(sessions)
            assert old is not None
            assert await queue.claim(sessions) is None
            clock.value += 121
            current = await queue.claim(sessions)
            assert current is not None
            await workers.process(old)
            assert sent == [], "The previous owner must not send after a reclaim"
            await workers.retry(old, RuntimeError("stale failure"))
            async with sessions() as db:
                row = await db.get(Inbox, 1)
                assert row is not None and row.state == "processing" and row.attempts == 2
            await workers.process(current)
            assert len(sent) == 1
            await workers.retry(old, RuntimeError("late failure after completion"))
            await workers.process(current)
            assert len(sent) == 1, "A completed claim must not resend"
            async with sessions() as db:
                row = await db.get(Inbox, 1)
                assert row is not None and row.state == "done" and row.last_error is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@asynccontextmanager
async def _queued_actions() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions.begin() as db:
            db.add(
                Inbox(
                    sequence=1,
                    update_id=1,
                    chat_id=42,
                    payload={},
                    actions=[
                        {"method": "sendMessage", "chat_id": 42, "text": "first"},
                        {"method": "sendMessage", "chat_id": 42, "text": "second"},
                    ],
                )
            )
        yield sessions
    finally:
        await engine.dispose()


def test_late_sender_cannot_finish_or_continue_reclaimed_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        clock = SimpleNamespace(value=1794432065.125)
        monkeypatch.setattr(queue, "time", SimpleNamespace(time=lambda: clock.value))
        entered, release = asyncio.Event(), asyncio.Event()
        sent = []

        async def send(action: dict[str, Any]) -> None:
            sent.append(action)
            entered.set()
            await release.wait()

        async with _queued_actions() as sessions:
            workers = queue.Workers(sessions, Catalogue(sessions), send)
            old = await queue.claim(sessions)
            assert old is not None
            task = asyncio.create_task(workers.process(old))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                clock.value += 121
                current = await queue.claim(sessions)
                assert current is not None and current.attempt > old.attempt
                release.set()
                assert await task is False
                assert len(sent) == 1, "Stale worker must not start the second action"
                await workers.retry(old, RuntimeError("late failure"))
                async with sessions() as db:
                    row = await db.get(Inbox, 1)
                    assert row is not None and row.attempts == current.attempt
                    assert row.state == "processing" and row.completed_at is None
                    assert row.last_error is None and row.lease_until == current.expires_at
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_processing_deadline_cancels_sender_and_can_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(queue, "LEASE_SECONDS", 0.5)
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def send(action: dict[str, Any]) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with _queued_actions() as sessions:
            workers = queue.Workers(sessions, Catalogue(sessions), send)
            lease = await queue.claim(sessions)
            assert lease is not None
            started = asyncio.get_running_loop().time()
            task = asyncio.create_task(workers.process(lease))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(task, 2)
                assert asyncio.get_running_loop().time() - started < 1.5
                assert cancelled.is_set()
                await workers.retry(lease, TimeoutError())
                async with sessions() as db:
                    row = await db.get(Inbox, 1)
                    assert row is not None and row.state == "pending"
                    assert row.lease_until == 0 and row.last_error == "TimeoutError"
                    assert row.next_attempt_at > queue.time.time()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
