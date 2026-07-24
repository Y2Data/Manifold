"""Phase 5: session resources (environments/filesystem) — thin wrappers
around app/project_files.py's existing walk_tree/read_file_text, already
used by app/routers/projects.py for the dashboard's own Files panel.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.omnigent_compat import ids
from app.store import get_project

router = APIRouter()


class WriteFileBody(BaseModel):
    content: str
    encoding: str = "utf-8"


@router.get("/v1/sessions/{session_id}/resources/environments/default")
async def get_default_environment(session_id: str):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    cwd = project["cwd"]
    # Shape verified against the real server (curl): id/object/type/name +
    # a loosely-typed metadata dict. "caller_process" is the real server's
    # value for "this is the process's own working directory, not a
    # container/VM" — the closest honest description of manifold-deck's
    # model, where the project cwd IS the environment.
    return {
        "id": "default",
        "object": "session.resource",
        "type": "environment",
        "session_id": session_id,
        "name": "Primary environment",
        "metadata": {
            "environment_type": "caller_process",
            "role": "primary",
            "root": cwd,
            "home": cwd,
        },
    }


@router.get("/v1/sessions/{session_id}/resources/environments/default/changes")
async def get_environment_changes(session_id: str):
    # No git-diff/change-tracking concept in manifold-deck today — an
    # honest empty list (nothing changed) rather than fabricated data.
    return {"object": "list", "data": [], "first_id": None, "last_id": None, "has_more": False}


@router.get("/v1/sessions/{session_id}/resources/environments/default/filesystem")
async def get_environment_filesystem(session_id: str, limit: int = 20, order: str = "desc"):
    """Real shape + behavior verified against the live server (curl): flat
    `{id, object, name, path, type, bytes, modified_at}` entries, and —
    despite no `path` query param existing on the real endpoint either —
    just the environment root's *immediate* children, not a recursive
    tree (confirmed: dotfiles directly under $HOME appeared as top-level
    entries, not nested). Deeper browsing apparently isn't done through
    this endpoint at all in single-environment mode."""
    from app.project_files import SKIP_DIRS

    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    root = Path(project["cwd"])
    entries = []
    if root.is_dir():
        try:
            children = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except PermissionError:
            children = []
        for p in children:
            if p.name in SKIP_DIRS:
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            entries.append(
                {
                    "id": p.name,
                    "object": "session.environment.filesystem.entry",
                    "name": p.name,
                    "path": p.name,
                    "type": "directory" if p.is_dir() else "file",
                    "bytes": None if p.is_dir() else stat.st_size,
                    "modified_at": int(stat.st_mtime),
                }
            )
    if order == "desc":
        entries.reverse()
    data = entries[:limit]
    return {
        "object": "list",
        "data": data,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
        "has_more": len(entries) > limit,
    }


@router.get("/v1/sessions/{session_id}/resources/environments/default/filesystem/{relative_path:path}")
async def get_environment_file_content(session_id: str, relative_path: str):
    from app.project_files import read_file_text

    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    root = Path(project["cwd"])
    text = read_file_text(root, relative_path)
    if text is None:
        raise HTTPException(404, "file not found or not a readable text file")
    return {"path": relative_path, "content": text}


@router.put("/v1/sessions/{session_id}/resources/environments/default/filesystem/{relative_path:path}")
async def put_environment_file_content(session_id: str, relative_path: str, body: WriteFileBody):
    """The rich file viewer autosaves through this route (full-content
    replace, not a diff) — was 405ing since only GET existed. Real writes
    to a real project file, so it reuses read_file_text's exact
    path-escape guard (write_file_text in app/project_files.py) and only
    overwrites files that already exist — no arbitrary new-file creation
    via this route."""
    from app.project_files import write_file_text

    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    root = Path(project["cwd"])
    if not write_file_text(root, relative_path, body.content):
        raise HTTPException(404, "file not found or path escapes the project root")
    return {"path": relative_path, "bytes": len(body.content.encode(body.encoding or "utf-8"))}
