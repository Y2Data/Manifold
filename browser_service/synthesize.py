"""Cited-answer synthesis.

Reuses the existing subscription-authenticated `claude` CLI path
(app.routing.backends.run_raw_claude, already used internally by the
classifier for bare prompt->text calls) instead of introducing a new API
key — consistent with the whole project's ethos. Citation numbers are
enforced by prompt instruction only: a bare `-p` CLI call gives no
structured-output guarantee, so treat [n] markers in the answer as
best-effort, not schema-guaranteed.
"""

from __future__ import annotations

from app.routing.backends import BackendError, run_raw_claude
from browser_service.models import ExtractedPage, Source

_MODEL = "sonnet"
_SYNTHESIS_TIMEOUT_S = 60.0
_PER_SOURCE_CHAR_BUDGET = 3000  # keeps N source extracts from blowing out the prompt

_PROMPT = (
    "Answer the question below using ONLY the numbered sources provided. "
    "Cite every claim inline with its source number in square brackets, e.g. [1] or [1][3]. "
    "If the sources don't answer the question, say so plainly instead of guessing.\n\n"
    "Question: {question}\n\n"
    "Sources:\n{sources_block}\n\n"
    "Write a concise, well-cited answer."
)


def _build_prompt(question: str, pages: list[ExtractedPage]) -> str:
    blocks = [f"[{i}] {p.title} ({p.url})\n{p.text[:_PER_SOURCE_CHAR_BUDGET]}" for i, p in enumerate(pages, start=1)]
    return _PROMPT.format(question=question, sources_block="\n\n".join(blocks))


async def synthesize(question: str, pages: list[ExtractedPage]) -> tuple[str, list[Source]]:
    if not pages:
        return "No usable sources were found for this question.", []
    prompt = _build_prompt(question, pages)
    try:
        answer = await run_raw_claude(prompt, model=_MODEL, timeout_s=_SYNTHESIS_TIMEOUT_S)
    except BackendError as exc:
        raise BackendError(f"synthesis failed: {exc}") from exc
    sources = [Source(n=i, url=p.url, title=p.title) for i, p in enumerate(pages, start=1)]
    return answer, sources
