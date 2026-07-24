from __future__ import annotations

import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "manifold.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cwd TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_used_at REAL NOT NULL,
    starred INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    role TEXT NOT NULL,              -- 'user' | 'assistant'
    backend TEXT,                    -- connection name at the time of the call — null for user turns
    model TEXT,
    tier TEXT,                       -- SIMPLE/MEDIUM/COMPLEX/PINNED — null for user turns
    content TEXT NOT NULL,
    latency_ms INTEGER,
    classify_ms INTEGER,             -- time spent on the routing decision itself
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    fanout_group TEXT,               -- shared across turns answering the same user turn
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

-- A named, independently-configurable way to reach a model. Multiple rows
-- can share the same `provider` (e.g. "Kimi Official" + "Kimi 3rd Party",
-- or "Claude Sub" + "Claude Azure Foundry") — they're just distinct rows,
-- nothing keys on provider being unique.
CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,          -- "claude" | "codex" | "kimi" | ... free label for grouping
    kind TEXT NOT NULL,              -- 'subscription_cli' | 'api_key_http'
    cli TEXT,                        -- subscription_cli: 'claude' | 'codex' | any custom CLI name
    base_url TEXT,                   -- api_key_http: endpoint root
    wire_api TEXT,                   -- api_key_http: 'openai' | 'anthropic'
    api_key_ref TEXT,                -- api_key_http: keyring reference — never the raw key
    default_model TEXT,
    cli_argv_template TEXT,          -- subscription_cli (non-claude/codex): JSON array of argv
                                      -- tokens with {prompt}/{model} placeholders, e.g.
                                      -- '["kimi","-p","{prompt}","-m","{model}","--output-format","text"]'
    cli_output_mode TEXT,            -- subscription_cli (non-claude/codex): only 'text' implemented
                                      -- today (raw stdout capture). Reserved for future
                                      -- 'json_blob'/'ndjson' values.
    is_default INTEGER NOT NULL DEFAULT 0,  -- used by auto-mode fan-out per provider
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

