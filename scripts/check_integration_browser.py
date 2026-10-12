"""Verify the shared catalogue UI and password-protected Beszel subpath in Chrome."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://dedvnutri.space")
    parser.add_argument("--credentials", type=Path, default=Path(".local/integration-secrets.json"))
    args = parser.parse_args()
    credentials = json.loads(args.credentials.read_text())
    output = Path(".local/screenshots")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        errors: list[str] = []
        failed_http: list[str] = []
        page.on("pageerror", lambda error: errors.append(f"{page.url}: {error.stack}"))
        page.on(
            "response",
            lambda response: (
                failed_http.append(f"{response.status} {response.url}")
                if response.status >= 400
                else None
            ),
        )
        page.goto(args.url, wait_until="networkidle")
        page.locator("#random-channel-button").click()
        page.locator('#random-channel-result a[href^="https://t.me/"]').wait_for()
        for width in (320, 375, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "random-channel-desktop.png"), full_page=True)
        print("Website random channel UI and responsive widths: OK")

        response = page.request.get(args.url + "/monitoring/api/beszel/info")
        assert response.status in (401, 403)
        for resource in ("systems", "system_stats", "network_monitors"):
            response = page.request.get(
                args.url + f"/monitoring/api/collections/{resource}/records"
            )
            assert response.status == 200
            assert response.json()["items"] == [], resource
            assert response.json()["totalItems"] == 0, resource
        page.goto(args.url + "/monitoring/", wait_until="networkidle")
        page.locator("#email").wait_for()
        # Beszel 0.21.0 probes trusted-header login on an anonymous page without
        # catching the rejected auth-refresh promise. Keep this known upstream
        # error visible, and require a clean authenticated session below.
        anonymous_errors = list(errors)
        assert all(
            "ClientResponseError 401: The request requires valid record authorization token."
            in error
            for error in anonymous_errors
        ), anonymous_errors
        assert all(
            item == f"401 {args.url}/monitoring/api/collections/users/auth-refresh"
            for item in failed_http
        ), failed_http
        if anonymous_errors:
            print("Beszel 0.21.0: known anonymous auth-refresh rejection observed")
        errors.clear()
        failed_http.clear()
        page.locator("#email").fill(credentials["monitoring_email"])
        page.locator("#pass").fill(credentials["monitoring_password"])
        page.locator("form button").click()
        page.locator("#email").wait_for(state="hidden")
        page.get_by_text("rockery-lips", exact=True).filter(visible=True).first.wait_for()
        token = page.evaluate("JSON.parse(localStorage.getItem('pocketbase_auth')).token")
        headers = {"Authorization": token}
        for resource in ("systems", "system_stats", "network_monitors"):
            response = page.request.get(
                args.url + f"/monitoring/api/collections/{resource}/records?perPage=1",
                headers=headers,
            )
            assert response.status == 200, (resource, response.status)
            assert response.json()["totalItems"] > 0, resource
        page.reload(wait_until="networkidle")
        page.get_by_text("rockery-lips", exact=True).filter(visible=True).first.wait_for()
        page.screenshot(path=str(output / "monitoring-dashboard.png"), full_page=True)
        assert "/monitoring" in page.url
        assert not errors, (errors, failed_http)
        assert not failed_http, failed_http
        print("Beszel: anonymous API denied, password login and dashboard under /monitoring/: OK")
        browser.close()


if __name__ == "__main__":
    main()
