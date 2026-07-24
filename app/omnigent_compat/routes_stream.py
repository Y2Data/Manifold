"""GET /v1/sessions/{id}/stream — live SSE event stream.

Turned out to be a **blocking** dependency, not a nice-to-have: the SPA
won't render already-fetched conversation items until this connects
successfully — it retries 10x over ~25s then gives up, leaving the
session stuck on the empty "What should we work on?" state even though
/items already returned real data. Confirmed via the real Omnigent
server's actual event wire format (`curl -N` against the live instance):

    event: session.heartbeat
    data: {"sequence_number": null, "type": "session.heartbeat", "server_time": null}

    event: session.presence
    data: {"sequence_number": null, "type": "session.presence", "conversation_id": "...", "viewers": []}

This first cut sends an initial presence event plus periodic heartbeats
to keep the connection alive and unblock rendering — not yet real
push-on-new-message events (that needs `route()` in
app/routing/router.py to notify a subscriber, which is a bigger change;
tracked separately rather than blocking this fix).
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.omnigent_compat import ids
from app.store import get_project

router = APIRouter()

_HEARTBEAT_INTERVAL_S = 20.0


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/v1/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    project_id = ids.project_id_from_session(session_id)
    if project_id is None or get_project(project_id) is None:
        raise HTTPException(404, "session not found")

    async def _events():
        yield _sse(
            "session.presence",
            {"sequence_number": None, "type": "session.presence", "conversation_id": session_id, "viewers": []},
        )
        while True:
            if await request.is_disconnected():
                break
            yield _sse(
                "session.heartbeat",
                {"sequence_number": None, "type": "session.heartbeat", "server_time": None},
            )
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
