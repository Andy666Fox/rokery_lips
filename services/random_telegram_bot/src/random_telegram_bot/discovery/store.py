"""Resumable local discovery ledger, separate from users, ratings and suggestions."""

import json
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from random_telegram_bot.catalogue import normalize_channel


class Store:
    """One operator process owns this ledger; every candidate update is committed."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS candidates (
                username TEXT PRIMARY KEY, depth INTEGER NOT NULL,
                discovered_at REAL NOT NULL, checked_at REAL,
                next_check REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'pending',
                decision TEXT NOT NULL DEFAULT 'pending',
                metadata TEXT NOT NULL DEFAULT '{}', observed_id INTEGER,
                published_channel_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS evidence (
                username TEXT NOT NULL REFERENCES candidates(username),
                source TEXT NOT NULL, attribution TEXT NOT NULL,
                found_at REAL NOT NULL, PRIMARY KEY(username, source)
            );
            CREATE INDEX IF NOT EXISTS ix_discovery_due ON candidates(next_check, depth);
            CREATE TABLE IF NOT EXISTS controls (key TEXT PRIMARY KEY, value REAL NOT NULL);
        """)

    def close(self) -> None:
        self.db.close()

    def add(
        self,
        names: Iterable[str],
        source: str,
        attribution: str,
        *,
        depth: int = 0,
        max_candidates: int = 10000,
    ) -> int:
        if not source.strip() or not attribution.strip():
            raise ValueError("Source and attribution are required")
        added = 0
        count = self.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        with self.db:
            for value in sorted(set(names)):
                name = normalize_channel(value)
                existing = self.db.execute(
                    "SELECT depth FROM candidates WHERE username=?", (name,)
                ).fetchone()
                if existing is None and count >= max_candidates:
                    continue
                self.db.execute(
                    "INSERT INTO candidates(username, depth, discovered_at) VALUES(?,?,?) "
                    "ON CONFLICT(username) DO UPDATE SET depth=MIN(depth,excluded.depth)",
                    (name, depth, time.time()),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?)",
                    (name, source, attribution, time.time()),
                )
                if existing is None:
                    added += 1
                    count += 1
        return added

    def available(self, scope: str, now: float) -> bool:
        key = "not_before" if scope == "bot" else "not_before_web"
        blocked = self.db.execute("SELECT value FROM controls WHERE key=?", (key,)).fetchone()
        return blocked is None or bool(blocked[0] <= now)

    def due(
        self,
        limit: int,
        max_depth: int,
        now: float,
        source: str | None = None,
        *,
        scope: str = "bot",
        shortlisted_only: bool = False,
    ) -> list[dict[str, Any]]:
        if not self.available(scope, now):
            return []
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM candidates WHERE next_check<=? AND depth<=? AND decision!='reject' "
                "AND (?=0 OR decision='shortlist') "
                "AND (? IS NULL OR EXISTS(SELECT 1 FROM evidence e "
                "WHERE e.username=candidates.username AND e.source=?)) "
                "ORDER BY attempts, depth, discovered_at, username LIMIT ?",
                (now, max_depth, shortlisted_only, source, source, limit),
            )
        ]

    def record(
        self, username: str, state: str, metadata: dict[str, Any], *, now: float, retry_after: float
    ) -> None:
        with self.db:
            identity = metadata.get("telegram_id")
            previous = self.db.execute(
                "SELECT observed_id FROM candidates WHERE username=?", (username,)
            ).fetchone()
            if previous is None:
                raise ValueError(f"Unknown candidate: {username}")
            if identity is not None and previous[0] is not None and identity != previous[0]:
                self.db.execute(
                    "UPDATE candidates SET decision='pending' WHERE username=?", (username,)
                )
            self.db.execute(
                "UPDATE candidates SET state=?, metadata=?, checked_at=?, next_check=?, "
                "observed_id=COALESCE(?,observed_id), attempts=attempts+1 WHERE username=?",
                (
                    state,
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    now + retry_after,
                    identity,
                    username,
                ),
            )

    def defer_all(self, until: float, scope: str = "bot") -> None:
        key = "not_before" if scope == "bot" else "not_before_web"
        with self.db:
            self.db.execute(
                "INSERT INTO controls VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=MAX(value,excluded.value)",
                (key, until),
            )

    def decide(self, names: Iterable[str], decision: str) -> int:
        if decision not in {"approve", "reject", "pending", "shortlist"}:
            raise ValueError("Unknown review decision")
        count = 0
        with self.db:
            for value in names:
                name = normalize_channel(value)
                if decision == "approve":
                    candidate = self.db.execute(
                        "SELECT state, observed_id FROM candidates WHERE username=?", (name,)
                    ).fetchone()
                    if (
                        candidate is None
                        or candidate[0] not in {"ready", "web_ready"}
                        or candidate[1] is None
                    ):
                        raise ValueError(
                            f"Candidate needs a successful check before approval: {name}"
                        )
                cursor = self.db.execute(
                    "UPDATE candidates SET decision=? WHERE username=?", (decision, name)
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"Unknown candidate: {name}")
                if decision in {"shortlist", "pending"}:
                    self.db.execute("UPDATE candidates SET next_check=0 WHERE username=?", (name,))
                count += 1
        return count

    def published(self, username: str, channel_id: int) -> None:
        with self.db:
            self.db.execute(
                "UPDATE candidates SET published_channel_id=? WHERE username=?",
                (channel_id, username),
            )

    def rows(self) -> list[dict[str, Any]]:
        result = []
        for row in self.db.execute("SELECT * FROM candidates ORDER BY username"):
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"])
            item["sources"] = [
                dict(source)
                for source in self.db.execute(
                    "SELECT source, attribution, found_at FROM evidence "
                    "WHERE username=? ORDER BY source",
                    (item["username"],),
                )
            ]
            result.append(item)
        return result

    def summary(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT state, COUNT(*) FROM candidates GROUP BY state"))
