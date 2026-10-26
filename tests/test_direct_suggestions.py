"""Propose channels by sending links, with no conversational suggestion state."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.types import Update
from random_telegram_bot.catalogue import Catalogue
from random_telegram_bot.handlers import prepare_actions
from random_telegram_bot.messages import message
from random_telegram_bot.models import Base, Channel, ChannelCheck, User
from random_telegram_bot.moderation import InvalidChannel, rejection_text
from random_telegram_bot.suggestions import ValidationUnavailable, VerifiedChannel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def incoming(text: str, /, **fields: Any) -> Update:
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1,
                "text": text,
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 1, "is_bot": False, "first_name": "Test"},
                **fields,
            },
        }
    )


@pytest.mark.parametrize(
    "update",
    [
        incoming("@TestChannel"),
        incoming("Посмотрите @testchannel"),
        incoming("https://t.me/TestChannel"),
        incoming("http://t.me/testchannel/"),
        incoming("t.me/testchannel"),
        incoming("https://telegram.me/testchannel"),
        incoming("https://t.me/s/testchannel/123"),
        incoming("Посмотрите: https://t.me/testchannel"),
        incoming(
            "Канал",
            entities=[
                {"type": "text_link", "offset": 0, "length": 5, "url": "https://t.me/testchannel"}
            ],
        ),
        incoming("", text=None, caption="https://t.me/testchannel"),
    ],
)
def test_link_is_verified_added_and_duplicate_does_not_increase_statistics(update: Update) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalogue = Catalogue(sessions)
        checked = []

        async def verify(username: str) -> VerifiedChannel:
            checked.append(username)
            return VerifiedChannel(username, 100, "100", datetime.now(UTC))

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                actions = await prepare_actions(update, db, catalogue, verify)
                assert actions[0]["text"] == message("channel_added")
                user = await db.get(User, 1)
                assert user is not None and user.suggested_channels == 1
                assert not user.awaiting_suggestion
                channel = await db.scalar(select(Channel))
                assert channel is not None and channel.status
                assert channel.channelnick == "testchannel"
                receipt = await db.scalar(select(ChannelCheck))
                assert receipt is not None and receipt.state == "valid"
            async with sessions.begin() as db:
                actions = await prepare_actions(update, db, catalogue, verify)
                assert actions[0]["text"] == message("channel_existing")
                user = await db.get(User, 1)
                assert user is not None and user.suggested_channels == 1
                assert await db.scalar(select(func.count()).select_from(Channel)) == 1
            assert checked == ["testchannel", "testchannel"]
        finally:
            await catalogue.stop()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("text", "reply_key"),
    [
        ("testchannel", "unknown_command"),
        ("/suggest", "unknown_command"),
        ("/suggest https://t.me/testchannel", "unknown_command"),
        ("https://example.com/testchannel", "suggest_bad_address"),
        ("https://t.me.evil.example/testchannel", "suggest_bad_address"),
        ("https://t.me/+privateinvite", "suggest_bad_address"),
        ("https://t.me/testchannel?start=token", "suggest_bad_address"),
        ("https://t.me/testchannel https://t.me/anotherchannel", "suggest_one_link"),
    ],
)
def test_other_input_does_not_check_or_create_channels(text: str, reply_key: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        catalogue = Catalogue(sessions)

        async def verify(username: str) -> VerifiedChannel:
            pytest.fail("Invalid input must not initiate a Telegram request")

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                db.add(User(telegram_id=1, awaiting_suggestion=True))
            async with sessions.begin() as db:
                actions = await prepare_actions(incoming(text), db, catalogue, verify)
                assert actions[0]["text"] == message(reply_key)
                user = await db.get(User, 1)
                assert user is not None and not user.awaiting_suggestion
                assert await db.scalar(select(Channel)) is None
        finally:
            await catalogue.stop()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "reason",
    [
        "not_public_channel",
        "subscriber_count_unknown",
        "too_many_subscribers",
        "last_post_unknown",
        "invalid_post_date",
        "last_post_too_old",
        "post_text_unknown",
        "language_unknown",
        "unsupported_or_uncertain_language",
        "future_unknown_reason",
        "temporarily_unavailable",
    ],
)
def test_failed_link_explains_the_reason_and_does_not_capture_next_command(reason: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalogue = Catalogue(sessions)
        checked = []

        async def reject(username: str) -> VerifiedChannel:
            checked.append(username)
            if reason == "temporarily_unavailable":
                raise ValidationUnavailable
            raise InvalidChannel(reason)

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                db.add(Channel(channelnick="existingchannel", status=True))
            async with sessions.begin() as db:
                actions = await prepare_actions(
                    incoming("https://t.me/testchannel"), db, catalogue, reject
                )
                expected = (
                    message("validation_unavailable")
                    if reason == "temporarily_unavailable"
                    else rejection_text(reason)
                )
                assert actions[0]["text"] == expected
                user = await db.get(User, 1)
                assert user is not None and not user.awaiting_suggestion
                assert user.suggested_channels == 0
                assert await db.scalar(select(func.count()).select_from(Channel)) == 1
            async with sessions.begin() as db:
                actions = await prepare_actions(incoming("/random"), db, catalogue, reject)
                assert actions[0]["text"] == message("random_channel", username="existingchannel")
                user = await db.get(User, 1)
                assert user is not None and user.viewed_channels == 1
                assert checked == ["testchannel"]
        finally:
            await catalogue.stop()
            await engine.dispose()

    asyncio.run(scenario())


def test_rejection_wording_and_limits_are_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    from random_telegram_bot import messages, moderation

    monkeypatch.setitem(messages._templates, "moderation_rejected", "Отказ: {reason}")
    monkeypatch.setitem(
        messages._templates, "moderation_reason_too_many_subscribers", "Лимит: {max_subscribers}"
    )
    monkeypatch.setattr(moderation, "MAX_SUBSCRIBERS", 123)
    assert rejection_text("too_many_subscribers") == "Отказ: Лимит: 123"
