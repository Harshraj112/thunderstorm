"""Tamper-evident chain-of-custody ledger.

entry_hash = SHA256(prev_hash || canonical_json(event)). Any edited, inserted or deleted
row breaks every later hash. Periodic Merkle roots ("anchors") are signed with the lab key
and printed in reports; they can optionally be RFC 3161-timestamped when a network exists.
Why not a blockchain: forensic labs are air-gapped. A signed hash-chain gives the same
tamper-evidence with no network, gas, or consensus dependency.
"""
from __future__ import annotations

import json
from typing import Any

from .. import db
from .hashing import sha256_bytes
from . import signing

GENESIS = "0" * 64


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def _entry_hash(prev_hash: str, seq: int, evidence_id: str | None, actor: str, action: str,
                details: dict, ts: float) -> str:
    body = {"seq": seq, "evidence_id": evidence_id, "actor": actor, "action": action,
            "details": details, "ts": round(ts, 6)}
    return sha256_bytes(prev_hash.encode() + canonical(body))


def append(evidence_id: str | None, actor: str, action: str, details: dict | None = None) -> dict:
    details = details or {}
    ts = db.now()
    with db.tx() as c:
        last = c.execute("SELECT seq, entry_hash FROM custody_events ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = last["entry_hash"] if last else GENESIS
        seq = (last["seq"] + 1) if last else 1
        h = _entry_hash(prev_hash, seq, evidence_id, actor, action, details, ts)
        c.execute("INSERT INTO custody_events (seq, evidence_id, actor, action, details, ts, prev_hash, entry_hash)"
                  " VALUES (?,?,?,?,?,?,?,?)",
                  (seq, evidence_id, actor, action, json.dumps(details, sort_keys=True, default=str), round(ts, 6),
                   prev_hash, h))
    return {"seq": seq, "entry_hash": h, "prev_hash": prev_hash, "ts": ts}


def events(evidence_id: str | None = None) -> list[dict]:
    if evidence_id:
        return db.q("SELECT * FROM custody_events WHERE evidence_id=? ORDER BY seq", (evidence_id,))
    return db.q("SELECT * FROM custody_events ORDER BY seq")


def verify_chain() -> dict:
    rows = db.q("SELECT * FROM custody_events ORDER BY seq")
    prev = GENESIS
    for i, r in enumerate(rows, start=1):
        details = r["details"] if isinstance(r["details"], dict) else json.loads(r["details"] or "{}")
        expected = _entry_hash(prev, r["seq"], r["evidence_id"], r["actor"], r["action"], details, r["ts"])
        if r["seq"] != i:
            return {"ok": False, "broken_at": r["seq"], "reason": f"sequence gap: expected {i}"}
        if r["prev_hash"] != prev or r["entry_hash"] != expected:
            return {"ok": False, "broken_at": r["seq"], "reason": "hash mismatch (row edited or reordered)"}
        prev = r["entry_hash"]
    return {"ok": True, "events": len(rows), "head": prev}


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return GENESIS
    level = [bytes.fromhex(x) for x in leaves]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [bytes.fromhex(sha256_bytes(level[i] + level[i + 1])) for i in range(0, len(level), 2)]
    return level[0].hex()


def anchor() -> dict:
    """Seal all events so far under a signed Merkle root."""
    rows = db.q("SELECT seq, entry_hash FROM custody_events ORDER BY seq")
    root = merkle_root([r["entry_hash"] for r in rows])
    upto = rows[-1]["seq"] if rows else 0
    sig = signing.sign(f"{upto}:{root}".encode())
    db.insert("anchors", {"upto_seq": upto, "merkle_root": root, "signature": sig, "ts": db.now()})
    return {"upto_seq": upto, "merkle_root": root, "signature": sig}
