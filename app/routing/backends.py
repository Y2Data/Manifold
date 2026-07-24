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
import time
from dataclasses import dataclass


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


async def _run(cmd: list[str], timeout_s: float | None = None, cwd: str | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
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
    prompt: str, model: str, timeout_s: float = 180.0, cwd: str | None = None
) -> BackendResult:
    t0 = time.monotonic()
    out = await _run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "json"],
        timeout_s=timeout_s,
        cwd=cwd,
    )
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
