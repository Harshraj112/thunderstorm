#!/usr/bin/env python3
"""Train the CLIP linear probe for AI-generated images -> models/aigi_probe.npz
(needs: pip install torch open_clip_torch). Use a few thousand real photos (incl. Indian phone photos
and WhatsApp-recompressed copies) and images from SEVERAL generators.

    python scripts/train_aigi_probe.py real_dir fake_dir
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pramaan.analyzers.image.dl_plugins import clip_features  # noqa: E402

EXT = {".jpg", ".jpeg", ".png", ".webp"}


def main(real, fake, out="models/aigi_probe.npz"):
    paths = [(p, 0) for p in Path(real).rglob("*") if p.suffix.lower() in EXT] + \
            [(p, 1) for p in Path(fake).rglob("*") if p.suffix.lower() in EXT]
    np.random.default_rng(0).shuffle(paths)
    X = np.concatenate([clip_features([p for p, _ in paths[i:i + 32]]) for i in range(0, len(paths), 32)])
    y = np.array([l for _, l in paths])
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    k = int(0.8 * len(y))
    clf = LogisticRegression(max_iter=3000, C=1.0, class_weight="balanced").fit(X[:k], y[:k])
    print(f"held-out AUC {roc_auc_score(y[k:], clf.decision_function(X[k:])):.3f}")
    np.savez(out, w=clf.coef_[0], b=clf.intercept_[0])
    print("saved", out)


if __name__ == "__main__":
    main(*sys.argv[1:3])
