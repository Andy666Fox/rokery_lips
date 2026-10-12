"""Build durable replies and apply database effects in the same transaction."""

from datetime import UTC, datetime
from typing import Any

from aiogram.types import Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from random_telegram_bot.catalogue import Catalogue, normalize_channel
from random_telegram_bot.models import Channel, Rating, User


async def prepare_actions(
    update: Update, db: AsyncSession, catalogue: Catalogue
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
        return [{"method": "answerCallbackQuery", "callback_query_id": callback.id, "text": text}]
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
                name = normalize_channel(candidate)
            except ValueError:
                response = "Нужна ссылка на публичный канал или @username. Отмена: /cancel."
            else:
                existing = await db.scalar(select(Channel).where(Channel.channelnick == name))
                if existing:
                    response = "Этот канал уже есть в каталоге или ожидает проверки."
                else:
                    db.add(Channel(channelnick=name, status=False, added_at=datetime.now(UTC)))
                    user.suggested_channels += 1
                    response = "Канал отправлен на проверку. Спасибо!"
                user.awaiting_suggestion = False
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
