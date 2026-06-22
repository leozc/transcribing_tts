"""SQLite-backed task store (queue + state) for the transcription service.

WAL mode lets the API process (reads/writes) and the GPU worker (claims + updates)
share one DB safely. No Redis — single-machine barebone. Artifacts live under
``<data>/tasks/<id>/`` (input + results/).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
DATA = Path(os.environ.get("TTS_SERVE_DATA", _ROOT / "data"))
DB = DATA / "tasks.db"

STATUSES = ("queued", "running", "done", "failed", "cancelled")


@contextmanager
def _conn():
    """Connection scoped to a transaction: commit on success, rollback on error,
    and ALWAYS close (no leaked connections in the long-lived worker)."""
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _canonical_state_db() -> Path:
    """Where the systemd service keeps state (StateDirectory = %S/tts_serve)."""
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "tts_serve" / "tasks.db"


def init() -> None:
    # Split-brain guard: if no explicit data root is set but a canonical state DB
    # exists (the systemd deployment), refuse to silently fall back to <repo>/data —
    # that stale duplicate would diverge from the real queue. Be explicit instead.
    if "TTS_SERVE_DATA" not in os.environ:
        canon = _canonical_state_db()
        if canon.exists() and canon.resolve() != DB.resolve():
            raise RuntimeError(
                f"TTS_SERVE_DATA is unset but a state DB exists at {canon}. Refusing to "
                f"use the fallback {DB} (would split-brain the queue). Set TTS_SERVE_DATA "
                f"explicitly (e.g. export TTS_SERVE_DATA={canon.parent}).")
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT,
                client_id TEXT, token TEXT, source TEXT, source_type TEXT,
                options_json TEXT, error TEXT, created_at REAL, updated_at REAL)"""
        )
        # migrate older DBs that predate a column (legacy rows get NULL token ->
        # _owned() fails closed, so they are inaccessible rather than open)
        cols = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        for col in ("client_id", "token"):
            if col not in cols:
                c.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_client ON tasks(client_id)")
        # registered clients: an authenticated identity so "list my tasks" is safe.
        # We store only the SHA-256 of the high-entropy key, never the key itself.
        c.execute(
            """CREATE TABLE IF NOT EXISTS clients(
                client_id TEXT PRIMARY KEY, key_hash TEXT NOT NULL, created_at REAL)"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_keyhash ON clients(key_hash)")


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_client(client_id: str) -> str | None:
    """Register a client_id and return a freshly minted secret client_key (shown
    once). Returns None if the client_id is already taken. Only the key's hash is
    stored, so the raw key cannot be recovered from the DB."""
    key = secrets.token_urlsafe(24)
    with _conn() as c:
        try:
            c.execute("INSERT INTO clients(client_id,key_hash,created_at) VALUES(?,?,?)",
                      (client_id, _hash_key(key), time.time()))
        except sqlite3.IntegrityError:
            return None  # client_id already registered
    return key


def client_for_key(key: str | None) -> str | None:
    """Resolve an X-Client-Key to the client_id it authenticates, or None. The key
    is high-entropy (token_urlsafe), so a direct hash lookup is sufficient."""
    if not key:
        return None
    with _conn() as c:
        r = c.execute("SELECT client_id FROM clients WHERE key_hash=?",
                      (_hash_key(key),)).fetchone()
    return r["client_id"] if r else None


def task_dir(tid: str) -> Path:
    return DATA / "tasks" / tid


def results_dir(tid: str) -> Path:
    return task_dir(tid) / "results"


def create(source: str, source_type: str, options: dict,
           client_id: str | None = None, token: str | None = None) -> str:
    tid = uuid.uuid4().hex
    now = time.time()
    task_dir(tid).mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute(
            "INSERT INTO tasks(id,status,stage,client_id,token,source,source_type,options_json,error,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "queued", None, client_id, token, source, source_type,
             json.dumps(options), None, now, now),
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


def delete(tid: str, allow_running: bool = False) -> bool:
    """Atomically delete a task (+ its files). By default REFUSES a running task
    in the same statement, closing the check-then-delete race with the worker
    (which could claim it between an API ownership check and this call). Returns
    whether a row was actually removed."""
    with _conn() as c:
        if allow_running:
            cur = c.execute("DELETE FROM tasks WHERE id=?", (tid,))
        else:
            cur = c.execute("DELETE FROM tasks WHERE id=? AND status != 'running'", (tid,))
        deleted = cur.rowcount > 0
    if deleted:
        shutil.rmtree(task_dir(tid), ignore_errors=True)
    return deleted


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


def list_tasks(limit: int = 100, status: str | None = None,
               client_id: str | None = None) -> list[dict]:
    conds, args = [], []
    if status:
        conds.append("status=?")
        args.append(status)
    if client_id:
        conds.append("client_id=?")
        args.append(client_id)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    args.append(limit)
    with _conn() as c:
        rows = c.execute(
            "SELECT id,status,stage,client_id,source_type,created_at,updated_at FROM tasks"
            + where + " ORDER BY created_at DESC LIMIT ?", args
        ).fetchall()
    return [dict(r) for r in rows]


def purge_old(max_age_days: float, statuses: tuple = ("done", "failed", "cancelled")) -> int:
    """Lifecycle maintenance: delete terminal tasks older than max_age_days (+ their
    files), then checkpoint the WAL. Never touches queued/running. 0/None disables."""
    if not max_age_days or max_age_days <= 0:
        return 0
    cutoff = time.time() - max_age_days * 86400
    ph = ",".join("?" * len(statuses))
    with _conn() as c:
        ids = [r[0] for r in c.execute(
            f"SELECT id FROM tasks WHERE status IN ({ph}) AND updated_at < ?",
            (*statuses, cutoff)).fetchall()]
        for tid in ids:
            c.execute("DELETE FROM tasks WHERE id=?", (tid,))
    # WAL truncate AFTER the delete transaction commits; best-effort (skip if locked)
    try:
        with _conn() as c2:
            c2.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass
    for tid in ids:
        shutil.rmtree(task_dir(tid), ignore_errors=True)
    return len(ids)
