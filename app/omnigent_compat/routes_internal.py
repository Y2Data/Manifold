"""Internal bridge for `app/hooks/permission_hook.py`. Not part of the
Omnigent-compat surface the vendored frontend calls directly — this is the
other end of the hook's blocking HTTP call, only ever reached from a
`claude -p` subprocess running on this same machine (manifold-deck already
only binds to localhost).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.omnigent_compat import ids, permissions, routes_stream
from app.store import get_project

router = APIRouter()

# Self-governed ceiling on how long we'll hold the hook's HTTP call open —
# comfortably under the hook's own `timeout: 3600` in the generated
# settings.json (see backends.py), so a decision (even an implicit deny)
# always comes from here rather than from Claude Code's own undocumented
# command-hook timeout behavior.
_MAX_WAIT_S = 3300.0


class PreToolUseBody(BaseModel):
    project_id: int
    tool_name: str
    tool_input: dict
    cwd: str
    permission_mode: str


@router.post("/internal/hooks/pretooluse")
async def pretooluse(body: PreToolUseBody):
    if get_project(body.project_id) is None:
        raise HTTPException(404, "project not found")

    if permissions.is_auto_allowed(body.project_id, body.tool_name):
        return {"behavior": "allow"}

    elicitation_id, future = permissions.create_pending(
        body.project_id, body.tool_name, body.tool_input, body.cwd, body.permission_mode
    )
    session_id = ids.session_id(body.project_id)
    elicitation = permissions.list_pending_for_project(body.project_id)
    entry = next((e for e in elicitation if e["elicitation_id"] == elicitation_id), None)
    # Real event name confirmed via raw SSE capture against the live Omnigent
    # server (`event: response.elicitation_request`, same nested/snake_case
    # `data` shape as GET .../sessions/{id}'s `pending_elicitations` entries)
    # — NOT the flat camelCase object a downstream JS reducer builds from it.
    # There's no real `elicitation_resolved` push either: the real server
    # doesn't emit one on resolve, it just continues with normal
    # response.output_item.done/session.status events once the underlying
    # tool call proceeds — matches _route_and_publish's own behavior here.
    routes_stream.publish(session_id, "response.elicitation_request", entry or {})

    try:
        decision = await asyncio.wait_for(future, timeout=_MAX_WAIT_S)
    except asyncio.TimeoutError:
        permissions.resolve_pending(elicitation_id, "decline")
        decision = {"behavior": "deny"}
    return decision
