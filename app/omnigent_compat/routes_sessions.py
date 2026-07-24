"""Compatibility routes mapping manifold-deck's projects/turns/connections
onto Omnigent's /v1/sessions, /v1/agents, /v1/harnesses, /v1/hosts contract.
Phase 1 (this file, initial cut): read-only session list + detail.
"""

from __future__ import annotations

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
    manifold-deck has no push-update source yet, so this just accepts and
    holds the connection open rather than erroring; real push updates are
    a Phase 4 (streaming) concern once the SSE session stream exists."""
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
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
