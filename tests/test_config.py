import os
import unittest
from unittest.mock import patch

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_rejects_non_finite_and_non_positive_numbers(self) -> None:
        for name in ("TG_CACHE_TTL", "TG_CACHE_STALE_TTL", "TG_HTTP_TIMEOUT"):
            for value in ("nan", "inf", "-inf", "0", "-1"):
                with (
                    self.subTest(name=name, value=value),
                    patch.dict(os.environ, {name: value}, clear=True),
                    self.assertRaisesRegex(ValueError, name),
                ):
                    Settings.from_env()

    def test_defaults_are_valid(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.telegram_channel, "Synchronisica")
        self.assertEqual(settings.telegram_cache_ttl, 60)
        self.assertEqual(settings.telegram_cache_stale_ttl, 3600)

    def test_channel_accepts_leading_at_sign(self) -> None:
        with patch.dict(os.environ, {"TG_CHANNEL": "@Synchronisica"}, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.telegram_channel, "Synchronisica")

    def test_stale_ttl_cannot_be_shorter_than_fresh_ttl(self) -> None:
        environment = {"TG_CACHE_TTL": "120", "TG_CACHE_STALE_TTL": "60"}
        with (
            patch.dict(os.environ, environment, clear=True),
            self.assertRaisesRegex(ValueError, "TG_CACHE_STALE_TTL"),
        ):
            Settings.from_env()
