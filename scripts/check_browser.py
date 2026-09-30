"""Check responsive layout against a running HTTP environment using Chrome."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def check_post_media(browser, url: str) -> None:
    """Exercise actual GIF decoding and muted MP4 playback through the site's CSP."""
    fixtures = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    page = browser.new_page(viewport={"width": 375, "height": 812})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for extension, mime in (("mp4", "video/mp4"), ("gif", "image/gif")):
        body = (fixtures / f"animation.{extension}").read_bytes()
        page.route(
            f"https://cdn.example/animation.{extension}",
            lambda route, _request, body=body, mime=mime: route.fulfill(
                body=body, content_type=mime
            ),
        )
    post = {
        "video": "https://cdn.example/animation.mp4",
        "photo": f"{url}/bg.jpg",
        "text": "Animation test " * 100,
        "link": "https://t.me/Synchronisica/42",
        "date": "2026-09-30T12:00:00Z",
    }
    page.route("**/api/telegram/latest", lambda route: route.fulfill(json=post))
    page.goto(url, wait_until="networkidle")
    page.wait_for_function("""() => {
        const v = document.querySelector('.tg-post-link video');
        return v && v.readyState >= 2 && !v.paused && v.currentTime > 0;
    }""")
    assert page.locator(".tg-post-link video").evaluate(
        "v => v.muted && v.loop && v.playsInline && v.videoWidth === 64"
    )
    assert page.locator(".tg-post-link img").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.emulate_media(reduced_motion="reduce")
    page.reload(wait_until="networkidle")
    assert page.locator(".tg-post-link video").evaluate("v => !v.autoplay && v.paused")

    post["video"] = None
    post["photo"] = "https://cdn.example/animation.gif"
    page.reload(wait_until="networkidle")
    page.wait_for_function("""() => {
        const image = document.querySelector('.tg-post-link img');
        return image && image.complete && image.naturalWidth === 64;
    }""")

    post["video"] = "https://cdn.example/broken.mp4"
    post["photo"] = f"{url}/bg.jpg"
    page.route("https://cdn.example/broken.mp4", lambda route: route.fulfill(status=404))
    page.reload(wait_until="networkidle")
    page.locator(".tg-post-link img").wait_for()
    assert page.locator(".tg-post-link video").count() == 0
    assert page.locator(".tg-post-link").get_attribute("href") == post["link"]
    assert not errors, errors
    page.close()
    print("Media OK: GIF, MP4 autoplay, reduced motion, unavailable video fallback.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--channel", default="chrome")
    args = parser.parse_args()
    output = Path(__file__).resolve().parent.parent / ".local" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        post = {
            "text": "Long post " * 300 + "<img src=x onerror=alert(1)>",
            "photo": f"{args.url}/bg.jpg",
            "link": "https://t.me/Synchronisica/42",
            "date": "2026-09-27T12:00:00Z",
        }
        page.route("**/api/telegram/latest", lambda route: route.fulfill(json=post))
        page.goto(args.url, wait_until="networkidle")
        page.locator(".credit").first.wait_for()
        for width, height in (
            (320, 640),
            (375, 812),
            (599, 800),
            (600, 900),
            (768, 1024),
            (959, 800),
            (960, 800),
            (1280, 720),
            (1920, 1080),
        ):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(100)
            geometry = page.evaluate("""() => {
                const rect = s => {
                    const r = document.querySelector(s).getBoundingClientRect();
                    return {x: r.x, y: r.y, right: r.right, bottom: r.bottom};
                };
                return {width: innerWidth, scroll: document.documentElement.scrollWidth,
                    stage: rect('.stage'), post: rect('.tg-post'), credits: rect('.credits'),
                    card: rect('.card'), date: rect('.tg-post-meta')};
            }""")
            assert geometry["scroll"] <= width, geometry
            assert geometry["stage"]["x"] <= geometry["card"]["x"], geometry
            assert geometry["card"]["right"] <= geometry["stage"]["right"], geometry
            assert geometry["card"]["bottom"] <= geometry["stage"]["bottom"], geometry
            assert geometry["date"]["bottom"] <= geometry["post"]["bottom"], geometry
            if width < 600:
                assert geometry["stage"]["bottom"] <= geometry["post"]["y"], geometry
                assert geometry["post"]["bottom"] <= geometry["credits"]["y"], geometry
            elif width < 960:
                assert abs(geometry["post"]["y"] - geometry["credits"]["y"]) < 1, geometry
            else:
                assert geometry["post"]["right"] <= geometry["stage"]["x"], geometry
            for button in page.locator(".actions .btn").all():
                assert button.bounding_box()["height"] >= 44
            if width in (375, 768, 1280):
                page.screenshot(path=str(output / f"{width}.png"), full_page=True)
            print(f"Layout OK: {width}x{height}")

        assert page.locator(".credits button").count() == 0
        page.locator(".credits-viewport").focus()
        assert (
            page.locator(".credits-track").evaluate("e => getComputedStyle(e).animationPlayState")
            == "paused"
        )
        page.emulate_media(reduced_motion="reduce")
        assert (
            page.locator(".credits-track").evaluate("e => getComputedStyle(e).animationName")
            == "none"
        )
        assert (
            page.locator(".credits-viewport").evaluate("e => getComputedStyle(e).overflowY")
            == "auto"
        )
        # Text zoom must not hide the actions in the main card.
        page.set_viewport_size({"width": 320, "height": 640})
        page.evaluate("document.documentElement.style.fontSize = '200%'")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert (
            page.locator(".card").bounding_box()["height"]
            <= page.locator(".stage").bounding_box()["height"]
        )

        page.unroute("**/api/telegram/latest")
        page.route("**/api/telegram/latest", lambda route: route.fulfill(status=503, json={}))
        page.route("**/api/credits", lambda route: route.fulfill(json=[]))
        page.reload(wait_until="networkidle")
        assert "временно недоступна" in page.locator("#tg-post-content").inner_text()
        assert "пока пуст" in page.locator("#credits-track").inner_text()
        assert page.locator("#tg-post-content").get_attribute("aria-busy") == "false"
        assert not errors, json.dumps(errors)
        mobile = browser.new_context(
            viewport={"width": 375, "height": 812},
            is_mobile=True,
            has_touch=True,
            device_scale_factor=2,
        )
        mobile_page = mobile.new_page()
        mobile_page.route("**/api/telegram/latest", lambda route: route.fulfill(json=post))
        mobile_page.goto(args.url, wait_until="networkidle")
        mobile_page.locator(".credit").first.wait_for()
        assert mobile_page.locator(".credits button").count() == 0
        assert mobile_page.locator(".credits-list-clone").is_hidden()
        assert (
            mobile_page.locator(".credits-track").evaluate("e => getComputedStyle(e).animationName")
            == "none"
        )
        mobile_page.locator(".credits-viewport").evaluate("e => e.scrollTop = e.scrollHeight")
        assert mobile_page.locator(".credits-viewport").evaluate("e => e.scrollTop > 0")
        mobile_page.screenshot(path=str(output / "touch-mobile.png"), full_page=True)
        mobile.close()
        check_post_media(browser, args.url)
        browser.close()
        print("Browser checks passed: layouts, controls, reduced motion, text zoom, API fallback.")


if __name__ == "__main__":
    main()
