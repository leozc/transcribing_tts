"""SQLite-backed task store (queue + state) for the transcription service.

WAL mode lets the API process (reads/writes) and the GPU worker (claims + updates)
share one DB safely. No Redis — single-machine barebone. Artifacts live under
``<data>/tasks/<id>/`` (input + results/).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
DATA = Path(os.environ.get("TTS_SERVE_DATA", _ROOT / "data"))
DB = DATA / "tasks.db"

STATUSES = ("queued", "running", "done", "failed", "cancelled")


def _conn() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def init() -> None:
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT,
                source TEXT, source_type TEXT, options_json TEXT, error TEXT,
                created_at REAL, updated_at REAL)"""
        )


def task_dir(tid: str) -> Path:
    return DATA / "tasks" / tid


def results_dir(tid: str) -> Path:
    return task_dir(tid) / "results"


def create(source: str, source_type: str, options: dict) -> str:
    tid = uuid.uuid4().hex
    now = time.time()
    task_dir(tid).mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute(
            "INSERT INTO tasks(id,status,stage,source,source_type,options_json,error,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (tid, "queued", None, source, source_type, json.dumps(options), None, now, now),
        )
    return tid


def get(tid: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["options"] = json.loads(d.pop("options_json") or "{}")
    return d


def update(tid: str, **fields) -> None:
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), tid))


def claim_next_queued() -> dict | None:
    """Atomically claim the oldest queued task (FIFO) -> running, but ONLY if no
    task is already running. Enforces global concurrency = 1 even if more than one
    worker polls. Returns the claimed task or None."""
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        running = c.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]
        if running:
            c.execute("COMMIT")
            return None
        r = c.execute(
            "SELECT * FROM tasks WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not r:
            c.execute("COMMIT")
            return None
        c.execute(
            "UPDATE tasks SET status='running', stage='downloading', updated_at=? WHERE id=?",
            (time.time(), r["id"]),
        )
        c.execute("COMMIT")
    d = dict(r)
    d["options"] = json.loads(d.pop("options_json") or "{}")
    d["status"] = "running"
    return d


def reclaim_stale() -> int:
    """Requeue tasks left 'running' by a crashed/restarted worker. Safe for the
    single-worker model (any 'running' at startup is orphaned). Returns count."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE tasks SET status='queued', stage=NULL, updated_at=? WHERE status='running'",
            (time.time(),),
        )
        return cur.rowcount


def delete(tid: str) -> bool:
    """Remove a task record and its files. Caller guards against deleting a
    running task."""
    if not get(tid):
        return False
    with _conn() as c:
        c.execute("DELETE FROM tasks WHERE id=?", (tid,))
    shutil.rmtree(task_dir(tid), ignore_errors=True)
    return True


def counts() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
    return {r[0]: r[1] for r in rows}


def running_task() -> dict | None:
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM tasks WHERE status='running' ORDER BY updated_at LIMIT 1"
        ).fetchone()
    if not r:
        return None
    d = dict(r)
    d["options"] = json.loads(d.pop("options_json") or "{}")
    return d


def list_tasks(limit: int = 100, status: str | None = None) -> list[dict]:
    q = ("SELECT id,status,stage,source_type,created_at,updated_at FROM tasks"
         + (" WHERE status=?" if status else "")
         + " ORDER BY created_at DESC LIMIT ?")
    args = ((status, limit) if status else (limit,))
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]
