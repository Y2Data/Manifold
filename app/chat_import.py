"""Imports existing local Claude Code and Codex CLI session transcripts into
manifold's own `turns` table, so past work (done outside manifold, directly
against those CLIs) shows up in the dashboard alongside live-routed turns.

Both sources are read-only on-disk JSONL transcripts; nothing here talks to
either CLI or any network API. Imported rows are marked with a distinct
`backend` value (e.g. "claude-code (imported)") and leave manifold-specific
routing fields (tier, latency_ms, classify_ms, cost_usd, fanout_group) NULL,
since those concepts don't exist in the source transcripts.

Idempotency: `imported_files` records each source file's path + mtime once
it's been processed. Re-running only re-parses files that are new or whose
mtime has changed since the last import.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.store import add_turn, get_imported_file, get_or_create_project, record_imported_file

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


def _iso_to_epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        # Handles the trailing "Z" that Python's fromisoformat rejects pre-3.11 quirks aside.
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _read_jsonl(path: Path):
    """Yields parsed JSON objects, silently skipping blank/malformed lines —
    a single corrupt line (truncated write, partial flush) shouldn't abort
    the whole file."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _text_from_blocks(blocks: list, wanted_types: set[str]) -> str:
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") in wanted_types:
            text = block.get("text")
            if text:
                parts.append(text)
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------


def import_claude_code() -> dict:
    result = {"files_scanned": 0, "files_imported": 0, "turns_added": 0, "projects_touched": []}
    if not CLAUDE_PROJECTS_DIR.is_dir():
        return result

    projects_touched: set[str] = set()
    for session_file in CLAUDE_PROJECTS_DIR.glob("*/*.jsonl"):
        result["files_scanned"] += 1
        try:
            mtime = session_file.stat().st_mtime
        except OSError:
            continue

        path_str = str(session_file)
        already = get_imported_file(path_str)
        if already is not None and already["mtime"] == mtime:
            continue  # unchanged since last import

        cwds_before: set[str] = set()
        added = _import_claude_code_file(session_file, cwds_before)

        record_imported_file(path_str, mtime, added)
        if added:
            result["files_imported"] += 1
            result["turns_added"] += added
            projects_touched |= cwds_before

    result["projects_touched"] = sorted(projects_touched)
    return result


def _import_claude_code_file(path: Path, cwds_seen: set[str]) -> int:
    """Returns the number of turns added for this file; records every cwd
    encountered into `cwds_seen` so the caller can report projects touched."""
    turns_added = 0
    for obj in _read_jsonl(path):
        if not isinstance(obj, dict):
            continue
        obj_type = obj.get("type")
        if obj_type not in ("user", "assistant"):
            continue

        cwd = obj.get("cwd")
        if not cwd:
            continue

        message = obj.get("message") or {}
        role = message.get("role") or obj_type
        content = message.get("content")

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = _text_from_blocks(content, {"text"})
        else:
            text = ""

        if not text:
            continue

        ts = _iso_to_epoch(obj.get("timestamp"))
        if ts is None:
            continue

        project = get_or_create_project(cwd)
        cwds_seen.add(cwd)
        usage = message.get("usage") or {}
        turn = {
            "project_id": project["id"],
            "ts": ts,
            "role": role,
            "backend": "claude-code (imported)" if role == "assistant" else None,
            "model": message.get("model") if role == "assistant" else None,
            "tier": None,
            "content": text,
            "latency_ms": None,
            "classify_ms": None,
            "cost_usd": None,
            "input_tokens": usage.get("input_tokens") if role == "assistant" else None,
            "output_tokens": usage.get("output_tokens") if role == "assistant" else None,
            "fanout_group": None,
        }
        add_turn(turn)
        turns_added += 1

    return turns_added


# --------------------------------------------------------------------------
# Codex
# --------------------------------------------------------------------------


def _import_codex_file(path: Path, cwds_seen: set[str]) -> int:
    turns_added = 0
    current_cwd: str | None = None
    current_model: str | None = None

    for obj in _read_jsonl(path):
        if not isinstance(obj, dict):
            continue
        obj_type = obj.get("type")

        if obj_type == "session_meta":
            payload = obj.get("payload") or {}
            if payload.get("cwd"):
                current_cwd = payload["cwd"]
            continue

        if obj_type == "turn_context":
            payload = obj.get("payload") or {}
            if payload.get("cwd"):
                current_cwd = payload["cwd"]
            if payload.get("model"):
                current_model = payload["model"]
            continue

        if obj_type != "response_item":
            continue  # skip event_msg (duplicates response_item text, see note below) and everything else

        payload = obj.get("payload") or {}
        if payload.get("type") != "message":
            continue
        role = payload.get("role")
        if role not in ("user", "assistant"):
            continue  # skip 'developer'/system-style entries

        content = payload.get("content")
        if not isinstance(content, list):
            continue
        text = _text_from_blocks(content, {"input_text", "output_text"})
        if not text:
            continue

        if not current_cwd:
            continue  # can't attribute to a project yet

        ts = _iso_to_epoch(obj.get("timestamp"))
        if ts is None:
            continue

        project = get_or_create_project(current_cwd)
        cwds_seen.add(current_cwd)
        turn = {
            "project_id": project["id"],
            "ts": ts,
            "role": role,
            "backend": "codex (imported)" if role == "assistant" else None,
            "model": current_model if role == "assistant" else None,
            "tier": None,
            "content": text,
            "latency_ms": None,
            "classify_ms": None,
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
            "fanout_group": None,
        }
        add_turn(turn)
        turns_added += 1

    return turns_added


def import_codex() -> dict:
    result = {"files_scanned": 0, "files_imported": 0, "turns_added": 0, "projects_touched": []}
    if not CODEX_SESSIONS_DIR.is_dir():
        return result

    projects_touched: set[str] = set()
    for session_file in CODEX_SESSIONS_DIR.glob("*/*/*/rollout-*.jsonl"):
        result["files_scanned"] += 1
        try:
            mtime = session_file.stat().st_mtime
        except OSError:
            continue

        path_str = str(session_file)
        already = get_imported_file(path_str)
        if already is not None and already["mtime"] == mtime:
            continue

        cwds_before: set[str] = set()
        added = _import_codex_file(session_file, cwds_before)

        record_imported_file(path_str, mtime, added)
        if added:
            result["files_imported"] += 1
            result["turns_added"] += added
            projects_touched |= cwds_before

    result["projects_touched"] = sorted(projects_touched)
    return result


def import_all() -> dict:
    claude_result = import_claude_code()
    codex_result = import_codex()
    return {
        "claude": claude_result,
        "codex": codex_result,
        "files_scanned": claude_result["files_scanned"] + codex_result["files_scanned"],
        "files_imported": claude_result["files_imported"] + codex_result["files_imported"],
        "turns_added": claude_result["turns_added"] + codex_result["turns_added"],
        "projects_touched": sorted(set(claude_result["projects_touched"]) | set(codex_result["projects_touched"])),
    }
