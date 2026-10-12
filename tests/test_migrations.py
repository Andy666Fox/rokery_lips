"""Frozen Alembic history must produce the schema expected by the running code."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from pytest import MonkeyPatch
from random_telegram_bot.models import Base
from sqlalchemy import create_engine


def test_upgrade_matches_models_and_receipt_migration_is_reversible(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    database = tmp_path / "migrations.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///" + database.as_posix())
    monkeypatch.setenv("WEBHOOK_SECRET", "s" * 40)
    config = Config("services/random_telegram_bot/alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine("sqlite:///" + database.as_posix())
    with engine.connect() as connection:
        assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()
    command.downgrade(config, "0001")
    command.upgrade(config, "head")
