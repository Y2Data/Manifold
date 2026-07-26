"""Thin HTTP client for the standalone browsing/research service
(browser_service/, run via `./manifold browser-up`).

Deliberately NOT a `connections` row / router.py dispatch target: the
browsing feature is its own always-or-never-running process, not a routable
model backend, so this stays a plain caller — same "thin httpx wrapper,
clear error on failure" shape as app/routing/http_backend.py, without
touching the connections table or keyring.
"""

from __future__ import annotations

import os

import httpx

_BASE_URL = os.environ.get("BROWSER_SERVICE_URL", "http://localhost:8090")
_TIMEOUT_S = 120.0


class BrowserServiceError(RuntimeError):
    pass


async def research(question: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        try:
            resp = await client.post(f"{_BASE_URL}/research", json={"question": question})
        except httpx.ConnectError as exc:
            raise BrowserServiceError(
                "browser service isn't running — start it with `./manifold browser-up`"
            ) from exc
    if resp.status_code >= 400:
        raise BrowserServiceError(f"browser service: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()
