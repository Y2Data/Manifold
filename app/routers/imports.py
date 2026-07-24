from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.chat_import import import_all, import_claude_code, import_codex

router = APIRouter(prefix="/api/import")


class ImportRequest(BaseModel):
    source: str = "all"  # "claude" | "codex" | "all"


@router.post("")
async def api_import(body: ImportRequest):
    if body.source == "claude":
        return import_claude_code()
    if body.source == "codex":
        return import_codex()
    if body.source == "all":
        return import_all()
    raise HTTPException(400, f"invalid source: {body.source!r} (expected 'claude', 'codex', or 'all')")
