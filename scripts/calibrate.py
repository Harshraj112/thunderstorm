#!/usr/bin/env python3
"""Fit per-detector calibration (raw score -> LLR) from a labelled validation run.

1. Analyse a labelled folder:   python -m pramaan.cli analyze val_set/
   with val_set/labels.json = {"file.jpg": true/false (tampered), ...}
2. Fit:                          python scripts/calibrate.py val_set/labels.json
Writes models/calibration.json (Platt scaling in log-odds space, minus the prior log-odds) and
prints Expected Calibration Error and false-positive rate on pristine files per detector – the
numbers forensic judges ask for.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pramaan import db  # noqa: E402

DETECTORS = {"IMG_GHOST_LOCAL": "jpeg_ghost_local", "IMG_NOISE": "noise_inconsistency",
             "IMG_COPYMOVE": "copymove_inliers", "IMG_ELA": "ela_ratio", "IMG_TRUFOR": "trufor_score",
             "IMG_AIGI": "aigi_clip"}


def ece(p, y, bins=10):
    p, y = np.asarray(p), np.asarray(y)
    e = 0.0
    for lo in np.linspace(0, 1, bins, endpoint=False):
        m = (p >= lo) & (p < lo + 1 / bins)
        if m.any():
            e += m.mean() * abs(p[m].mean() - y[m].mean())
    return e


def main(labels_path):
    labels = json.loads(Path(labels_path).read_text())
    data = defaultdict(lambda: ([], []))
    verdicts = []
    for ev in db.q("SELECT id, original_filename FROM evidence"):
        if ev["original_filename"] not in labels:
            continue
        y = int(bool(labels[ev["original_filename"]]))
        for f in db.q("SELECT code, raw_score FROM findings WHERE evidence_id=?", (ev["id"],)):
            if f["code"] in DETECTORS:
                data[f["code"]][0].append(f["raw_score"])
                data[f["code"]][1].append(y)
        v = db.q1("SELECT category FROM verdicts WHERE evidence_id=? ORDER BY created_at DESC", (ev["id"],))
        if v:
            verdicts.append((y, v["category"]))
    from sklearn.linear_model import LogisticRegression
    cal = {}
    for code, (x, y) in data.items():
        x, y = np.array(x).reshape(-1, 1), np.array(y)
        if len(set(y)) < 2 or len(y) < 10:
            print(f"{code}: not enough labelled data ({len(y)})")
            continue
        lr = LogisticRegression().fit(x, y)
        prior = np.log(y.mean() / (1 - y.mean()))
        a, b = float(lr.coef_[0][0]), float(lr.intercept_[0] - prior)
        p = lr.predict_proba(x)[:, 1]
        cal[DETECTORS[code]] = {"a": a, "b": b, "cap": 4.0, "n": int(len(y))}
        print(f"{code:16s} a={a:+.3f} b={b:+.3f}  ECE={ece(p, y):.3f}  n={len(y)}")
    if verdicts:
        neg = [c for y, c in verdicts if y == 0]
        pos = [c for y, c in verdicts if y == 1]
        fp = sum("ndications of manipulation" in c and not c.startswith("No") for c in neg)
        tp = sum("ndications of manipulation" in c and not c.startswith("No") for c in pos)
        inc = sum(c == "Inconclusive" for _, c in verdicts)
        print(f"\nPlatform: false-positive rate on pristine {fp}/{len(neg)}, detection rate {tp}/{len(pos)}, "
              f"abstained {inc}/{len(verdicts)}")
    out = Path("models/calibration.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(cal, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1])
