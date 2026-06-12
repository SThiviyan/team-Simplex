"""SQLite log of every agent action — the data source for the live UI feed.

One row per event: what the agent is doing (which MCP endpoint, which tool,
what came back) and why it decided what it decided (reasoning, grounding,
calibrated confidence). The UI polls GET /api/pipeline/runs/{run_id}/events
with a `after=<seq>` cursor to render a live "agent thinking" view.

SQLite in WAL mode, stdlib only; writes are sub-ms and offloaded to a thread
from async code. The DB file is gitignored and disposable.
"""

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_events.db"

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
    payload    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
"""


def configure(path: Path) -> None:
    """Point the log at a different DB file (tests use a tmp path)."""
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _db_path = path


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        _conn = conn
    return _conn


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def log_event_sync(run_id: str, event_type: str, query_id: str | None = None, **payload) -> None:
    with _lock:
        conn = _connection()
        conn.execute(
            "INSERT INTO events (run_id, query_id, ts, event_type, payload) VALUES (?, ?, ?, ?, ?)",
            (run_id, query_id, time.time(), event_type, json.dumps(payload, default=str)),
        )
        conn.commit()


async def log_event(run_id: str, event_type: str, query_id: str | None = None, **payload) -> None:
    await asyncio.to_thread(log_event_sync, run_id, event_type, query_id, **payload)


def list_runs(limit: int = 20) -> list[dict]:
    with _lock:
        rows = _connection().execute(
            """
            SELECT run_id,
                   MIN(ts)  AS started_at,
                   MAX(ts)  AS last_event_at,
                   COUNT(*) AS event_count,
                   MAX(CASE WHEN event_type = 'run_completed' THEN 1 ELSE 0 END) AS completed
            FROM events GROUP BY run_id ORDER BY MIN(ts) DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
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
    with _lock:
        rows = _connection().execute(
            "SELECT seq, query_id, ts, event_type, payload FROM events "
            "WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (run_id, after, limit),
        ).fetchall()
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
