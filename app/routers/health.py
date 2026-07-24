import shutil

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "ui": "up",
        "claude_cli": "found" if shutil.which("claude") else "missing",
        "codex_cli": "found" if shutil.which("codex") else "missing",
    }
