"""Read-only administrator statistics with rolling UTC windows."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from random_telegram_bot.messages import message
from random_telegram_bot.models import Channel, Inbox, MessageDelivery, User


async def info_text(db: AsyncSession, *, now: datetime | None = None) -> str:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    active_days, message_hours = 14, 24
    until = now.replace(tzinfo=None)
    day = until - timedelta(hours=message_hours)
    fortnight = until - timedelta(days=active_days)
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
            Inbox.received_at >= epoch - message_hours * 3600,
            Inbox.received_at <= epoch,
            Inbox.payload["message"]["message_id"].as_integer().is_not(None),
        )
        .scalar_subquery()
    )
    outgoing = (
        select(func.count())
        .select_from(MessageDelivery)
        .where(
            MessageDelivery.delivered_at >= epoch - message_hours * 3600,
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
    return message(
        "admin_info",
        channels=channels,
        confirmed=valid,
        users=users,
        active=active,
        active_days=active_days,
        message_hours=message_hours,
        messages=received + sent,
        received=received,
        sent=sent,
        added=new,
        approved=approved,
    )
