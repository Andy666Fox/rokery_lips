import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

_CHANNEL_PATTERN = re.compile(r"[A-Za-z0-9_]{5,32}")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _positive_float(name: str, default: str) -> float:
    raw_value = os.getenv(name, default)
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number, got {raw_value!r}") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated application settings loaded from environment variables."""

    telegram_channel: str
    telegram_cache_ttl: float
    telegram_cache_stale_ttl: float
    telegram_http_timeout: float
    telegram_proxy: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        channel = os.getenv("TG_CHANNEL", "Synchronisica").strip().removeprefix("@")
        if not _CHANNEL_PATTERN.fullmatch(channel):
            raise ValueError("TG_CHANNEL must contain 5-32 ASCII letters, digits, or underscores")

        cache_ttl = _positive_float("TG_CACHE_TTL", "60")
        stale_ttl = _positive_float("TG_CACHE_STALE_TTL", "3600")
        if stale_ttl < cache_ttl:
            raise ValueError("TG_CACHE_STALE_TTL must be greater than or equal to TG_CACHE_TTL")

        return cls(
            telegram_channel=channel,
            telegram_cache_ttl=cache_ttl,
            telegram_cache_stale_ttl=stale_ttl,
            telegram_http_timeout=_positive_float("TG_HTTP_TIMEOUT", "5"),
            telegram_proxy=os.getenv("TG_PROXY") or None,
        )