-- Tracks which on-disk Claude Code / Codex session files have already been
-- imported into `turns`, keyed by absolute path, so re-running the importer
-- only processes new or changed files (mtime mismatch => re-import).
CREATE TABLE IF NOT EXISTS imported_files (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    imported_at REAL NOT NULL,
    turns_added INTEGER NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    # Lightweight migration for DBs created before cli_argv_template/cli_output_mode
    # existed — CREATE TABLE IF NOT EXISTS above is a no-op on an already-existing
    # connections table, so new columns need an explicit ALTER TABLE.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(connections)")}
    for col in ("cli_argv_template", "cli_output_mode"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE connections ADD COLUMN {col} TEXT")
    project_cols = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    if "starred" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
    return conn


def get_or_create_project(cwd: str) -> dict:
    now = __import__("time").time()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM projects WHERE cwd = ?", (cwd,)).fetchone()
        if row is None:
            name = Path(cwd).name or cwd
            cur = conn.execute(
                "INSERT INTO projects (cwd, name, created_at, last_used_at) VALUES (?, ?, ?, ?)",
                (cwd, name, now, now),
            )
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        else:
            conn.execute("UPDATE projects SET last_used_at = ? WHERE id = ?", (now, row["id"]))
        return dict(row)


def list_projects() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM projects ORDER BY last_used_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_project(project_id: int) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None


def delete_project(project_id: int) -> None:
    """Deletes a project and its turns. Leaves `imported_files` alone — a
    file already marked imported stays skipped on future import runs, so
    deleting a junk imported project doesn't cause it to reappear."""
    with _connect() as conn:
        conn.execute("DELETE FROM turns WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def touch_project(project_id: int) -> None:
    """Bumps last_used_at to now — called whenever a project is actually
    routed through, so the sidebar's recency ordering reflects real activity
    rather than just creation time."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET last_used_at = ? WHERE id = ?", (__import__("time").time(), project_id)
        )


def set_project_starred(project_id: int, starred: bool) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE projects SET starred = ? WHERE id = ?", (1 if starred else 0, project_id))
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None


def add_turn(turn: dict) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO turns
                (project_id, ts, role, backend, model, tier, content, latency_ms,
                 classify_ms, cost_usd, input_tokens, output_tokens, fanout_group)
            VALUES (:project_id, :ts, :role, :backend, :model, :tier, :content, :latency_ms,
                    :classify_ms, :cost_usd, :input_tokens, :output_tokens, :fanout_group)
            """,
            turn,
        )
        return cur.lastrowid


def get_project_turns(project_id: int, limit: int = 200) -> list[dict]:
    """The most recent `limit` turns, oldest-first. Fetches newest-first
    (`ORDER BY ts DESC LIMIT ?`) then reverses — taking the oldest `limit`
    rows instead silently drops everything recent once a project has more
    turns than `limit` (this project has 3000+; caught live)."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM turns WHERE project_id = ? ORDER BY ts DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def recent_decisions(limit: int = 50) -> list[dict]:
    """Global, cross-project feed of assistant turns (dashboard-level view)."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT turns.*, projects.name AS project_name
            FROM turns JOIN projects ON projects.id = turns.project_id
            WHERE turns.role = 'assistant'
            ORDER BY turns.ts DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def seed_default_connections() -> None:
    """Preserves pre-connections behavior: a 'Claude Sub' and 'Codex Sub'
    connection, each subscription_cli / default for their provider — only
    runs once (no-op if any connection already exists)."""
    with _connect() as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM connections").fetchone()
        if count:
            return
        now = __import__("time").time()
        for name, provider, cli in (("Claude Sub", "claude", "claude"), ("Codex Sub", "codex", "codex")):
            conn.execute(
                """
                INSERT INTO connections (name, provider, kind, cli, default_model, is_default, enabled, created_at)
                VALUES (?, ?, 'subscription_cli', ?, NULL, 1, 1, ?)
                """,
                (name, provider, cli, now),
            )


def list_connections() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM connections ORDER BY provider, id").fetchall()
        return [dict(r) for r in rows]


def get_connection(connection_id: int) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
        return dict(row) if row else None


def get_default_connection(provider: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM connections WHERE provider = ? AND is_default = 1 AND enabled = 1 LIMIT 1",
            (provider,),
        ).fetchone()
        return dict(row) if row else None


def list_default_connections() -> list[dict]:
    """Every provider's chosen default, enabled — this is the auto-mode
    fan-out set. Mark/unmark a connection as default (set_default_connection)
    to control which providers participate, instead of it being hardcoded."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM connections WHERE is_default = 1 AND enabled = 1 ORDER BY provider, id"
        ).fetchall()
        return [dict(r) for r in rows]


def set_default_connection(connection_id: int) -> dict:
    """Makes this connection the default for its provider — un-defaults any
    sibling connection sharing that provider first (one default per provider)."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
        if row is None:
            raise ValueError(f"connection {connection_id} not found")
        conn.execute("UPDATE connections SET is_default = 0 WHERE provider = ?", (row["provider"],))
        conn.execute("UPDATE connections SET is_default = 1 WHERE id = ?", (connection_id,))
        row = conn.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
        return dict(row)


def add_connection(conn_data: dict) -> dict:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        # First connection for a given provider becomes its default automatically.
        (existing,) = conn.execute(
            "SELECT COUNT(*) FROM connections WHERE provider = ?", (conn_data["provider"],)
        ).fetchone()
        conn_data["is_default"] = 1 if existing == 0 else 0
        conn_data["created_at"] = __import__("time").time()
        conn_data.setdefault("cli_argv_template", None)
        conn_data.setdefault("cli_output_mode", None)
        cur = conn.execute(
            """
            INSERT INTO connections
                (name, provider, kind, cli, base_url, wire_api, api_key_ref, default_model,
                 cli_argv_template, cli_output_mode, is_default, enabled, created_at)
            VALUES (:name, :provider, :kind, :cli, :base_url, :wire_api, :api_key_ref, :default_model,
                    :cli_argv_template, :cli_output_mode, :is_default, :enabled, :created_at)
            """,
            conn_data,
        )
        row = conn.execute("SELECT * FROM connections WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def delete_connection(connection_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM connections WHERE id = ?", (connection_id,))


def get_imported_file(path: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM imported_files WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None


def record_imported_file(path: str, mtime: float, turns_added: int) -> None:
    now = __import__("time").time()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO imported_files (path, mtime, imported_at, turns_added)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                mtime = excluded.mtime,
                imported_at = excluded.imported_at,
                turns_added = excluded.turns_added
            """,
            (path, mtime, now, turns_added),
        )


def summary() -> dict:
    with _connect() as conn:
        total, cost = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM turns WHERE role = 'assistant'"
        ).fetchone()
        by_tier = dict(
            conn.execute(
                "SELECT tier, COUNT(*) FROM turns WHERE role = 'assistant' GROUP BY tier"
            ).fetchall()
        )
        return {"total_requests": total, "estimated_cost_usd": cost, "by_tier": by_tier}
