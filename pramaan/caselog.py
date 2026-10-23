"""Case log: a permanent, append-only record of every case's life — when it was opened, what it held,
and when (and by whom, and why) it was deleted.

Deleting a case removes its files and analysis results, but never its history: the case_log table
rejects UPDATE/DELETE (SQLite triggers) and every row is hash-chained like the custody ledger, so a
removed or edited log entry is detectable. A CASE_DELETED entry carries a full snapshot of what the
case contained (exhibits, SHA-256 hashes, verdicts, reports) at the moment it was deleted.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import config, db
from .custody import ledger
from .custody.hashing import sha256_bytes

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS case_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, action TEXT NOT NULL, case_id TEXT NOT NULL,
    case_label TEXT, actor TEXT NOT NULL, details TEXT, prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS case_log_no_update BEFORE UPDATE ON case_log
BEGIN SELECT RAISE(ABORT, 'case_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS case_log_no_delete BEFORE DELETE ON case_log
BEGIN SELECT RAISE(ABORT, 'case_log is append-only'); END;
"""

_ready = False


def _ensure() -> None:
    global _ready
    if not _ready:
        with db.tx() as c:
            c.executescript(SCHEMA)
        _ready = True


def _hash(prev: str, seq: int, ts: float, action: str, case_id: str, label: str | None, actor: str, details: dict) -> str:
    body = {"seq": seq, "ts": round(ts, 6), "action": action, "case_id": case_id, "case_label": label,
            "actor": actor, "details": details}
    return sha256_bytes(prev.encode() + ledger.canonical(body))


