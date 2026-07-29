"""Phase 5: session resources (environments/filesystem) — thin wrappers
around app/project_files.py's existing walk_tree/read_file_text, already
used by app/routers/projects.py for the dashboard's own Files panel.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.omnigent_compat import ids
from app.store import (
    add_session_file,
    delete_session_file,
    get_project,
    get_session_file,
)
from app.store import list_session_files as store_list_session_files

router = APIRouter()


class WriteFileBody(BaseModel):
    content: str
    encoding: str = "utf-8"


def _default_environment_object(session_id: str, cwd: str) -> dict:
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


@router.get("/v1/sessions/{session_id}/resources/environments/default")
async def get_default_environment(session_id: str):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    return _default_environment_object(session_id, project["cwd"])


@router.get("/v1/sessions/{session_id}/resources/environments")
async def list_environments(session_id: str):
    # manifold-deck has exactly one environment per session — the project's
    # own cwd — so the list is always this same single entry, matching the
    # real SessionResourcePaginatedList shape (object/data/first_id/last_id/
    # has_more).
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    env = _default_environment_object(session_id, project["cwd"])
    return {"object": "list", "data": [env], "first_id": env["id"], "last_id": env["id"], "has_more": False}


@router.get("/v1/sessions/{session_id}/resources")
async def list_resources(session_id: str):
    # Generic top-level resource listing (per SessionResourcePaginatedList)
    # — every resource kind manifold-deck actually backs (environment,
    # filesystem entries) has its own dedicated route above; nothing else
    # (terminals beyond the empty list already served, uploaded "files" as
    # a distinct concept from the environment filesystem) is real here, so
    # an honest empty list rather than duplicating the environment entry
    # under a second path.
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    return {"object": "list", "data": [], "first_id": None, "last_id": None, "has_more": False}


def _file_resource_object(row: dict) -> dict:
    # Shape verified live against the real server (curl multipart upload):
    # {id, object:"session.resource", type:"file", session_id, name,
    # metadata:{filename, bytes, created_at}}.
    return {
        "id": row["id"],
        "object": "session.resource",
        "type": "file",
        "session_id": ids.session_id(row["project_id"]),
        "name": row["filename"],
        "metadata": {
            "filename": row["filename"],
            "bytes": row["bytes"],
            "created_at": int(row["created_at"]),
        },
    }


def _upload_disk_path(cwd: str, file_id: str, filename: str) -> Path:
    # Shared with router.py's attachment-content injection (project_files.
    # attachment_path) so both sides agree on where an upload physically
    # lives. file_id prefix guarantees no collision even if two uploads
    # share a filename.
    from app.project_files import attachment_path

    path = attachment_path(cwd, file_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/v1/sessions/{session_id}/resources/files")
async def list_session_files(session_id: str):
    # "Files" is a distinct real-server concept from the environment
    # filesystem above — standalone uploaded attachments ("Attach files" in
    # the message composer). Real implementation: uploads land inside the
    # project's own cwd (see app/project_files.UPLOADS_DIRNAME) so Claude's
    # own Read/Glob tools can see them, tracked in store.session_files for
    # the metadata this listing needs.
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    data = [_file_resource_object(r) for r in store_list_session_files(project_id)]
    return {
        "object": "list",
        "data": data,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
        "has_more": False,
    }


@router.post("/v1/sessions/{session_id}/resources/files", status_code=201)
async def upload_session_file(session_id: str, file: UploadFile = File(...)):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    filename = Path(file.filename or "upload").name
    content = await file.read()
    file_id = uuid.uuid4().hex
    _upload_disk_path(project["cwd"], file_id, filename).write_bytes(content)
    row = add_session_file(file_id, project_id, filename, len(content))
    return _file_resource_object(row)


@router.get("/v1/sessions/{session_id}/resources/files/{file_id}")
async def get_session_file_metadata(session_id: str, file_id: str):
    project_id = ids.project_id_from_session(session_id)
    if project_id is None or get_project(project_id) is None:
        raise HTTPException(404, "session not found")
    row = get_session_file(file_id)
    if row is None or row["project_id"] != project_id:
        raise HTTPException(404, "file not found")
    return _file_resource_object(row)


@router.get("/v1/sessions/{session_id}/resources/files/{file_id}/content")
async def get_session_file_content(session_id: str, file_id: str):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    row = get_session_file(file_id)
    if row is None or row["project_id"] != project_id:
        raise HTTPException(404, "file not found")
    path = _upload_disk_path(project["cwd"], file_id, row["filename"])
    if not path.is_file():
        raise HTTPException(404, "file content missing on disk")
    return Response(content=path.read_bytes(), media_type="application/octet-stream")


@router.delete("/v1/sessions/{session_id}/resources/files/{file_id}")
async def delete_session_file_route(session_id: str, file_id: str):
    project_id = ids.project_id_from_session(session_id)
    project = get_project(project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(404, "session not found")
    row = get_session_file(file_id)
    if row is None or row["project_id"] != project_id:
        raise HTTPException(404, "file not found")
    path = _upload_disk_path(project["cwd"], file_id, row["filename"])
    path.unlink(missing_ok=True)
    delete_session_file(file_id)
    # Real shape verified live: {id, object:"session.resource.deleted", deleted:true}.
    return {"id": file_id, "object": "session.resource.deleted", "deleted": True}


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
