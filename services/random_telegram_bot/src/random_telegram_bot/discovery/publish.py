"""Idempotent promotion of reviewed observations into the existing bot catalogue."""

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from random_telegram_bot.catalogue import normalize_channel
from random_telegram_bot.models import Channel, DiscoveredChannel


def reviewed(
    rows: list[dict[str, Any]], now: float, *, allow_web: bool = False
) -> list[dict[str, Any]]:
    """An approval never substitutes for a recent type, identity and activity check."""
    return [
        row
        for row in rows
        if row["decision"] == "approve"
        and (row["state"] == "ready" or (allow_web and row["state"] == "web_ready"))
        and row["checked_at"] is not None
        and now - 86400 <= row["checked_at"] <= now + 300
        and row["metadata"].get("type") == "channel"
        and row["metadata"].get("identity_verified") is True
        and row["metadata"].get("identity_source")
        == ("web_preview" if row["state"] == "web_ready" else "bot_api")
        and isinstance(row["metadata"].get("telegram_id"), int)
        and row["metadata"]["telegram_id"] < 0
        and row["sources"]
    ]


async def promote(
    sessions: async_sessionmaker[AsyncSession],
    rows: list[dict[str, Any]],
    *,
    apply: bool = False,
    now: float | None = None,
    allow_web: bool = False,
) -> list[dict[str, Any]]:
    """Use the immutable Telegram ID without changing legacy IDs, ratings or suggestions.

    The CLI rechecks identity immediately before applying this operation. Existing
    unmanaged rows and renames require explicit reconciliation outside this importer.
    Uniqueness constraints roll back the entire batch on concurrent catalogue changes.
    """
    now = time.time() if now is None else now
    result = []
    async with sessions.begin() as db:
        channels: dict[str, list[Channel]] = {}
        for channel in await db.scalars(select(Channel)):
            try:
                name = normalize_channel(channel.channelnick)
            except ValueError:
                continue
            channels.setdefault(name, []).append(channel)
        identities = {row.telegram_id: row for row in await db.scalars(select(DiscoveredChannel))}
        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for row in reviewed(rows, now, allow_web=allow_web):
            metadata = row["metadata"]
            name = normalize_channel(metadata["username"])
            identity = metadata["telegram_id"]
            action: dict[str, Any] = {
                "candidate": row["username"],
                "username": name,
                "telegram_id": identity,
            }
            managed = identities.get(identity)
            if managed is not None:
                action.update(
                    outcome="existing" if managed.username == name else "rename_conflict",
                    channel_id=managed.channel_id,
                )
            elif name in channels:
                action["outcome"] = "username_conflict"
            elif identity in seen_ids or name in seen_names:
                action["outcome"] = "duplicate_in_batch"
            else:
                action["outcome"] = "insert" if apply else "would_insert"
                seen_ids.add(identity)
                seen_names.add(name)
                if apply:
                    channel = Channel(
                        channelnick=name,
                        status=True,
                        added_at=datetime.fromtimestamp(now, UTC).replace(tzinfo=None),
                    )
                    db.add(channel)
                    await db.flush()
                    db.add(
                        DiscoveredChannel(
                            telegram_id=identity,
                            channel_id=channel.channel_id,
                            username=name,
                            sources=row["sources"],
                            identity_source=metadata["identity_source"],
                            verified_at=now,
                        )
                    )
                    action["channel_id"] = channel.channel_id
            result.append(action)
    return result
