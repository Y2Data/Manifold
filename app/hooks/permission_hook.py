#!/usr/bin/env python3
"""Claude Code `PreToolUse` hook, attached per-invocation via the `--settings`
temp file `app/routing/backends.py::run_claude` generates — never touches the
user's real `~/.claude/settings.json` or a project's checked-in settings.

Invoked by Claude Code itself as a subprocess (stdin = the hook payload,
stdout = the expected decision JSON, per
https://code.claude.com/docs/en/hooks.md). Runs with the same interpreter as
the manifold-deck server (`sys.executable`, passed in verbatim when the
settings file is generated), so `httpx` (an existing manifold-deck
dependency) is available.

Fails closed (deny) on any error — missing env vars, a bridge that isn't
reachable, a malformed response — since this is a permission gate, not a
best-effort convenience.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

# Comfortably longer than the bridge's own ~55min self-imposed decision
# ceiling (app/omnigent_compat/routes_internal.py::_MAX_WAIT_S) so a real
# decision (even an implicit deny) always comes back before this read
# times out — deliberately not relying on Claude Code's own undocumented
# command-hook timeout behavior for that ceiling.
_HTTP_TIMEOUT_S = 3540.0


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _allow() -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps(_deny("manifold permission hook: could not parse hook input")))
        return

    project_id = os.environ.get("MANIFOLD_PROJECT_ID")
    port = os.environ.get("MANIFOLD_BRIDGE_PORT")
    if not project_id or not port:
        print(json.dumps(_deny("manifold permission hook: missing MANIFOLD_PROJECT_ID/MANIFOLD_BRIDGE_PORT")))
        return

    body = {
        "project_id": int(project_id),
        "tool_name": payload.get("tool_name", ""),
        "tool_input": payload.get("tool_input") or {},
        "cwd": payload.get("cwd", ""),
        "permission_mode": payload.get("permission_mode", "default"),
    }

    try:
        resp = httpx.post(
            f"http://127.0.0.1:{port}/internal/hooks/pretooluse",
            json=body,
            timeout=_HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        decision = resp.json()
    except Exception as exc:  # bridge unreachable, timed out, bad response, etc.
        print(json.dumps(_deny(f"manifold permission hook: bridge call failed ({exc})")))
        return

    if decision.get("behavior") == "allow":
        print(json.dumps(_allow()))
    else:
        print(json.dumps(_deny("Denied by user via manifold-deck")))


if __name__ == "__main__":
    main()
