"""Headless execution against the ALREADY-LOGGED-IN claude/codex CLIs.

Deliberately subprocess, not an SDK: it's the exact same auth path as the
interactive `claude`/`codex` sessions you already have working (subscription
OAuth/keychain — no ANTHROPIC_API_KEY, no OPENAI_API_KEY, ever touched by
this code). Never pass --bare to claude: that flag forces API-key-only auth
and would defeat the entire point.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BackendResult:
    text: str
    model: str
    backend: str
    latency_ms: int
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    raw: dict


class BackendError(RuntimeError):
    pass


# app/hooks/permission_hook.py — a standalone PreToolUse hook script (not
# imported) attached per-invocation via a generated --settings file below,
# never the user's real ~/.claude/settings.json. See run_claude().
_PERMISSION_HOOK_SCRIPT = str(Path(__file__).resolve().parent.parent / "hooks" / "permission_hook.py")
_INTERACTIVE_TIMEOUT_S = 3600.0  # must comfortably exceed the hook's own timeout below
_DEFAULT_TIMEOUT_S = 180.0


def _permission_settings_json() -> str:
    return json.dumps(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{sys.executable} {_PERMISSION_HOOK_SCRIPT}",
                                "timeout": 3600,
                            }
                        ],
                    }
                ]
            }
        }
    )


async def _run(cmd: list[str], timeout_s: float | None = None, cwd: str | None = None, env: dict | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,  # else these can hang waiting on inherited stdin
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"{cmd[0]} timed out after {timeout_s}s")
    if proc.returncode != 0:
        raise BackendError(stderr.decode(errors="replace") or f"exit {proc.returncode}")
    return stdout.decode(errors="replace")


async def run_raw_claude(prompt: str, model: str, timeout_s: float | None = None) -> str:
    """Bare result text, for internal calls (the routing classifier) that
    don't need full BackendResult bookkeeping."""
    out = await _run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "json"],
        timeout_s=timeout_s,
    )
    return json.loads(out).get("result", "")


async def run_claude(
    prompt: str, model: str, timeout_s: float | None = None, cwd: str | None = None, project_id: int | None = None
) -> BackendResult:
    """project_id, when given, attaches a PreToolUse hook (see
    _permission_settings_json) that routes any tool-permission decision
    through manifold-deck's own UI instead of the headless-mode default of
    silently auto-denying restricted tools — see app/hooks/permission_hook.py
    and app/omnigent_compat/routes_internal.py for the other end of this.
    Without it (e.g. the classifier's internal calls), behavior is unchanged
    from before this existed."""
    t0 = time.monotonic()
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json"]
    env = None
    settings_path = None
    if project_id is not None:
        timeout_s = timeout_s or _INTERACTIVE_TIMEOUT_S
        fd, settings_path = tempfile.mkstemp(prefix="manifold-claude-settings-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write(_permission_settings_json())
        cmd += ["--settings", settings_path, "--permission-mode", "default"]
        env = {
            **os.environ,
            "MANIFOLD_PROJECT_ID": str(project_id),
            "MANIFOLD_BRIDGE_PORT": os.environ.get("UI_PORT", "8080"),
        }
    else:
        timeout_s = timeout_s or _DEFAULT_TIMEOUT_S
    try:
        out = await _run(cmd, timeout_s=timeout_s, cwd=cwd, env=env)
    finally:
        if settings_path:
            os.unlink(settings_path)
    latency_ms = int((time.monotonic() - t0) * 1000)
    data = json.loads(out)
    usage = data.get("usage") or {}
    return BackendResult(
        text=data.get("result", ""),
        model=model,
        backend="claude",
        latency_ms=latency_ms,
        cost_usd=data.get("total_cost_usd"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        raw=data,
    )


async def run_generic_cli(
    connection: dict, prompt: str, model: str, cwd: str | None = None, timeout_s: float = 180.0
) -> BackendResult:
    """Runs any headless-capable CLI configured via the Connections UI/API as a
    'subscription_cli' connection with a non-claude/codex `cli` value. The
    connection declares its own argv shape (`cli_argv_template`, a JSON array
    of tokens with {prompt}/{model} placeholders — covers positional args,
    flags, or any mix) so no per-CLI Python code is needed.

    v1 only supports cli_output_mode == 'text': the raw stdout is captured
    and returned as-is, with no usage/cost parsing — that requires knowing
    each CLI's own output schema, which this mode deliberately avoids.
    """
    argv_template = json.loads(connection["cli_argv_template"])
    argv = [token.replace("{prompt}", prompt).replace("{model}", model or "") for token in argv_template]

    t0 = time.monotonic()
    out = await _run(argv, timeout_s=timeout_s, cwd=cwd)
    latency_ms = int((time.monotonic() - t0) * 1000)
    return BackendResult(
        text=out.strip(),
        model=model,
        backend=connection["cli"],
        latency_ms=latency_ms,
        cost_usd=None,
        input_tokens=None,
        output_tokens=None,
        raw={},
    )


async def run_codex(prompt: str, reasoning_effort: str, cwd: str | None = None) -> BackendResult:
    """Runs codex exec at a given reasoning effort (low/medium/high) — the
    model itself is left at whatever's configured in ~/.codex/config.toml;
    effort is the tier dimension here, not the model name, since codex's
    model catalog doesn't map cleanly to fixed named tiers the way Claude's
    haiku/sonnet/opus does.

    Real event schema (verified live, not guessed): `item.completed` events
    carry `item.type == "agent_message"` / `item.text`; the final
    `turn.completed` event carries a `usage` dict with input/output tokens
    (no cost_usd — codex doesn't report one, unlike claude).
    """
    t0 = time.monotonic()
    out = await _run(
        [
            "codex", "exec", prompt,
            "-c", f"model_reasoning_effort={reasoning_effort}",
            "--json", "--skip-git-repo-check",
        ],
        timeout_s=180.0,
        cwd=cwd,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    text = ""
    usage: dict = {}
    last_event: dict = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        last_event = event
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text", text)
        elif event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
        elif event.get("type") == "error":
            raise BackendError(event.get("message", "codex error"))
    return BackendResult(
        text=text,
        model=f"gpt/{reasoning_effort}",
        backend="codex",
        latency_ms=latency_ms,
        cost_usd=None,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        raw=last_event,
    )
