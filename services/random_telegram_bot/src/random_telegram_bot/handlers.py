"""Build durable replies and apply database effects in the same transaction."""

import re
from datetime import UTC, datetime
from typing import Any

from aiogram.types import Message, Update
from sqlalchemy.ext.asyncio import AsyncSession

from random_telegram_bot.admin_info import info_text
from random_telegram_bot.catalogue import Catalogue
from random_telegram_bot.messages import message as reply
from random_telegram_bot.models import Channel, Rating, User
from random_telegram_bot.moderation import criteria_text
from random_telegram_bot.suggestions import CheckChannel, accept_suggestion
from random_telegram_bot.telegram_web import public_username


def message_addresses(message: Message) -> list[str]:
    """Read channel links and @usernames, including media captions."""
    text = message.text or message.caption or ""
    entities = message.entities if message.text is not None else message.caption_entities
    links = [
        entity.url if entity.type == "text_link" else entity.extract_from(text)
        for entity in entities or []
        if entity.type in {"url", "text_link", "mention"}
    ]
    # Plain-text fallback also supports t.me/name without a scheme or URL entity.
    links += re.findall(
        r"(?i)(?<![\w@./])(?:(?:https?://|(?:t\.me|telegram\.me)/)[^\s<>]+"
        r"|@[A-Za-z][A-Za-z0-9_]{4,31}(?!\w))",
        text,
    )
    return list(dict.fromkeys(link.rstrip(".,;!)]") for link in links if link is not None))


async def prepare_actions(
    update: Update,
    db: AsyncSession,
    catalogue: Catalogue,
    check_channel: CheckChannel | None = None,
    *,
    admin_telegram_id: int = 0,
) -> list[dict[str, Any]]:
    message = update.message
    callback = update.callback_query
    sender = message.from_user if message else callback.from_user if callback else None
    if sender is None:
        return []
    user = await db.get(User, sender.id, with_for_update=True)
    if user is None:
        user = User(
            telegram_id=sender.id, username=sender.username or "", registred_at=datetime.now(UTC)
        )
        db.add(user)
        await db.flush()
    user.last_online = datetime.now(UTC)
    # Clear the legacy flag without requiring a destructive schema migration.
    user.awaiting_suggestion = False
    if callback:
        parts = (callback.data or "").split(":")
        text = reply("rating_unknown")
        rated = False
        if (
            len(parts) == 3
            and parts[0] == "rate"
            and parts[1].isdigit()
            and parts[2] in {"1", "-1"}
        ):
            channel = await db.get(Channel, int(parts[1]), with_for_update=True)
            if channel:
                score = int(parts[2])
                old = await db.get(Rating, (sender.id, channel.channel_id))
                if old:
                    channel.likes -= int(old.score == 1)
                    channel.dislikes -= int(old.score == -1)
                    old.score = score
                else:
                    db.add(
                        Rating(telegram_id=sender.id, channel_id=channel.channel_id, score=score)
                    )
                channel.likes += int(score == 1)
                channel.dislikes += int(score == -1)
                text = reply("rating_thanks")
                rated = True
        actions: list[dict[str, Any]] = []
        if rated:
            edit: dict[str, Any] = {
                "method": "editMessageReplyMarkup",
                "reply_markup": {"inline_keyboard": []},
            }
            if callback.message is not None:
                edit.update(
                    chat_id=callback.message.chat.id, message_id=callback.message.message_id
                )
                actions.append(edit)
            elif callback.inline_message_id is not None:
                edit["inline_message_id"] = callback.inline_message_id
                actions.append(edit)
        actions.append(
            {"method": "answerCallbackQuery", "callback_query_id": callback.id, "text": text}
        )
        return actions
    if not message:
        return []
    text = (message.text or "").strip()
    command = text.split(" ", 1)[0].split("@", 1)[0]
    find_button = reply("find_button")
    keyboard = {"keyboard": [[{"text": find_button}]], "resize_keyboard": True}
    if command == "/info":
        if (
            not admin_telegram_id
            or sender.id != admin_telegram_id
            or message.chat.type != "private"
        ):
            response = reply("admin_only")
        else:
            response = await info_text(db)
    elif command in {"/start", "/help"}:
        response = reply("start", find_button=find_button)
        if command == "/help":
            response = reply("help", criteria=criteria_text())
    elif command == "/extra":
        response = reply("extra", viewed=user.viewed_channels, suggested=user.suggested_channels)
    elif command == "/random" or text == find_button:
        chosen = await catalogue.choose()
        if chosen is None:
            response = reply("catalogue_empty")
        else:
            channel_id, name = chosen
            user.last_channel_id = channel_id
            user.viewed_channels += 1
            return [
                {
                    "method": "sendMessage",
                    "chat_id": message.chat.id,
                    "text": reply("random_channel", username=name),
                    "link_preview_options": {"is_disabled": True},
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {
                                    "text": reply("like_button"),
                                    "callback_data": f"rate:{channel_id}:1",
                                },
                                {
                                    "text": reply("dislike_button"),
                                    "callback_data": f"rate:{channel_id}:-1",
                                },
                            ]
                        ]
                    },
                }
            ]
    else:
        links = [] if command.startswith("/") else message_addresses(message)
        if len(links) > 1:
            response = reply("suggest_one_link")
        elif links:
            name = public_username(links[0])
            response = (
                await accept_suggestion(db, user, name, check_channel)
                if name is not None
                else reply("suggest_bad_address")
            )
        else:
            response = reply("unknown_command")
    return [
        {
            "method": "sendMessage",
            "chat_id": message.chat.id,
            "text": response,
            "reply_markup": keyboard,
        }
    ]