def record(action: str, case_id: str, actor: str, details: dict | None = None, label: str | None = None) -> dict:
    _ensure()
    details = details or {}
    if label is None:
        c = db.q1("SELECT fir_no, title FROM cases WHERE id=?", (case_id,))
        label = (c or {}).get("fir_no") or (c or {}).get("title")
    ts = round(db.now(), 6)
    with db.tx() as c:
        last = c.execute("SELECT seq, entry_hash FROM case_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last["entry_hash"] if last else GENESIS
        seq = (last["seq"] + 1) if last else 1
        h = _hash(prev, seq, ts, action, case_id, label, actor, details)
        c.execute("INSERT INTO case_log (seq, ts, action, case_id, case_label, actor, details, prev_hash, entry_hash)"
                  " VALUES (?,?,?,?,?,?,?,?,?)",
                  (seq, ts, action, case_id, label, actor, json.dumps(details, sort_keys=True, default=str), prev, h))
    return {"seq": seq, "entry_hash": h}


def entries(case_id: str | None = None, limit: int = 500) -> list[dict]:
    _ensure()
    if case_id:
        return db.q("SELECT * FROM case_log WHERE case_id=? ORDER BY seq DESC LIMIT ?", (case_id, limit))
    return db.q("SELECT * FROM case_log ORDER BY seq DESC LIMIT ?", (limit,))


def verify() -> dict:
    _ensure()
    prev = GENESIS
    rows = db.q("SELECT * FROM case_log ORDER BY seq")
    for i, r in enumerate(rows, 1):
        d = r["details"] if isinstance(r["details"], dict) else json.loads(r["details"] or "{}")
        if r["seq"] != i or r["prev_hash"] != prev or \
                _hash(prev, r["seq"], r["ts"], r["action"], r["case_id"], r["case_label"], r["actor"], d) != r["entry_hash"]:
            return {"ok": False, "entries": len(rows), "broken_at": r["seq"]}
        prev = r["entry_hash"]
    return {"ok": True, "entries": len(rows), "head": prev}


# --------------------------------------------------------------------------- lifecycle hooks
def case_fields(c: dict) -> dict:
    return {k: c.get(k) for k in ("fir_no", "police_station", "io_name", "title", "created_by", "created_at")}


def case_created(case_id: str, actor: str) -> None:
    c = db.q1("SELECT * FROM cases WHERE id=?", (case_id,)) or {}
    record("CASE_CREATED", case_id, actor, {"case": case_fields(c)})


def evidence_added(ev: dict, actor: str) -> None:
    record("EVIDENCE_ADDED", ev["case_id"], actor, {
        "evidence_id": ev["id"], "exhibit_no": ev.get("exhibit_no"), "filename": ev.get("original_filename"),
        "modality": ev.get("modality"), "size": ev.get("size"), "sha256": ev.get("sha256"), "md5": ev.get("md5"),
        "acquisition_source": ev.get("acquisition_source")})


def backfill() -> int:
    """Cases created before the case log existed get a CASE_CREATED entry (marked backfilled) with their contents."""
    _ensure()
    logged = {r["case_id"] for r in db.q("SELECT DISTINCT case_id FROM case_log WHERE action='CASE_CREATED'")}
    n = 0
    for c in db.q("SELECT * FROM cases ORDER BY created_at"):
        if c["id"] in logged:
            continue
        ev = db.q("SELECT * FROM evidence WHERE case_id=? ORDER BY ingested_at", (c["id"],))
        record("CASE_CREATED", c["id"], c.get("created_by") or "system",
               {"case": case_fields(c), "backfilled": True,
                "exhibits_at_backfill": [_exhibit_snapshot(e) for e in ev]})
        n += 1
    return n


# --------------------------------------------------------------------------- deletion
class CaseBusy(Exception):
    pass


def _exhibit_snapshot(e: dict) -> dict:
    job = db.q1("SELECT * FROM jobs WHERE evidence_id=? ORDER BY started_at DESC LIMIT 1", (e["id"],))
    v = db.q1("SELECT category, confidence, total_llr FROM verdicts WHERE job_id=?", (job["id"],)) if job else None
    reports = db.q("SELECT id, version, pdf_sha256, created_at FROM reports WHERE evidence_id=?", (e["id"],))
    return {"evidence_id": e["id"], "exhibit_no": e.get("exhibit_no"), "filename": e.get("original_filename"),
            "modality": e.get("modality"), "size": e.get("size"), "sha256": e.get("sha256"), "md5": e.get("md5"),
            "ingested_at": e.get("ingested_at"), "ingested_by": e.get("ingested_by"),
            "verdict": v, "reports": reports}


def _unlink(p: str | os.PathLike | None) -> bool:
    if not p:
        return False
    p = Path(p)
    try:
        if p.exists():
            p.chmod(0o644)               # originals are read-only; Windows refuses to delete read-only files
            p.unlink()
            return True
    except OSError:
        pass
    return False


def delete_case(case_id: str, actor: str, reason: str) -> dict:
    """Remove a case, its exhibits, analysis results, artefacts and reports. History is kept:
    a CASE_DELETED snapshot goes to the case log and an EVIDENCE_DELETED event to each exhibit's custody chain."""
    c = db.q1("SELECT * FROM cases WHERE id=?", (case_id,))
    if not c:
        raise KeyError(case_id)
    ev = db.q("SELECT * FROM evidence WHERE case_id=? ORDER BY ingested_at", (case_id,))
    ids = [e["id"] for e in ev]
    if ids:
        ph = ",".join("?" * len(ids))
        busy = db.q(f"SELECT id FROM jobs WHERE evidence_id IN ({ph}) AND status IN ('queued','running')", tuple(ids))
        if busy:
            raise CaseBusy(f"{len(busy)} exhibit(s) are still being analysed; delete the case when they finish")
    snapshot = {"case": case_fields(c), "reason": reason, "exhibits": [_exhibit_snapshot(e) for e in ev]}

    # 1. history first, so a crash mid-delete still leaves a record of the intent
    for e in ev:
        ledger.append(e["id"], actor, "EVIDENCE_DELETED", {"case_id": case_id, "sha256": e["sha256"], "reason": reason})
    entry = record("CASE_DELETED", case_id, actor, snapshot, label=c.get("fir_no") or c.get("title"))

    # 2. files: originals (unless another exhibit shares the same content-addressed file), artefacts, reports
    files = 0
    jobs = db.q(f"SELECT id FROM jobs WHERE evidence_id IN ({','.join('?' * len(ids))})", tuple(ids)) if ids else []
    reports = db.q(f"SELECT id, pdf_path FROM reports WHERE evidence_id IN ({','.join('?' * len(ids))})", tuple(ids)) if ids else []
    for e in ev:
        shared = db.q1("SELECT COUNT(*) n FROM evidence WHERE storage_path=? AND case_id<>?", (e["storage_path"], case_id))
        if not (shared or {}).get("n"):
            files += _unlink(e["storage_path"])
    for j in jobs:
        for d in (config.ARTIFACT_DIR / j["id"], config.WORK_DIR / j["id"]):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                files += 1
    for r in reports:
        if r.get("pdf_path"):
            files += _unlink(r["pdf_path"])
        for ext in (".manifest.json", ".sig"):
            _unlink(config.REPORT_DIR / f"{r['id']}{ext}")

    # 3. rows
    with db.tx() as cx:
        if ids:
            ph = ",".join("?" * len(ids))
            for t in ("findings", "verdicts", "reports", "jobs"):
                cx.execute(f"DELETE FROM {t} WHERE evidence_id IN ({ph})", tuple(ids))
            cx.execute(f"DELETE FROM evidence WHERE id IN ({ph})", tuple(ids))
        cx.execute("DELETE FROM cases WHERE id=?", (case_id,))
    return {"deleted": case_id, "exhibits": len(ev), "files_removed": files, "log_seq": entry["seq"]}
