from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.store import get_or_create_project, get_project, get_project_turns, list_projects

router = APIRouter(prefix="/api/projects")

_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".DS_Store", "dist", "build"}
_MAX_DEPTH = 3
_MAX_ENTRIES = 300


class NewProject(BaseModel):
    cwd: str


class NewFolder(BaseModel):
    parent: str
    name: str


@router.get("")
async def api_list_projects():
    return list_projects()


@router.post("")
async def api_create_project(body: NewProject):
    cwd = str(Path(body.cwd).expanduser().resolve())
    if os.path.exists(cwd) and not os.path.isdir(cwd):
        raise HTTPException(400, f"exists and is not a directory: {cwd}")
    os.makedirs(cwd, exist_ok=True)  # create it if it's new — that's the point of typing a new path
    return get_or_create_project(cwd)


@router.get("/browse")
async def api_browse(path: str | None = None):
    """Directories only, for the 'Select' folder-picker modal — this server
    only binds to 127.0.0.1, so listing the local filesystem over HTTP is
    fine here the way it wouldn't be on a shared host."""
    current = Path(path).expanduser().resolve() if path else Path.home()
    if not current.is_dir():
        raise HTTPException(400, f"not a directory: {current}")
    try:
        entries = sorted(
            (p for p in current.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.name.lower(),
        )
    except PermissionError:
        entries = []
    return {
        "path": str(current),
        "parent": str(current.parent) if current.parent != current else None,
        "dirs": [{"name": p.name, "path": str(p)} for p in entries],
    }


@router.post("/browse/mkdir")
async def api_browse_mkdir(body: NewFolder):
    parent = Path(body.parent).expanduser().resolve()
    if not parent.is_dir():
        raise HTTPException(400, f"not a directory: {parent}")
    name = body.name.strip()
    if not name or "/" in name or name in (".", ".."):
        raise HTTPException(400, "invalid folder name")
    new_dir = parent / name
    if new_dir.exists():
        raise HTTPException(400, f"already exists: {new_dir}")
    new_dir.mkdir()
    return {"path": str(new_dir), "name": name}


@router.get("/{project_id}/turns")
async def api_project_turns(project_id: int, limit: int = 200):
    if get_project(project_id) is None:
        raise HTTPException(404, "project not found")
    return get_project_turns(project_id, limit=limit)


def _walk(root: Path, rel: Path, depth: int) -> list[dict]:
    if depth > _MAX_DEPTH:
        return []
    try:
        entries = sorted(
            (root / rel).iterdir(), key=lambda p: (p.is_file(), p.name.lower())
        )
    except (PermissionError, FileNotFoundError):
        return []
    out = []
    for p in entries[:_MAX_ENTRIES]:
        if p.name in _SKIP_DIRS:
            continue
        node = {"name": p.name, "path": str(rel / p.name), "is_dir": p.is_dir()}
        if p.is_dir():
            node["children"] = _walk(root, rel / p.name, depth + 1)
        out.append(node)
    return out


@router.get("/{project_id}/files")
async def api_project_files(project_id: int):
    project = get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    root = Path(project["cwd"])
    return {"cwd": str(root), "tree": _walk(root, Path("."), 0)}


_MAX_FILE_BYTES = 512_000
_MARKDOWN_EXTS = {".md", ".markdown"}


@router.get("/{project_id}/file")
async def api_project_file(project_id: int, path: str):
    project = get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    root = Path(project["cwd"]).resolve()
    full = (root / path).resolve()
    if not full.is_relative_to(root):
        raise HTTPException(400, "path escapes project root")
    if not full.is_file():
        raise HTTPException(404, "not a file")
    size = full.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise HTTPException(413, f"file too large to preview ({size} bytes)")
    try:
        content = full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(415, "binary file, cannot display as text")
    return {"path": path, "content": content, "is_markdown": full.suffix.lower() in _MARKDOWN_EXTS}
