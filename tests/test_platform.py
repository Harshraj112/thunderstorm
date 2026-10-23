"""Regression tests: custody integrity + golden-set verdicts.

    python -m unittest discover -s tests -v
Generates demo samples on first run (≈30 s) and analyses them in an isolated data dir.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="pramaan_test_"))
os.environ["PRAMAAN_DATA"] = str(TMP / "data")
os.environ["PRAMAAN_MODELS"] = str(TMP / "models")   # no optional models: test the core only
sys.path.insert(0, str(ROOT))

from pramaan import db, pipeline  # noqa: E402
from pramaan.custody import ledger, signing  # noqa: E402

SAMPLES = ROOT / "demo_samples"


def setUpModule():
    if not (SAMPLES / "expected.json").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_demo_samples.py"), str(SAMPLES)], check=True)


def _analyse(path: Path) -> tuple[dict, dict]:
    cid = db.new_id("case")
    db.insert("cases", {"id": cid, "fir_no": "TEST", "created_at": db.now()})
    with open(path, "rb") as f:
        ev = pipeline.ingest(cid, path.name, f, "tester")
    job = db.new_id("job")
    db.insert("jobs", {"id": job, "evidence_id": ev["id"], "status": "running", "started_at": db.now()})
    return ev, pipeline.run_job(job, ev["id"], "tester")


class Custody(unittest.TestCase):
    def test_chain_detects_tampering(self):
        for i in range(5):
            ledger.append(None, "t", "TEST", {"i": i})
        self.assertTrue(ledger.verify_chain()["ok"])
        c = db.conn()
        # an attacker with raw DB access drops the protective trigger and edits a row
        c.execute("DROP TRIGGER custody_no_update")
        c.execute("UPDATE custody_events SET actor='mallory' WHERE seq=(SELECT MIN(seq)+1 FROM custody_events)")
        c.commit()
        r = ledger.verify_chain()
        self.assertFalse(r["ok"])
        # restore for other tests
        c.execute("UPDATE custody_events SET actor='t' WHERE actor='mallory'")
        c.executescript(db.SCHEMA)
        c.commit()

    def test_append_only_trigger(self):
        ledger.append(None, "t", "TEST", {})
        with self.assertRaises(Exception):
            db.conn().execute("DELETE FROM custody_events")

    def test_signature(self):
        s = signing.sign(b"hello")
        self.assertTrue(signing.verify(b"hello", s))
        self.assertFalse(signing.verify(b"hellO", s))

    def test_merkle(self):
        self.assertNotEqual(ledger.merkle_root(["00" * 32, "11" * 32]), ledger.merkle_root(["11" * 32, "00" * 32]))


class CaseDeletion(unittest.TestCase):
    def test_delete_keeps_permanent_log(self):
        from pramaan import caselog
        cid = db.new_id("case")
        db.insert("cases", {"id": cid, "fir_no": "DEL/1", "created_by": "tester", "created_at": db.now()})
        caselog.case_created(cid, "tester")
        src = ROOT / "samples" / "real" / "R02_canon_resized_in_gimp.jpg"
        with open(src, "rb") as f:
            ev = pipeline.ingest(cid, "x.jpg", f, "tester")
        out = caselog.delete_case(cid, "tester", "opened by mistake")
        self.assertEqual(out["exhibits"], 1)
        self.assertIsNone(db.q1("SELECT id FROM cases WHERE id=?", (cid,)))
        self.assertIsNone(db.q1("SELECT id FROM evidence WHERE id=?", (ev["id"],)))
        acts = [e["action"] for e in caselog.entries(cid)]
        self.assertEqual(acts, ["CASE_DELETED", "EVIDENCE_ADDED", "CASE_CREATED"])
        snap = caselog.entries(cid)[0]["details"]
        self.assertEqual(snap["exhibits"][0]["sha256"], ev["sha256"])
        self.assertEqual(snap["reason"], "opened by mistake")
        self.assertTrue(caselog.verify()["ok"])
        self.assertTrue(ledger.verify_chain()["ok"])
        with self.assertRaises(Exception):
            db.conn().execute("DELETE FROM case_log")


class Golden(unittest.TestCase):
    """Every tampered sample must be flagged; no pristine sample may be flagged (false positive)."""

    @classmethod
    def setUpClass(cls):
        cls.expected = json.loads((SAMPLES / "expected.json").read_text())

    def _check(self, sub: str):
        for name, exp in self.expected.items():
            p = SAMPLES / sub / name
            if not p.exists():
                continue
            with self.subTest(file=name):
                ev, v = _analyse(p)
                flagged = "ndications of manipulation" in v["category"] and not v["category"].startswith("No")
                self.assertEqual(flagged, exp["tampered"], f"{name}: {v['category']} ({v['total_llr']})")
                self.assertTrue(pipeline.verify_hash(ev["id"])["ok"])

    def test_images(self):
        self._check("images")

    def test_audio(self):
        self._check("audio")

    def test_cctv(self):
        self._check("cctv")

    def test_real_world_samples(self):
        """Real material (public datasets): never flag an original; catch the edits we claim to catch."""
        real = ROOT / "samples" / "real"
        if not (real / "manifest.json").exists():
            self.skipTest("run scripts/fetch_real_samples.py")
        must_flag = {"R02", "R04", "R12", "R13", "R15", "R16", "R24"}
        for m in json.loads((real / "manifest.json").read_text()):
            p = real / m["file"]
            if not p.exists():
                continue
            with self.subTest(file=m["file"]):
                _, v = _analyse(p)
                flagged = "ndications of manipulation" in v["category"] and not v["category"].startswith("No")
                if m["tampered"] is False:
                    self.assertFalse(flagged, f"false positive on {m['file']}: {v['category']}")
                if m["file"][:3] in must_flag:
                    self.assertTrue(flagged, f"missed {m['file']}: {v['category']} ({v['total_llr']})")

    def test_report_signed_and_verifiable(self):
        from pramaan.report import pdf
        ev, _ = _analyse(SAMPLES / "images" / "02_spliced_photoshop.jpg")
        r = pdf.build(ev["id"], "tester")
        self.assertTrue(pdf.verify(r["report_id"])["ok"])
        data = bytearray(Path(r["pdf_path"]).read_bytes())
        data[len(data) // 2] ^= 0xFF
        self.assertFalse(pdf.verify(r["report_id"], bytes(data))["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
