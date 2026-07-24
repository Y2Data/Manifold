"""Shared file-tree/content helpers for a project's cwd.

Used by both the dashboard's file browser (app/routers/projects.py) and the
router (app/routing/router.py). The router needs this because HTTP-backed
connections (Kimi, any api_key_http endpoint) are plain completion calls with
no tool-calling loop — the only way to give them any file awareness at all is
to paste a tree + matched file contents into the prompt text. CLI backends
(claude/codex) don't need this: they get the project dir as their subprocess
cwd instead and use their own real file tools.
"""

from __future__ import annotations

from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".DS_Store", "dist", "build"}
MAX_DEPTH = 3
MAX_ENTRIES = 300
MAX_FILE_BYTES = 512_000
MAX_CONTEXT_CHARS = 20_000  # cap on file text injected into an HTTP-backend prompt


def walk_tree(root: Path, rel: Path = Path("."), depth: int = 0) -> list[dict]:
    if depth > MAX_DEPTH:
        return []
    try:
        entries = sorted((root / rel).iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except (PermissionError, FileNotFoundError):
        return []
    out = []
    for p in entries[:MAX_ENTRIES]:
        if p.name in SKIP_DIRS:
            continue
        node = {"name": p.name, "path": str(rel / p.name), "is_dir": p.is_dir()}
        if p.is_dir():
            node["children"] = walk_tree(root, rel / p.name, depth + 1)
        out.append(node)
    return out


def read_file_text(root: Path, rel_path: str) -> str | None:
    full = (root / rel_path).resolve()
    if not full.is_relative_to(root.resolve()) or not full.is_file():
        return None
    if full.stat().st_size > MAX_FILE_BYTES:
        return None
    try:
        return full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _render_tree(nodes: list[dict], indent: int = 0) -> list[str]:
    lines = []
    for n in nodes:
        lines.append("  " * indent + "- " + n["name"] + ("/" if n["is_dir"] else ""))
        if n["is_dir"]:
            lines.extend(_render_tree(n.get("children", []), indent + 1))
    return lines


def _iter_flat(nodes: list[dict]):
    for n in nodes:
        yield n
        if n["is_dir"]:
            yield from _iter_flat(n.get("children", []))


def build_file_context(cwd: str | None, prompt: str) -> str:
    """Prepends a file tree (and the content of any file/folder the prompt
    names by basename) ahead of `prompt`. Returns `prompt` unchanged if
    there's no cwd or the project directory has nothing in it."""
    if not cwd:
        return prompt
    root = Path(cwd)
    if not root.is_dir():
        return prompt

    tree = walk_tree(root)
    if not tree:
        return prompt

    prompt_lower = prompt.lower()
    sections = [f"Project files (cwd: {cwd}):", *_render_tree(tree)]

    budget = MAX_CONTEXT_CHARS
    for node in _iter_flat(tree):
        if budget <= 0:
            break
        if node["name"].lower() not in prompt_lower:
            continue
        if node["is_dir"]:
            children = ", ".join(c["name"] for c in node.get("children", [])) or "(empty)"
            sections.append(f"\nContents of {node['path']}/: {children}")
        else:
            text = read_file_text(root, node["path"])
            if text is None:
                continue
            text = text[:budget]
            sections.append(f'\nContents of {node["path"]}:\n"""\n{text}\n"""')
            budget -= len(text)

    return "\n".join(sections) + "\n\n" + prompt
