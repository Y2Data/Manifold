"""Phase 6: best-effort stubs for Omnigent endpoints with no manifold-deck
equivalent — schema-valid empty/disabled responses so the SPA doesn't
hard-error when a user pokes at these UI areas, rather than pretending to
implement any of them for real. Each is commented with what it would
take to implement genuinely, so a future decision to actually build one
isn't starting from zero context.
"""

from __future__ import annotations

from fastapi import APIRouter

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
