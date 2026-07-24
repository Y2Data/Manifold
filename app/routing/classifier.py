"""Two classifiers: a fast local heuristic (fallback), and a small-model
routing decision (the default) that also picks a backend, not just a tier.

Measured live: asking haiku for a *structured* (--json-schema) decision costs
an internal validation-retry loop — 3 turns, 6-17s. Asking for two bare words
in plain text is a single turn, still 6-12s (inherent CLI/model latency, not
something to hide) but meaningfully faster. If the model call errors or times
out, fall back to the free instant heuristic rather than blocking the request.
"""

from __future__ import annotations

import re
import time
from enum import Enum

from app.routing.backends import BackendError, run_raw_claude


class Tier(str, Enum):
    SIMPLE = "SIMPLE"
    MEDIUM = "MEDIUM"
    COMPLEX = "COMPLEX"


_COMPLEX_MARKERS = re.compile(
    r"\b(refactor|architect(ure)?|design a|implement|migrat(e|ion)|debug|"
    r"root cause|race condition|concurren(t|cy)|optimi[sz]e|security|"
    r"vulnerab|distributed|scal(e|ing)|trade[- ]?off|build)\b",
    re.IGNORECASE,
)
_CODE_BLOCK = re.compile(r"```")
_SIMPLE_MARKERS = re.compile(
    r"^\s*(what is|who is|when (is|was)|define|translate|convert|"
    r"how do i spell|what does .* mean)\b",
    re.IGNORECASE,
)

_CLASSIFY_TIMEOUT_S = 15.0
_ROUTING_PROMPT = (
    "Reply with ONLY two comma-separated words, nothing else: <tier>,<backend>.\n"
    "tier: SIMPLE (quick factual/lookup), MEDIUM (normal coding/writing task), "
    "or COMPLEX (multi-step design, architecture, debugging a hard bug, or a "
    "build/implementation task worth getting a second opinion on).\n"
    "backend: claude, codex, or both — pick 'both' only for COMPLEX build/design "
    "requests where a second model's perspective on the plan is worth the extra cost.\n"
    "Request: {prompt}"
)


def classify_heuristic(prompt: str) -> Tier:
    text = prompt.strip()
    word_count = len(text.split())
    code_blocks = len(_CODE_BLOCK.findall(text))
    has_complex_markers = bool(_COMPLEX_MARKERS.search(text))
    has_simple_markers = bool(_SIMPLE_MARKERS.match(text))

    if has_complex_markers or code_blocks >= 2 or word_count > 400:
        return Tier.COMPLEX
    if has_simple_markers and word_count < 40 and code_blocks == 0:
        return Tier.SIMPLE
    if word_count < 15 and code_blocks == 0:
        return Tier.SIMPLE
    return Tier.MEDIUM


async def classify_with_model(prompt: str) -> tuple[Tier, str, int, bool]:
    """Returns (tier, backend, classify_ms, used_model). Falls back to the
    heuristic (backend fixed to 'claude') on any error or timeout."""
    t0 = time.monotonic()
    try:
        raw = await run_raw_claude(
            _ROUTING_PROMPT.format(prompt=prompt),
            model="haiku",
            timeout_s=_CLASSIFY_TIMEOUT_S,
        )
        classify_ms = int((time.monotonic() - t0) * 1000)
        tier_str, backend = (p.strip() for p in raw.strip().split(",", 1))
        return Tier(tier_str.upper()), backend.lower(), classify_ms, True
    except (BackendError, ValueError, TimeoutError):
        classify_ms = int((time.monotonic() - t0) * 1000)
        return classify_heuristic(prompt), "claude", classify_ms, False
