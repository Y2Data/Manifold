"""Phase 6: best-effort stubs for Omnigent endpoints with no manifold-deck
equivalent — schema-valid empty/disabled responses so the SPA doesn't
hard-error when a user pokes at these UI areas, rather than pretending to
implement any of them for real. Each is commented with what it would
take to implement genuinely, so a future decision to actually build one
isn't starting from zero context.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/v1/sessions/{session_id}/comments")
async def get_session_comments(session_id: str, path: str | None = None):
    # Real shape (verified via curl against the live server): a flat list,
    # empty when there are none. manifold-deck has no per-file/per-session
    # commenting concept — genuinely implementing this would need a new
    # `comments` table (session/turn id, path, author, text, timestamps)
    # and CRUD routes; not attempted here.
    return []
