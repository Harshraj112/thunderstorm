from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

MAX_SIDE = 2048


def load_rgb(path: Path, max_side: int = MAX_SIDE) -> np.ndarray:
    # Deliberately NOT exif-transposed: forensic maps must align with the stored sensor pixels.
    im = Image.open(path).convert("RGB")
    if max(im.size) > max_side:
        f = max_side / max(im.size)
        im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
    return np.asarray(im)


def is_jpeg(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(2) == b"\xff\xd8"


def blocks(a: np.ndarray, b: int) -> np.ndarray:
    """Reshape HxW to (H//b, W//b, b, b)."""
    h, w = (a.shape[0] // b) * b, (a.shape[1] // b) * b
    a = a[:h, :w]
    return a.reshape(h // b, b, w // b, b).swapaxes(1, 2)


def largest_component(mask: np.ndarray) -> tuple[np.ndarray, int]:
    import cv2
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros_like(mask, bool), 0
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return lab == i, int(stats[i, cv2.CC_STAT_AREA])


def bbox_of(mask: np.ndarray, scale: float = 1.0) -> dict | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return {"x": int(xs.min() * scale), "y": int(ys.min() * scale),
            "w": int((xs.max() - xs.min() + 1) * scale), "h": int((ys.max() - ys.min() + 1) * scale)}
