"""PRNU source-camera identification (Lukáš/Fridrich/Goljan; Chen et al. IEEE TIFS 2008).

Register a device: 20–50 flat, well-lit reference shots from the seized phone/camera
(POST /api/devices). Its fingerprint K = Σ W_i I_i / Σ I_i² is stored. For questioned images of
the same resolution, the Peak-to-Correlation Energy (PCE) between the image's noise residual and
K·I decides attribution (PCE > 60 is the conventional threshold).
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ... import config, db
from ..base import Context, Finding

CROP = 1024
PCE_THRESHOLD = 60.0


def _gray(path: Path) -> np.ndarray:
    a = np.asarray(Image.open(path).convert("L"), dtype=np.float32)
    h, w = a.shape
    c = min(CROP, h, w)
    y, x = (h - c) // 2, (w - c) // 2
    return a[y:y + c, x:x + c]


def residual(img: np.ndarray) -> np.ndarray:
    """Noise residual W = I - F(I). Wavelet denoising (skimage) is the textbook filter; we fall back
    to a fast edge-preserving filter so registration of 50 images stays quick on CPU."""
    try:
        from skimage.restoration import denoise_wavelet
        den = denoise_wavelet(img / 255.0, method="BayesShrink", mode="soft", rescale_sigma=True) * 255.0
    except Exception:
        den = cv2.bilateralFilter(img, 5, 10, 5)
    w = img - den
    w -= w.mean(axis=1, keepdims=True)   # zero-mean rows/cols removes linear artefacts (CFA/JPEG grid)
    w -= w.mean(axis=0, keepdims=True)
    return w.astype(np.float32)


def build_fingerprint(paths: list[Path]) -> np.ndarray:
    num, den = None, None
    for p in paths:
        I = _gray(p)
        W = residual(I)
        num = W * I if num is None else num + W * I
        den = I * I if den is None else den + I * I
    K = num / (den + 1e-6)
    K -= K.mean(axis=1, keepdims=True)
    K -= K.mean(axis=0, keepdims=True)
    return K.astype(np.float32)


def pce(a: np.ndarray, b: np.ndarray, excl: int = 5) -> float:
    a = a - a.mean()
    b = b - b.mean()
    xc = np.real(np.fft.ifft2(np.fft.fft2(a) * np.conj(np.fft.fft2(b))))
    pk = np.unravel_index(np.argmax(np.abs(xc)), xc.shape)
    peak = xc[pk]
    mask = np.ones_like(xc, bool)
    for dy in range(-excl, excl + 1):
        for dx in range(-excl, excl + 1):
            mask[(pk[0] + dy) % xc.shape[0], (pk[1] + dx) % xc.shape[1]] = False
    energy = np.mean(xc[mask] ** 2)
    return float(np.sign(peak) * peak ** 2 / (energy + 1e-12))


def register_device(name: str, paths: list[Path]) -> dict:
    sizes = {Image.open(p).size for p in paths}
    K = build_fingerprint(paths)
    did = db.new_id("dev")
    out = config.DEVICE_DIR / f"{did}.npy"
    np.save(out, K)
    meta = {"n_images": len(paths), "sizes": sorted(sizes)}
    db.insert("devices", {"id": did, "name": name, "kind": "prnu", "path": str(out), "meta": meta, "created_at": db.now()})
    return {"id": did, "name": name, **meta}


def run(ctx: Context) -> list[Finding]:
    devs = db.q("SELECT * FROM devices WHERE kind='prnu'")
    if not devs:
        return []
    size = Image.open(ctx.path).size
    I = _gray(ctx.path)
    W = residual(I)
    results = []
    for d in devs:
        sizes = [tuple(s) for s in (d["meta"] or {}).get("sizes", [])]
        if sizes and size not in sizes and size[::-1] not in sizes:
            results.append({"device": d["name"], "pce": None, "note": "resolution differs"})
            continue
        K = np.load(d["path"])
        if K.shape != I.shape:
            results.append({"device": d["name"], "pce": None, "note": "crop size differs"})
            continue
        results.append({"device": d["name"], "pce": round(pce(W, K * I), 1)})
    matched = [r for r in results if r["pce"] is not None and r["pce"] > PCE_THRESHOLD]
    if matched:
        best = max(matched, key=lambda r: r["pce"])
        return [Finding(code="IMG_PRNU", module="image", family="provenance",
                        title=f"Sensor fingerprint matches device '{best['device']}' (PCE {best['pce']})",
                        status="pass", llr=-0.8, reliability=0.8, raw_score=best["pce"],
                        explanation="The photo-response non-uniformity (PRNU) noise of this image matches the registered "
                                    "device fingerprint above the PCE threshold of 60, supporting that it was captured "
                                    "by that sensor.", data={"comparisons": results})]
    return [Finding(code="IMG_PRNU", module="image", family="provenance",
                    title="No registered device matched the sensor fingerprint", status="info", llr=0.0,
                    reliability=0.3, explanation="PCE below 60 for all registered devices of matching resolution. "
                    "Heavy compression, resizing or cropping also destroys PRNU.", data={"comparisons": results})]
