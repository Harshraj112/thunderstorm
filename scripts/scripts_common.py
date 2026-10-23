"""Helpers shared by training scripts."""
import subprocess

import numpy as np

SR = 16000


def load_wav(path, sr: int = SR) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, "<i2").astype(np.float32) / 32768


def eer(scores, labels) -> float:
    """Equal error rate; labels 1 = positive (spoof) class with higher scores."""
    s, l = np.asarray(scores, float), np.asarray(labels, int)
    order = np.argsort(s)
    s, l = s[order], l[order]
    P, N = l.sum(), (1 - l).sum()
    if P == 0 or N == 0:
        return float("nan")
    fnr = np.cumsum(l) / P
    fpr = 1 - np.cumsum(1 - l) / N
    i = np.argmin(np.abs(fnr - fpr))
    return float((fnr[i] + fpr[i]) / 2)
