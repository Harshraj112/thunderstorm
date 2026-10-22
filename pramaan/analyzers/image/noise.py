"""Sensor-noise inconsistency map.

Every camera + ISP leaves a characteristic noise level. A spliced region from another photo
usually carries a different noise level. We estimate local noise with Immerkaer's fast
estimator (robust to edges) on 32x32 blocks, regress out texture, and look for a coherent
cluster of blocks whose noise deviates strongly from the rest of the image.
"""
from __future__ import annotations

import cv2
import numpy as np

from ..base import Context, Finding, calibrated, robust_z, save_heatmap, status_from_llr
from .common import bbox_of, blocks, largest_component, load_rgb

KERNEL = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=float)


def run(ctx: Context) -> list[Finding]:
    rgb = load_rgb(ctx.path, max_side=1536)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(float)
    B = 32
    conv = np.abs(cv2.filter2D(gray, -1, KERNEL))
    sigma = blocks(conv, B).mean(axis=(2, 3)) * np.sqrt(np.pi / 2) / 6
    grad = blocks(np.hypot(cv2.Sobel(gray, cv2.CV_64F, 1, 0), cv2.Sobel(gray, cv2.CV_64F, 0, 1)), B).mean(axis=(2, 3))
    mean = blocks(gray, B).mean(axis=(2, 3))
    usable = (mean > 15) & (mean < 240)   # avoid clipped blocks (no noise)
    if usable.sum() < 30:
        return [Finding(code="IMG_NOISE", module="image", family="pixel", title="Noise analysis: image too small",
                        status="info", reliability=0.0, explanation="Not enough usable 32x32 blocks.")]
    ls, lg = np.log(sigma + 0.05), np.log(grad + 1)
    # remove dependence of measured noise on texture with a robust linear fit
    A = np.stack([lg[usable], np.ones(usable.sum())], 1)
    coef, *_ = np.linalg.lstsq(A, ls[usable], rcond=None)
    resid = ls - (coef[0] * lg + coef[1])
    z = robust_z(np.where(usable, resid, np.nan))
    z = np.nan_to_num(z)
    # smooth the z map so isolated blocks don't trigger
    zs = cv2.blur(z, (3, 3))
    mask = usable & (np.abs(zs) > 2.2)
    comp, area = largest_component(mask)
    q = ctx.shared.get("jpeg_quality", 95)
    reliability = 0.65 if q >= 80 else 0.4 if q >= 65 else 0.2
    heat = np.clip(np.abs(zs) / 4, 0, 1) * usable
    art = save_heatmap(rgb, heat, ctx.artifact("noise_map.png"))
    frac = area / usable.sum()
    if area >= 8 and frac < 0.45:
        raw = float(np.abs(zs[comp]).mean())
        direction = "lower" if zs[comp].mean() < 0 else "higher"
        llr = calibrated("noise_inconsistency", raw, a=1.0, b=-3.3, cap=2.5)
        sc = rgb.shape[1] and (_orig_w(ctx) / rgb.shape[1])
        return [Finding(code="IMG_NOISE", module="image", family="pixel",
                        title=(f"Noise-level inconsistency in a {area}-block region" if llr >= 0.5 else
                               f"Minor noise variation in a {area}-block region (below threshold)"),
                        status=status_from_llr(llr), raw_score=raw, llr=llr, reliability=reliability,
                        location={"bbox": bbox_of(comp, B * sc), "blocks": int(area)},
                        explanation=f"A connected region has {direction} sensor noise than the rest of the image "
                                    f"(mean robust z = {raw:.1f}) after accounting for texture. Content pasted from "
                                    "another photo, or locally retouched/blurred, typically behaves like this.",
                        innocent=["Depth-of-field blur or motion blur in part of the scene",
                                  "Strong local tone-mapping / HDR / portrait-mode processing by the phone"],
                        artifacts=[art])]
    return [Finding(code="IMG_NOISE", module="image", family="pixel", title="Noise level consistent across image",
                    status="pass", llr=-0.5, reliability=reliability, explanation="No coherent region with an "
                    "anomalous noise level was found.", artifacts=[art])]


def _orig_w(ctx: Context) -> int:
    from PIL import Image
    return Image.open(ctx.path).width
