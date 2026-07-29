"""Internal bridge for `app/hooks/permission_hook.py`. Not part of the
Omnigent-compat surface the vendored frontend calls directly — this is the
other end of the hook's blocking HTTP call, only ever reached from a
`claude -p` subprocess running on this same machine (manifold-deck already
only binds to localhost).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.omnigent_compat import permissions
from app.store import get_project

router = APIRouter()


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
    # Real event name confirmed via raw SSE capture against the live Omnigent
    # server (`event: response.elicitation_request`, same nested/snake_case
    # `data` shape as GET .../sessions/{id}'s `pending_elicitations` entries)
    # — NOT the flat camelCase object a downstream JS reducer builds from it.
    # There's no real `elicitation_resolved` push either: the real server
    # doesn't emit one on resolve, it just continues with normal
    # response.output_item.done/session.status events once the underlying
    # tool call proceeds — matches _route_and_publish's own behavior here.
    approved = await permissions.request_decision(
        body.project_id, body.tool_name, body.tool_input, body.cwd, body.permission_mode
    )
    return {"behavior": "allow" if approved else "deny"}
