"""Fixed read-only operational views, authorized by the existing Beszel session."""

import os
import re
import struct
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import MetaData, func, select

from random_telegram_bot.models import Channel, ChannelCheck, User, VerificationControl
from random_telegram_bot.schema import mermaid, svg

router = APIRouter(prefix="/monitoring/operations")
ASSETS = Path(__file__).with_name("operations_assets")


async def authorize(request: Request) -> None:
    url = os.getenv("BESZEL_URL", "")
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not url or not token or len(token) > 8192:
        raise HTTPException(401, "Sign in to Beszel first")
    try:
        response = await request.app.state.web_client.post(
            url + "/api/collections/users/auth-refresh", headers={"Authorization": token}, timeout=5
        )
        if response.status_code in {401, 403}:
            raise HTTPException(401, "Beszel session expired")
        response.raise_for_status()
        record = response.json()["record"]
        if record.get("role") != "admin":
            raise HTTPException(403, "Beszel administrator access required")
    except (httpx.HTTPError, KeyError, ValueError) as error:
        raise HTTPException(503, "Beszel authorization unavailable") from error


@router.get("/")
async def page() -> FileResponse:
    return FileResponse(ASSETS / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/assets/{name}")
async def asset(name: str) -> FileResponse:
    if name not in {"operations.js", "operations.css"}:
        raise HTTPException(404)
    return FileResponse(ASSETS / name)


@router.get("/api/database", dependencies=[Depends(authorize)])
async def database(request: Request) -> JSONResponse:
    async with request.app.state.sessions() as db:
        previews = {}
        for model in (Channel, User):
            table = model.__table__
            rows = (
                (await db.execute(select(table).order_by(*table.primary_key).limit(10)))
                .mappings()
                .all()
            )
            previews[model.__tablename__] = {
                "columns": list(table.columns.keys()),
                "rows": [dict(row) for row in rows],
            }
        states = dict(
            (state, count)
            for state, count in await db.execute(
                select(ChannelCheck.state, func.count()).group_by(ChannelCheck.state)
            )
        )
        total = await db.scalar(select(func.count()).select_from(Channel))
        active = await db.scalar(
            select(func.count()).select_from(Channel).where(Channel.status.is_(True))
        )
        control = await db.get(VerificationControl, "telegram_web")
    return JSONResponse(
        jsonable_encoder(
            {
                "tables": previews,
                "channels": total,
                "active": active,
                "verification": states,
                "cooldown_until": control.not_before if control else 0,
            }
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/schema/{format}", dependencies=[Depends(authorize)])
async def schema(request: Request, format: str) -> Response:
    if format not in {"svg", "mmd"}:
        raise HTTPException(404)
    metadata = MetaData()
    async with request.app.state.sessions() as db:
        connection = await db.connection()
        await connection.run_sync(lambda sync: metadata.reflect(sync))
    content = svg(metadata) if format == "svg" else mermaid(metadata)
    return Response(
        content,
        media_type="image/svg+xml" if format == "svg" else "text/plain",
        headers={"Cache-Control": "no-store"},
    )


def decode_logs(data: bytes) -> str:
    """Docker multiplexed stdout/stderr; TTY containers return plain bytes."""
    if len(data) >= 8 and data[0] in {0, 1, 2} and data[1:4] == b"\x00\x00\x00":
        chunks = []
        offset = 0
        while offset + 8 <= len(data):
            length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
            if offset + 8 + length > len(data):
                break
            chunks.append(data[offset + 8 : offset + 8 + length])
            offset += 8 + length
        data = b"".join(chunks)
    return data.decode("utf-8", errors="replace")


def redact(value: str, secrets: list[str]) -> str:
    for secret in secrets:
        if len(secret) >= 6:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b", "[BOT_TOKEN]", value)
    return re.sub(
        r'(?i)(password|secret|token|authorization)(["\s:=]+)[^\s,;}]+', r"\1\2[REDACTED]", value
    )


@router.get("/api/logs", dependencies=[Depends(authorize)])
async def logs(request: Request) -> JSONResponse:
    client = request.app.state.web_client
    host = os.getenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")
    project = os.getenv("COMPOSE_PROJECT_NAME", "synchronisica-prod")
    try:
        response = await client.get(host + "/containers/json", params={"all": "1"}, timeout=5)
        response.raise_for_status()
        containers = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(503, "Container inventory unavailable") from error
    config = request.app.state.settings
    from sqlalchemy.engine import make_url

    secrets = [
        config.bot_token,
        config.webhook_secret,
        make_url(config.database_url).password or "",
    ]
    result = []
    for container in sorted(containers, key=lambda c: c.get("Names", [""])[0]):
        if container.get("Labels", {}).get("com.docker.compose.project") != project:
            continue
        identifier = container["Id"]
        if not re.fullmatch("[a-f0-9]{64}", identifier):
            continue
        entry = {"name": container["Names"][0].lstrip("/"), "state": container["State"]}
        try:
            async with client.stream(
                "GET",
                host + f"/containers/{identifier}/logs",
                params={"stdout": "1", "stderr": "1", "timestamps": "1", "tail": "10"},
                timeout=5,
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise ValueError("Oversized log response")
            entry["logs"] = redact("\n".join(decode_logs(bytes(data)).splitlines()[-10:]), secrets)
        except (httpx.HTTPError, ValueError):
            entry["logs"] = "Logs temporarily unavailable"
        result.append(entry)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
