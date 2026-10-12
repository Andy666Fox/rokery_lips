"""Database contracts, including the original table names for data migration."""

import time
from datetime import datetime
from typing import cast

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ID = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = cast("Mapped[int]", mapped_column(BigInteger, primary_key=True))
    username: Mapped[str] = cast("Mapped[str]", mapped_column(String(255), default=""))
    registred_at: Mapped[datetime | None] = cast("Mapped[datetime | None]", mapped_column(DateTime))
    level: Mapped[int] = cast("Mapped[int]", mapped_column(default=1))
    level_points: Mapped[int] = cast("Mapped[int]", mapped_column(default=0))
    viewed_channels: Mapped[int] = cast("Mapped[int]", mapped_column(default=0))
    suggested_channels: Mapped[int] = cast("Mapped[int]", mapped_column(default=0))
    likes: Mapped[int] = cast("Mapped[int]", mapped_column(default=0))
    dislikes: Mapped[int] = cast("Mapped[int]", mapped_column(default=0))
    ld_ratio: Mapped[float] = cast("Mapped[float]", mapped_column(Float, default=50))
    last_channel_id: Mapped[int] = cast("Mapped[int]", mapped_column(default=0))
    last_online: Mapped[datetime | None] = cast("Mapped[datetime | None]", mapped_column(DateTime))
    awaiting_suggestion: Mapped[bool] = cast("Mapped[bool]", mapped_column(default=False))


class Subscription(Base):
    # The spelling belongs to the legacy database and must be preserved during import.
    __tablename__ = "subscribtions"
    user_id: Mapped[int] = cast("Mapped[int]", mapped_column(BigInteger, primary_key=True))
    sub_level: Mapped[int] = cast("Mapped[int]", mapped_column(default=1))
    expires_at: Mapped[datetime | None] = cast("Mapped[datetime | None]", mapped_column(DateTime))


class Channel(Base):
    __tablename__ = "channels"
    channel_id: Mapped[int] = cast(
        "Mapped[int]", mapped_column(ID, primary_key=True, autoincrement=True)
    )
    channelnick: Mapped[str] = cast("Mapped[str]", mapped_column(String(255), unique=True))
    added_at: Mapped[datetime | None] = cast("Mapped[datetime | None]", mapped_column(DateTime))
    status: Mapped[bool] = cast("Mapped[bool]", mapped_column(Boolean, default=True, index=True))
    likes: Mapped[int] = cast("Mapped[int]", mapped_column(default=0))
    dislikes: Mapped[int] = cast("Mapped[int]", mapped_column(default=0))
    avg_score: Mapped[float] = cast("Mapped[float]", mapped_column(Float, default=0))


class Rating(Base):
    __tablename__ = "ratings"
    telegram_id: Mapped[int] = cast("Mapped[int]", mapped_column(BigInteger, primary_key=True))
    channel_id: Mapped[int] = cast("Mapped[int]", mapped_column(ID, primary_key=True))
    score: Mapped[int] = cast("Mapped[int]", mapped_column(Integer))


class Inbox(Base):
    __tablename__ = "webhook_inbox"
    __table_args__ = (
        Index("ix_inbox_pending", "state", "next_attempt_at", "sequence"),
        Index("ix_inbox_chat", "chat_id", "sequence", "state"),
    )
    sequence: Mapped[int] = cast(
        "Mapped[int]", mapped_column(ID, primary_key=True, autoincrement=True)
    )
    update_id: Mapped[int] = cast("Mapped[int]", mapped_column(BigInteger, unique=True))
    chat_id: Mapped[int] = cast("Mapped[int]", mapped_column(BigInteger))
    payload: Mapped[dict[str, object]] = cast("Mapped[dict[str, object]]", mapped_column(JSON))
    received_at: Mapped[float] = cast("Mapped[float]", mapped_column(Float, default=time.time))
    state: Mapped[str] = cast("Mapped[str]", mapped_column(String(16), default="pending"))
    attempts: Mapped[int] = cast("Mapped[int]", mapped_column(default=0))
    next_attempt_at: Mapped[float] = cast("Mapped[float]", mapped_column(Float, default=0))
    lease_until: Mapped[float] = cast("Mapped[float]", mapped_column(Float, default=0))
    actions: Mapped[list[dict[str, object]] | None] = cast(
        "Mapped[list[dict[str, object]] | None]", mapped_column(JSON, nullable=True)
    )
    completed_at: Mapped[float | None] = cast(
        "Mapped[float | None]", mapped_column(Float, nullable=True)
    )
    last_error: Mapped[str | None] = cast("Mapped[str | None]", mapped_column(Text, nullable=True))


class ImportReceipt(Base):
    # Keep the completed import ledger compatible with the existing migration history.
    __tablename__ = "legacy_imports"
    sha256: Mapped[str] = cast("Mapped[str]", mapped_column(String(64), primary_key=True))
    completed_at: Mapped[datetime] = cast("Mapped[datetime]", mapped_column(DateTime))
