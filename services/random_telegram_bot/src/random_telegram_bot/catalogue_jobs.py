"""Idempotent inactive CSV import and bounded, resumable HTML verification.

Run with python -m random_telegram_bot.catalogue_jobs --help.
No Bot API token or personal Telegram account is used.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import httpx
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from random_telegram_bot.catalogue import normalize_channel
from random_telegram_bot.models import Channel, ChannelCheck, VerificationControl
from random_telegram_bot.suggestions import (
    ChannelValidator,
    CheckChannel,
    InvalidChannel,
    ValidationUnavailable,
    apply_verification,
)


@asynccontextmanager
async def exclusive(engine: AsyncEngine) -> AsyncGenerator[bool]:
    # The dedicated connection holds a MySQL advisory lock across batch commits.
    # A killed process releases it automatically. SQLite is only used in local tests.
    async with engine.connect() as connection:
        mysql = engine.dialect.name == "mysql"
        acquired = not mysql or bool(
            await connection.scalar(text("SELECT GET_LOCK('catalogue_jobs', 0)"))
        )
        try:
            yield acquired
        finally:
            if mysql and acquired:
                await connection.execute(text("SELECT RELEASE_LOCK('catalogue_jobs')"))


class CSVInspection(TypedDict):
    rows: int
    sha256: str


def inspect_csv(path: Path) -> CSVInspection:
    with path.open("rb") as data:
        digest = hashlib.file_digest(data, "sha256").hexdigest()
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"username", "source_urls", "attribution"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("CSV requires username, source_urls, attribution")
        for number, row in enumerate(reader, 2):
            try:
                name = normalize_channel(row["username"])
            except ValueError as error:
                raise ValueError(f"Invalid username at CSV line {number}") from error
            if name != row["username"].lower() or name in seen:
                raise ValueError(f"Invalid or duplicate username at CSV line {number}")
            if len(row["source_urls"]) > 60000 or len(row["attribution"]) > 60000:
                raise ValueError(f"Oversized provenance at CSV line {number}")
            seen.add(name)
    return {"rows": len(seen), "sha256": digest}


async def import_csv(engine: AsyncEngine, path: Path) -> dict[str, object]:
    report = inspect_csv(path)  # Validate the entire input before any DB write.
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with exclusive(engine) as acquired:
        if not acquired:
            raise RuntimeError("Another catalogue job is running; retry later")
        async with sessions() as db:
            existing = set()
            for nick in await db.scalars(select(Channel.channelnick)):
                try:
                    existing.add(normalize_channel(nick))
                except ValueError:
                    continue  # Preserve malformed legacy rows without adopting them.
        inserted = 0
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            batch: list[dict[str, str]] = []
            for row in reader:
                if row["username"].lower() not in existing:
                    batch.append(row)
                if len(batch) >= 1000:
                    inserted += await import_batch(sessions, batch, str(report["sha256"]))
                    batch = []
            if batch:
                inserted += await import_batch(sessions, batch, str(report["sha256"]))
        return {
            **report,
            "inserted_inactive": inserted,
            "existing_preserved": report["rows"] - inserted,
        }


async def import_batch(
    sessions: async_sessionmaker[AsyncSession], rows: list[dict[str, str]], digest: str
) -> int:
    # One transaction owns both the channel and its queue/provenance entry.
    async with sessions.begin() as db:
        # Concurrent /suggest can create a name while the import is running.
        statement = insert(Channel)
        if db.bind is not None and db.bind.dialect.name == "mysql":
            from sqlalchemy.dialects.mysql import insert as mysql_insert

            statement = mysql_insert(Channel).on_duplicate_key_update(channel_id=Channel.channel_id)
        elif db.bind is not None and db.bind.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            statement = sqlite_insert(Channel).on_conflict_do_nothing()
        # Return only rows absent immediately before this transaction.
        names = [row["username"].lower() for row in rows]
        present = set(
            await db.scalars(select(Channel.channelnick).where(Channel.channelnick.in_(names)))
        )
        fresh = [row for row in rows if row["username"].lower() not in present]
        if not fresh:
            return 0
        await db.execute(
            statement,
            [
                {
                    "channelnick": row["username"].lower(),
                    "status": False,
                    "added_at": datetime.now(UTC).replace(tzinfo=None),
                }
                for row in fresh
            ],
        )
        found = dict(
            (nick, ident)
            for ident, nick in (
                await db.execute(
                    select(Channel.channel_id, Channel.channelnick).where(
                        Channel.channelnick.in_([r["username"].lower() for r in fresh]),
                        Channel.status.is_(False),
                    )
                )
            )
        )
        queued = set(
            await db.scalars(
                select(ChannelCheck.channel_id).where(
                    ChannelCheck.channel_id.in_(list(found.values()))
                )
            )
        )
        checks = [
            {
                "channel_id": found[r["username"].lower()],
                "state": "pending",
                "next_check_at": 0,
                "attempts": 0,
                "import_sha256": digest,
                "source_urls": r["source_urls"],
                "attribution": r["attribution"],
            }
            for r in fresh
            if r["username"].lower() in found and found[r["username"].lower()] not in queued
        ]
        if checks:
            await db.execute(insert(ChannelCheck), checks)
        return len(checks)


async def verify_batch(
    engine: AsyncEngine, check: CheckChannel, *, limit: int = 100, delay: float = 1.5
) -> dict[str, int]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    counts = {"valid": 0, "invalid": 0, "deferred": 0}
    async with exclusive(engine) as acquired:
        if not acquired:
            return {"busy": 1}
        async with sessions() as db:
            control = await db.get(VerificationControl, "telegram_web")
            if control is not None and control.not_before > time.time():
                return {"cooldown": int(control.not_before - time.time())}
            identifiers = list(
                await db.scalars(
                    select(ChannelCheck.channel_id)
                    .where(ChannelCheck.next_check_at <= time.time())
                    .order_by(
                        ChannelCheck.next_check_at,
                        ChannelCheck.queue_order,
                        ChannelCheck.channel_id,
                    )
                    .limit(limit)
                )
            )
        for index, identifier in enumerate(identifiers):
            if index:
                await asyncio.sleep(delay)
            async with sessions() as db:
                channel = await db.get(Channel, identifier)
                if channel is None:
                    raise RuntimeError("Queue references a missing channel")
                name = normalize_channel(channel.channelnick)
            outcome = "valid"
            stop = False
            retry_after = 30 * 86400  # Successful and invalid entries are periodically refreshed.
            verified = None
            try:
                verified = await check(name)
            except InvalidChannel:
                outcome = "invalid"
            except ValidationUnavailable as error:
                outcome, stop = "deferred", error.stop
                retry_after = max(300, error.retry_after)
            async with sessions.begin() as db:
                channel = await db.get(Channel, identifier, with_for_update=True)
                entry = await db.get(ChannelCheck, identifier, with_for_update=True)
                if channel is None or entry is None:
                    raise RuntimeError("Queue entry disappeared")
                entry.state = outcome
                entry.attempts += 1
                if outcome == "valid":
                    assert verified is not None
                    apply_verification(channel, verified)
                elif outcome == "invalid":
                    channel.status = False
                    channel.last_checked_at = datetime.now(UTC).replace(tzinfo=None)
                else:
                    retry_after = max(
                        retry_after, min(86400, 300 * 2 ** min(entry.attempts - 1, 8))
                    )
                entry.next_check_at = time.time() + retry_after
                entry.last_error = "temporary_telegram_failure" if outcome == "deferred" else None
                if stop:
                    control = await db.get(VerificationControl, "telegram_web")
                    if control is None:
                        control = VerificationControl(name="telegram_web")
                        db.add(control)
                    control.not_before = entry.next_check_at
            counts[outcome] += 1
            if stop:
                break
    return counts


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("csv", type=Path)
    importer = commands.add_parser("import")
    importer.add_argument("csv", type=Path)
    for command in ("verify", "watch"):
        job = commands.add_parser(command)
        job.add_argument("--limit", type=int, default=100)
        job.add_argument("--delay", type=float, default=1.5)
        job.add_argument("--interval", type=float, default=300)
    args = parser.parse_args()
    if args.command == "inspect":
        print(json.dumps(inspect_csv(args.csv)))
        return
    if args.command in {"verify", "watch"} and (
        not 1 <= args.limit <= 1000 or args.delay < 1.5 or args.interval < 60
    ):
        parser.error("limit must be 1..1000, delay >=1.5, interval >=60")
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    try:
        if args.command == "import":
            print(json.dumps(await import_csv(engine, args.csv)), flush=True)
        else:
            async with httpx.AsyncClient() as client:
                validator = ChannelValidator(client)
                while True:
                    result = await verify_batch(
                        engine, validator.verify, limit=args.limit, delay=args.delay
                    )
                    print(json.dumps({"at": datetime.now(UTC).isoformat(), **result}), flush=True)
                    if args.command == "verify":
                        break
                    await asyncio.sleep(args.interval)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
