#!/usr/bin/env python3
"""Run a whole evidence folder through PRAMAAN, show it in the dashboard, and score it against known answers.

    python scripts/eval_dataset.py "<folder>"                 # e.g. the official PS-05 dataset folder
    python scripts/eval_dataset.py "<folder>" --out results   # also writes results.csv, metadata.csv, summary.md

Each top-level sub-folder becomes one case in the dashboard (so copies and originals in the same folder are
compared with each other). Expected answers come from, in order:
  1. a labels.csv in the folder:  file,expected   (expected = tampered | original | ai | empty | mismatch | ?)
  2. the PS-05 description sheets ("Voice clip details.txt", "Video details sheet.txt", folder names)
Anything without an expected answer is listed as 'unlabelled'.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PRAMAAN_SEED_DEMO", "0")
import warnings  # noqa: E402

warnings.filterwarnings("ignore")

SKIP = {".txt", ".md", ".csv"}


def ps05_labels(root: Path) -> dict[str, str]:
    """Expected answers derived from the organisers' description files and folder names."""
    lab: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        folder = p.parent.name.lower()
        if "mismatched" in rel.lower():
            lab[rel] = "empty" if p.stat().st_size == 0 else "mismatch"
        elif folder == "voice data" and p.suffix.lower() in (".mp3", ".wav"):
            m = re.search(r"(\d+)", p.stem)
            note = (p.parent / "Voice clip details.txt")
            txt = note.read_text(errors="ignore").lower() if note.exists() else ""
            if m and "1-5" in txt and "6 is real" in txt:
                lab[rel] = "ai" if int(m.group(1)) <= 5 else "original"
        elif folder == "video data" and p.suffix.lower() in (".mp4", ".mov", ".avi"):
            m = re.match(r"video-?(\d+)", p.stem.lower())
            sheet = p.parent / "Video details sheet.txt"
            if m and sheet.exists():
                for line in sheet.read_text(errors="ignore").splitlines():
                    mm = re.match(r"\s*(\d+)\.\s*(.*)", line)
                    if mm and mm.group(1) == m.group(1):
                        lab[rel] = "original" if "original" in mm.group(2).lower() else "tampered"
        elif folder == "image data" and " copy" in p.stem.lower():
            lab[rel] = "tampered"
    return lab


def flagged(cat: str) -> bool:
    return "ndications of manipulation" in cat and not cat.startswith("No")


def score(expected: str, cat: str, nature: list[str], codes: set[str]) -> str:
    if expected in ("?", "", None):
        return "unlabelled"
    if expected == "empty":
        return "ok" if "FILE_EMPTY" in codes else "MISS"
    if expected == "mismatch":
        return "ok" if "FILE_EXT_MISMATCH" in codes else "MISS"
    if expected == "ai":
        return "ok" if flagged(cat) and any("AI" in n or "synthetic" in n.lower() for n in nature) else \
            ("partial" if flagged(cat) else "MISS")
    if expected == "tampered":
        return "ok" if flagged(cat) else "MISS"
    if expected == "original":
        return "FALSE ALARM" if flagged(cat) else "ok"
    return "unlabelled"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, help="write results.csv, metadata.csv and summary.md here")
    ap.add_argument("--actor", default="dataset-runner")
    a = ap.parse_args()
    from pramaan import caselog, db, pipeline
    from pramaan.analyzers.metadata.standard import FIELDS
    root = a.folder.resolve()
    labels = ps05_labels(root)
    lf = root / "labels.csv"
    if lf.exists():
        for r in csv.DictReader(lf.open()):
            labels[r["file"]] = r["expected"].strip().lower()
    files = [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() not in SKIP
             and not (p.suffix.lower() == ".docx" and "mismatched" not in str(p).lower())]
    # originals first inside each folder, so copies are compared on their own run
    files.sort(key=lambda p: (str(p.relative_to(root).parts[0]), "original" not in str(p).lower(), p.name))
    cases: dict[str, str] = {}
    rows = []
    t0 = time.time()
    for p in files:
        rel = str(p.relative_to(root))
        top = p.relative_to(root).parts[0] if len(p.relative_to(root).parts) > 1 else root.name
        if top not in cases:
            cid = db.new_id("case")
            db.insert("cases", {"id": cid, "fir_no": f"{root.name[:40]} / {top}", "title": f"Dataset folder: {top}",
                                "created_by": a.actor, "created_at": db.now()})
            caselog.case_created(cid, a.actor)
            cases[top] = cid
        with open(p, "rb") as f:
            ev = pipeline.ingest(cases[top], p.name, f, a.actor, exhibit_no=p.stem[:24],
                                 acquisition_source=f"dataset folder: {rel}")
        job = db.new_id("job")
        db.insert("jobs", {"id": job, "evidence_id": ev["id"], "status": "running", "started_at": db.now()})
        pipeline.run_job(job, ev["id"], a.actor)
        print(f"  examined {rel}", flush=True)
    pipeline._pool.shutdown(wait=True)      # re-examinations triggered by copy/original matches
    meta_rows = [["Folder", "Exhibit"] + FIELDS]
    for top, cid in cases.items():
        for ev in db.q("SELECT * FROM evidence WHERE case_id=? ORDER BY ingested_at", (cid,)):
            r = pipeline.results(ev["id"])
            v = r["verdict"]
            rat = v["rationale"] if isinstance(v["rationale"], dict) else {}
            codes = {f["code"] for f in r["findings"]}
            rel = ev["acquisition_source"].split(": ", 1)[1]
            exp = labels.get(rel, "?")
            rows.append({"file": rel, "type": ev["modality"], "expected": exp, "verdict": v["category"],
                         "what_was_found": "; ".join(rat.get("nature") or []), "score": round(v["total_llr"], 2),
                         "result": score(exp, v["category"], rat.get("nature") or [], codes),
                         "top_findings": " | ".join(f["title"] for f in r["findings"] if f["status"] in ("fail", "warn"))[:300]})
            mt = next((f for f in r["findings"] if f["code"] == "META_TABLE"), None)
            if mt:
                meta_rows.append([top, rel] + [v for _, v in mt["data"]["standard"]])
    w = max(len(r["file"]) for r in rows)
    print(f"\n{'file'.ljust(w)}  {'expected':9s} {'result':11s} verdict / what was found")
    for r in rows:
        print(f"{r['file'].ljust(w)}  {r['expected']:9s} {r['result']:11s} {r['verdict']}"
              + (f"  [{r['what_was_found']}]" if r["what_was_found"] else ""))
    lab = [r for r in rows if r["result"] != "unlabelled"]
    ok = sum(r["result"] == "ok" for r in lab)
    fa = sum(r["result"] == "FALSE ALARM" for r in lab)
    print(f"\n{ok}/{len(lab)} labelled files correct, {fa} false alarm(s), {len(rows) - len(lab)} unlabelled; "
          f"{time.time() - t0:.0f} s. Open the dashboard → Cases to inspect each one.")
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        with open(a.out / "results.csv", "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)
        with open(a.out / "metadata.csv", "w", newline="") as f:
            csv.writer(f).writerows(meta_rows)
        md = ["| File | Expected | Result | Verdict | What was found |", "|---|---|---|---|---|"]
        md += [f"| {r['file']} | {r['expected']} | {r['result']} | {r['verdict']} | {r['what_was_found']} |" for r in rows]
        (a.out / "summary.md").write_text("\n".join(md) + f"\n\n{ok}/{len(lab)} labelled correct, {fa} false alarms.\n")
        print(f"Wrote {a.out}/results.csv, metadata.csv, summary.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
