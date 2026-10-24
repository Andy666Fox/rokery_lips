"""Protect private administrator access, rolling counts and delivery accounting."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from aiogram.types import Update
from alembic import command
from alembic.config import Config
from random_telegram_bot.admin_info import info_text
from random_telegram_bot.catalogue import Catalogue
from random_telegram_bot.config import Settings
from random_telegram_bot.handlers import prepare_actions
from random_telegram_bot.main import create_app
from random_telegram_bot.models import Base, Channel, Inbox, MessageDelivery, User
from random_telegram_bot.queue import Workers
from random_telegram_bot.suggestions import VerifiedChannel, apply_verification
from sqlalchemy import func, insert, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def message(sender: int, *, chat_type: str = "private", text: str = "/info") -> Update:
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": int(time.time()),
                "text": text,
                "chat": {"id": sender if chat_type == "private" else -123, "type": chat_type},
                "from": {"id": sender, "is_bot": False, "first_name": "Test"},
            },
        }
    )


def test_rolling_statistics() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        now = datetime(2026, 10, 13, 12, tzinfo=UTC)
        day = now.replace(tzinfo=None) - timedelta(days=1)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                db.add_all(
                    [
                        Channel(
                            channelnick="newvalid", status=True, added_at=day, confirmed_at=day
                        ),
                        Channel(channelnick="newpending", status=False, added_at=day),
                        Channel(
                            channelnick="oldvalid",
                            status=True,
                            added_at=day - timedelta(seconds=1),
                            confirmed_at=day - timedelta(seconds=1),
                            last_checked_at=day,
                        ),
                        Channel(
                            channelnick="futurechan",
                            status=False,
                            added_at=now.replace(tzinfo=None) + timedelta(seconds=1),
                        ),
                        User(
                            telegram_id=1, last_online=now.replace(tzinfo=None) - timedelta(days=14)
                        ),
                        User(telegram_id=2, last_online=day),
                        User(telegram_id=3, last_online=day - timedelta(days=13, seconds=1)),
                        User(telegram_id=4),
                        User(
                            telegram_id=5,
                            last_online=now.replace(tzinfo=None) + timedelta(seconds=1),
                        ),
                    ]
                )
                for identifier, seconds, payload in (
                    (1, 86400, {"message": {"message_id": 1}}),
                    (2, 0, {"message": {"message_id": 2}}),
                    (3, 1, {"callback_query": {"id": "callback"}}),
                    (4, 86401, {"message": {"message_id": 4}}),
                    (5, -1, {"message": {"message_id": 5}}),
                ):
                    db.add(
                        Inbox(
                            update_id=identifier,
                            chat_id=1,
                            payload=payload,
                            received_at=now.timestamp() - seconds,
                        )
                    )
                for seconds in (86400, 0, 86401, -1):
                    db.add(MessageDelivery(delivered_at=now.timestamp() - seconds))
            async with sessions() as db:
                text = await info_text(db, now=now)
                assert "4 всего / 2 подтверждено" in text
                assert "5 всего / 2 активны за 14 дней" in text
                assert "Сообщения за 24 часа: 4 (входящие: 2, исходящие: 2)" in text
                assert "Добавлено каналов за 24 часа: 2" in text
                assert "Впервые подтверждено за 24 часа: 1" in text
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "sender,chat_type,admin,allowed",
    [
        (42, "private", 42, True),
        (43, "private", 42, False),
        (42, "group", 42, False),
        (42, "private", 0, False),
    ],
)
def test_admin_only(sender: int, chat_type: str, admin: int, allowed: bool) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                # /info takes priority even when /suggest is waiting for input.
                db.add(User(telegram_id=sender, awaiting_suggestion=True))
            async with sessions.begin() as db:
                actions = await prepare_actions(
                    message(sender, chat_type=chat_type, text="/info@sample_bot"),
                    db,
                    Catalogue(sessions),
                    admin_telegram_id=admin,
                )
                assert len(actions) == 1 and actions[0]["method"] == "sendMessage"
                text = actions[0]["text"]
                assert ("Статистика бота" in text) is allowed
                assert ("только администратору" in text) is not allowed
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_delivery_success_failure_and_callbacks() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        sent = []
        fail = True

        async def send(action: dict[str, Any]) -> None:
            if fail:
                raise RuntimeError("Temporary send failure")
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
                        payload=message(42).model_dump(mode="json", exclude_none=True),
                    )
                )
                db.add(
                    Inbox(
                        sequence=2,
                        update_id=2,
                        chat_id=42,
                        payload={},
                        actions=[
                            {"method": "answerCallbackQuery", "callback_query_id": "test"},
                            {"method": "editMessageReplyMarkup", "chat_id": 42, "message_id": 1},
                        ],
                    )
                )
            workers = Workers(sessions, Catalogue(sessions), send, admin_telegram_id=42)
            with pytest.raises(RuntimeError, match="Temporary send failure"):
                await workers.process(1)
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(MessageDelivery)) == 0
            fail = False
            await workers.process(1)
            await workers.process(2)
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(MessageDelivery)) == 1
            assert "Статистика бота" in sent[0]["text"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_first_confirmation_is_immutable() -> None:
    first = datetime(2026, 10, 1, tzinfo=UTC)
    channel = Channel(channelnick="testchan", status=False)
    apply_verification(channel, VerifiedChannel("testchan", checked_at=first))
    assert channel.status and channel.confirmed_at == first.replace(tzinfo=None)
    channel.status = False
    apply_verification(channel, VerifiedChannel("testchan", checked_at=first + timedelta(days=2)))
    assert channel.status and channel.confirmed_at == first.replace(tzinfo=None)
    assert channel.last_checked_at == (first + timedelta(days=2)).replace(tzinfo=None)


def test_operations_api_removed() -> None:
    async def scenario() -> None:
        app = create_app(Settings("sqlite+aiosqlite://", "x" * 32, ""))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            for path in ("/", "/api/database", "/api/schema/svg", "/api/logs"):
                assert (await client.get("/monitoring/operations" + path)).status_code == 404

    asyncio.run(scenario())


def test_duplicate_webhooks_are_one_incoming_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        url = "sqlite+aiosqlite:///" + (tmp_path / "webhooks.sqlite").as_posix()
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()
        app = create_app(Settings(url, "x" * 32, ""))
        async with app.router.lifespan_context(app):
            assert app.state.catalogue.running
            app.state.workers = SimpleNamespace(wake=asyncio.Event())
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                for _ in range(2):
                    response = await client.post(
                        "/telegram/webhook",
                        json=message(42).model_dump(mode="json", exclude_none=True),
                        headers={"x-telegram-bot-api-secret-token": "x" * 32},
                    )
                    assert response.status_code == 200
            async with app.state.sessions() as db:
                assert await db.scalar(select(func.count()).select_from(Inbox)) == 1
                assert "входящие: 1, исходящие: 0" in await info_text(db)
        assert not app.state.catalogue.running

    asyncio.run(scenario())


def test_migration_backfills_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "sqlite+aiosqlite:///" + (tmp_path / "migration.sqlite").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("WEBHOOK_SECRET", "x" * 32)
    config = Config("services/random_telegram_bot/alembic.ini")
    command.upgrade(config, "0006")

    async def seed() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sql_text(
                        "INSERT INTO channels (channel_id, channelnick, status, likes, dislikes, "
                        "avg_score, last_checked_at) VALUES "
                        "(1, 'validchan', 1, 0, 0, 0, '2026-10-13 12:00:00'), "
                        "(2, 'invalidchan', 0, 0, 0, 0, '2026-10-13 12:00:00')"
                    )
                )
                inbox = insert(Inbox)
                await connection.execute(
                    inbox,
                    [
                        {
                            "update_id": 1,
                            "chat_id": 42,
                            "payload": {},
                            "state": "done",
                            "actions": [
                                {"method": "sendMessage"},
                                {"method": "answerCallbackQuery"},
                            ],
                            "completed_at": 123.0,
                        },
                        {
                            "update_id": 2,
                            "chat_id": 42,
                            "payload": {},
                            "state": "dead",
                            "actions": [{"method": "sendMessage"}],
                            "completed_at": 124.0,
                        },
                    ],
                )
        finally:
            await engine.dispose()

    asyncio.run(seed())
    command.upgrade(config, "0007")
    command.upgrade(config, "0007")  # An already deployed revision must not duplicate history.

    async def verify() -> None:
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine)() as db:
                channels = list(await db.scalars(select(Channel).order_by(Channel.channel_id)))
                assert channels[0].confirmed_at == datetime(2026, 10, 13, 12)
                assert channels[1].confirmed_at is None
                assert list(await db.scalars(select(MessageDelivery.delivered_at))) == [123.0]
                assert (
                    await db.scalar(sql_text("SELECT version_num FROM alembic_version")) == "0007"
                )
        finally:
            await engine.dispose()

    asyncio.run(verify())
