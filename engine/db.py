"""Shared persistence layer for the desktop and web editions.

One processing engine (``engine/``) serves both the standalone desktop app and
the multi-user web deployment; storage follows the same pattern:

- Standalone desktop: SQLite database file under RUNTIME_DIR — zero external
  services, works offline.
- Web / multi-user server: PostgreSQL when the ``DATABASE_URL`` environment
  variable is set (e.g. a Render-managed Postgres).

Everything is written dialect-neutral (JSON text columns, one parameter style
helper), so the application code is identical for both backends. Jobs and
their logs/outputs are persisted so history survives restarts and is shared
across users of the server deployment.
"""

import json
import os
import threading
import time

BACKEND_NAME = None


def _env_url():
    return os.environ.get("DATABASE_URL", "").strip()


def backend_name():
    """'postgres' when DATABASE_URL is set, otherwise 'sqlite'."""
    global BACKEND_NAME
    if BACKEND_NAME is None:
        url = _env_url()
        BACKEND_NAME = "postgres" if url else "sqlite"
    return BACKEND_NAME


def _sqlite_path():
    base = os.environ.get("RUNTIME_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime"
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "app.db")


_sqlite_conn = None
_sqlite_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id         TEXT PRIMARY KEY,
    payload        TEXT NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'queued',
    current_status TEXT NOT NULL DEFAULT 'Queued',
    completed      INTEGER NOT NULL DEFAULT 0,
    total          INTEGER NOT NULL DEFAULT 0,
    success_count  INTEGER NOT NULL DEFAULT 0,
    fail_count     INTEGER NOT NULL DEFAULT 0,
    logs           TEXT NOT NULL DEFAULT '[]',
    errors         TEXT NOT NULL DEFAULT '[]',
    outputs        TEXT NOT NULL DEFAULT '[]',
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (created_at DESC);
"""


def _q():
    """Parameter placeholder for the active backend."""
    return "%s" if backend_name() == "postgres" else "?"


def _sqlite():
    global _sqlite_conn
    if _sqlite_conn is None:
        import sqlite3

        _sqlite_conn = sqlite3.connect(_sqlite_path(), check_same_thread=False)
    return _sqlite_conn


def _pg_conn():
    import psycopg

    return psycopg.connect(_env_url(), connect_timeout=15)


def _jobj(job):
    """Convert a job dict into its DB row."""
    request = job.get("request") or {}
    return {
        "job_id": job.get("id") or job.get("job_id") or "",
        "payload": json.dumps(request, default=str),
        "status": job.get("status", "queued"),
        "current_status": job.get("current_status", "Queued"),
        "completed": int(job.get("completed", 0) or 0),
        "total": int(job.get("total", 0) or 0),
        "success_count": int(job.get("success_count", 0) or 0),
        "fail_count": int(job.get("fail_count", 0) or 0),
        "logs": json.dumps(job.get("logs", []), default=str),
        "errors": json.dumps(job.get("errors", []), default=str),
        "outputs": json.dumps(job.get("outputs", []), default=str),
        "created_at": int(job.get("created_at", time.time())),
        "updated_at": int(time.time()),
    }


_SCHEMA_STATEMENTS = [s.strip() for s in _SCHEMA.split(";") if s.strip()]


def init_db():
    """Create the jobs table if it does not exist (idempotent)."""
    if backend_name() == "postgres":
        # psycopg executes one statement per cursor.execute() call.
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                for stmt in _SCHEMA_STATEMENTS:
                    cur.execute(stmt)
        return
    with _sqlite_lock:
        conn = _sqlite()
        with conn:
            conn.executescript(_SCHEMA)


def save_job(job):
    """Insert or replace the persisted copy of a job."""
    r = _jobj(job)
    cols = list(r.keys())
    placeholders = ", ".join(_q() for _ in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "job_id")
    sql = (
        f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT (job_id) DO UPDATE SET {updates}"
    )
    params = tuple(r[c] for c in cols)
    if backend_name() == "postgres":
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
        return
    with _sqlite_lock:
        conn = _sqlite()
        with conn:
            conn.execute(sql, params)


def load_job(job_id):
    """Return the persisted job dict, or None when it does not exist."""
    sql = f"SELECT job_id, payload, status, current_status, completed, total, success_count, fail_count, logs, errors, outputs, created_at, updated_at FROM jobs WHERE job_id = {_q()}"
    if backend_name() == "postgres":
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (job_id,))
                row = cur.fetchone()
        return _row_to_job(row)
    with _sqlite_lock:
        conn = _sqlite()
        cur = conn.execute(sql, (job_id,))
        row = cur.fetchone()
    return _row_to_job(row)


def _row_to_job(row):
    if row is None:
        return None
    keys = ["job_id", "payload", "status", "current_status", "completed", "total",
            "success_count", "fail_count", "logs", "errors", "outputs", "created_at", "updated_at"]
    row = dict(zip(keys, row))

    def _j(s):
        if isinstance(s, str):
            try:
                return json.loads(s)
            except Exception:
                return s
        return s if s is not None else []

    payload = _j(row["payload"])
    if not isinstance(payload, dict):
        payload = {}

    job = {
        "id": row["job_id"],
        "job_id": row["job_id"],
        "payload": payload,
        "request": payload,
        "status": row["status"],
        "current_status": row["current_status"],
        "completed": row["completed"],
        "total": row["total"],
        "success_count": row["success_count"],
        "fail_count": row["fail_count"],
        "logs": _j(row["logs"]),
        "errors": _j(row["errors"]),
        "outputs": _j(row["outputs"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return job


def load_job_list(limit=50):
    """Recent jobs, newest first, as summary dicts."""
    sql = f"SELECT job_id, payload, status, current_status, completed, total, success_count, fail_count, outputs, created_at, updated_at FROM jobs ORDER BY created_at DESC LIMIT {int(limit)}"
    if backend_name() == "postgres":
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
    else:
        with _sqlite_lock:
            conn = _sqlite()
            rows = conn.execute(sql).fetchall()
    keys = ["job_id", "payload", "status", "current_status", "completed", "total",
            "success_count", "fail_count", "outputs", "created_at", "updated_at"]
    out = []
    for row in rows:
        row = dict(zip(keys, row))
        payload = {}
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else (row["payload"] or {})
        except Exception:
            payload = {}
        out.append({
            "job_id": row["job_id"],
            "status": row["status"],
            "current_status": row["current_status"],
            "completed": row["completed"],
            "total": row["total"],
            "success_count": row["success_count"],
            "fail_count": row["fail_count"],
            "files": payload.get("files", []),
            "range": payload.get("range", ""),
            "output_count": len(row["outputs"]) if isinstance(row["outputs"], list) else 0,
            "created_at": row["created_at"],
        })
    return out


def delete_job(job_id):
    """Remove one persisted job. Returns True when a row was deleted."""
    sql = f"DELETE FROM jobs WHERE job_id = {_q()}"
    if backend_name() == "postgres":
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (job_id,))
                return cur.rowcount > 0
    with _sqlite_lock:
        conn = _sqlite()
        with conn:
            cur = conn.execute(sql, (job_id,))
            return cur.rowcount > 0


def clear_jobs():
    """Delete every persisted job. Returns the number of rows removed."""
    sql = "DELETE FROM jobs"
    if backend_name() == "postgres":
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.rowcount
    with _sqlite_lock:
        conn = _sqlite()
        with conn:
            return conn.execute(sql).rowcount
