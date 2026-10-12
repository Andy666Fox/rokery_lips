"""Public catalogue API and authenticated, durable Telegram webhook."""

import hmac
import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from random_telegram_bot.catalogue import Catalogue, RateLimiter
from random_telegram_bot.config import Settings
from random_telegram_bot.models import Inbox
from random_telegram_bot.queue import Workers, deliver


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        config = settings or Settings.from_env()
        engine = create_async_engine(config.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalogue = Catalogue(sessions, config.catalogue_ttl)
        bot = (
            Bot(config.bot_token, session=AiohttpSession(timeout=10)) if config.bot_token else None
        )
        workers = (
            Workers(sessions, catalogue, partial(deliver, bot), config.workers) if bot else None
        )
        application.state.settings = config
        application.state.sessions = sessions
        application.state.catalogue = catalogue
        application.state.limiter = RateLimiter()
        application.state.workers = workers
        if workers:
            workers.start()
        try:
            yield
        finally:
            if workers:
                await workers.stop()
            if bot:
                await bot.session.close()
            await engine.dispose()

    application = FastAPI(
        title="Random Telegram channel", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @application.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/db")
    async def database(request: Request) -> dict[str, str]:
        try:
            async with request.app.state.sessions() as db:
                await db.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            raise HTTPException(503, "database_unavailable") from error
        return {"status": "ok"}

    @application.get("/health/ready")
    async def ready(request: Request) -> dict[str, str]:
        await database(request)
        workers = request.app.state.workers
        if not workers or any(task.done() for task in workers.tasks):
            raise HTTPException(503, "bot_not_configured")
        async with request.app.state.sessions() as db:
            dead = await db.scalar(
                select(func.count())
                .select_from(Inbox)
                .where(
                    Inbox.state == "dead",
                    Inbox.last_error.not_in(("TelegramForbiddenError", "TelegramBadRequest")),
                )
            )
            oldest = await db.scalar(
                select(func.min(Inbox.received_at)).where(
                    Inbox.state.in_(("pending", "processing"))
                )
            )
        if dead:
            raise HTTPException(503, "delivery_errors")
        if oldest is not None and time.time() - oldest > 120:
            raise HTTPException(503, "queue_stalled")
        return {"status": "ok"}

    @application.get("/api/random/channel")
    async def random_channel(request: Request) -> JSONResponse:
        address = request.client.host if request.client else "unknown"
        if not request.app.state.limiter.allow(address):
            return JSONResponse(
                {"error": "rate_limited"},
                429,
                headers={"Retry-After": "3", "Cache-Control": "no-store"},
            )
        try:
            channel = await request.app.state.catalogue.choose()
        except SQLAlchemyError:
            return JSONResponse(
                {"error": "database_unavailable"}, 503, headers={"Cache-Control": "no-store"}
            )
        if channel is None:
            return JSONResponse(
                {"error": "catalogue_empty"}, 404, headers={"Cache-Control": "no-store"}
            )
        _, username = channel
        return JSONResponse(
            {"channel": {"username": username, "url": f"https://t.me/{username}"}},
            headers={"Cache-Control": "no-store"},
        )

    @application.post("/telegram/webhook")
    async def webhook(request: Request) -> dict[str, bool]:
        config = request.app.state.settings
        supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
        if not hmac.compare_digest(supplied, config.webhook_secret):
            raise HTTPException(403, "invalid_webhook_secret")
        if not request.app.state.workers:
            raise HTTPException(503, "bot_not_configured")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise HTTPException(413, "update_too_large")
        try:
            payload = json.loads(body)
            update = Update.model_validate(payload)
        except (ValueError, ValidationError) as error:
            raise HTTPException(422, "invalid_update") from error
        message = update.message
        callback = update.callback_query
        if message:
            chat_id = message.chat.id
        elif callback:
            chat_id = callback.from_user.id
        else:
            return {"ok": True}
        try:
            async with request.app.state.sessions.begin() as db:
                duplicate = await db.scalar(
                    select(Inbox.sequence).where(Inbox.update_id == update.update_id)
                )
                if duplicate is not None:
                    return {"ok": True}
                pending = await db.scalar(
                    select(func.count())
                    .select_from(Inbox)
                    .where(Inbox.state.in_(("pending", "processing")))
                )
                if pending >= config.max_pending:
                    raise HTTPException(503, "queue_full")
                db.add(
                    Inbox(
                        update_id=update.update_id,
                        chat_id=chat_id,
                        # Persist validated wire JSON. Aiogram injects Default
                        # sentinels into nested models which cannot be JSON-encoded.
                        payload=payload,
                    )
                )
        except IntegrityError as error:
            # Concurrent delivery of the same update_id: the unique index is authoritative.
            async with request.app.state.sessions() as db:
                duplicate = await db.scalar(
                    select(Inbox.sequence).where(Inbox.update_id == update.update_id)
                )
            if duplicate is not None:
                return {"ok": True}
            raise HTTPException(503, "database_unavailable") from error
        except SQLAlchemyError as error:
            raise HTTPException(503, "database_unavailable") from error
        request.app.state.workers.wake.set()
        return {"ok": True}

    return application


app = create_app()
