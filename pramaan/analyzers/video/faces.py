"""Face detection that works on any OpenCV version with no downloads.

Order of preference:
  1. YuNet CNN detector (cv2.FaceDetectorYN) if models/face_detection_yunet_2023mar.onnx exists
     (scripts/fetch_real_samples.py downloads it when the network allows Git LFS / raw GitHub).
  2. cv2.CascadeClassifier (OpenCV 4.x) with the bundled Viola–Jones frontal-face cascade.
  3. A vectorised NumPy evaluator for the same cascade XML (OpenCV 5 removed CascadeClassifier).
All return boxes (x, y, w, h) in the coordinates of the image passed in.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from ... import config

CASCADE = Path(__file__).with_name("haarcascade_frontalface_default.xml")


class NumpyCascade:
    """Viola–Jones stump cascade evaluated with integral images (same maths as OpenCV's HaarEvaluator)."""

    def __init__(self, path: Path):
        root = ET.parse(path).getroot()
        c = root.find("cascade")
        self.W, self.H = int(c.find("width").text), int(c.find("height").text)
        feats = []
        for f in c.find("features"):
            rects = []
            for r in f.find("rects"):
                x, y, w, h, wt = r.text.split()
                rects.append((int(x), int(y), int(w), int(h), float(wt)))
            feats.append(rects)
        self.stages = []
        for st in c.find("stages"):
            thr = float(st.find("stageThreshold").text)
            weak = []
            for wc in st.find("weakClassifiers"):
                n = wc.find("internalNodes").text.split()
                lv = [float(v) for v in wc.find("leafValues").text.split()]
                weak.append((feats[int(n[2])], float(n[3]), lv[0], lv[1]))
            self.stages.append((thr, weak))
        self.inv_area = 1.0 / ((self.W - 2) * (self.H - 2))
        # pack each stage as arrays (K weak classifiers x 3 rects; unused rects have zero weight)
        self.packed = []
        for thr, weak in self.stages:
            K = len(weak)
            R = np.zeros((K, 3, 5))
            for k, (rects, *_rest) in enumerate(weak):
                for j, r in enumerate(rects[:3]):
                    R[k, j] = r
            self.packed.append((thr, R[..., 0].astype(int), R[..., 1].astype(int), R[..., 2].astype(int),
                                R[..., 3].astype(int), R[..., 4], np.array([w[1] for w in weak]),
                                np.array([w[2] for w in weak]), np.array([w[3] for w in weak])))

    def _detect_scaled(self, g: np.ndarray, step: int) -> list[tuple[int, int]]:
        h, w = g.shape
        if h < self.H or w < self.W:
            return []
        gi = g.astype(np.float64)
        ii = np.zeros((h + 1, w + 1))
        ii[1:, 1:] = gi.cumsum(0).cumsum(1)
        sq = np.zeros((h + 1, w + 1))
        sq[1:, 1:] = (gi ** 2).cumsum(0).cumsum(1)
        ys, xs = np.mgrid[0:h - self.H + 1:step, 0:w - self.W + 1:step]
        ys, xs = ys.ravel(), xs.ravel()

        def rsum(tab, x, y, rw, rh):
            return tab[ys + y + rh, xs + x + rw] - tab[ys + y, xs + x + rw] - tab[ys + y + rh, xs + x] + tab[ys + y, xs + x]
        s = rsum(ii, 1, 1, self.W - 2, self.H - 2) * self.inv_area
        q = rsum(sq, 1, 1, self.W - 2, self.H - 2) * self.inv_area
        std = np.sqrt(np.maximum(q - s * s, 1.0))
        W1 = w + 1
        flat = ii.ravel()
        for thr, rx, ry, rw, rh, wt, t, left, right in self.packed:
            if not len(ys):
                return []
            base = (ys * W1 + xs)[:, None, None]                     # (P,1,1)
            tl = base + (ry * W1 + rx)[None]                          # (P,K,3)
            tr = tl + rw[None]
            bl = tl + (rh * W1)[None]
            br = bl + rw[None]
            sums = flat[br] - flat[tr] - flat[bl] + flat[tl]
            v = (sums * wt[None]).sum(axis=2) * self.inv_area          # (P,K)
            total = np.where(v < t[None] * std[:, None], left[None], right[None]).sum(axis=1)
            keep = total >= thr
            ys, xs, std = ys[keep], xs[keep], std[keep]
        return list(zip(xs.tolist(), ys.tolist()))

    def detectMultiScale(self, g: np.ndarray, scaleFactor: float = 1.15, minNeighbors: int = 3, minSize=(30, 30)):
        boxes = []
        f = 1.0
        while True:
            w, h = int(g.shape[1] / f), int(g.shape[0] / f)
            if w < self.W or h < self.H:
                break
            if self.W * f >= minSize[0]:
                sg = cv2.resize(g, (w, h), interpolation=cv2.INTER_LINEAR) if f != 1.0 else g
                for x, y in self._detect_scaled(sg, 2 if f < 2 else 1):
                    boxes.append((int(x * f), int(y * f), int(self.W * f), int(self.H * f)))
            f *= scaleFactor
        return group(boxes, minNeighbors)


def group(boxes: list, min_neighbors: int) -> list:
    """Cluster overlapping raw detections (like cv::groupRectangles) and average each cluster."""
    clusters: list[list] = []
    for b in boxes:
        for cl in clusters:
            r = cl[0]
            d = 0.2 * (min(r[2], b[2]))
            if abs(r[0] - b[0]) <= d and abs(r[1] - b[1]) <= d and abs(r[2] - b[2]) <= d * 1.5:
                cl.append(b)
                break
        else:
            clusters.append([b])
    cands = sorted(((len(cl), tuple(int(v) for v in np.mean(cl, axis=0))) for cl in clusters if len(cl) > min_neighbors),
                   reverse=True)
    out: list = []
    for _, b in cands:   # suppress boxes whose centre lies inside a stronger box (nested scales)
        cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
        if not any(o[0] <= cx <= o[0] + o[2] and o[1] <= cy <= o[1] + o[3] for o in out):
            out.append(b)
    return np.array(out) if out else np.empty((0, 4), int)


class _YuNet:
    def __init__(self, path: Path):
        self.det = cv2.FaceDetectorYN.create(str(path), "", (320, 320), 0.8, 0.3, 50)

    def detectMultiScale(self, g, scaleFactor=1.1, minNeighbors=3, minSize=(30, 30)):
        bgr = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR) if g.ndim == 2 else g
        self.det.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, faces = self.det.detect(bgr)
        if faces is None:
            return np.empty((0, 4), int)
        return np.array([f[:4].astype(int) for f in faces if f[2] >= minSize[0]])


@lru_cache(maxsize=1)
def detector():
    """Return (detector, backend_name) or (None, reason)."""
    yunet = config.MODELS_DIR / "face_detection_yunet_2023mar.onnx"
    if yunet.exists() and yunet.stat().st_size > 10000 and hasattr(cv2, "FaceDetectorYN"):
        try:
            return _YuNet(yunet), "YuNet CNN"
        except cv2.error:
            pass
    if not CASCADE.exists():
        return None, "no face model (run scripts/fetch_real_samples.py)"
    if hasattr(cv2, "CascadeClassifier"):
        det = cv2.CascadeClassifier(str(CASCADE))
        if not det.empty():
            return det, "OpenCV Viola–Jones cascade"
    return NumpyCascade(CASCADE), "Viola–Jones cascade (NumPy)"
