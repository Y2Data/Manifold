"""In-memory registry for tool-permission requests — originally built for
`app/hooks/permission_hook.py` (a PreToolUse hook attached to every `claude
-p` call in `app/routing/backends.py::run_claude`), now shared with any
other tool-calling backend that needs the same human-in-the-loop approval
UX (see request_decision below — used by Kimi's real function-calling loop
in `app/routing/http_backend.py` too). Surfaced to the vendored Omnigent UI
as `pending_elicitations` / `elicitation_request` — shapes captured live
against the real Omnigent server's own pending "weather Tokyo today"
approval (Playwright network capture + `GET /v1/sessions/{id}`), not
guessed.

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
# else (Bash, Write, Edit, WebFetch, WebSearch, any MCP tool, write_file,
# ...) goes through the approval flow. Plain, editable list rather than an
# attempt to replicate Claude Code's own internal permission-mode
# allow-list — read_file/list_files are Kimi's own tool-calling loop's
# names (see http_backend.py), listed here alongside Claude Code's own
# tool names since both go through this same registry.
_AUTO_ALLOW_TOOLS = {"Read", "Glob", "Grep", "TodoWrite", "NotebookRead", "BashOutput", "read_file", "list_files"}


class _Pending:
    __slots__ = ("elicitation_id", "project_id", "tool_name", "tool_input", "cwd", "permission_mode", "agent_name", "created_at", "future")

    def __init__(
        self,
        elicitation_id: str,
        project_id: int,
        tool_name: str,
        tool_input: dict,
        cwd: str,
        permission_mode: str,
        agent_name: str = "Claude",
    ):
        self.elicitation_id = elicitation_id
        self.project_id = project_id
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.cwd = cwd
        self.permission_mode = permission_mode
        self.agent_name = agent_name
        self.created_at = time.time()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()


_pending: dict[str, _Pending] = {}
_remembered: dict[int, set[str]] = {}  # project_id -> tool names remembered "allow" for this run


def is_auto_allowed(project_id: int, tool_name: str) -> bool:
    return tool_name in _AUTO_ALLOW_TOOLS or tool_name in _remembered.get(project_id, ())


def _content_preview(tool_name: str, tool_input: dict) -> str:
    import json

    return f"{tool_name}({json.dumps(tool_input)})"


def create_pending(
    project_id: int, tool_name: str, tool_input: dict, cwd: str, permission_mode: str, agent_name: str = "Claude"
) -> tuple[str, asyncio.Future]:
    elicitation_id = f"elicit_{uuid.uuid4().hex}"
    pending = _Pending(elicitation_id, project_id, tool_name, tool_input, cwd, permission_mode, agent_name)
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
            "message": f"{pending.agent_name} wants to call **{pending.tool_name}**",
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


# Self-governed ceiling on how long we'll hold a caller open waiting for a
# human — comfortably under the Claude PreToolUse hook's own `timeout:
# 3600` in the generated settings.json (see backends.py), so a decision
# (even an implicit deny) always comes from here rather than relying on
# undocumented outer-timeout behavior.
_MAX_WAIT_S = 3300.0


async def request_decision(
    project_id: int, tool_name: str, tool_input: dict, cwd: str, permission_mode: str = "default", agent_name: str = "Claude"
) -> bool:
    """Creates a pending elicitation, publishes it live over the session's
    SSE stream, and blocks until a human resolves it (or the timeout
    elapses, resolved as a deny) — returns True if approved. Shared by the
    Claude PreToolUse hook bridge (routes_internal.py) and any in-process
    tool-calling loop (e.g. http_backend.py's Kimi function-calling loop)
    that needs the exact same human-in-the-loop approval UX."""
    if is_auto_allowed(project_id, tool_name):
        return True
    from app.omnigent_compat import ids, routes_stream

    elicitation_id, future = create_pending(project_id, tool_name, tool_input, cwd, permission_mode, agent_name)
    session_id = ids.session_id(project_id)
    entry = next((e for e in list_pending_for_project(project_id) if e["elicitation_id"] == elicitation_id), None)
    routes_stream.publish(session_id, "response.elicitation_request", entry or {})
    try:
        decision = await asyncio.wait_for(future, timeout=_MAX_WAIT_S)
    except asyncio.TimeoutError:
        resolve_pending(elicitation_id, "decline")
        decision = {"behavior": "deny"}
    return decision.get("behavior") == "allow"
