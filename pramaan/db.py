"""SQLite persistence (zero-dependency, works air-gapped).

The custody_events table is append-only: SQLite triggers reject UPDATE and DELETE,
and every row is additionally hash-chained (see custody/ledger.py), so even edits made
outside the application (e.g. with a raw SQLite editor that drops the triggers) are
detected by /api/ledger/verify.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

_lock = threading.RLock()

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, badge_no TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY, fir_no TEXT, police_station TEXT, io_name TEXT, title TEXT,
    created_by TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, exhibit_no TEXT, original_filename TEXT,
    mime TEXT, modality TEXT, size INTEGER, sha256 TEXT NOT NULL, sha1 TEXT, md5 TEXT,
    storage_path TEXT NOT NULL, acquisition_source TEXT, device_details TEXT, seized_at TEXT,
    ingested_at REAL, ingested_by TEXT, status TEXT DEFAULT 'ingested'
);
CREATE TABLE IF NOT EXISTS custody_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT, actor TEXT NOT NULL,
    action TEXT NOT NULL, details TEXT, ts REAL NOT NULL, prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS custody_no_update BEFORE UPDATE ON custody_events
BEGIN SELECT RAISE(ABORT, 'custody_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS custody_no_delete BEFORE DELETE ON custody_events
BEGIN SELECT RAISE(ABORT, 'custody_events is append-only'); END;
CREATE TABLE IF NOT EXISTS anchors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, upto_seq INTEGER, merkle_root TEXT, signature TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, evidence_id TEXT, status TEXT, progress REAL, stage TEXT, error TEXT,
    started_at REAL, finished_at REAL, versions TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY, job_id TEXT, evidence_id TEXT, code TEXT, module TEXT, family TEXT,
    title TEXT, status TEXT, raw_score REAL, llr REAL, reliability REAL, location TEXT,
    explanation TEXT, innocent TEXT, artifacts TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS verdicts (
    id TEXT PRIMARY KEY, job_id TEXT, evidence_id TEXT, category TEXT, confidence TEXT,
    total_llr REAL, coverage REAL, abstained INTEGER, rationale TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, evidence_id TEXT, job_id TEXT, version INTEGER, pdf_path TEXT,
    pdf_sha256 TEXT, manifest TEXT, signature TEXT, signer TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY, name TEXT, kind TEXT, path TEXT, meta TEXT, created_at REAL
);
"""


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect()
            _conn.executescript(SCHEMA)
        return _conn


def reset_connection() -> None:
    """Used by tests when PRAMAAN_DATA changes."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


def row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("details", "location", "innocent", "artifacts", "data", "rationale", "manifest", "meta", "versions"):
        if k in d and isinstance(d[k], str) and d[k][:1] in "[{":
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d


def q(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        return [row(r) for r in conn().execute(sql, args).fetchall()]  # type: ignore[misc]


def q1(sql: str, args: tuple = ()) -> dict[str, Any] | None:
    with _lock:
        return row(conn().execute(sql, args).fetchone())


def insert(table: str, values: dict[str, Any]) -> None:
    vals = {k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v) for k, v in values.items()}
    cols = ",".join(vals)
    ph = ",".join("?" for _ in vals)
    with tx() as c:
        c.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", tuple(vals.values()))


def update(table: str, key: str, key_val: Any, values: dict[str, Any]) -> None:
    vals = {k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v) for k, v in values.items()}
    sets = ",".join(f"{k}=?" for k in vals)
    with tx() as c:
        c.execute(f"UPDATE {table} SET {sets} WHERE {key}=?", (*vals.values(), key_val))
