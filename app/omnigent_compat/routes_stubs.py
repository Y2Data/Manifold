"""Phase 6: best-effort stubs for Omnigent endpoints with no manifold-deck
equivalent — schema-valid empty/disabled responses so the SPA doesn't
hard-error when a user pokes at these UI areas, rather than pretending to
implement any of them for real. Each is commented with what it would
take to implement genuinely, so a future decision to actually build one
isn't starting from zero context.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/v1/sessions/{session_id}/comments")
async def get_session_comments(session_id: str, path: str | None = None):
    # Real shape (verified via curl against the live server): a flat list,
    # empty when there are none. manifold-deck has no per-file/per-session
    # commenting concept — genuinely implementing this would need a new
    # `comments` table (session/turn id, path, author, text, timestamps)
    # and CRUD routes; not attempted here.
    return []


_EMPTY_LIST_OBJ = {"object": "list", "data": []}


@router.get("/v1/policies")
async def list_policies():
    # Real shape verified via curl: {"object":"list","data":[]} on an
    # instance with none configured. manifold-deck has no policy-engine
    # concept (approval gates, tool-call limits, blocked skills) — would
    # need a real enforcement point inside app/routing/router.py's
    # _run_connection to mean anything; an empty catalog here is honest,
    # not a placeholder for something partially built.
    return _EMPTY_LIST_OBJ


@router.get("/v1/policy-registry")
async def get_policy_registry():
    # The *available policy types* catalog (built-ins like "max tool calls
    # per session", "require approval for file/shell ops") — verified via
    # curl this is a substantial built-in list on the real server. Since
    # manifold-deck has no policy engine to back any of them, returning an
    # empty catalog rather than advertising policy types that would silently
    # do nothing if a user tried to configure one.
    return _EMPTY_LIST_OBJ


@router.get("/v1/sessions/{session_id}/policies")
async def list_session_policies(session_id: str):
    # Session-scoped policies (distinct from the global /v1/policies above)
    # — same "no engine to back it" reasoning, same real empty shape.
    return _EMPTY_LIST_OBJ


@router.get("/v1/sessions/{session_id}/owner")
async def get_session_owner(session_id: str):
    # Real shape verified via curl: {"owner": "local"} in single-user mode
    # — manifold-deck has no multi-user/ownership concept, so this is
    # always the same fixed answer, not per-session state.
    return {"owner": "local"}


@router.get("/v1/sessions/{session_id}/labels")
async def get_session_labels(session_id: str):
    # Real shape verified via curl: {"id": ..., "labels": {...free-form
    # metadata...}}. manifold-deck's own mapping.py already exposes an
    # (always-empty) `labels` field on session objects; this just answers
    # the same way for the dedicated endpoint.
    return {"id": session_id, "labels": {}}


@router.get("/api/version")
async def api_version():
    # Real shape verified via curl: {"version": "0.6.0"} (omnigent's own
    # version). Reports manifold-deck's own identity instead of pretending
    # to be omnigent — nothing in the vendored bundle branches on this
    # beyond display, per a scan of the bundle's own usage of the value.
    return {"version": "manifold-deck-compat-0.1"}


@router.get("/v1/scheduled-tasks")
async def list_scheduled_tasks():
    # Real shape verified via curl: {"scheduled_tasks": [...]}. No
    # scheduler/cron concept in manifold-deck — would need a background
    # task runner plus a UI for defining recurring prompts; not attempted.
    return {"scheduled_tasks": []}


@router.get("/v1/sharing")
async def get_sharing():
    # Real shape verified via curl: {"object":"sharing","sharing_mode",
    # "editable","options","public_sharing_enabled","public_sharing_editable"}.
    # Mirrors /v1/info's sharing_mode="off" — the SPA shouldn't even call
    # this given that gate, but answering consistently (fully locked down,
    # not editable) rather than 404ing if something does call it anyway.
    return {
        "object": "sharing",
        "sharing_mode": "off",
        "editable": False,
        "options": ["off"],
        "public_sharing_enabled": False,
        "public_sharing_editable": False,
    }


@router.get("/v1/runners/{runner_id}/status")
async def get_runner_status(runner_id: str):
    # Real shape verified via curl: {"runner_id","online"}. manifold-deck's
    # one synthetic runner (its own server process) is always online.
    return {"runner_id": runner_id, "online": True}


@router.get("/v1/sessions/{session_id}/agent/mcp-servers")
async def list_agent_mcp_servers(session_id: str):
    # Real shape verified via curl: {"object":"list","data":[]}. manifold's
    # claude/codex connections are plain CLI subprocess calls — whatever
    # MCP servers the user's own ~/.claude.json already configures apply
    # transparently, but there's no per-session MCP-server management UI
    # backing this (would need real config read/write, not attempted).
    return {"object": "list", "data": []}


@router.get("/v1/sessions/{session_id}/codex_goal")
async def get_codex_goal(session_id: str):
    # Real behavior verified via curl against a non-codex-native session:
    # 400 {"error":{"code":"invalid_input","message":"codex_goal is only
    # supported for codex-native sessions"}}. manifold-deck's codex
    # connections are plain `codex exec` subprocess calls, never the
    # codex-native SDK harness this feature is scoped to — so every
    # session gets the same real "not applicable" answer, not a fake goal.
    raise HTTPException(400, "codex_goal is only supported for codex-native sessions")


@router.get("/v1/sessions/{session_id}/permissions")
async def list_session_permissions(session_id: str):
    # Real shape verified via curl: a list of {"user_id","conversation_id",
    # "level"} — manifold-deck is single-user, always full (owner) access.
    return [{"user_id": "local", "conversation_id": session_id, "level": 4}]
