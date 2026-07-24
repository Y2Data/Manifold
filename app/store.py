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
    last_used_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    role TEXT NOT NULL,              -- 'user' | 'assistant'
    backend TEXT,                    -- 'claude' | 'codex' — null for user turns
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
"""


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
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
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM turns WHERE project_id = ? ORDER BY ts ASC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


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
