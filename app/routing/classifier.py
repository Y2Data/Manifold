"""Three-tier classification: a cheap HTTP model (preferred, when a
"vectorengine" connection is configured), a claude-haiku CLI call
(fallback), and a fast local heuristic (last resort).

Measured live: asking haiku for a *structured* (--json-schema) decision costs
an internal validation-retry loop — 3 turns, 6-17s. Asking for two bare words
in plain text is a single turn, still 6-12s (inherent CLI/model latency, not
something to hide) but meaningfully faster. If the model call errors or times
out, fall back to the free instant heuristic rather than blocking the request.

The claude-haiku path ties every single auto-routed message to a real
`claude -p` subprocess call just to classify it — even when the answer ends
up going to Codex or Kimi. Prefer a cheap HTTP model instead when one's
configured (see _classify_via_http): same latency ballpark, no Claude quota
spent on classification, and its prompt can name any configured backend
(claude/codex/kimi) as a first-class pick instead of only ever picking
between claude and codex.
"""

from __future__ import annotations

import re
import time
from enum import Enum

from app.routing.backends import BackendError, run_raw_claude
from app.routing.http_backend import run_http_connection
from app.store import get_default_connection


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
# Cheap HTTP model used for classification specifically — deliberately not
# the connection's own default_model (that's whatever the user wants Kimi
# itself to answer with, e.g. kimi-k3), classification just needs *a* fast
# model on the same already-configured endpoint/key.
_HTTP_CLASSIFIER_MODEL = "gpt-5.6-luna"
_ROUTING_PROMPT = (
    "Reply with ONLY two comma-separated words, nothing else: <tier>,<backend>.\n"
    "tier: SIMPLE (quick factual/lookup), MEDIUM (normal coding/writing task), "
    "or COMPLEX (multi-step design, architecture, debugging a hard bug, or a "
    "build/implementation task worth getting a second opinion on).\n"
    "backend: claude, codex, kimi, or both — pick 'both' only for COMPLEX "
    "build/design requests where a second model's perspective on the plan is "
    "worth the extra cost; pick 'kimi' for requests that mention or clearly "
    "involve an image/attachment, or general quick queries.\n"
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


def _parse_verdict(raw: str) -> tuple[Tier, str]:
    tier_str, backend = (p.strip() for p in raw.strip().split(",", 1))
    return Tier(tier_str.upper()), backend.lower()


async def _classify_via_http(prompt: str) -> tuple[Tier, str, int] | None:
    """Tries the cheap HTTP classifier model, if a "vectorengine" connection
    is configured. Returns None on missing connection, any request failure,
    or an unparseable reply — classify_with_model falls through to the next
    tier in every one of those cases, so this deliberately swallows broadly
    rather than distinguishing failure modes the caller can't act on."""
    connection = get_default_connection("vectorengine")
    if connection is None:
        return None
    t0 = time.monotonic()
    try:
        result = await run_http_connection(
            _ROUTING_PROMPT.format(prompt=prompt),
            {**connection, "default_model": _HTTP_CLASSIFIER_MODEL},
            timeout_s=_CLASSIFY_TIMEOUT_S,
        )
        tier, backend = _parse_verdict(result.text)
    except Exception:
        return None
    return tier, backend, int((time.monotonic() - t0) * 1000)


async def classify_with_model(prompt: str) -> tuple[Tier, str, int, bool]:
    """Returns (tier, backend, classify_ms, used_model). Tries a cheap HTTP
    model first (see _classify_via_http), then claude-haiku, then falls back
    to the free instant heuristic (backend fixed to 'claude') on any error
    or timeout from either model call."""
    http_result = await _classify_via_http(prompt)
    if http_result is not None:
        tier, backend, classify_ms = http_result
        return tier, backend, classify_ms, True

    t0 = time.monotonic()
    try:
        raw = await run_raw_claude(
            _ROUTING_PROMPT.format(prompt=prompt),
            model="haiku",
            timeout_s=_CLASSIFY_TIMEOUT_S,
        )
        classify_ms = int((time.monotonic() - t0) * 1000)
        tier, backend = _parse_verdict(raw)
        return tier, backend, classify_ms, True
    except (BackendError, ValueError, TimeoutError):
        classify_ms = int((time.monotonic() - t0) * 1000)
        return classify_heuristic(prompt), "claude", classify_ms, False
