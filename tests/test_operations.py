"""Operational data must require a verified Beszel administrator session."""

import asyncio
import struct
from pathlib import Path

import httpx
import pytest
from random_telegram_bot.config import Settings
from random_telegram_bot.main import create_app
from random_telegram_bot.models import Base, Channel, User
from random_telegram_bot.operations import decode_logs, redact
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_logs_frames_and_secrets() -> None:
    data = b"2026-01-01 password=abcdef\n"
    framed = b"\x01\0\0\0" + struct.pack(">I", len(data)) + data
    assert decode_logs(framed) == data.decode()
    assert decode_logs(b"plain\n") == "plain\n"
    assert "abcdef" not in redact(decode_logs(framed), ["abcdef"])


@pytest.mark.parametrize("role,code", [("admin", 200), ("readonly", 403)])
def test_authorization_and_previews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str, code: int
) -> None:
    url = "sqlite+aiosqlite:///" + (tmp_path / "db.sqlite").as_posix()

    async def prepare() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine).begin() as db:
            for number in reversed(range(1, 14)):
                db.add(Channel(channel_id=number, channelnick=f"channel{number}", status=False))
                db.add(User(telegram_id=number))
        await engine.dispose()

    asyncio.run(prepare())
    monkeypatch.setenv("BESZEL_URL", "http://beszel:8090")
    original = httpx.AsyncClient

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/collections/users/auth-refresh"
        if request.headers.get("authorization") != "valid-session":
            return httpx.Response(401)
        return httpx.Response(200, json={"record": {"role": role}})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda: original(transport=httpx.MockTransport(transport))
    )
    app = create_app(Settings(url, "x" * 32, ""))

    async def requests() -> None:
        async with (
            app.router.lifespan_context(app),
            original(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
        ):
            for path in ("database", "schema/svg", "schema/mmd", "logs"):
                assert (await client.get("/monitoring/operations/api/" + path)).status_code == 401
            assert (
                await client.get(
                    "/monitoring/operations/api/database",
                    headers={"Authorization": "Bearer fake"},
                )
            ).status_code == 401
            response = await client.get(
                "/monitoring/operations/api/database",
                headers={"Authorization": "Bearer valid-session"},
            )
            assert response.status_code == code
            if code == 200:
                tables = response.json()["tables"]
                assert [r["channel_id"] for r in tables["channels"]["rows"]] == list(range(1, 11))
                assert [r["telegram_id"] for r in tables["users"]["rows"]] == list(range(1, 11))
                response = await client.get(
                    "/monitoring/operations/api/schema/mmd",
                    headers={"Authorization": "Bearer valid-session"},
                )
                assert "channel_checks" in response.text and "logical" in response.text

    asyncio.run(requests())
