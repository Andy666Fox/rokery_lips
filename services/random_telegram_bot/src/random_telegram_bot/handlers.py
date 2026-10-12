"""Build durable replies and apply database effects in the same transaction."""

from datetime import UTC, datetime
from typing import Any

from aiogram.types import Update
from sqlalchemy.ext.asyncio import AsyncSession

from random_telegram_bot.catalogue import Catalogue, normalize_channel
from random_telegram_bot.models import Channel, Rating, User
from random_telegram_bot.suggestions import CheckChannel, accept_suggestion
from random_telegram_bot.telegram_web import public_username


async def prepare_actions(
    update: Update,
    db: AsyncSession,
    catalogue: Catalogue,
    check_channel: CheckChannel | None = None,
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
    if callback:
        parts = (callback.data or "").split(":")
        text = "Оценка не распознана"
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
                text = "Спасибо за оценку!"
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
    keyboard = {"keyboard": [[{"text": "Найти канал"}]], "resize_keyboard": True}
    if command in {"/start", "/help"}:
        user.awaiting_suggestion = False
        response = "Нажмите «Найти канал» или /random. Предложить канал: /suggest @username."
    elif command == "/extra":
        response = (
            f"Просмотрено каналов: {user.viewed_channels}. Предложено: {user.suggested_channels}."
        )
    elif command == "/cancel":
        user.awaiting_suggestion = False
        response = "Действие отменено."
    elif command == "/suggest" or user.awaiting_suggestion:
        candidate = text.partition(" ")[2] if command == "/suggest" else text
        if not candidate:
            user.awaiting_suggestion = True
            response = "Отправьте @username или https://t.me/username. Отмена: /cancel."
        else:
            try:
                name = public_username(candidate) or normalize_channel(candidate)
            except ValueError:
                response = "Нужна ссылка на публичный канал или @username. Отмена: /cancel."
            else:
                response = await accept_suggestion(db, user, name, check_channel)
    elif command == "/random" or text == "Найти канал":
        chosen = await catalogue.choose()
        if chosen is None:
            response = "Каталог пока пуст. Предложить канал: /suggest @username."
        else:
            channel_id, name = chosen
            user.last_channel_id = channel_id
            user.viewed_channels += 1
            return [
                {
                    "method": "sendMessage",
                    "chat_id": message.chat.id,
                    "text": f"Случайный канал: https://t.me/{name}",
                    "link_preview_options": {"is_disabled": True},
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {"text": "👍", "callback_data": f"rate:{channel_id}:1"},
                                {"text": "👎", "callback_data": f"rate:{channel_id}:-1"},
                            ]
                        ]
                    },
                }
            ]
    else:
        response = "Выдать случайный канал: /random. Справка: /help."
    return [
        {
            "method": "sendMessage",
            "chat_id": message.chat.id,
            "text": response,
            "reply_markup": keyboard,
        }
    ]
