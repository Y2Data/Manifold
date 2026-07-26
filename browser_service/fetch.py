"""Page-open step: HTTP-first, browser-escalation-second.

Most research targets (docs, articles, wikis, blog posts, news) render their
main content in the initial HTML response — no reason to pay for a real
browser process on the common case. Only escalate to Playwright when a plain
httpx GET clearly didn't get real content: a non-2xx, a non-HTML
content-type, or a body trafilatura can't pull a meaningful amount of text
out of (the tell for a JS-rendered SPA shell).

A single headless Chromium instance is started lazily on first escalation
and reused across fetches — launching a new browser process per page would
dwarf the cost of the fetch itself.
"""

from __future__ import annotations

import asyncio

import httpx

from browser_service.extract import extract_text
from browser_service.models import FetchedPage

_UA = "Mozilla/5.0 (compatible; manifold-deck-browser/0.1; +local research tool)"
_TIMEOUT_S = 15.0
_MIN_HTTP_TEXT_CHARS = 200  # below this, assume a JS shell and escalate to a real browser

_browser_lock = asyncio.Lock()
_browser = None


async def _get_browser():
    global _browser
    async with _browser_lock:
        if _browser is None:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            _browser = await playwright.chromium.launch(headless=True)
    return _browser


async def _fetch_via_http(url: str) -> str | None:
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers={"User-Agent": _UA}, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", ""):
        return None
    return resp.text


async def _fetch_via_browser(url: str) -> str:
    browser = await _get_browser()
    page = await browser.new_page(user_agent=_UA)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_S * 1000)
        return await page.content()
    finally:
        await page.close()


async def fetch_page(url: str) -> FetchedPage:
    html = await _fetch_via_http(url)
    if html is not None:
        probe = extract_text(html, url)
        if probe is not None and len(probe.text) >= _MIN_HTTP_TEXT_CHARS:
            return FetchedPage(html=html, method="http")
    html = await _fetch_via_browser(url)
    return FetchedPage(html=html, method="browser")


async def shutdown_browser() -> None:
    """Called on service shutdown so a lingering headless Chromium doesn't outlive the process."""
    global _browser
    async with _browser_lock:
        if _browser is not None:
            await _browser.close()
            _browser = None
