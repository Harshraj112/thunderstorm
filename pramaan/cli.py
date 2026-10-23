"""Command line: batch-analyse files or folders (e.g. a DVR export) and produce signed reports.

    python -m pramaan.cli analyze demo_samples/ --fir "FIR 123/2026" --ps "Connaught Place" --report
    python -m pramaan.cli verify-ledger
    python -m pramaan.cli register-device "Seized phone S911B" ref_photos/*.jpg
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import caselog, db, pipeline
from .custody import ledger

SKIP = {".json", ".md", ".txt", ".csv", ".pdf"}


def _files(paths: list[str]) -> list[Path]:
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() not in SKIP
                          and not f.name.startswith(("_", ".")))
        elif p.is_file():
            out.append(p)
    return out


def cmd_analyze(a) -> int:
    cid = db.new_id("case")
    db.insert("cases", {"id": cid, "fir_no": a.fir, "police_station": a.ps, "io_name": a.io, "title": a.title,
                        "created_by": a.actor, "created_at": db.now()})
    ledger.append(None, a.actor, "CASE_CREATED", {"case_id": cid, "fir_no": a.fir})
    caselog.case_created(cid, a.actor)
    rows = []
    for k, f in enumerate(_files(a.paths), 1):
        with open(f, "rb") as fh:
            ev = pipeline.ingest(cid, f.name, fh, a.actor, exhibit_no=f"EX-{k:03d}",
                                 acquisition_source=a.source, device_details=None)
        job = db.new_id("job")
        db.insert("jobs", {"id": job, "evidence_id": ev["id"], "status": "running", "progress": 0, "started_at": db.now()})
        v = pipeline.run_job(job, ev["id"], a.actor)
        row = {"file": f.name, "modality": ev["modality"], "verdict": v["category"], "llr": v["total_llr"],
               "confidence": v["confidence"], "evidence_id": ev["id"]}
        if a.report:
            from .report import pdf
            r = pdf.build(ev["id"], examiner=a.actor)
            row["report"] = r["pdf_path"]
        rows.append(row)
        print(f"{f.name:34s} {ev['modality']:6s} {v['category']:40s} LLR {v['total_llr']:+6.2f}  {v['confidence']}",
              flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=2))
    return 0


def cmd_verify(a) -> int:
    r = ledger.verify_chain()
    print(json.dumps(r, indent=2))
    return 0 if r["ok"] else 2


def cmd_register(a) -> int:
    from .analyzers.image.prnu import register_device
    print(json.dumps(register_device(a.name, [Path(p) for p in a.images]), indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pramaan")
    sub = p.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("analyze")
    an.add_argument("paths", nargs="+")
    an.add_argument("--fir", default="DEMO-FIR-001/2026")
    an.add_argument("--ps", default="Demo Police Station")
    an.add_argument("--io", default="Investigating Officer")
    an.add_argument("--title", default="Batch analysis")
    an.add_argument("--actor", default="cli-examiner")
    an.add_argument("--source", default="Copy received on USB from IO")
    an.add_argument("--report", action="store_true")
    an.add_argument("--json")
    an.set_defaults(fn=cmd_analyze)
    vl = sub.add_parser("verify-ledger")
    vl.set_defaults(fn=cmd_verify)
    rg = sub.add_parser("register-device")
    rg.add_argument("name")
    rg.add_argument("images", nargs="+")
    rg.set_defaults(fn=cmd_register)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
