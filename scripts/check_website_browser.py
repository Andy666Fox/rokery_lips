"""Verify the homepage layout, real WebGL viewer, camera controls and error recovery.

Run with uv run --locked --group browser python scripts/check_website_browser.py
--url http://127.0.0.1:8773 --fixture .local/viewer-fixture.glb.
Omit --fixture to check the configured production model and real website APIs.
"""

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import Route, async_playwright, expect


async def verify(base: str, fixture: Path | None, output: Path, *, mock_api: bool = False) -> None:
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.add_init_script(
                "window.cspViolations = []; document.addEventListener('securitypolicyviolation', "
                "event => window.cspViolations.push("
                "event.violatedDirective + ': ' + event.blockedURI));"
            )
            if fixture is not None:
                configuration = {
                    "src": "/models/browser-fixture.glb",
                    "title": "Тестовая модель",
                    "alt": "Объёмные металлические кольца",
                    "autoRotate": False,
                }

                async def config_response(route: Route) -> None:
                    await route.fulfill(json=configuration)

                async def model_response(route: Route) -> None:
                    await route.fulfill(path=fixture, content_type="model/gltf-binary")

                await page.route("**/model-viewer.json", config_response)
                await page.route("**/models/browser-fixture.glb", model_response)
            if fixture is not None or mock_api:
                await page.route(
                    "**/api/telegram/latest",
                    lambda route: route.fulfill(
                        json={
                            "text": "Новая публикация Synchronisica",
                            "link": "https://t.me/Synchronisica/1",
                        }
                    ),
                )
                await page.route(
                    "**/api/credits",
                    lambda route: route.fulfill(json=[{"title": "Test track", "artist": "Artist"}]),
                )
                await page.route(
                    "**/api/random/channel",
                    lambda route: route.fulfill(json={"channel": {"username": "testchannel"}}),
                )
            await page.goto(base + "/", wait_until="domcontentloaded")
            frame = page.frame_locator(".model-panel iframe")
            await frame.locator("#reset-view").wait_for(state="visible")
            await expect(frame.locator("#reset-view")).to_be_enabled(timeout=45000)
            iframe = page.frames[1]
            initial = await iframe.evaluate(
                "JSON.stringify(document.querySelector('model-viewer').getCameraOrbit())"
            )
            box = await frame.locator("model-viewer").bounding_box()
            assert box is not None
            center_x, center_y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            await page.mouse.move(center_x, center_y)
            await page.mouse.down()
            await page.mouse.move(center_x + 90, center_y + 25, steps=12)
            await page.mouse.up()
            await page.wait_for_timeout(300)
            rotated = await iframe.evaluate(
                "JSON.stringify(document.querySelector('model-viewer').getCameraOrbit())"
            )
            assert rotated != initial, "Dragging must rotate the model"
            radius = await iframe.evaluate(
                "document.querySelector('model-viewer').getCameraOrbit().radius"
            )
            await page.mouse.wheel(0, -250)
            await page.wait_for_timeout(300)
            assert (
                await iframe.evaluate(
                    "document.querySelector('model-viewer').getCameraOrbit().radius"
                )
                != radius
            )
            target = await iframe.evaluate(
                "JSON.stringify(document.querySelector('model-viewer').getCameraTarget())"
            )
            await page.mouse.move(center_x, center_y)
            await page.mouse.down(button="right")
            await page.mouse.move(center_x + 40, center_y + 20, steps=8)
            await page.mouse.up(button="right")
            await page.wait_for_timeout(300)
            assert (
                await iframe.evaluate(
                    "JSON.stringify(document.querySelector('model-viewer').getCameraTarget())"
                )
                != target
            )
            await frame.locator("#reset-view").click()
            await page.wait_for_timeout(100)
            restored = await iframe.evaluate(
                "JSON.stringify(document.querySelector('model-viewer').getCameraOrbit())"
            )
            assert restored == initial, "Reset must restore the original orbit"
            if await frame.locator("#fullscreen").is_visible():
                await frame.locator("#fullscreen").click()
                await expect(frame.locator("html:fullscreen")).to_have_count(1)
                await frame.locator("#fullscreen").click()
                await expect(frame.locator("html:fullscreen")).to_have_count(0)
            for width in (1920, 1440, 1280, 1024, 768, 390, 360):
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(100)
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    f"Overflow at {width}px"
                )
                viewer_box = await page.locator(".model-panel").bounding_box()
                content_box = await page.locator(".site-content").bounding_box()
                assert viewer_box is not None and content_box is not None
                if width >= 1200:
                    assert viewer_box["x"] + viewer_box["width"] <= content_box["x"] + 1
                else:
                    assert viewer_box["y"] + viewer_box["height"] <= content_box["y"] + 1
                if width in (1440, 390):
                    await page.screenshot(
                        path=str(output / f"homepage-{width}.png"), full_page=True
                    )
            await page.locator("#random-channel-button").click()
            await page.locator("#random-channel-result a").wait_for()
            assert not await page.evaluate("window.cspViolations"), "Homepage CSP violation"
            violations = await iframe.evaluate("window.cspViolations")
            assert not violations, "Viewer CSP violation: " + " | ".join(violations)
            assert not errors, "Browser errors: " + " | ".join(errors)
            if fixture is not None:

                async def unavailable(route: Route) -> None:
                    await route.fulfill(status=404)

                await page.route("**/models/browser-fixture.glb", unavailable)
                await iframe.goto(base + "/viewer/")
                await frame.locator("#retry-model").wait_for(state="visible")
                assert await frame.locator("#reset-view").is_disabled()
                await page.unroute("**/models/browser-fixture.glb", unavailable)
                await frame.locator("#retry-model").click()
                await expect(frame.locator("#reset-view")).to_be_enabled(timeout=45000)
            print(
                json.dumps(
                    {
                        "webgl": "ok",
                        "camera": ["rotate", "zoom", "pan", "reset", "fullscreen"],
                        "responsive_widths": [1920, 1440, 1280, 1024, 768, 390, 360],
                        "csp": "ok",
                        "error_retry": fixture is not None,
                        "screenshots": str(output),
                    }
                )
            )
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8773")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--mock-api", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(".local/viewer-browser"))
    args = parser.parse_args()
    asyncio.run(verify(args.url.rstrip("/"), args.fixture, args.output, mock_api=args.mock_api))


if __name__ == "__main__":
    main()
