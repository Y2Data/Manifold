"""Compatibility routes mapping manifold-deck's projects/turns/connections
onto Omnigent's /v1/sessions, /v1/agents, /v1/harnesses, /v1/hosts contract.
Phase 1: read-only session list + detail. Phase 2: real conversation
items, per-session agent, read-state. Phase 3 (added here): creating a
session and sending a message for real.
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.omnigent_compat import ids, mapping, permissions, routes_stream
from app.routing.backends import BackendError
from app.routing.router import route
from app.store import (
    create_project,
    delete_project,
    get_default_connection,
    get_project,
    get_project_turns,
    list_connections,
    list_default_connections,
    list_projects,
    rename_project,
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
    # Always a fresh project row (see store.create_project) — "New session"
    # should start a distinct conversation even in an already-used folder,
    # matching the real Omnigent UI rather than silently resuming whatever
    # project already lives at that cwd.
    project = create_project(body.workspace)
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
    data = [
        mapping.project_to_session_list_item(p, default_connection, permissions.list_pending_for_project(p["id"]))
        for p in projects
    ]
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
    pending = permissions.list_pending_for_project(project_id)
    return mapping.project_to_session_response(project, turns, default_connection, pending)


class UpdateSessionBody(BaseModel):
    # Full real request shape (verified via curl + OpenAPI) — most of these
    # fields (runner_id, labels, reasoning_effort, model_override,
    # collaboration_mode, cost_control_mode_override, external_session_id,
    # terminal_launch_args, archived) have no manifold-deck storage to back
    # them and are accepted-then-ignored, same as read-state, rather than
    # half-wired. `title` maps onto the project's own name (see
    # store.rename_project) since that's an obvious, real analog.
    runner_id: str | None = None
    title: str | None = None
    labels: dict | None = None
    reasoning_effort: str | None = None
    model_override: str | None = None
    collaboration_mode: str | None = None
    cost_control_mode_override: str | None = None
    external_session_id: str | None = None
    terminal_launch_args: list[str] | None = None
    archived: bool | None = None
    silent: bool = False


@router.patch("/v1/sessions/{session_id}")
async def patch_session(session_id: str, body: UpdateSessionBody):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    if body.title is not None:
        project = rename_project(project_id, body.title)
    default_connection = _fallback_default_connection()
    turns = get_project_turns(project_id)
    pending = permissions.list_pending_for_project(project_id)
    return mapping.project_to_session_response(project, turns, default_connection, pending)


@router.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    # Real shape verified via OpenAPI spec: ConversationDeleted
    # {"id","object":"conversation.deleted","deleted":true}.
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    delete_project(project_id)
    return {"id": session_id, "object": "conversation.deleted", "deleted": True}


class AutoTitleBody(BaseModel):
    # Real shape (OpenAPI: AutomaticSessionRenameRequest) — the *caller*
    # proposes a title (usually agent-generated) and the server decides
    # whether to actually apply it. manifold-deck sessions are always
    # "top-level" (no sub-agent/child-session concept), so the only real
    # gating condition (AutomaticSessionRenameResponse.reason ==
    # "not_top_level") never applies here — always rename if given a title.
    title: str


@router.post("/v1/sessions/{session_id}/auto-title")
async def auto_title_session(session_id: str, body: AutoTitleBody):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    rename_project(project_id, body.title)
    return {"renamed": True, "title": body.title, "reason": None}


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


class ElicitationResolveBody(BaseModel):
    # Real shape confirmed straight from the vendored bundle's own JS
    # (function O4e / the button click handlers in index-CFYup66L.js):
    # plain Approve/Reject send {"action": "accept"|"decline"} with no
    # `content`; the "don't ask again" variant adds a `content` dict.
    action: str
    content: dict | None = None


@router.post("/v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve", status_code=202)
async def resolve_elicitation(session_id: str, elicitation_id: str, body: ElicitationResolveBody):
    # Real status code (202) and response body confirmed via live network
    # capture against the real Omnigent server's own resolve click.
    if permissions.find_project_id_for_elicitation(elicitation_id) is None:
        raise HTTPException(404, "elicitation not found")
    if body.action not in ("accept", "decline"):
        raise HTTPException(400, f"unsupported action: {body.action!r}")
    ok = permissions.resolve_pending(elicitation_id, body.action, body.content)
    if not ok:
        raise HTTPException(409, "elicitation already resolved")
    return {"queued": False}


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


_HARNESS_LABELS = {"claude": "Claude", "codex": "Codex", "http": "HTTP"}


def _harness_entry(harness_id: str, model_family: str) -> dict:
    # Shape verified via curl: {"data": [{id, label, capabilities: {...}}]}
    # — completely different from an earlier untested guess (a dict keyed
    # by cli name), which is almost certainly why the new-session agent
    # picker showed "No agents" despite /v1/agents and /v1/hosts's
    # configured_harnesses both being correct: the picker couldn't parse
    # any harness definitions out of the old shape at all. Capabilities
    # are filled in honestly for what manifold-deck's backends.py actually
    # does (a one-shot blocking subprocess call) rather than copied from
    # the real server's richer SDK-based entries — no streaming/interrupt/
    # resume support exists here, so those are false/cold-only, not
    # guessed as fully-featured.
    return {
        "id": harness_id,
        "label": _HARNESS_LABELS.get(harness_id, harness_id.title()),
        "capabilities": {
            "integration_mode": "cli-subprocess",
            "elicitation": "none",
            "resume": "cold-only",
            "effort": model_family,
            "model_family": model_family,
            "auth": "omnigent-credential",
            "subagents": False,
            "interrupt": False,
            "streaming": False,
            "steering": None,
            "live_queue": None,
            "images": None,
            "compaction": None,
        },
    }


@router.get("/v1/harnesses")
async def list_harnesses():
    seen: dict[str, str] = {}
    for c in list_connections():
        harness_id = c.get("cli") if c["kind"] == "subscription_cli" else "http"
        seen.setdefault(harness_id, harness_id)
    return {"data": [_harness_entry(h, h) for h in seen]}


@router.get("/v1/agents")
async def list_agents():
    # Real shape verified via curl: {"object":"list","data":[...]} — same
    # wrapper pattern as /v1/sessions etc. An earlier version returned a
    # bare array, which is almost certainly why the new-session agent
    # picker showed "No agents" and stayed disabled despite /v1/harnesses
    # and /v1/hosts's configured_harnesses both being correct: the
    # frontend couldn't find any items in a shape it wasn't expecting.
    data = [mapping.connection_to_agent(c) for c in list_connections()]
    return {"object": "list", "data": data}


def _local_host() -> dict:
    # Real shape verified via curl against the live server: {host_id, name,
    # owner, status, sandbox_provider, configured_harnesses}. The earlier
    # version used made-up field names (id/online/runners) that don't
    # exist on the real object at all — the frontend's new-session picker
    # reads `configured_harnesses` to know which agents can run on a host,
    # so a mismatched/empty one is exactly why it reported "no host".
    return {
        "host_id": ids.LOCAL_HOST_ID,
        "name": socket.gethostname(),
        "owner": "local",
        "status": "online",
        "sandbox_provider": None,
        "configured_harnesses": mapping.build_configured_harnesses(list_connections()),
    }


@router.get("/v1/hosts")
async def list_hosts():
    return {"hosts": [_local_host()]}


@router.get("/v1/hosts/{host_id}")
async def get_host(host_id: str):
    if host_id != ids.LOCAL_HOST_ID:
        raise HTTPException(404, "host not found")
    return _local_host()


def _list_host_dir(path: Path, limit: int) -> dict:
    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except (PermissionError, FileNotFoundError):
        raise HTTPException(404, "path not found or not readable")
    data = []
    for p in children[:limit]:
        try:
            stat = p.stat()
        except OSError:
            continue
        data.append(
            {
                "name": p.name,
                "path": str(p),
                "type": "directory" if p.is_dir() else "file",
                "bytes": None if p.is_dir() else stat.st_size,
                "modified_at": int(stat.st_mtime),
            }
        )
    return {"object": "list", "data": data, "has_more": len(children) > limit}


@router.get("/v1/hosts/{host_id}/filesystem")
async def get_host_filesystem_root(host_id: str, limit: int = 20):
    # Used by the new-session folder picker. Real behavior (verified via
    # curl): no path param exists on this bare route at all — it always
    # lists $HOME, and browsing elsewhere goes through the *other* route
    # below (/filesystem/{path}, a path segment).
    if host_id != ids.LOCAL_HOST_ID:
        raise HTTPException(404, "host not found")
    return _list_host_dir(Path.home(), limit)


@router.get("/v1/hosts/{host_id}/filesystem/{path:path}")
async def get_host_filesystem_path(host_id: str, path: str, limit: int = 20):
    # Real semantics (from the live server's own docstring): the client
    # sends an absolute path with FastAPI's leading "/" stripped by the
    # :path converter, so it's re-added here; "~" is expanded server-side
    # too. Same "whole local filesystem, single local user" posture this
    # project already uses for app/routers/projects.py's /browse endpoint
    # (this server only binds to 127.0.0.1).
    if host_id != ids.LOCAL_HOST_ID:
        raise HTTPException(404, "host not found")
    raw = path if path.startswith("~") else "/" + path
    target = Path(raw).expanduser()
    if not target.is_dir():
        raise HTTPException(404, "path not found or not a directory")
    return _list_host_dir(target, limit)


@router.get("/v1/hosts/{host_id}/worktrees")
async def get_host_worktrees(host_id: str, path: str):
    """Real shape verified via curl — and it's genuinely backed by `git
    worktree list --porcelain` on the given path (confirmed: it surfaced
    an actual worktree from a different session working on this same
    repo). Implemented for real rather than stubbed empty, since it's a
    cheap read-only git command and the new-session worktree picker is
    useless without it."""
    if host_id != ids.LOCAL_HOST_ID:
        raise HTTPException(404, "host not found")
    if not Path(path).is_dir():
        raise HTTPException(400, "path is not a directory")
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", path, "worktree", "list", "--porcelain",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (OSError, asyncio.TimeoutError):
        return {"object": "list", "data": []}
    if proc.returncode != 0:
        return {"object": "list", "data": []}

    data: list[dict] = []
    current: dict | None = None
    for line in stdout.decode(errors="replace").splitlines():
        if not line.strip():
            if current:
                data.append(current)
                current = None
        elif line.startswith("worktree "):
            current = {
                "path": line[len("worktree ") :],
                "branch": None,
                "is_main": len(data) == 0,
                "detached": False,
            }
        elif current is not None and line.startswith("branch "):
            current["branch"] = line[len("branch ") :].removeprefix("refs/heads/")
        elif current is not None and line == "detached":
            current["detached"] = True
    if current:
        data.append(current)
    return {"object": "list", "data": data}


@router.get("/v1/runners")
async def list_runners():
    # Real shape verified via curl: {"data": [{"runner_id", "online",
    # "harnesses": [...]}]}. Distinct from /v1/hosts's configured_harnesses
    # dict — this is the flat list of harness names actually available,
    # used elsewhere in the new-session flow.
    harnesses = sorted(k for k, v in mapping.build_configured_harnesses(list_connections()).items() if v)
    return {"data": [{"runner_id": ids.LOCAL_RUNNER_ID, "online": True, "harnesses": harnesses}]}
