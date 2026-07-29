"""Ties classification to connection execution, threaded through a project's
persistent history, with fan-out for COMPLEX/build requests.

Auto mode decides tier + a coarse provider hint via a small-model call
(classifier.classify_with_model), then dispatches to that provider's
*default* connection. The routing target is itself selectable: each
provider's default is just whichever connection has is_default=1 (toggle it
via POST /api/connections/{id}/set-default — the Connections UI's "Set
Default" button), and COMPLEX-tier fan-out runs across *every* connection
currently marked default, not a hardcoded claude+codex pair — mark a third
provider (Kimi, say) as its own default and it joins the fan-out too. This
is deliberately still not the same as the classifier intelligently picking
among arbitrary providers — it's the user's own explicit configuration
(which connection is "the" default) driving what auto mode reaches for.
"""

from __future__ import annotations

import asyncio
import time
import uuid

from app.project_files import build_file_context
from app.routing.backends import BackendError, BackendResult, run_claude, run_codex, run_generic_cli
from app.routing.classifier import Tier, classify_with_model
from app.routing.http_backend import run_http_connection
from app.store import (
    add_turn,
    get_connection,
    get_default_connection,
    get_project,
    get_project_turns,
    list_default_connections,
    touch_project,
)

_CLAUDE_TIER_MODEL = {
    Tier.SIMPLE: "haiku",
    Tier.MEDIUM: "sonnet",
    Tier.COMPLEX: "opus",
}
_CODEX_TIER_EFFORT = {
    Tier.SIMPLE: "low",
    Tier.MEDIUM: "medium",
    Tier.COMPLEX: "high",
}
_HISTORY_TURNS = 10  # last N turns folded into the prompt as context


def _build_context(project_id: int, prompt: str) -> str:
    history = get_project_turns(project_id, limit=_HISTORY_TURNS * 2)
    if not history:
        return prompt
    lines = ["Previous turns in this project (for context):"]
    for t in history:
        if t["role"] == "user":
            lines.append(f"User: {t['content']}")
        else:
            lines.append(f"Assistant ({t['backend']}/{t['model']}): {t['content']}")
    lines.append("")
    lines.append("New request:")
    lines.append(prompt)
    return "\n".join(lines)


async def _run_connection(
    connection: dict, tier: Tier, prompt: str, project_cwd: str | None, project_id: int
) -> BackendResult:
    if connection["kind"] == "subscription_cli":
        # Real file tools live inside claude/codex themselves — just point
        # the subprocess at the project dir, no text injection needed.
        if connection["cli"] == "codex":
            return await run_codex(prompt, _CODEX_TIER_EFFORT[tier], cwd=project_cwd)
        if connection["cli"] == "claude":
            return await run_claude(prompt, _CLAUDE_TIER_MODEL[tier], cwd=project_cwd, project_id=project_id)
        # Any other CLI: routable purely from its stored connection config
        # (cli_argv_template / cli_output_mode), no hardcoded per-CLI Python.
        return await run_generic_cli(
            connection, prompt, connection.get("default_model") or "", cwd=project_cwd
        )
    # HTTP connections (Kimi, etc.) have no tool-calling loop at all — the
    # only way to give them any file awareness is pasting it into the prompt.
    return await run_http_connection(build_file_context(project_cwd, prompt), connection)


async def route(
    project_id: int,
    prompt: str,
    *,
    forced_connection_id: int | None = None,
    forced_tier: Tier | None = None,
    display_content: str | None = None,
) -> list[dict]:
    """Returns a list of decision dicts (len 1 normally, len 2 on fan-out).
    Each also carries the BackendResult under 'result' for the caller.

    display_content, when given, is what gets persisted/shown as the user's
    turn instead of `prompt` itself — used by the Omnigent-compat "Attach
    files" flow (routes_sessions.py), which folds an uploaded file's
    content into `prompt` so every backend (including tool-less HTTP ones)
    actually sees it, but shouldn't dump that raw text into the visible
    chat history every time the conversation is reloaded."""
    context_prompt = _build_context(project_id, prompt)
    project = get_project(project_id)
    project_cwd = project["cwd"] if project else None
    touch_project(project_id)

    add_turn(
        {
            "project_id": project_id, "ts": time.time(), "role": "user",
            "backend": None, "model": None, "tier": None,
            "content": display_content if display_content is not None else prompt,
            "latency_ms": None, "classify_ms": None, "cost_usd": None,
            "input_tokens": None, "output_tokens": None, "fanout_group": None,
        }
    )

    if forced_connection_id is not None:
        connection = get_connection(forced_connection_id)
        if connection is None:
            raise BackendError(f"connection {forced_connection_id} not found")
        tier = forced_tier or Tier.MEDIUM
        classify_ms, model_used = 0, False
        connections_to_run = [connection]
    elif forced_tier is not None:
        tier, classify_ms, model_used = forced_tier, 0, False
        default = get_default_connection("claude")
        connections_to_run = [default] if default else []
    else:
        # Context-aware: a terse follow-up ("now handle the retry logic too")
        # reads as SIMPLE in isolation but is a continuation of something
        # complex — the classifier needs the same history the answering
        # model(s) get, not just the raw new message.
        tier, backend_hint, classify_ms, model_used = await classify_with_model(context_prompt)
        if tier == Tier.COMPLEX:
            connections_to_run = list_default_connections()
        else:
            preferred = get_default_connection(backend_hint) or get_default_connection("claude")
            connections_to_run = [preferred] if preferred else []

    if not connections_to_run:
        raise BackendError("no connection configured to handle this request")

    fanout_group = uuid.uuid4().hex[:12] if len(connections_to_run) > 1 else None

    results = await asyncio.gather(
        *(_run_connection(c, tier, context_prompt, project_cwd, project_id) for c in connections_to_run),
        return_exceptions=True,
    )

    decisions = []
    for c, result in zip(connections_to_run, results):
        if isinstance(result, Exception):
            result = BackendResult(
                text=f"[{c['name']} error: {result}]", model="error", backend=c["name"],
                latency_ms=0, cost_usd=None, input_tokens=None, output_tokens=None, raw={},
            )
        turn = {
            "project_id": project_id, "ts": time.time(), "role": "assistant",
            "backend": result.backend, "model": result.model,
            "tier": tier.value, "content": result.text,
            "latency_ms": result.latency_ms, "classify_ms": classify_ms,
            "cost_usd": result.cost_usd, "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens, "fanout_group": fanout_group,
        }
        turn_id = add_turn(turn)
        decisions.append({**turn, "id": turn_id, "result": result, "classifier_used_model": model_used})
    return decisions
