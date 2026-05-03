from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from routers import credits, health, telegram

app = FastAPI(
    title="tgrbservice",
    description="Synchronisica — landing & API",
    version="0.2.0",
)

app.include_router(health.router, prefix="/api")
app.include_router(telegram.router, prefix="/api")
app.include_router(credits.router, prefix="/api")

app.mount("/", StaticFiles(directory="static", html=True), name="static")
