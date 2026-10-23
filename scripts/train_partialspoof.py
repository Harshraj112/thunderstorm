#!/usr/bin/env python3
"""Train the frame-level partial-spoof localiser -> models/partialspoof_frame.npz

PartialSpoof database (https://github.com/nii-yamagishilab/PartialSpoof, data on Zenodo):
    python scripts/train_partialspoof.py --partialspoof <db>/train/con_wav <db>/segment_labels/train_seglab_0.02.npy
    (label file: dict utt_id -> array of '1' (bona fide) / '0' (spoof) per 20 ms)
Generic CSV (any language, e.g. your own Hindi splices):
    python scripts/train_partialspoof.py --csv segments.csv     # rows: wav_path,start_s,end_s  (spoof spans)

Prints frame-level EER on a held-out utterance split; report the out-of-domain number too.
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pramaan.analyzers.audio.partial_spoof import FRAME_S, frame_features  # noqa: E402
from scripts_common import eer, load_wav  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--partialspoof", nargs=2, metavar=("WAV_DIR", "SEGLAB_NPY"))
    g.add_argument("--csv")
    ap.add_argument("--limit", type=int, default=3000, help="max utterances")
    ap.add_argument("--frames-per-utt", type=int, default=150)
    ap.add_argument("--out", default="models/partialspoof_frame.npz")
    a = ap.parse_args()
    items = []   # (wav_path, per-frame spoof label array or None, spans)
    if a.partialspoof:
        labs = np.load(a.partialspoof[1], allow_pickle=True).item()
        wd = Path(a.partialspoof[0])
        for utt, lab in list(labs.items())[:a.limit]:
            items.append((wd / f"{utt}.wav", np.array([1 - int(v) for v in lab]), None))  # 1 = spoof
    else:
        spans = defaultdict(list)
        for row in csv.reader(open(a.csv)):
            if row and not row[0].startswith("#"):
                spans[row[0]].append((float(row[1]), float(row[2])))
        items = [(Path(p), None, s) for p, s in list(spans.items())[:a.limit]]
    rng = np.random.default_rng(0)
    rng.shuffle(items)
    X, Y, G = [], [], []
    for k, (p, lab, sp) in enumerate(items):
        x = load_wav(p)
        F = frame_features(x)
        n = len(F)
        if lab is None:
            lab = np.zeros(n, int)
            for s, e in sp:
                lab[int(s / FRAME_S):int(e / FRAME_S)] = 1
        n = min(n, len(lab))
        idx = rng.choice(n, size=min(n, a.frames_per_utt), replace=False)
        X.append(F[idx])
        Y.append(lab[idx])
        G.append(np.full(len(idx), k))
        if k % 200 == 0:
            print(f"{k}/{len(items)} utterances", flush=True)
    X, Y, G = np.concatenate(X), np.concatenate(Y), np.concatenate(G)
    test = G >= int(0.8 * len(items))
    mu, sd = X[~test].mean(0), X[~test].std(0) + 1e-6
    from sklearn.linear_model import LogisticRegression
    clf = LogisticRegression(max_iter=3000, C=0.3, class_weight="balanced").fit((X[~test] - mu) / sd, Y[~test])
    s = clf.decision_function((X[test] - mu) / sd)
    print(f"held-out frame EER: {eer(s, Y[test]) * 100:.2f}%  ({test.sum()} frames)")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    np.savez(a.out, w=clf.coef_[0], b=clf.intercept_[0], mu=mu, sd=sd)
    print("saved", a.out)


if __name__ == "__main__":
    main()
