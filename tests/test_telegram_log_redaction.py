"""Telegram refresh failures must not write exception text to logs (audit T1-02)."""

import asyncio
import logging

import httpx
import pytest
from app.services.telegram import TelegramService, TelegramUnavailableError

SECRET = "proxy-user:s3cret-pass@proxy.example"


def test_refresh_failure_logs_only_error_type(caplog: pytest.LogCaptureFixture) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError(f"Unable to connect to http://{SECRET}", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail), timeout=5) as client:
            service = TelegramService(
                client=client, channel="Synchronisica", cache_ttl=60, stale_ttl=600
            )
            with pytest.raises(TelegramUnavailableError):
                await service.latest_post()

    with caplog.at_level(logging.WARNING, logger="app.services.telegram"):
        asyncio.run(scenario())
    assert "ProxyError" in caplog.text
    assert SECRET not in caplog.text
