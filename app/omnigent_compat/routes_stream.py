"""GET /v1/sessions/{id}/stream — live SSE event stream, plus the
in-process pub/sub registry that Phase 3 (routes_sessions.py's
POST .../events handler) uses to push real events when a routed response
finishes.

Turned out to be a **blocking** dependency, not a nice-to-have: the SPA
won't render already-fetched conversation items until this connects
successfully — it retries 10x over ~25s then gives up, leaving the
session stuck on the empty "What should we work on?" state even though
/items already returned real data.

Event shapes below are all verified against the real Omnigent server's
actual wire format — captured two ways: `curl -N` against the live
instance for the connect-time events, and a background `curl -N`
listening on a throwaway test session's stream while sending it a real
message through the real UI, to see genuine push-on-response events
(session_id: 21fe9f32330f4c82a965fb1ec0025ede — created for this capture
and deleted immediately after; not left behind). Notably,
`response.output_item.done`'s `item` field is the *same* flattened shape
`mapping.turn_to_conversation_items` already produces for `/items`, so no
separate serialization path is needed for the two.
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

# session_id -> set of subscriber queues. Each open /stream connection owns
# one queue; publish() fans out to all of them (supports multiple viewers).
_subscribers: dict[str, set[asyncio.Queue]] = {}


def subscribe(session_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(session_id, set()).add(queue)
    return queue


def unsubscribe(session_id: str, queue: asyncio.Queue) -> None:
    queues = _subscribers.get(session_id)
    if queues:
        queues.discard(queue)
        if not queues:
            _subscribers.pop(session_id, None)


def publish(session_id: str, event: str, data: dict) -> None:
    for queue in _subscribers.get(session_id, ()):
        queue.put_nowait((event, data))


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/v1/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    project_id = ids.project_id_from_session(session_id)
    if project_id is None or get_project(project_id) is None:
        raise HTTPException(404, "session not found")

    async def _events():
        queue = subscribe(session_id)
        try:
            yield _sse(
                "session.presence",
                {"sequence_number": None, "type": "session.presence", "conversation_id": session_id, "viewers": []},
            )
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_INTERVAL_S)
                    yield _sse(event, data)
                except asyncio.TimeoutError:
                    yield _sse(
                        "session.heartbeat",
                        {"sequence_number": None, "type": "session.heartbeat", "server_time": None},
                    )
        finally:
            unsubscribe(session_id, queue)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
