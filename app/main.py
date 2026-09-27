from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, Settings
from app.routers import credits, health, telegram
from app.services.telegram import TelegramService

USER_AGENT = "Mozilla/5.0 (compatible; Synchronisica/1.0)"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.from_env()
    async with httpx.AsyncClient(
        timeout=settings.telegram_http_timeout,
        follow_redirects=True,
        proxy=settings.telegram_proxy,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        app.state.telegram_service = TelegramService(
            client=client,
            channel=settings.telegram_channel,
            cache_ttl=settings.telegram_cache_ttl,
            stale_ttl=settings.telegram_cache_stale_ttl,
        )
        yield


app = FastAPI(
    title="Synchronisica",
    description="Synchronisica landing page and API",
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(health.router, prefix="/api")
app.include_router(telegram.router, prefix="/api")
app.include_router(credits.router, prefix="/api")
# Direct uv development also serves the page; deployed static files are served by Caddy.
app.mount("/", StaticFiles(directory=PROJECT_ROOT / "html", html=True), name="static")
