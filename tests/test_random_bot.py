"""Contracts for catalogue isolation, webhook durability and restart recovery."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from random_telegram_bot.catalogue import Catalogue, RateLimiter, normalize_channel
from random_telegram_bot.config import Settings
from random_telegram_bot.main import create_app
from random_telegram_bot.models import Base, Channel, Inbox, User
from random_telegram_bot.queue import Workers, claim
from server_monitor.probes import snapshot_is_fresh
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def database_url(path: Path) -> str:
    return "sqlite+aiosqlite:///" + path.as_posix()


async def initialize(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


def update(update_id: int, chat_id: int = 9876543210) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": int(time.time()),
            "text": "/random",
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
        },
    }


def test_public_catalogue_filters_inactive_invalid_and_never_registers_visitors(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "bot.db")
    asyncio.run(initialize(url))

    async def seed() -> None:
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine)
        async with sessions.begin() as db:
            db.add_all(
                [
                    Channel(channelnick="active_one"),
                    Channel(channelnick="hidden_one", status=False),
                    Channel(channelnick="bad/link"),
                ]
            )
        await engine.dispose()

    asyncio.run(seed())
    with TestClient(create_app(Settings(url, "s" * 40, ""))) as client:
        response = client.get("/api/random/channel")
        assert response.status_code == 200
        assert response.json() == {
            "channel": {"username": "active_one", "url": "https://t.me/active_one"}
        }
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/health/db").status_code == 200
        assert client.get("/health/ready").status_code == 503

    async def count() -> None:
        engine = create_async_engine(url)
        async with async_sessionmaker(engine)() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 0
        await engine.dispose()

    asyncio.run(count())


def test_empty_catalogue_and_rate_limit(tmp_path: Path) -> None:
    url = database_url(tmp_path / "bot.db")
    asyncio.run(initialize(url))
    with TestClient(create_app(Settings(url, "s" * 40, ""))) as client:
        assert client.get("/api/random/channel").status_code == 404
        for _ in range(19):
            client.get("/api/random/channel")
        assert client.get("/api/random/channel").status_code == 429
    limiter = RateLimiter(max_clients=1)
    assert limiter.allow("a")
    assert not limiter.allow("b")


def test_webhook_auth_validation_and_duplicate_admission(tmp_path: Path) -> None:
    url = database_url(tmp_path / "bot.db")
    asyncio.run(initialize(url))
    config = Settings(url, "s" * 40, "12345678:" + "a" * 35)
    headers = {"X-Telegram-Bot-Api-Secret-Token": config.webhook_secret}
    # Hold workers to verify admission persists before any handler executes.
    with patch.object(Workers, "start"), TestClient(create_app(config)) as client:
        assert client.post("/telegram/webhook", json=update(1)).status_code == 403
        assert client.post("/telegram/webhook", headers=headers, content="{bad").status_code == 422
        assert (
            client.post("/telegram/webhook", headers=headers, content="a" * 65537).status_code
            == 413
        )
        for _ in range(2):
            assert (
                client.post("/telegram/webhook", headers=headers, json=update(1)).status_code == 200
            )

    async def verify() -> None:
        engine = create_async_engine(url)
        async with async_sessionmaker(engine)() as db:
            rows = (await db.scalars(select(Inbox))).all()
            assert len(rows) == 1 and rows[0].state == "pending"
            assert rows[0].chat_id == 9876543210
        await engine.dispose()

    asyncio.run(verify())


def test_database_failure_never_acknowledges_update(tmp_path: Path) -> None:
    # A database with no schema simulates an unavailable migration / table.
    config = Settings(database_url(tmp_path / "missing.db"), "s" * 40, "12345678:" + "a" * 35)
    with patch.object(Workers, "start"), TestClient(create_app(config)) as client:
        response = client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": config.webhook_secret},
            json=update(1),
        )
        assert response.status_code == 503


@pytest.mark.parametrize("callback", [False, True])
def test_webhook_preserves_wire_json_with_link_preview_defaults(
    tmp_path: Path, callback: bool
) -> None:
    url = database_url(tmp_path / "bot.db")
    asyncio.run(initialize(url))
    config = Settings(url, "s" * 40, "12345678:" + "a" * 35)
    payload = json.loads(json.dumps(update(42)))
    payload["message"]["link_preview_options"] = {"is_disabled": True}
    if callback:
        payload = {
            "update_id": 42,
            "callback_query": {
                "id": "test-callback",
                "from": payload["message"]["from"],
                "chat_instance": "test-instance",
                "data": "rate:1:1",
                "message": payload["message"],
            },
        }
    with patch.object(Workers, "start"), TestClient(create_app(config)) as client:
        for _ in range(2):
            response = client.post(
                "/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": config.webhook_secret},
                json=payload,
            )
            assert response.status_code == 200

    async def verify() -> None:
        engine = create_async_engine(url)
        async with async_sessionmaker(engine)() as db:
            rows = (await db.scalars(select(Inbox))).all()
            assert len(rows) == 1
            assert rows[0].payload == payload
        await engine.dispose()

    asyncio.run(verify())


def test_leases_preserve_chat_order_and_restart_does_not_repeat_database_effects(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        url = database_url(tmp_path / "bot.db")
        await initialize(url)
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as db:
            db.add(Channel(channelnick="active_one"))
            db.add_all(
                [
                    Inbox(
                        update_id=i,
                        chat_id=10 if i < 3 else 20,
                        payload=update(i, 10 if i < 3 else 20),
                    )
                    for i in (1, 2, 3)
                ]
            )
        first = await claim(sessions)
        other_chat = await claim(sessions)
        assert first == 1 and other_chat == 3
        assert await claim(sessions) is None

        async def fail_send(action: dict[str, object]) -> None:
            raise ConnectionError("simulated network failure")

        worker = Workers(sessions, Catalogue(sessions), fail_send)
        assert first is not None
        try:
            await worker.process(first)
        except ConnectionError as error:
            await worker.retry(first, error)
        async with sessions.begin() as db:
            row = await db.get(Inbox, first)
            assert row is not None and row.actions is not None
            row.next_attempt_at = 0
            user = await db.get(User, 10)
            assert user is not None and user.viewed_channels == 1
        sent: list[dict[str, object]] = []

        async def send(action: dict[str, object]) -> None:
            sent.append(action)

        restarted = Workers(sessions, Catalogue(sessions), send)
        assert await claim(sessions) == first
        await restarted.process(first)
        async with sessions() as db:
            user = await db.get(User, 10)
            assert user is not None and user.viewed_channels == 1
        assert len(sent) == 1
        assert await claim(sessions) == 2
        # The abandoned lease on the other chat can be reclaimed after expiry.
        async with sessions.begin() as db:
            row = await db.get(Inbox, 3)
            assert row is not None
            row.lease_until = 0
        assert await claim(sessions) == 3
        await engine.dispose()

    asyncio.run(exercise())


def test_snapshot_probe_rejects_stale_malformed_and_future_data(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    for generated_at, expected in ((99, True), (70, False), (110, False)):
        path.write_text(json.dumps({"schema_version": 1, "generated_at": generated_at}))
        assert snapshot_is_fresh(path, 100) is expected
    path.write_text("broken")
    assert not snapshot_is_fresh(path, 100)
    assert normalize_channel("https://t.me/Active_one/") == "active_one"
