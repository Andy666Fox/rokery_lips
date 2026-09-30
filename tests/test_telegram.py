import asyncio
import unittest

import httpx

from app.services.telegram import (
    TelegramResponseError,
    TelegramService,
    TelegramUnavailableError,
    parse_latest_post,
)

VALID_HTML = """
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message_text">Older post</div>
</div>
<div class="tgme_widget_message_wrap">
  <a class="tgme_widget_message_photo_wrap"
     style="background-image:url('https://cdn.example/post.jpg')"></a>
  <div class="tgme_widget_message_text">Hello <b>world</b><img onerror="alert(1)"></div>
  <a class="tgme_widget_message_date" href="https://t.me/Synchronisica/42"></a>
  <time datetime="2026-09-05T00:00:00+00:00"></time>
</div>
"""


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TelegramParserTests(unittest.TestCase):
    def test_extracts_telegram_animation_and_preview(self) -> None:
        html = """
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message_video_thumb"
               style="background-image:url('https://cdn.example/poster.jpg')"></div>
          <video class="tgme_widget_message_video blured" src="https://cdn.example/blur.mp4"></video>
          <video class="tgme_widget_message_video js-message_video" autoplay loop muted
                 src="https://cdn.example/animation.mp4?token=public"></video>
        </div>
        """
        post = parse_latest_post(html)
        self.assertEqual(post.video, "https://cdn.example/animation.mp4?token=public")
        self.assertEqual(post.photo, "https://cdn.example/poster.jpg")

    def test_extracts_video_source_child(self) -> None:
        post = parse_latest_post("""
        <div class="tgme_widget_message_wrap">
          <video class="tgme_widget_message_video" poster="https://cdn.example/preview.jpg">
            <source src="https://cdn.example/animation.mp4" type="video/mp4">
          </video>
        </div>""")
        self.assertEqual(post.video, "https://cdn.example/animation.mp4")
        self.assertEqual(post.photo, "https://cdn.example/preview.jpg")

    def test_preserves_direct_gif_image(self) -> None:
        post = parse_latest_post("""
        <div class="tgme_widget_message_wrap">
          <a class="tgme_widget_message_photo_wrap">
            <img src="https://cdn.example/animation.gif">
          </a>
        </div>""")
        self.assertEqual(post.photo, "https://cdn.example/animation.gif")
        self.assertIsNone(post.video)

    def test_rejects_unsafe_video_and_poster_urls(self) -> None:
        post = parse_latest_post("""
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message_text">Safe text</div>
          <video class="tgme_widget_message_video" src="javascript:alert(1)"
                 poster="data:text/html,unsafe"></video>
        </div>""")
        self.assertIsNone(post.video)
        self.assertIsNone(post.photo)

    def test_returns_plain_text_and_safe_urls(self) -> None:
        post = parse_latest_post(VALID_HTML)

        self.assertEqual(post.text, "Hello\nworld")
        self.assertNotIn("onerror", post.text or "")
        self.assertEqual(post.photo, "https://cdn.example/post.jpg")
        self.assertEqual(post.link, "https://t.me/Synchronisica/42")
        self.assertIsNotNone(post.date)

    def test_rejects_active_link_schemes(self) -> None:
        html = """
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message_text">Safe text</div>
          <a class="tgme_widget_message_date" href="javascript:alert(1)"></a>
        </div>
        """

        post = parse_latest_post(html)

        self.assertIsNone(post.link)

    def test_requires_at_least_one_post(self) -> None:
        with self.assertRaises(TelegramResponseError):
            parse_latest_post("<html></html>")


class TelegramServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_backoff_does_not_extend_stale_lifetime(self) -> None:
        clock = MutableClock()

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=VALID_HTML) if clock.now == 0 else httpx.Response(502)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = TelegramService(
                client=client,
                channel="Synchronisica",
                cache_ttl=60,
                stale_ttl=120,
                clock=clock,
            )
            await service.latest_post()
            clock.now = 119
            await service.latest_post()
            clock.now = 120
            with self.assertRaises(TelegramUnavailableError):
                await service.latest_post()

    async def test_cold_failure_is_coalesced_for_concurrent_requests(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(502)

        clock = MutableClock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = TelegramService(
                client=client,
                channel="Synchronisica",
                cache_ttl=60,
                stale_ttl=120,
                clock=clock,
            )
            results = await asyncio.gather(
                *(service.latest_post() for _ in range(5)), return_exceptions=True
            )
            self.assertTrue(all(isinstance(result, TelegramUnavailableError) for result in results))
            self.assertEqual(request_count, 1)
            clock.now = 60
            with self.assertRaises(TelegramUnavailableError):
                await service.latest_post()
            self.assertEqual(request_count, 2)

    async def test_reuses_fresh_cache(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, text=VALID_HTML)

        clock = MutableClock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = TelegramService(
                client=client,
                channel="Synchronisica",
                cache_ttl=60,
                stale_ttl=3600,
                clock=clock,
            )
            first, second = await asyncio.gather(service.latest_post(), service.latest_post())

        self.assertEqual(first, second)
        self.assertEqual(request_count, 1)

    async def test_uses_recent_stale_cache_on_refresh_failure(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(200, text=VALID_HTML)
            return httpx.Response(502)

        clock = MutableClock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = TelegramService(
                client=client,
                channel="Synchronisica",
                cache_ttl=60,
                stale_ttl=3600,
                clock=clock,
            )
            cached = await service.latest_post()
            clock.now = 61
            stale = await service.latest_post()
            same_stale = await service.latest_post()

        self.assertEqual(stale, cached)
        self.assertEqual(same_stale, cached)
        self.assertEqual(request_count, 2)

    async def test_rejects_expired_stale_cache(self) -> None:
        should_fail = False

        def handler(_request: httpx.Request) -> httpx.Response:
            if should_fail:
                return httpx.Response(502)
            return httpx.Response(200, text=VALID_HTML)

        clock = MutableClock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = TelegramService(
                client=client,
                channel="Synchronisica",
                cache_ttl=60,
                stale_ttl=3600,
                clock=clock,
            )
            await service.latest_post()
            should_fail = True
            clock.now = 3600

            with self.assertRaises(TelegramUnavailableError):
                await service.latest_post()
