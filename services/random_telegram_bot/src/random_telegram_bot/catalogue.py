"""Uniform selection over active channels; no scraping on the request path."""

import asyncio
import re
import secrets
import time
from array import array

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
        # Eight bytes per ID; retaining ORM rows/usernames for a large catalogue
        # can exhaust the bot's memory while refreshing the cache.
        self._channels: array[int] = array("q")
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        if time.monotonic() >= self._expires:
            async with self._lock:
                if time.monotonic() >= self._expires:
                    async with self.sessions() as db:
                        rows = await db.stream(
                            select(Channel.channel_id, Channel.channelnick)
                            .where(Channel.status.is_(True))
                            .execution_options(yield_per=1000)
                        )
                        valid: array[int] = array("q")
                        try:
                            async for channel_id, username in rows:
                                try:
                                    normalize_channel(username)
                                except ValueError:
                                    continue
                                valid.append(channel_id)
                        finally:
                            await rows.close()
                    self._channels = valid
                    self._expires = time.monotonic() + self.ttl

    async def choose(self) -> tuple[int, str] | None:
        # Recheck status so a retired channel is not served from a warm cache.
        for _ in range(2):
            await self._refresh()
            if not self._channels:
                return None
            identifier = secrets.choice(self._channels)
            async with self.sessions() as db:
                username = await db.scalar(
                    select(Channel.channelnick).where(
                        Channel.channel_id == identifier, Channel.status.is_(True)
                    )
                )
            if username is not None:
                try:
                    return identifier, normalize_channel(username)
                except ValueError:
                    pass
            self._expires = 0
        return None


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
