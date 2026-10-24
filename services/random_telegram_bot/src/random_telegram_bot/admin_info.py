"""Read-only administrator statistics with rolling UTC windows."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from random_telegram_bot.models import Channel, Inbox, MessageDelivery, User


async def info_text(db: AsyncSession, *, now: datetime | None = None) -> str:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    until = now.replace(tzinfo=None)
    day = until - timedelta(days=1)
    fortnight = until - timedelta(days=14)
    epoch = now.timestamp()

    # A single statement gives all counters the same database snapshot.
    total_channels = select(func.count()).select_from(Channel).scalar_subquery()
    confirmed = (
        select(func.count()).select_from(Channel).where(Channel.status.is_(True)).scalar_subquery()
    )
    total_users = select(func.count()).select_from(User).scalar_subquery()
    active_users = (
        select(func.count())
        .select_from(User)
        .where(User.last_online >= fortnight, User.last_online <= until)
        .scalar_subquery()
    )
    incoming = (
        select(func.count())
        .select_from(Inbox)
        .where(
            Inbox.received_at >= epoch - 86400,
            Inbox.received_at <= epoch,
            Inbox.payload["message"]["message_id"].as_integer().is_not(None),
        )
        .scalar_subquery()
    )
    outgoing = (
        select(func.count())
        .select_from(MessageDelivery)
        .where(
            MessageDelivery.delivered_at >= epoch - 86400,
            MessageDelivery.delivered_at <= epoch,
        )
        .scalar_subquery()
    )
    added = (
        select(func.count())
        .select_from(Channel)
        .where(Channel.added_at >= day, Channel.added_at <= until)
        .scalar_subquery()
    )
    newly_confirmed = (
        select(func.count())
        .select_from(Channel)
        .where(Channel.confirmed_at >= day, Channel.confirmed_at <= until)
        .scalar_subquery()
    )
    await db.flush()  # Include this request's user activity in the snapshot.
    row = (
        await db.execute(
            select(
                total_channels,
                confirmed,
                total_users,
                active_users,
                incoming,
                outgoing,
                added,
                newly_confirmed,
            )
        )
    ).one()
    channels, valid, users, active, received, sent, new, approved = row
    return (
        "Статистика бота\n"
        f"1) Каналы: {channels} всего / {valid} подтверждено\n"
        f"2) Пользователи: {users} всего / {active} активны за 14 дней\n"
        f"3) Сообщения за 24 часа: {received + sent} "
        f"(входящие: {received}, исходящие: {sent})\n"
        f"4) Добавлено каналов за 24 часа: {new}\n"
        f"5) Впервые подтверждено за 24 часа: {approved}"
    )
