from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routing.browser_client import BrowserServiceError, research
from app.store import add_turn, get_project, touch_project

router = APIRouter()


class BrowseRequest(BaseModel):
    project_id: int
    question: str


@router.post("/api/browse")
async def api_browse(req: BrowseRequest):
    if get_project(req.project_id) is None:
        raise HTTPException(404, "project not found")

    touch_project(req.project_id)
    add_turn(
        {
            "project_id": req.project_id, "ts": time.time(), "role": "user",
            "backend": None, "model": None, "tier": None, "content": req.question,
            "latency_ms": None, "classify_ms": None, "cost_usd": None,
            "input_tokens": None, "output_tokens": None, "fanout_group": None,
        }
    )

    try:
        result = await research(req.question)
    except BrowserServiceError as exc:
        raise HTTPException(502, str(exc)) from exc

    sources = result.get("sources", [])
    citations = "\n".join(f"[{s['n']}] {s['title']} — {s['url']}" for s in sources)
    content = result["answer"] + (f"\n\nSources:\n{citations}" if citations else "")

    turn = {
        "project_id": req.project_id, "ts": time.time(), "role": "assistant",
        "backend": "browser", "model": "sonnet", "tier": "BROWSE", "content": content,
        "latency_ms": result.get("latency_ms"), "classify_ms": 0, "cost_usd": None,
        "input_tokens": None, "output_tokens": None, "fanout_group": None,
    }
    turn_id = add_turn(turn)
    return {**turn, "id": turn_id, "sources": sources}
