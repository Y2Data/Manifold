"""In-memory registry for Claude tool-permission requests raised by
`app/hooks/permission_hook.py` (a PreToolUse hook attached to every `claude
-p` call in `app/routing/backends.py::run_claude`), surfaced to the vendored
Omnigent UI as `pending_elicitations` / `elicitation_request` /
`elicitation_resolved` — shapes captured live against the real Omnigent
server's own pending "weather Tokyo today" approval (Playwright network
capture + `GET /v1/sessions/{id}`), not guessed.

Deliberately in-process/in-memory: manifold-deck is a single-process local
app, and a pending permission request only means anything for the one
subprocess call currently blocked on it — nothing here needs to survive a
server restart.
"""

from __future__ import annotations

import asyncio
import time
import uuid

# Read-only/no-side-effect tools that never pause for a human. Everything
# else (Bash, Write, Edit, WebFetch, WebSearch, any MCP tool, ...) goes
# through the approval flow. Plain, editable list rather than an attempt to
# replicate Claude Code's own internal permission-mode allow-list.
_AUTO_ALLOW_TOOLS = {"Read", "Glob", "Grep", "TodoWrite", "NotebookRead", "BashOutput"}


class _Pending:
    __slots__ = ("elicitation_id", "project_id", "tool_name", "tool_input", "cwd", "permission_mode", "created_at", "future")

    def __init__(self, elicitation_id: str, project_id: int, tool_name: str, tool_input: dict, cwd: str, permission_mode: str):
        self.elicitation_id = elicitation_id
        self.project_id = project_id
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.cwd = cwd
        self.permission_mode = permission_mode
        self.created_at = time.time()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()


_pending: dict[str, _Pending] = {}
_remembered: dict[int, set[str]] = {}  # project_id -> tool names remembered "allow" for this run


def is_auto_allowed(project_id: int, tool_name: str) -> bool:
    return tool_name in _AUTO_ALLOW_TOOLS or tool_name in _remembered.get(project_id, ())


def _content_preview(tool_name: str, tool_input: dict) -> str:
    import json

    return f"{tool_name}({json.dumps(tool_input)})"


def create_pending(project_id: int, tool_name: str, tool_input: dict, cwd: str, permission_mode: str) -> tuple[str, asyncio.Future]:
    elicitation_id = f"elicit_{uuid.uuid4().hex}"
    pending = _Pending(elicitation_id, project_id, tool_name, tool_input, cwd, permission_mode)
    _pending[elicitation_id] = pending
    return elicitation_id, pending.future


def resolve_pending(elicitation_id: str, action: str, content: dict | None = None) -> bool:
    pending = _pending.pop(elicitation_id, None)
    if pending is None or pending.future.done():
        return False
    if action == "accept":
        if content and content.get("remember"):
            _remembered.setdefault(pending.project_id, set()).add(pending.tool_name)
        pending.future.set_result({"behavior": "allow"})
    else:
        pending.future.set_result({"behavior": "deny"})
    return True


def _to_elicitation_dict(pending: _Pending) -> dict:
    return {
        "sequence_number": None,
        "type": "response.elicitation_request",
        "elicitation_id": pending.elicitation_id,
        "method": "elicitation/create",
        "params": {
            "mode": "form",
            "message": f"Claude wants to call **{pending.tool_name}**",
            "requestedSchema": None,
            "url": None,
            "phase": "pre_tool_use",
            "policy_name": "manifold_permission",
            "content_preview": _content_preview(pending.tool_name, pending.tool_input),
            "target_session_id": None,
            "tool_name": pending.tool_name,
            "cwd": pending.cwd,
            "permission_mode": pending.permission_mode,
            "remember_scope": {"tool": pending.tool_name},
        },
    }


def list_pending_for_project(project_id: int) -> list[dict]:
    return [_to_elicitation_dict(p) for p in _pending.values() if p.project_id == project_id]


def find_project_id_for_elicitation(elicitation_id: str) -> int | None:
    pending = _pending.get(elicitation_id)
    return pending.project_id if pending else None
