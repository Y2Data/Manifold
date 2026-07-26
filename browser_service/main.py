"""Standalone browsing/research service — a separate process from the main
manifold-deck router (see scripts/browser-*.sh), deliberately decoupled from
the `connections` table/router.py: it's callable over HTTP, not a `kind`
the router dispatches to. See app/routing/browser_client.py for the caller
on the main-app side.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, HTTPException

from browser_service.extract import extract_text
from browser_service.fetch import fetch_page, shutdown_browser
from browser_service.models import ResearchRequest, ResearchResponse
from browser_service.search import search
from browser_service.synthesize import synthesize

app = FastAPI(title="manifold-deck browser service")


@app.get("/health")
async def health():
    return {"status": "up"}


@app.on_event("shutdown")
async def _shutdown():
    await shutdown_browser()


@app.post("/research", response_model=ResearchResponse)
async def research(req: ResearchRequest):
    t0 = time.monotonic()
    results = await search(req.question)
    if not results:
        raise HTTPException(502, "search returned no results")

    fetched = await asyncio.gather(*(fetch_page(r.url) for r in results), return_exceptions=True)
    pages = []
    for result, page in zip(results, fetched):
        if isinstance(page, Exception):
            continue
        extracted = extract_text(page.html, result.url)
        if extracted is not None:
            pages.append(extracted)

    answer, sources = await synthesize(req.question, pages)
    return ResearchResponse(answer=answer, sources=sources, latency_ms=int((time.monotonic() - t0) * 1000))
