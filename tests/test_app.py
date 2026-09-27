import unittest

import httpx

from app.main import app
from app.routers.telegram import get_telegram_service
from app.services.telegram import TelegramPost, TelegramUnavailableError


class StubTelegram:
    async def latest_post(self) -> TelegramPost:
        return TelegramPost(text="Test post", link="https://t.me/Synchronisica/1")


class FailedTelegram:
    async def latest_post(self) -> TelegramPost:
        raise TelegramUnavailableError("offline")


class AppTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    async def test_app_serves_page_assets_and_data_from_project_root(self) -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            for path in ("/", "/styles.css", "/app.js", "/bg.jpg", "/dragon_alphabet.woff2"):
                with self.subTest(path=path):
                    self.assertEqual((await client.get(path)).status_code, 200)
            self.assertEqual((await client.get("/api/health")).json(), {"status": "ok"})
            response = await client.get("/api/credits")
            self.assertEqual(response.status_code, 200)
            self.assertGreater(len(response.json()), 0)
            self.assertEqual(set(response.json()[0]), {"title", "artist"})
            self.assertEqual((await client.get("/api/missing")).status_code, 404)
            self.assertEqual((await client.get("/.env")).status_code, 404)

    async def test_telegram_response_and_unavailable_contract(self) -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            app.dependency_overrides[get_telegram_service] = StubTelegram
            response = await client.get("/api/telegram/latest")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["text"], "Test post")
            app.dependency_overrides[get_telegram_service] = FailedTelegram
            response = await client.get("/api/telegram/latest")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["cache-control"], "no-store")
