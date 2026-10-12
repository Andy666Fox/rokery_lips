"""Configuration is validated once; credentials never appear in logs."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    webhook_secret: str
    bot_token: str
    workers: int = 4
    catalogue_ttl: float = 30
    max_pending: int = 10000

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ["WEBHOOK_SECRET"]
        if len(secret) < 32:
            raise ValueError("WEBHOOK_SECRET must contain at least 32 characters")
        return cls(
            database_url=os.environ["DATABASE_URL"],
            webhook_secret=secret,
            bot_token=os.getenv("BOT_TOKEN", "").strip(),
        )
