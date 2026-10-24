"""Uniform selection over active channels; no scraping on the request path."""

import asyncio
import logging
import math
import re
import secrets
import time
from array import array
from contextlib import suppress

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from random_telegram_bot.models import Channel

USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")
logger = logging.getLogger(__name__)


def normalize_channel(value: str) -> str:
    value = value.strip().removeprefix("https://t.me/").removeprefix("http://t.me/")
    value = value.removeprefix("@").rstrip("/")
    if not USERNAME.fullmatch(value):
        raise ValueError("Expected a public Telegram channel username")
    return value.lower()


class Catalogue:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], ttl: float = 30) -> None:
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("Catalogue refresh interval must be finite and positive")
        self.sessions = sessions
        self.ttl = ttl
        # Eight bytes per ID; retaining ORM rows/usernames for a large catalogue
        # can exhaust the bot's memory while refreshing the cache.
        self._channels: array[int] = array("q")
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Warm the snapshot before accepting requests, then refresh it in the background."""
        async with self._lock:
            if self._task is None:
                await self._refresh()
                self._wake.clear()
                self._task = asyncio.create_task(self._run(), name="catalogue-refresh")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _refresh(self) -> None:
        valid: array[int] = array("q")
        async with self.sessions() as db:
            rows = await db.stream(
                select(Channel.channel_id, Channel.channelnick)
                .where(Channel.status.is_(True))
                .execution_options(yield_per=1000)
            )
            try:
                async for batch in rows.partitions(1000):
                    for channel_id, username in batch:
                        try:
                            normalize_channel(username)
                        except ValueError:
                            continue
                        valid.append(channel_id)
                    # Parsing a large snapshot must leave time for webhook/API requests.
                    await asyncio.sleep(0)
            finally:
                await rows.close()
        # Cancellation or a DB error leaves the previous complete snapshot intact.
        self._channels = valid

    async def _run(self) -> None:
        delay = self.ttl
        try:
            while True:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                self._wake.clear()
                try:
                    await self._refresh()
                except SQLAlchemyError as error:
                    delay = min(5.0, self.ttl)
                    logger.warning(
                        "Catalogue refresh failed: %s; keeping snapshot and retrying in %.2fs",
                        type(error).__name__,
                        delay,
                    )
                else:
                    delay = self.ttl
        except Exception as error:
            logger.error("Catalogue refresh stopped: %s", type(error).__name__)
            raise

    async def choose(self) -> tuple[int, str] | None:
        # Standalone callers can initialize lazily; the application's lifespan prewarms.
        if self._task is None:
            await self.start()
        # Recheck status so a retired channel is not served from a warm cache.
        for _ in range(2):
            snapshot = self._channels
            if not snapshot:
                return None
            identifier = secrets.choice(snapshot)
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
            # Concurrent lookups may retire the same ID. Never rebuild in a request.
            with suppress(ValueError):
                snapshot.remove(identifier)
            self._wake.set()
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
