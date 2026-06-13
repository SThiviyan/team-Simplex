"""Persistent on-disk cache for searches — survives process restarts.

Two uses, both keyed by the human search terms so re-running the same query is
instant (great for testing / repeated lookups, and it spares the slow sources —
the Handelsregister scrape, the rate-limited GLEIF API):

- namespace "provider:<name>:<limit>" -> the provider's SearchResult list for a
  company name (used by search_cache). Safe: registry content is stable for
  minutes/hours, and the LLM still runs, so code changes still take effect.
- namespace "result" -> a whole ExtractionResult for a (name, jurisdiction).
  Opt-in (settings.pipeline_result_cache); replays the entire pipeline answer
  with no API/LLM calls at all.

SQLite with a rollback journal + self-heal, same robustness as the event log
(safe on a Docker bind mount). Path overridable via SEARCH_CACHE_PATH.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "search_cache.db"
_db_path = Path(os.environ.get("SEARCH_CACHE_PATH") or _DEFAULT_PATH)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    ns  TEXT NOT NULL,
    k   TEXT NOT NULL,
    v   TEXT NOT NULL,
    ts  REAL NOT NULL,
    PRIMARY KEY (ns, k)
);
"""


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.executescript(_SCHEMA)
    return conn


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        try:
            _conn = _open(_db_path)
        except sqlite3.DatabaseError as exc:
            logger.warning("search cache unusable (%s); recreating", exc)
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(_db_path) + suffix).unlink(missing_ok=True)
            _conn = _open(_db_path)
    return _conn


def get(namespace: str, key: str, max_age: float) -> object | None:
    """Cached value for (namespace, key) if present and younger than max_age."""
    with _lock:
        try:
            row = _connection().execute(
                "SELECT v, ts FROM cache WHERE ns = ? AND k = ?", (namespace, key)
            ).fetchone()
        except sqlite3.DatabaseError:
            return None
    if not row:
        return None
    value_json, ts = row
    if time.time() - ts > max_age:
        return None
    try:
        return json.loads(value_json)
    except (json.JSONDecodeError, ValueError):
        return None


def set(namespace: str, key: str, value: object) -> None:
    payload = json.dumps(value, default=str)
    with _lock:
        try:
            conn = _connection()
            conn.execute(
                "INSERT OR REPLACE INTO cache (ns, k, v, ts) VALUES (?, ?, ?, ?)",
                (namespace, key, payload, time.time()),
            )
            conn.commit()
        except sqlite3.DatabaseError:
            logger.exception("search cache write failed; dropping entry")


def clear(namespace: str | None = None) -> int:
    """Clear the whole cache, or just one namespace. Returns rows removed."""
    with _lock:
        conn = _connection()
        cur = (
            conn.execute("DELETE FROM cache WHERE ns = ?", (namespace,))
            if namespace
            else conn.execute("DELETE FROM cache")
        )
        conn.commit()
        return cur.rowcount
