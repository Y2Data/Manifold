"""Ties classification to backend execution, threaded through a project's
persistent history, with fan-out for COMPLEX/build requests.

Auto mode now decides BOTH the tier and the backend (claude / codex / both)
via a small-model call (classifier.classify_with_model) — not just the tier
within a fixed backend like the first version did. "both" (or any COMPLEX
classification) runs claude-opus and codex-high in parallel and returns both
as separate turns sharing a fanout_group, so the UI can show them side by
side rather than picking one — same idea as Omnigent's Polly/Debby, just
without the sub-agent orchestration machinery.
"""

from __future__ import annotations

import asyncio
import time
import uuid

from app.routing.backends import BackendResult, run_claude, run_codex
from app.routing.classifier import Tier, classify_with_model
from app.store import add_turn, get_project_turns

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
    for t in history[-_HISTORY_TURNS * 2 :]:
        if t["role"] == "user":
            lines.append(f"User: {t['content']}")
        else:
            lines.append(f"Assistant ({t['backend']}/{t['model']}): {t['content']}")
    lines.append("")
    lines.append("New request:")
    lines.append(prompt)
    return "\n".join(lines)


async def _run_one(backend: str, tier: Tier, prompt: str) -> BackendResult:
    if backend == "codex":
        return await run_codex(prompt, _CODEX_TIER_EFFORT[tier])
    return await run_claude(prompt, _CLAUDE_TIER_MODEL[tier])


async def route(
    project_id: int,
    prompt: str,
    *,
    forced_backend: str | None = None,
    forced_tier: Tier | None = None,
) -> list[dict]:
    """Returns a list of decision dicts (len 1 normally, len 2 on fan-out).
    Each also carries the BackendResult under 'result' for the caller."""
    context_prompt = _build_context(project_id, prompt)

    add_turn(
        {
            "project_id": project_id, "ts": time.time(), "role": "user",
            "backend": None, "model": None, "tier": None, "content": prompt,
            "latency_ms": None, "classify_ms": None, "cost_usd": None,
            "input_tokens": None, "output_tokens": None, "fanout_group": None,
        }
    )

    if forced_tier is not None:
        tier, backend, classify_ms, model_used = forced_tier, (forced_backend or "claude"), 0, False
    else:
        # Context-aware: a terse follow-up ("now handle the retry logic too")
        # reads as SIMPLE in isolation but is a continuation of something
        # complex — the classifier needs the same history the answering
        # model(s) get, not just the raw new message.
        tier, backend, classify_ms, model_used = await classify_with_model(context_prompt)
        if forced_backend is not None:
            backend = forced_backend

    fan_out = backend == "both" or (tier == Tier.COMPLEX and forced_backend is None and forced_tier is None)
    backends_to_run = ["claude", "codex"] if fan_out else [backend if backend != "both" else "claude"]
    fanout_group = uuid.uuid4().hex[:12] if len(backends_to_run) > 1 else None

    results = await asyncio.gather(
        *(_run_one(b, tier, context_prompt) for b in backends_to_run),
        return_exceptions=True,
    )

    decisions = []
    for b, result in zip(backends_to_run, results):
        if isinstance(result, Exception):
            content = f"[{b} error: {result}]"
            result = BackendResult(
                text=content, model="error", backend=b, latency_ms=0,
                cost_usd=None, input_tokens=None, output_tokens=None, raw={},
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
