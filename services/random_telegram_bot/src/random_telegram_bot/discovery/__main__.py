"""Operator CLI for local discovery, review, and explicit catalogue publication."""

import argparse
import asyncio
import csv
import hashlib
import hmac
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from random_telegram_bot.discovery.client import Deferred, Reader
from random_telegram_bot.discovery.links import extract_usernames
from random_telegram_bot.discovery.publish import promote, reviewed
from random_telegram_bot.discovery.sources import extract_source
from random_telegram_bot.discovery.store import Store


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be greater than zero")
    return number


def discovery_token() -> str:
    token = os.environ.get("DISCOVERY_BOT_TOKEN", "")
    if not token:
        raise ValueError("Set DISCOVERY_BOT_TOKEN to a separate bot token")
    production = os.environ.get("BOT_TOKEN", "")
    same_bot = (
        ":" in token and ":" in production and token.split(":", 1)[0] == production.split(":", 1)[0]
    )
    if hmac.compare_digest(token, production) or same_bot:
        raise ValueError("Discovery must use a different token from the production BOT_TOKEN")
    return token


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--ledger", type=Path, default=Path(".local/discovery/catalogue.sqlite"))
    commands = result.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="Import versioned, attributed public seed sources")
    seed.add_argument("--sources", type=Path, required=True)
    seed.add_argument("--max-candidates", type=positive, default=10000)
    load = commands.add_parser("import", help="Extract links from an operator-supplied document")
    load.add_argument("file", type=Path)
    load.add_argument("--source", required=True)
    load.add_argument("--attribution", required=True)
    load.add_argument(
        "--format",
        choices=("links", "csv-usernames", "csv-graph-channels", "one-s", "wikidata", "tgstat"),
        default="links",
    )
    load.add_argument("--mentions", action="store_true")
    load.add_argument("--max-candidates", type=positive, default=10000)
    scan = commands.add_parser(
        "scan", help="Check due candidates and optionally follow public links"
    )
    scan.add_argument("--limit", type=positive, default=100)
    scan.add_argument("--delay", type=float, default=1.5)
    scan.add_argument("--max-depth", type=int, choices=range(4), default=1)
    scan.add_argument("--max-candidates", type=positive, default=10000)
    scan.add_argument("--max-age-days", type=positive, default=90)
    scan.add_argument("--expand", action="store_true")
    scan.add_argument("--source", help="Check candidates from this exact source URL only")
    scan.add_argument(
        "--verify-identity",
        action="store_true",
        help="Use a separate discovery bot for shortlisted candidates only",
    )
    report = commands.add_parser(
        "report", help="Export every candidate, including unresolved/rejected ones"
    )
    report.add_argument("--output", type=Path, required=True)
    report.add_argument(
        "--state", action="append", help="Include this state; repeat for more states"
    )
    report.add_argument(
        "--max-subscribers",
        type=positive,
        help="Include exact audience counts only, up to this limit",
    )
    review = commands.add_parser(
        "review", help="Record operator decisions from one username per line"
    )
    review.add_argument("file", type=Path)
    review.add_argument(
        "--decision", choices=("approve", "reject", "pending", "shortlist"), required=True
    )
    publish = commands.add_parser(
        "publish", help="Plan or apply reviewed additions to the bot database"
    )
    publish.add_argument("--apply", action="store_true")
    publish.add_argument("--limit", type=positive, default=20)
    publish.add_argument(
        "--allow-web-identity",
        action="store_true",
        help="Accept identities inferred from Telegram public HTML instead of Bot API",
    )
    commands.add_parser("stats")
    return result


async def seed_sources(store: Store, reader: Reader, path: Path, max_candidates: int) -> None:
    sources = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(sources, list):
        raise ValueError("Source manifest must be a JSON list")
    if not store.available("web", time.time()):
        raise ValueError("Public web cooldown active; retry after it expires")
    failed = 0
    for source in sources:
        try:
            await seed_source(store, reader, source, max_candidates)
        except (ValueError, Deferred) as error:
            failed += 1
            print(json.dumps({"source": source.get("name", "unnamed"), "error": str(error)}))
            if isinstance(error, Deferred) and error.stop:
                store.defer_all(time.time() + error.retry_after, error.scope)
                break
    if failed:
        raise ValueError(f"{failed} seed source(s) failed; successful imports were kept")


