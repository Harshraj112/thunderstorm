#!/usr/bin/env python3
"""Train the CPU synthetic-voice baseline (LFCC + logistic regression) -> models/lfcc_spoof.npz

ASVspoof 2019 LA layout:
    python scripts/train_lfcc_spoof.py --asvspoof LA/ASVspoof2019_LA_train/flac \
        LA/ASVspoof2019_LA_cm_protocols/ASVspoof2019.LA.cm.train.trn.txt
Folder layout (any language, e.g. IndicSynth / your own recordings):
    python scripts/train_lfcc_spoof.py --folders real_dir fake_dir

Reports EER on a held-out 20 % split. Always ALSO evaluate on an out-of-domain set
(In-the-Wild, an Indic set) and quote that number to judges – it will be much worse.
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pramaan.analyzers.audio.spoof import SR, window_features  # noqa: E402


def load(path: Path) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, "<i2").astype(np.float32) / 32768


def feats(path: Path) -> np.ndarray:
    x = load(path)
    n = 2 * SR
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    st = (len(x) - n) // 2
    return window_features(x[st:st + n])


def eer(scores, labels):
    order = np.argsort(scores)
    s, l = np.asarray(scores)[order], np.asarray(labels)[order]
    P, N = l.sum(), (1 - l).sum()
    fnr = np.cumsum(l) / P           # spoof scored below threshold
    fpr = 1 - np.cumsum(1 - l) / N   # bona fide above threshold
    i = np.argmin(np.abs(fnr - fpr))
    return float((fnr[i] + fpr[i]) / 2)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--asvspoof", nargs=2, metavar=("AUDIO_DIR", "PROTOCOL"))
    g.add_argument("--folders", nargs=2, metavar=("REAL_DIR", "FAKE_DIR"))
    ap.add_argument("--limit", type=int, default=6000)
    ap.add_argument("--out", default="models/lfcc_spoof.npz")
    a = ap.parse_args()
    items = []
    if a.asvspoof:
        d = Path(a.asvspoof[0])
        for line in open(a.asvspoof[1]):
            p = line.split()
            items.append((d / f"{p[1]}.flac", 1 if p[-1] == "spoof" else 0))
    else:
        for lab, d in ((0, a.folders[0]), (1, a.folders[1])):
            items += [(p, lab) for p in Path(d).rglob("*") if p.suffix.lower() in (".wav", ".flac", ".mp3", ".m4a")]
    rng = np.random.default_rng(0)
    rng.shuffle(items)
    items = items[:a.limit]
    X = np.stack([feats(p) for p, _ in items])
    y = np.array([l for _, l in items])
    from sklearn.linear_model import LogisticRegression
    k = int(0.8 * len(y))
    mu, sd = X[:k].mean(0), X[:k].std(0) + 1e-6
    clf = LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced").fit((X[:k] - mu) / sd, y[:k])
    s = clf.decision_function((X[k:] - mu) / sd)
    print(f"held-out EER: {eer(s, y[k:]) * 100:.2f}%  (n={len(y) - k})")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    np.savez(a.out, w=clf.coef_[0], b=clf.intercept_[0], mu=mu, sd=sd)
    print("saved", a.out)


if __name__ == "__main__":
    main()
