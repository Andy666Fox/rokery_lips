"""Editable copy, live /help criteria and moderation across all catalogue sources."""

import asyncio
import importlib
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from aiogram.types import Update
from random_telegram_bot import messages
from random_telegram_bot.catalogue import Catalogue
from random_telegram_bot.discovery.client import Reader
from random_telegram_bot.discovery.publish import reviewed
from random_telegram_bot.handlers import prepare_actions
from random_telegram_bot.models import Base, Channel, ChannelCheck, User
from random_telegram_bot.moderation import POLICY_VERSION, criteria_text
from random_telegram_bot.suggestions import VerifiedChannel, accept_suggestion
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_editable_file_override_and_placeholders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "messages.toml"
    path.write_text('start = "Мой текст: {find_button}"\n', encoding="utf-8")
    try:
        with monkeypatch.context() as change:
            change.setenv("BOT_MESSAGES_FILE", str(path))
            importlib.reload(messages)
            assert messages.message("start", find_button="Кнопка") == "Мой текст: Кнопка"
            with pytest.raises(ValueError, match="check messages.toml"):
                messages.message("start")
    finally:
        importlib.reload(messages)


@pytest.mark.parametrize("content", ["text = 42", 'text = ""', 'text = "{user.password}"'])
def test_rejects_invalid_message_configuration(tmp_path: Path, content: str) -> None:
    path = tmp_path / "messages.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        messages.load_messages(path)


def test_help_command_includes_current_criteria() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        catalogue = Catalogue(sessions)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            update = Update.model_validate(
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 1,
                        "date": int(time.time()),
                        "text": "/help",
                        "chat": {"id": 1, "type": "private"},
                        "from": {"id": 1, "is_bot": False, "first_name": "Test"},
                    },
                }
            )
            async with sessions.begin() as db:
                actions = await prepare_actions(update, db, catalogue)
                assert actions[0]["text"] == messages.message("help", criteria=criteria_text())
                assert actions[0]["reply_markup"]["keyboard"][0][0]["text"] == messages.message(
                    "find_button"
                )
        finally:
            await catalogue.stop()
            await engine.dispose()

    asyncio.run(scenario())


def test_accepted_suggestion_is_queued_for_later_moderation() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        now = datetime.now(UTC)

        async def check(username: str) -> VerifiedChannel:
            return VerifiedChannel(username, 100, "100", now, now)

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                user = User(telegram_id=1)
                db.add(user)
                await db.flush()
                assert await accept_suggestion(db, user, "testchannel", check) == messages.message(
                    "channel_added"
                )
                channel = await db.scalar(select(Channel))
                receipt = await db.scalar(select(ChannelCheck))
                assert channel is not None and channel.status and channel.confirmed_at
                assert receipt is not None and receipt.state == "valid"
                assert receipt.next_check_at > now.timestamp()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_discovery_does_not_bypass_the_audience_filter() -> None:
    async def scenario() -> None:
        document = (
            '<div class="tgme_channel_info_counter"><span class="counter_value">9999</span>'
            '<span class="counter_type">subscribers</span></div>'
            '<div class="tgme_widget_message" data-post="testchannel/1">'
            '<div class="tgme_widget_message_text">Today we are sharing useful ideas '
            "and answering questions from our readers.</div>"
            f'<time datetime="{datetime.now(UTC).isoformat()}"></time></div>'
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=document))
        ) as client:
            observation = await Reader(client, "", delay=0).preview(
                "testchannel", now=time.time(), max_age_days=90
            )
            assert observation.state == "moderation_rejected"
            assert observation.metadata["moderation_reason"] == "too_many_subscribers"
        row = {
            "decision": "approve",
            "state": "web_ready",
            "checked_at": time.time(),
            "metadata": {
                "type": "channel",
                "identity_verified": True,
                "identity_source": "web_preview",
                "telegram_id": -1000000000001,
            },
            "sources": ["source"],
        }
        assert reviewed([row], time.time(), allow_web=True) == []
        row["metadata"]["moderation_version"] = POLICY_VERSION
        assert reviewed([row], time.time(), allow_web=True) == [row]

    asyncio.run(scenario())
