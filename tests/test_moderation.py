"""Public-channel moderation boundaries, evidence parsing and suggestion responses."""

import asyncio
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

import httpx
import pytest
from random_telegram_bot.messages import message
from random_telegram_bot.models import Base, Channel, ChannelCheck, User
from random_telegram_bot.moderation import (
    MAX_SUBSCRIBERS,
    TEXT_POSTS,
    InvalidChannel,
    check_preview,
)
from random_telegram_bot.suggestions import (
    ChannelValidator,
    ValidationUnavailable,
    accept_suggestion,
)
from random_telegram_bot.telegram_web import parse_preview
from random_telegram_bot.verification_queue import enqueue_untracked
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
RUSSIAN = (
    "Сегодня мы рассказываем о новых проектах, обсуждаем интересные книги "
    "и делимся полезными советами для наших читателей."
)
ENGLISH = (
    "Today we are sharing useful ideas about science and technology, discussing new projects "
    "and answering questions from our readers."
)
FRENCH = (
    "Bonjour à tous, nous partageons les dernières nouvelles de notre communauté "
    "et les prochains événements dans notre ville."
)


def preview_html(
    *,
    subscribers: str = "1500",
    age: timedelta = timedelta(days=1),
    texts: list[str] | None = None,
    dates: bool = True,
    username: str = "testchannel",
) -> str:
    posts = []
    for index, text in enumerate([ENGLISH] if texts is None else texts, 1):
        date = f'<time datetime="{(NOW - age).isoformat()}"></time>' if dates else ""
        posts.append(
            f'<div class="tgme_widget_message" data-post="{username}/{index}">'
            f'<div class="tgme_widget_message_text">{escape(text)}</div>{date}</div>'
        )
    return (
        '<div class="tgme_channel_info_counter">'
        f'<span class="counter_value">{subscribers}</span>'
        '<span class="counter_type">subscribers</span></div>' + "".join(posts)
    )


@pytest.mark.parametrize("text", [RUSSIAN, ENGLISH, RUSSIAN + " " + ENGLISH])
def test_accepts_supported_languages_and_inclusive_limits(text: str) -> None:
    preview = parse_preview(
        preview_html(subscribers="1 500", age=timedelta(days=14), texts=[text]), "testchannel"
    )
    assert check_preview(preview, NOW) == NOW - timedelta(days=14)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"subscribers": "1501"}, "too_many_subscribers"),
        ({"subscribers": "1.5K"}, "subscriber_count_unknown"),
        ({"age": timedelta(days=14, seconds=1)}, "last_post_too_old"),
        ({"age": timedelta(seconds=-1)}, "invalid_post_date"),
        ({"dates": False}, "last_post_unknown"),
        ({"texts": [""]}, "post_text_unknown"),
        ({"texts": ["https://example.com 12345 🎉"]}, "language_unknown"),
        ({"texts": [FRENCH]}, "unsupported_or_uncertain_language"),
        (
            {"texts": ["这是一个中文频道，我们每天分享技术新闻以及生活中的有趣故事。"]},
            "unsupported_or_uncertain_language",
        ),
    ],
)
def test_rejects_failed_or_missing_evidence(changes: dict[str, Any], reason: str) -> None:
    preview = parse_preview(preview_html(**changes), "testchannel")
    with pytest.raises(InvalidChannel, match=reason):
        check_preview(preview, NOW)


def test_language_uses_ten_latest_text_posts_and_ignores_foreign_widgets() -> None:
    html = preview_html(texts=[FRENCH] + [ENGLISH, RUSSIAN] * (TEXT_POSTS // 2))
    html += preview_html(texts=[FRENCH], username="anotherchannel")
    preview = parse_preview(html, "testchannel")
    assert check_preview(preview, NOW)
    preview = parse_preview(preview_html(texts=[ENGLISH] * TEXT_POSTS + [FRENCH]), "testchannel")
    with pytest.raises(InvalidChannel, match="unsupported_or_uncertain_language"):
        check_preview(preview, NOW)


def test_recent_media_post_does_not_supply_language_evidence() -> None:
    preview = parse_preview(preview_html(texts=[ENGLISH, ""]), "testchannel")
    assert check_preview(preview, NOW)


def test_incidental_mathematical_symbols_do_not_change_english_language() -> None:
    preview = parse_preview(
        preview_html(texts=[ENGLISH + " Coordinates α and β are shown below."]), "testchannel"
    )
    assert check_preview(preview, NOW)


def test_validator_enforces_moderation_and_preserves_transport_failures() -> None:
    async def scenario() -> None:
        # Dates are relative to a fixed past instant, so the channel is certainly stale now.
        document = preview_html(age=timedelta(days=3650))
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=document))
        ) as client:
            with pytest.raises(InvalidChannel, match="last_post_too_old"):
                await ChannelValidator(client).verify("testchannel")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503))
        ) as client:
            with pytest.raises(ValidationUnavailable):
                await ChannelValidator(client).verify("testchannel")

    asyncio.run(scenario())


def test_failed_suggestion_is_not_confirmed_and_uses_editable_message() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def reject(username: str):
            raise InvalidChannel("too_many_subscribers")

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                user = User(telegram_id=1)
                db.add(user)
                await db.flush()
                assert await accept_suggestion(db, user, "testchannel", reject) == message(
                    "moderation_rejected"
                )
                assert user.suggested_channels == 0
                assert await db.scalar(select(Channel)) is None
                channel = Channel(channelnick="testchannel", status=True)
                db.add(channel)
                await db.flush()
                await accept_suggestion(db, user, "testchannel", reject)
                assert not channel.status
                check = await db.get(ChannelCheck, channel.channel_id)
                assert check is not None and check.state == "invalid"
                assert check.last_error == "too_many_subscribers"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_old_confirmations_remain_until_their_queued_recheck() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions.begin() as db:
                db.add(Channel(channelnick="legacychannel", status=True, subscriber_count=99999))
            assert await enqueue_untracked(engine) == 1
            assert await enqueue_untracked(engine) == 0
            async with sessions() as db:
                channel = await db.scalar(select(Channel))
                check = await db.scalar(select(ChannelCheck))
                assert channel is not None and channel.status
                assert check is not None and check.state == "pending" and check.next_check_at == 0
                assert 0 <= check.queue_order < 2**53
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_help_limits_match_the_policy() -> None:
    from random_telegram_bot.moderation import criteria_text

    text = criteria_text()
    assert str(MAX_SUBSCRIBERS) in text
    assert "14" in text and str(TEXT_POSTS) in text