async def seed_source(
    store: Store, reader: Reader, source: dict[str, Any], max_candidates: int
) -> None:
    url = source["url"]
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Seed sources must use public HTTPS URLs without credentials")
    if not source.get("attribution"):
        raise ValueError("Every source needs attribution and a reuse basis")
    status, _, text = await reader.fetch(url)
    if status != 200:
        raise ValueError(f"Seed source {source['name']} returned HTTP {status}")
    names = extract_source(text, source["format"], source.get("section"))
    digest = hashlib.sha256(text.encode()).hexdigest()
    added = store.add(
        names, url, source["attribution"] + f"; SHA256 {digest}", max_candidates=max_candidates
    )
    print(json.dumps({"source": source["name"], "candidates": len(names), "added": added}))


async def scan_candidates(store: Store, reader: Reader, args: argparse.Namespace) -> None:
    counts: Counter[str] = Counter()
    verify = getattr(args, "verify_identity", False)
    if verify and not reader.token:
        raise ValueError("Set DISCOVERY_BOT_TOKEN to a separate bot token before identity checks")
    if verify and args.limit > 20:
        raise ValueError("Identity checks are limited to 20 shortlisted candidates per invocation")
    # New graph candidates are picked up on the next invocation; batch size remains bounded.
    for item in store.due(
        args.limit,
        args.max_depth,
        time.time(),
        getattr(args, "source", None),
        scope="bot" if verify else "web",
        shortlisted_only=verify,
    ):
        name = item["username"]
        now = time.time()
        try:
            observe = reader.observe if verify else reader.preview
            observation = await observe(name, now=now, max_age_days=args.max_age_days)
            store.record(name, observation.state, observation.metadata, now=now, retry_after=86400)
            counts[observation.state] += 1
            if (
                args.expand
                and observation.state in {"ready", "web_ready", "preview_ready"}
                and item["depth"] < args.max_depth
            ):
                counts["discovered"] += store.add(
                    observation.metadata["links"][:50],
                    f"https://t.me/s/{name}",
                    "Public outbound links in visible channel posts; discovery evidence only",
                    depth=item["depth"] + 1,
                    max_candidates=args.max_candidates,
                )
        except Deferred as error:
            store.record(
                name, "deferred", {"reason": error.reason}, now=now, retry_after=error.retry_after
            )
            counts[error.reason] += 1
            if error.stop:
                store.defer_all(now + error.retry_after, error.scope)
                break
    print(
        json.dumps(
            {"batch": dict(counts), "http_requests": reader.requests, "total": store.summary()}
        )
    )


async def publish_candidates(store: Store, args: argparse.Namespace) -> None:
    url = os.environ.get("CATALOGUE_DATABASE_URL", "")
    if not url:
        raise ValueError(
            "Set CATALOGUE_DATABASE_URL for the target catalogue; migrate to 0005 first"
        )
    rows = [
        row
        for row in reviewed(store.rows(), time.time(), allow_web=args.allow_web_identity)
        if row["published_channel_id"] is None
    ][: args.limit]
    if args.apply and rows:
        uses_api = any(row["state"] == "ready" for row in rows)
        if uses_api and args.limit > 20:
            raise ValueError("Apply at most 20 reviewed additions per invocation")
        async with httpx.AsyncClient() as client:
            if uses_api and not store.available("bot", time.time()):
                raise ValueError("Bot API cooldown active; retry after it expires")
            if not store.available("web", time.time()):
                raise ValueError("Public web cooldown active; retry after it expires")
            reader = Reader(
                client, discovery_token() if uses_api else "", delay=5 if uses_api else 1.5
            )
            for row in rows:
                metadata = row["metadata"]
                try:
                    if row["state"] == "web_ready":
                        observation = await reader.preview(
                            metadata["username"],
                            now=time.time(),
                            max_age_days=metadata.get("max_age_days", 90),
                        )
                        chat = {
                            "id": observation.metadata.get("telegram_id"),
                            "type": observation.metadata.get("type"),
                            "username": observation.metadata.get("username"),
                        }
                    else:
                        observation = await reader.observe(
                            row["username"],
                            now=time.time(),
                            max_age_days=metadata.get("max_age_days", 90),
                        )
                        chat = observation.metadata
                        chat["id"] = chat.get("telegram_id")
                except Deferred as error:
                    if error.stop:
                        store.defer_all(time.time() + error.retry_after, error.scope)
                    raise
                if observation.state not in {"ready", "web_ready"}:
                    store.record(
                        row["username"],
                        observation.state,
                        observation.metadata,
                        now=time.time(),
                        retry_after=86400,
                    )
                    raise ValueError(
                        f"Candidate failed its current check: {row['username']}; "
                        "scan and review again"
                    )
                if (
                    chat.get("id") != metadata["telegram_id"]
                    or chat["type"] != "channel"
                    or str(chat.get("username", "")).lower() != metadata["username"]
                ):
                    store.record(
                        row["username"],
                        "identity_mismatch",
                        {"telegram_id": chat.get("id")},
                        now=time.time(),
                        retry_after=0,
                    )
                    raise ValueError(f"Identity changed: {row['username']}; scan and review again")
                # Publish fresh moderation evidence, not the older review snapshot.
                row["metadata"].update(observation.metadata)
                row["checked_at"] = time.time()
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
    except SQLAlchemyError as error:
        raise ValueError(
            f"Invalid catalogue configuration ({type(error).__name__}); "
            "check CATALOGUE_DATABASE_URL and the installed database driver"
        ) from None
    try:
        actions = await promote(
            async_sessionmaker(engine, expire_on_commit=False),
            rows,
            apply=args.apply,
            allow_web=args.allow_web_identity,
        )
        if args.apply:
            for action in actions:
                if action["outcome"] in {"insert", "existing"}:
                    store.published(action["candidate"], action["channel_id"])
        print(
            json.dumps(
                {
                    "apply": args.apply,
                    "allow_web_identity": args.allow_web_identity,
                    "actions": actions,
                }
            )
        )
    except SQLAlchemyError as error:
        raise ValueError(
            f"Catalogue operation failed ({type(error).__name__}); "
            "check connectivity, migration 0005 and concurrent changes, then retry"
        ) from None
    finally:
        await engine.dispose()


