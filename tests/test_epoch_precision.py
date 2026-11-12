"""Epoch deadlines must retain sub-second precision in the MySQL schema."""

import asyncio
import io
import runpy
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from random_telegram_bot.models import Base, Inbox
from sqlalchemy import Double, inspect, select
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

EPOCH_COLUMNS = {
    "webhook_inbox": ("received_at", "next_attempt_at", "lease_until", "completed_at"),
    "channel_checks": ("next_check_at",),
    "verification_control": ("not_before",),
    "discovered_channels": ("verified_at",),
    "message_deliveries": ("delivered_at",),
}


def test_mysql_epoch_columns_use_double_precision() -> None:
    for table, columns in EPOCH_COLUMNS.items():
        for name in columns:
            actual = Base.metadata.tables[table].c[name].type.compile(dialect=dialect())
            assert actual == "DOUBLE", f"{table}.{name} loses deadline precision: {actual}"


def test_mysql_migration_emits_double_without_changing_units() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": output}
    )
    migration = runpy.run_path(
        "services/random_telegram_bot/migrations/versions/0009_epoch_precision.py"
    )
    with Operations.context(context):
        migration["upgrade"]()
    sql = output.getvalue()
    assert sql.count(" DOUBLE") == 8
    assert "MODIFY completed_at DOUBLE NULL" in sql
    assert "MODIFY lease_until DOUBLE NOT NULL" in sql
    assert "MODIFY received_at DOUBLE NOT NULL DEFAULT 0" in sql
    assert "UPDATE " not in sql


def test_epoch_migration_preserves_deadlines_and_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "sqlite+aiosqlite:///" + (tmp_path / "epoch.sqlite").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("WEBHOOK_SECRET", "x" * 32)
    config = Config("services/random_telegram_bot/alembic.ini")
    command.upgrade(config, "0008")
    deadlines = (1794432065.125, 1794432067.125, 1794432185.125)

    async def seed() -> None:
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine).begin() as db:
                db.add(
                    Inbox(
                        update_id=1,
                        chat_id=1,
                        payload={},
                        state="processing",
                        attempts=2,
                        received_at=deadlines[0],
                        next_attempt_at=deadlines[1],
                        lease_until=deadlines[2],
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(seed())
    command.upgrade(config, "0009")
    command.upgrade(config, "0009")

    async def verify() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                for table, names in EPOCH_COLUMNS.items():
                    columns = await connection.run_sync(
                        lambda c, table=table: inspect(c).get_columns(table)
                    )
                    assert all(isinstance(c["type"], Double) for c in columns if c["name"] in names)
                indexes = await connection.run_sync(
                    lambda c: inspect(c).get_indexes("webhook_inbox")
                )
                assert {tuple(i["column_names"]) for i in indexes} >= {
                    ("state", "next_attempt_at", "sequence"),
                    ("chat_id", "sequence", "state"),
                    ("received_at",),
                }
            async with async_sessionmaker(engine)() as db:
                row = (await db.scalars(select(Inbox))).one()
                assert (row.received_at, row.next_attempt_at, row.lease_until) == deadlines
                assert row.state == "processing" and row.attempts == 2
                assert row.completed_at is None
        finally:
            await engine.dispose()

    asyncio.run(verify())
