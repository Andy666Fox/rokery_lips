"""Verify native Beszel login and the shared operations session in real Chrome.

Run with uv run --locked --group browser python scripts/check_monitoring_browser.py
--access-file .local/integration-secrets.json. Credentials are never printed.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import Error, async_playwright


async def verify(base: str, access: Path) -> None:
    credentials = json.loads(access.read_text(encoding="utf-8"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            channel="chrome", headless=True, args=["--disable-gpu"]
        )
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            authenticated = False

            def record_error(error: Error) -> None:
                # Beszel attempts auth-refresh before its login form is shown.
                if not authenticated and str(error) == (
                    "The request requires valid record authorization token."
                ):
                    return
                errors.append(str(error))

            page.on("pageerror", record_error)
            await page.goto(base + "/monitoring/operations/", wait_until="networkidle")
            await page.wait_for_function("!document.querySelector('#refresh').disabled")
            assert not await page.locator("main").is_visible(), "Anonymous data exposure"

            # Use the real form; a synthetic localStorage token can hide integration bugs.
            await page.goto(base + "/monitoring/", wait_until="networkidle")
            await page.locator("input[name=email]").fill(credentials["monitoring_email"])
            await page.locator("input[name=password]").fill(credentials["monitoring_password"])
            await page.get_by_role("button", name=re.compile(r"^(Войти|Sign in|Log in)$")).click()
            await page.locator("input[name=password]").wait_for(state="hidden", timeout=15000)
            authenticated = True
            await page.goto(base + "/monitoring/containers", wait_until="networkidle")
            assert await page.locator("tbody tr").count() > 0, "Beszel containers missing"

            await page.goto(base + "/monitoring/operations/", wait_until="networkidle")
            await page.wait_for_function("!document.querySelector('#refresh').disabled")
            assert await page.locator("main").is_visible(), "Shared Beszel session failed"
            for table in ("channels", "users"):
                assert 0 < await page.locator(f"#{table} tbody tr").count() <= 10
            logs = page.locator("#logs article pre")
            assert await logs.count() > 0
            for content in await logs.all_text_contents():
                assert content != "Logs temporarily unavailable", "Docker log access failed"
                assert len(content.splitlines()) <= 10
            assert await page.locator("#schema").evaluate(
                "img => img.complete && img.naturalWidth > 0"
            ), "ER image missing"
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            assert not errors, "Browser JavaScript error: " + " | ".join(errors)
            print("Native Beszel login, shared session, previews, ER, logs and mobile layout: OK")
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://dedvnutri.space")
    parser.add_argument("--access-file", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(verify(args.url.rstrip("/"), args.access_file))


if __name__ == "__main__":
    main()