def export_report(rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix == ".json":
        output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    fields = (
        "username",
        "url",
        "state",
        "decision",
        "telegram_id",
        "identity_source",
        "subscriber_count",
        "subscriber_display",
        "title",
        "about",
        "latest_post",
        "checked_at",
        "post_count",
        "reason",
        "sample",
        "sources",
    )
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            metadata = row["metadata"]
            values = {key: metadata.get(key, "") for key in fields}
            values.update(
                {key: row[key] for key in ("username", "state", "decision", "checked_at")}
            )
            values["url"] = "https://t.me/" + row["username"]
            values["sources"] = json.dumps(row["sources"], ensure_ascii=False)
            # Spreadsheet formulas in remote channel names must remain plain text.
            writer.writerow(
                {
                    key: "'" + value
                    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                    else value
                    for key, value in values.items()
                }
            )


async def run(args: argparse.Namespace) -> None:
    store = Store(args.ledger)
    try:
        if args.command == "publish":
            await publish_candidates(store, args)
        elif args.command in {"seed", "scan"}:
            async with httpx.AsyncClient(
                headers={"User-Agent": "Synchronisica-ChannelDiscovery/1.0"}
            ) as client:
                verify = getattr(args, "verify_identity", False)
                reader = Reader(
                    client,
                    discovery_token() if verify else "",
                    delay=max(args.delay, 5) if verify else getattr(args, "delay", 1.5),
                )
                if args.command == "seed":
                    await seed_sources(store, reader, args.sources, args.max_candidates)
                else:
                    await scan_candidates(store, reader, args)
        elif args.command == "import":
            text = args.file.read_text(encoding="utf-8")
            if args.mentions and args.format != "links":
                raise ValueError("--mentions is only supported with --format links")
            names = (
                extract_usernames(text, mentions=args.mentions)
                if args.format == "links"
                else extract_source(text, args.format)
            )
            count = store.add(
                names,
                args.source,
                args.attribution,
                max_candidates=args.max_candidates,
            )
            print(json.dumps({"added": count, "total": store.summary()}))
        elif args.command == "report":
            rows = store.rows()
            if args.state:
                rows = [row for row in rows if row["state"] in args.state]
            if args.max_subscribers is not None:
                rows = [
                    row
                    for row in rows
                    if type(count := row["metadata"].get("subscriber_count")) is int
                    and 0 <= count <= args.max_subscribers
                ]
            export_report(rows, args.output)
            print(
                json.dumps(
                    {"output": str(args.output), "exported": len(rows), "total": store.summary()}
                )
            )
        elif args.command == "review":
            names = [
                line.strip()
                for line in args.file.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            print(json.dumps({"reviewed": store.decide(names, args.decision)}))
        else:
            print(json.dumps(store.summary()))
    finally:
        store.close()


def main() -> None:
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, OSError, Deferred) as error:
        # Network exceptions are sanitized by Reader before reaching this boundary.
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
