"""Compatibility routes mapping manifold-deck's projects/turns/connections
onto Omnigent's /v1/sessions, /v1/agents, /v1/harnesses, /v1/hosts contract.
Phase 1: read-only session list + detail. Phase 2: real conversation
items, per-session agent, read-state. Phase 3 (added here): creating a
session and sending a message for real.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.omnigent_compat import ids, mapping, routes_stream
from app.routing.backends import BackendError
from app.routing.router import route
from app.store import (
    get_default_connection,
    get_or_create_project,
    get_project,
    get_project_turns,
    list_connections,
    list_default_connections,
    list_projects,
)

router = APIRouter()


def _fallback_default_connection() -> dict | None:
    """manifold-deck has no per-project connection binding — sessions show
    the global default (preferring claude) as their nominal agent."""
    return get_default_connection("claude") or (list_default_connections() or [None])[0]


class NewSessionBody(BaseModel):
    # Shape verified against the real server (network capture): agent_id/
    # host_id/labels select which agent+machine a *new* Omnigent session
    # runs on — manifold-deck has no per-project connection binding yet, so
    # these are accepted but unused; only workspace (-> project cwd) drives
    # anything here.
    agent_id: str | None = None
    host_id: str | None = None
    workspace: str
    labels: dict | None = None


@router.post("/v1/sessions", status_code=201)
async def create_session(body: NewSessionBody):
    project = get_or_create_project(body.workspace)
    default_connection = _fallback_default_connection()
    return mapping.project_to_session_response(project, [], default_connection)


@router.get("/v1/sessions")
async def list_sessions(
    limit: int = 100,
    order: str | None = None,
    sort_by: str | None = None,
    include_archived: bool = False,
    kind: str | None = None,
):
    # manifold-deck has no archived/kind concept and list_projects() is
    # already ordered by recency (last_used_at DESC) — the filters above are
    # accepted (so FastAPI doesn't 422 on the frontend's real query strings)
    # but otherwise unused.
    default_connection = _fallback_default_connection()
    projects = list_projects()[:limit]
    data = [mapping.project_to_session_list_item(p, default_connection) for p in projects]
    return {
        "object": "list",
        "data": data,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
        "has_more": False,
    }


@router.get("/v1/sessions/projects")
async def list_session_projects():
    """Real shape confirmed against the live Omnigent server's own OpenAPI
    description ("Return all project names ... List of project names") —
    a flat list of strings, not objects. manifold-deck's own project list
    already covers this; reuse it."""
    return sorted(p["name"] for p in list_projects())


@router.websocket("/v1/sessions/updates")
async def session_updates_ws(websocket: WebSocket):
    """The SPA opens this alongside the REST session list for live updates
    (new/renamed/deleted sessions elsewhere). Not in the OpenAPI schema
    (WebSockets don't show up there) — found via live network capture,
    where rejecting it with the default 403 surfaced as a console error.

    Needs to proactively *send* something periodically, not just wait to
    receive: an earlier version only read incoming frames (which the
    client never sends — it just listens), so the client's own "no frame
    in 70000ms" watchdog kept reconnecting. manifold-deck has no push
    source for session-list changes yet, so this just heartbeats to keep
    the connection considered alive; real push updates are a later
    streaming concern once /stream carries real events."""
    await websocket.accept()
    try:
        while True:
            await asyncio.sleep(30.0)
            await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass


@router.get("/v1/sessions/{session_id}")
async def get_session(session_id: str, include_items: bool = True, include_liveness: bool = False, refresh_state: bool = False):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    default_connection = _fallback_default_connection()
    turns = get_project_turns(project_id) if include_items else []
    return mapping.project_to_session_response(project, turns, default_connection)


@router.get("/v1/sessions/{session_id}/items")
async def get_session_items(session_id: str, limit: int = 100, order: str = "asc"):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    turns = get_project_turns(project_id, limit=limit)
    items: list[dict] = []
    for turn in turns:
        items.extend(mapping.turn_to_conversation_items(turn))
    if order == "desc":
        items = list(reversed(items))
    items = items[:limit]
    return {
        "object": "list",
        "data": items,
        "first_id": items[0]["id"] if items else None,
        "last_id": items[-1]["id"] if items else None,
        "has_more": False,
    }


class SessionEventBody(BaseModel):
    # Real shape verified via live network capture (this exact endpoint —
    # POST /v1/sessions/{id}/events — isn't in the OpenAPI spec at all,
    # same as the /v1/sessions/updates websocket): {"type": "message",
    # "data": {"role": "user", "content": [{"type": "input_text", "text": "..."}]}}.
    # Note this is the *documented-nested* `data` shape, unlike the
    # flattened shape /items actually returns — the two aren't symmetric.
    type: str
    data: dict


@router.post("/v1/sessions/{session_id}/events", status_code=202)
async def post_session_event(session_id: str, body: SessionEventBody, background_tasks: BackgroundTasks):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    if body.type != "message":
        raise HTTPException(400, f"unsupported event type: {body.type!r}")
    content = body.data.get("content") or []
    prompt = "\n".join(
        c.get("text", "") for c in content if isinstance(c, dict) and c.get("text")
    ).strip()
    if not prompt:
        raise HTTPException(400, "empty message")

    # Real response shape is just {"queued": true, "pending_id": "..."} —
    # the actual result arrives later over /stream, not in this response.
    # manifold-deck's route() is a blocking call (confirmed: no streaming
    # backend exists today), so run it in a background task and push the
    # result as real SSE events once it resolves, rather than blocking
    # this request for however long the model takes.
    pending_id = f"pending_{uuid.uuid4().hex}"
    background_tasks.add_task(_route_and_publish, session_id, project_id, prompt, pending_id)
    return {"queued": True, "pending_id": pending_id}


async def _route_and_publish(session_id: str, project_id: int, prompt: str, pending_id: str) -> None:
    _publish_status(session_id, "running")
    routes_stream.publish(
        session_id,
        "session.input.consumed",
        {
            "sequence_number": None,
            "type": "session.input.consumed",
            "data": {
                "item_id": f"user_{pending_id}",
                "type": "message",
                "data": {"role": "user", "content": [{"type": "input_text", "text": prompt}], "agent": None},
                "created_by": None,
            },
            "cleared_pending_id": pending_id,
        },
    )
    try:
        decisions = await route(project_id, prompt)
    except BackendError as exc:
        _publish_status(session_id, "idle", error=str(exc))
        return
    for decision in decisions:
        for item in mapping.turn_to_conversation_items(decision):
            routes_stream.publish(
                session_id,
                "response.output_item.done",
                {"sequence_number": None, "type": "response.output_item.done", "item": item},
            )
    _publish_status(session_id, "idle")


def _publish_status(session_id: str, status: str, error: str | None = None) -> None:
    routes_stream.publish(
        session_id,
        "session.status",
        {
            "sequence_number": None,
            "type": "session.status",
            "conversation_id": session_id,
            "status": status,
            "response_id": None,
            "error": error,
            "background_task_count": 0 if status == "idle" else None,
        },
    )


_EMPTY_LIST = {"object": "list", "data": [], "first_id": None, "last_id": None, "has_more": False}


@router.get("/v1/sessions/{session_id}/child_sessions")
async def get_child_sessions(session_id: str):
    # manifold-deck has no sub-agent/child-session concept — always empty,
    # matching the real server's own shape (verified via curl) for a
    # session with no children rather than guessing.
    return _EMPTY_LIST


@router.get("/v1/sessions/{session_id}/resources/terminals")
async def get_session_terminals(session_id: str, order: str = "asc", limit: int = 1000):
    # No terminal/PTY concept in manifold-deck (Phase 6 stub territory) —
    # empty list so the UI's terminal panel just shows "none" instead of
    # erroring.
    return _EMPTY_LIST


@router.get("/v1/sessions/{session_id}/agent")
async def get_session_agent(session_id: str):
    project_id = ids.project_id_from_session(session_id)
    if project_id is None or get_project(project_id) is None:
        raise HTTPException(404, "session not found")
    connection = _fallback_default_connection()
    if connection is None:
        raise HTTPException(404, "no connection configured")
    return mapping.connection_to_agent(connection)


@router.put("/v1/sessions/{session_id}/read-state", status_code=204)
async def put_session_read_state(session_id: str):
    # manifold-deck has no per-user read tracking (single_user mode) — the
    # client already has the optimistic state and re-reads on the next
    # GET /v1/sessions poll, per the real endpoint's own docstring; nothing
    # to persist here.
    return None


@router.get("/v1/harnesses")
async def list_harnesses():
    """dict of harness-kind -> list of raw objects (loosely typed upstream
    too — no dedicated schema). One entry per distinct CLI/kind currently
    configured as a connection."""
    out: dict[str, list[dict]] = {}
    for c in list_connections():
        kind = c.get("cli") if c["kind"] == "subscription_cli" else "http"
        out.setdefault(kind, []).append({"id": kind, "name": kind})
    return out


@router.get("/v1/agents")
async def list_agents():
    return [mapping.connection_to_agent(c) for c in list_connections()]


@router.get("/v1/hosts")
async def list_hosts():
    return {
        "hosts": [
            {
                "id": ids.LOCAL_HOST_ID,
                "name": "local",
                "online": True,
                "runners": [{"id": ids.LOCAL_RUNNER_ID, "online": True}],
            }
        ]
    }
