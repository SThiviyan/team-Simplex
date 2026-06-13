"""SQLite log of every agent action — the data source for the live UI feed.

One row per event: what the agent is doing (which MCP endpoint, which tool,
what came back) and why it decided what it decided (reasoning, grounding,
calibrated confidence). The UI polls GET /api/pipeline/runs/{run_id}/events
with a `after=<seq>` cursor to render a live "agent thinking" view.

SQLite with a rollback journal (journal_mode=DELETE), stdlib only; writes are
sub-ms and offloaded to a thread from async code. The DB file is gitignored and
disposable.

WAL is deliberately NOT used: in local dev the file can live on a Docker bind
mount written by both the container (uvicorn) and host CLI runs, and WAL's
shared-memory locking corrupts across the macOS<->Linux boundary ("database
disk image is malformed"). DELETE journaling tolerates that; and if the file is
already corrupt, `_connection()` self-heals by recreating it. The path is
overridable via EVENT_DB_PATH (point it off the bind mount in compose).
"""

import asyncio
import contextvars
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# The browser tab / client session that owns the run currently being processed.
# Set once per run (see set_session, called from run_pipeline) and read by
# log_event_sync, so every event row is stamped with its session WITHOUT having
# to thread the id through dozens of log_event call sites. asyncio.to_thread and
# child tasks copy the current context, so the value set at run_pipeline entry
# is visible to all nested (even threaded) event writes for that run.
_session_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "event_session_id", default=""
)


def set_session(session_id: str | None) -> None:
    """Bind the current async context to a client session id (or clear it)."""
    _session_var.set(session_id or "")

_ENV_DB_PATH = os.environ.get("EVENT_DB_PATH")
DEFAULT_DB_PATH = (
    Path(_ENV_DB_PATH)
    if _ENV_DB_PATH
    else Path(__file__).resolve().parents[2] / "data" / "agent_events.db"
)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_db_path = DEFAULT_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    query_id   TEXT,
    ts         REAL NOT NULL,
    event_type TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    session_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, run_id);
"""


def configure(path: Path) -> None:
    """Point the log at a different DB file (tests use a tmp path)."""
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _db_path = path


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    # Rollback journal, not WAL — survives bind-mount / cross-process access.
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.executescript(_SCHEMA)
    # Migrate older DBs that predate the session_id column (CREATE TABLE IF NOT
    # EXISTS won't add a column to an existing table).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "session_id" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, run_id)")
        conn.commit()
    return conn


def _quarantine_corrupt_db() -> None:
    """Move a corrupt DB (and its journals) aside so a fresh one can be created.
    The event log is disposable telemetry — losing it is preferable to 500-ing
    the whole pipeline run."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        f = Path(str(_db_path) + suffix)
        if f.exists():
            try:
                f.rename(Path(str(f) + ".corrupt"))
            except OSError:
                try:
                    f.unlink()
                except OSError:
                    pass


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        try:
            _conn = _open(_db_path)
        except sqlite3.DatabaseError as exc:
            # "database disk image is malformed" / "file is not a database" —
            # recreate from scratch rather than crash the request.
            logger.warning("event DB unusable (%s); recreating %s", exc, _db_path)
            _quarantine_corrupt_db()
            _conn = _open(_db_path)
    return _conn


def _reset_after_corruption() -> sqlite3.Connection:
    """Drop the cached handle, quarantine the bad file, reopen fresh."""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except sqlite3.Error:
            pass
        _conn = None
    _quarantine_corrupt_db()
    _conn = _open(_db_path)
    return _conn


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def log_event_sync(run_id: str, event_type: str, query_id: str | None = None, **payload) -> None:
    row = (
        run_id, query_id, time.time(), event_type,
        json.dumps(payload, default=str), _session_var.get(),
    )
    sql = (
        "INSERT INTO events (run_id, query_id, ts, event_type, payload, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )
    with _lock:
        try:
            conn = _connection()
            conn.execute(sql, row)
            conn.commit()
        except sqlite3.DatabaseError as exc:
            # The file went corrupt under us (e.g. a concurrent writer on a bind
            # mount). Telemetry must never take down the run: heal and retry once,
            # then give up silently.
            logger.warning("event write failed (%s); resetting event DB", exc)
            try:
                conn = _reset_after_corruption()
                conn.execute(sql, row)
                conn.commit()
            except sqlite3.DatabaseError:
                logger.exception("event write failed after reset; dropping event")


async def log_event(run_id: str, event_type: str, query_id: str | None = None, **payload) -> None:
    await asyncio.to_thread(log_event_sync, run_id, event_type, query_id, **payload)


def _query(sql: str, params: tuple) -> list:
    """Run a read query under the lock, healing once if the DB is corrupt so a
    polling UI never gets a 500 from stale telemetry."""
    with _lock:
        try:
            return _connection().execute(sql, params).fetchall()
        except sqlite3.DatabaseError as exc:
            logger.warning("event read failed (%s); resetting event DB", exc)
            try:
                return _reset_after_corruption().execute(sql, params).fetchall()
            except sqlite3.DatabaseError:
                logger.exception("event read failed after reset; returning empty")
                return []


def list_runs(limit: int = 20, session_id: str | None = None) -> list[dict]:
    """Recent runs, newest first. When `session_id` is given, only that client's
    runs are returned — this is what keeps two browser tabs isolated (each tab
    polls with its own session, so neither sees the other's runs)."""
    where = ""
    params: tuple = (limit,)
    if session_id:
        where = "WHERE session_id = ?"
        params = (session_id, limit)
    rows = _query(
        f"""
        SELECT run_id,
               MIN(ts)  AS started_at,
               MAX(ts)  AS last_event_at,
               COUNT(*) AS event_count,
               MAX(CASE WHEN event_type = 'run_completed' THEN 1 ELSE 0 END) AS completed
        FROM events {where} GROUP BY run_id ORDER BY MIN(ts) DESC LIMIT ?
        """,
        params,
    )
    return [
        {
            "run_id": r[0],
            "started_at": r[1],
            "last_event_at": r[2],
            "event_count": r[3],
            "status": "completed" if r[4] else "running",
        }
        for r in rows
    ]


def list_events(run_id: str, after: int = 0, limit: int = 1000) -> list[dict]:
    rows = _query(
        "SELECT seq, query_id, ts, event_type, payload FROM events "
        "WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
        (run_id, after, limit),
    )
    return [
        {
            "seq": r[0],
            "query_id": r[1],
            "ts": r[2],
            "event_type": r[3],
            "payload": json.loads(r[4]),
        }
        for r in rows
    ]
