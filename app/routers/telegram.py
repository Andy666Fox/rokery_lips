import os
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/telegram", tags=["telegram"])

CHANNEL = os.getenv("TG_CHANNEL", "Synchronisica")
CACHE_TTL_SECONDS = int(os.getenv("TG_CACHE_TTL", "60"))
HTTP_TIMEOUT = 5.0
USER_AGENT = "Mozilla/5.0 (compatible; tgrbservice/1.0)"

_cache: dict[str, Any] = {"data": None, "ts": 0.0}


def _parse(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    messages = soup.select("div.tgme_widget_message_wrap")
    if not messages:
        return None
    msg = messages[-1]

    text_el = msg.select_one("div.tgme_widget_message_text")
    text_html = text_el.decode_contents() if text_el else None

    photo_url = None
    photo_el = msg.select_one("a.tgme_widget_message_photo_wrap")
    if photo_el and (style := photo_el.get("style")):
        if m := re.search(r"background-image:\s*url\(['\"]?([^'\")]+)", style):
            photo_url = m.group(1)

    link_el = msg.select_one("a.tgme_widget_message_date")
    link = link_el.get("href") if link_el else None

    date_el = msg.select_one("time")
    date = date_el.get("datetime") if date_el else None

    return {"text": text_html, "photo": photo_url, "link": link, "date": date}


@router.get("/latest")
async def latest_post() -> dict:
    now = time.monotonic()
    cached = _cache["data"]
    if cached and now - _cache["ts"] < CACHE_TTL_SECONDS:
        return cached

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                f"https://t.me/s/{CHANNEL}",
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
    except httpx.HTTPError:
        if cached:
            return cached
        raise HTTPException(status_code=503, detail="telegram unavailable")

    parsed = _parse(resp.text)
    if not parsed:
        if cached:
            return cached
        raise HTTPException(status_code=404, detail="no posts found")

    _cache["data"] = parsed
    _cache["ts"] = now
    return parsed
