"""Compatibility routes mapping manifold-deck's projects/turns/connections
onto Omnigent's /v1/sessions, /v1/agents, /v1/harnesses, /v1/hosts contract.
Phase 1: read-only session list + detail. Phase 2 (added here): real
conversation items, per-session agent, read-state.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.omnigent_compat import ids, mapping
from app.store import (
    get_default_connection,
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
