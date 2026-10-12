"""Uniform selection over active channels; no scraping on the request path."""

import asyncio
import re
import secrets
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from random_telegram_bot.models import Channel

USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")


def normalize_channel(value: str) -> str:
    value = value.strip().removeprefix("https://t.me/").removeprefix("http://t.me/")
    value = value.removeprefix("@").rstrip("/")
    if not USERNAME.fullmatch(value):
        raise ValueError("Expected a public Telegram channel username")
    return value.lower()


class Catalogue:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], ttl: float = 30) -> None:
        self.sessions = sessions
        self.ttl = ttl
        self._expires = 0.0
        self._channels: tuple[tuple[int, str], ...] = ()
        self._lock = asyncio.Lock()

    async def choose(self) -> tuple[int, str] | None:
        if time.monotonic() >= self._expires:
            async with self._lock:
                if time.monotonic() >= self._expires:
                    async with self.sessions() as db:
                        rows = (
                            await db.execute(
                                select(Channel.channel_id, Channel.channelnick).where(
                                    Channel.status.is_(True)
                                )
                            )
                        ).all()
                    valid = []
                    for channel_id, username in rows:
                        try:
                            valid.append((channel_id, normalize_channel(username)))
                        except ValueError:
                            continue
                    self._channels = tuple(valid)
                    self._expires = time.monotonic() + self.ttl
        return secrets.choice(self._channels) if self._channels else None


class RateLimiter:
    """Bounded in-process buckets; deployment runs one API process."""

    def __init__(self, capacity: int = 20, period: float = 60, max_clients: int = 4096) -> None:
        self.capacity = capacity
        self.period = period
        self.max_clients = max_clients
        self.buckets: dict[str, tuple[float, float]] = {}

    def allow(self, address: str) -> bool:
        now = time.monotonic()
        if address not in self.buckets and len(self.buckets) >= self.max_clients:
            self.buckets = {
                key: value for key, value in self.buckets.items() if now - value[1] < self.period
            }
            if len(self.buckets) >= self.max_clients:
                return False
        tokens, previous = self.buckets.get(address, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - previous) * self.capacity / self.period)
        accepted = tokens >= 1
        self.buckets[address] = (tokens - 1 if accepted else tokens, now)
        return accepted
