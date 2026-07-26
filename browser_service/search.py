"""Search-the-web step for the browsing research loop.

Default: scrape DuckDuckGo's non-JS HTML results endpoint
(html.duckduckgo.com/html/) via plain httpx — no search API key required,
matching this project's "reuse what you already have, don't add new API
keys" ethos, and it works with zero signup out of the box.

Honest caveat: this is still automated access to a service whose own ToS
doesn't explicitly bless scraping. It's meaningfully more tolerant of
low-volume, single-user, sequential access than Google (which CAPTCHA-gates
non-browser traffic almost immediately) — acceptable for a personal local
research tool used at low volume, not something to parallelize or scale up.
If you outgrow that, swap this module's `search()` for a call to a paid
search API (Brave/Bing/Tavily) — same signature, no caller changes needed.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

import httpx
from lxml import html as lxml_html

from browser_service.models import SearchResult

_SEARCH_URL = "https://html.duckduckgo.com/html/"
_UA = "Mozilla/5.0 (compatible; manifold-deck-browser/0.1; +local research tool)"
_TIMEOUT_S = 15.0
_MAX_RESULTS = 6


async def search(query: str) -> list[SearchResult]:
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers={"User-Agent": _UA}) as client:
        resp = await client.post(_SEARCH_URL, data={"q": query})
        resp.raise_for_status()
    return _parse_results(resp.text)[:_MAX_RESULTS]


def _resolve_href(href: str) -> str:
    """DDG's HTML results wrap outbound links in a /l/?uddg=<encoded> redirect — unwrap it
    so callers get the real target URL, not a duckduckgo.com bounce link."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if target:
            return unquote(target)
    return href


def _parse_results(body: str) -> list[SearchResult]:
    tree = lxml_html.fromstring(body)
    results = []
    for node in tree.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " result ")]'):
        links = node.xpath('.//a[contains(concat(" ", normalize-space(@class), " "), " result__a ")]')
        if not links:
            continue
        href = links[0].get("href") or ""
        if not href:
            continue
        title = links[0].text_content().strip()
        snippet_nodes = node.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " result__snippet ")]')
        snippet = snippet_nodes[0].text_content().strip() if snippet_nodes else ""
        results.append(SearchResult(url=_resolve_href(href), title=title, snippet=snippet))
    return results
