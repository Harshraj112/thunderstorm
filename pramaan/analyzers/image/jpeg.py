"""JPEG compression forensics.

1. Quantisation-table analysis: estimate the encoder quality and whether the table is the plain
   IJG/libjpeg table (used by GIMP, Pillow, FFmpeg, most re-save paths and messaging apps) versus a
   camera-specific table.
2. JPEG ghosts (Farid, IEEE TIFS 2009): re-compress at a range of qualities. A region that was
   previously saved at a lower quality q1 shows an anomalously small difference ("ghost") at q1.
   This gives both GLOBAL recompression evidence and LOCAL splice localisation.
"""
from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from ..base import Context, Finding, calibrated, robust_z, save_heatmap, status_from_llr
from .common import bbox_of, blocks, is_jpeg, largest_component, load_rgb

STD_LUMA = np.array([
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99], dtype=float)
ZIGZAG = np.array([0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27,
                   20, 13, 6, 7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58,
                   59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63])


def ijg_table(q: int) -> np.ndarray:
    s = 5000 / q if q < 50 else 200 - 2 * q
    return np.clip(np.floor((STD_LUMA * s + 50) / 100), 1, 255)


def estimate_quality(table: list[int]) -> tuple[int, bool, float]:
    """Return (closest IJG quality, exact_match, mean_abs_error)."""
    t = np.asarray(table, dtype=float)
    best = (50, False, 1e9)
    for order in (t, t[np.argsort(ZIGZAG)]):  # try natural and zig-zag orderings
        for q in range(1, 101):
            err = float(np.abs(order - ijg_table(q)).mean())
            if err < best[2]:
                best = (q, err == 0.0, err)
    return best


def run(ctx: Context) -> list[Finding]:
    if not is_jpeg(ctx.path):
        return [Finding(code="IMG_JPEG", module="image", family="compression", title="Not a JPEG",
                        status="info", explanation="Compression-history tests apply to JPEG files only. "
                        "PNG/WebP screenshots or exports carry no JPEG history in their container.")]
    out: list[Finding] = []
    meta = ctx.shared.get("meta") or {}
    qt = (meta.get("jpeg") or {}).get("quantization") or {}
    if not qt:
        qt = {int(k): list(v) for k, v in (getattr(Image.open(ctx.path), "quantization", {}) or {}).items()}
    q_final, exact, err = estimate_quality(qt[0]) if 0 in qt else (90, False, 99.0)
    make = str((meta.get("exif") or {}).get("Make", "")).strip()
    ctx.shared["jpeg_quality"] = q_final

    if exact and make:
        out.append(Finding(code="IMG_QTABLE", module="image", family="compression",
                           title=f"Standard libjpeg table (Q{q_final}) on a file claiming a {make} camera",
                           status="warn", llr=0.8, reliability=0.5, raw_score=q_final,
                           explanation="The luminance quantisation table is exactly the IJG/libjpeg standard table "
                                       "scaled to quality %d. Many cameras use custom tables; the standard table is "
                                       "typical of software re-saves (GIMP, Pillow, FFmpeg, messaging apps)." % q_final,
                           innocent=["Some phone vendors use the IJG tables natively",
                                     "Lossless metadata edits do not change the table, so this indicates re-encoding only"]))
    else:
        out.append(Finding(code="IMG_QTABLE", module="image", family="compression",
                           title=f"Quantisation table ≈ quality {q_final}" + (" (standard IJG)" if exact else " (custom)"),
                           status="info", reliability=0.2, raw_score=q_final,
                           explanation=f"Estimated encoder quality {q_final} (mean table error {err:.1f}). "
                                       + ("Quality 70–85 with no EXIF is typical of WhatsApp/social-media transmission."
                                          if 68 <= q_final <= 86 and not make else "")))

    out.extend(_ghosts(ctx, q_final))
    return out


def _ghosts(ctx: Context, q_final: int) -> list[Finding]:
    rgb = load_rgb(ctx.path, max_side=1536)
    gray_src = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(float)
    B = 16
    qualities = [q for q in range(35, 96, 5) if q <= q_final - 8]
    if len(qualities) < 3:
        return [Finding(code="IMG_GHOST", module="image", family="compression", title="JPEG ghost analysis skipped",
                        status="info", explanation=f"Final quality {q_final} is too low to search for earlier, "
                        "lower-quality compressions.")]
    D = []
    for q in qualities:
        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, "JPEG", quality=q)
        g = cv2.cvtColor(np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB")), cv2.COLOR_RGB2GRAY)
        d = (gray_src - g.astype(float)) ** 2
        D.append(blocks(d, B).mean(axis=(2, 3)))
    D = np.stack(D)                                  # (nq, by, bx)
    own = D.mean(axis=0) + 1e-6                      # per-block scale → removes texture dependence
    R = D / own                                      # per-block relative curve
    med = np.median(R.reshape(len(qualities), -1), axis=1)[:, None, None]
    strength = (med - R).max(axis=0)                 # how much deeper this block dips than typical
    q_at = np.array(qualities)[(med - R).argmax(axis=0)]
    texture = blocks(np.abs(cv2.Laplacian(gray_src, cv2.CV_64F)), B).mean(axis=(2, 3))
    usable = (own > 0.5) & (texture > np.percentile(texture, 10))
    z = robust_z(np.where(usable, strength, np.nan))
    mask = usable & (z > 4.0) & (strength > 0.25)
    comp, area = largest_component(mask)

    # global recompression: dip in the image-wide curve
    glob = D.mean(axis=(1, 2)) / D.mean()
    dips = [(qualities[i], glob[i - 1] - glob[i] + glob[i + 1] - glob[i]) for i in range(1, len(qualities) - 1)]
    best_dip = max(dips, key=lambda x: x[1]) if dips else (None, 0.0)

    heat = np.clip(np.nan_to_num(z) / 8, 0, 1) * usable
    art = save_heatmap(rgb, heat, ctx.artifact("jpeg_ghost.png"))
    out = []
    frac = area / max(usable.sum(), 1)
    if area >= 4 and frac < 0.5:
        q1 = int(np.median(q_at[comp]))
        raw = float(np.nan_to_num(z[comp]).mean())
        llr = calibrated("jpeg_ghost_local", raw, a=0.45, b=-2.5, cap=3.0)
        out.append(Finding(code="IMG_GHOST_LOCAL", module="image", family="compression",
                           title=f"JPEG ghost: region previously compressed at ~Q{q1}",
                           status=status_from_llr(llr), raw_score=raw, llr=llr,
                           reliability=0.7 if q_final >= 85 else 0.45,   # resized/low-Q images mimic ghosts
                           location={"bbox": bbox_of(comp, B * _scale(ctx, rgb)), "blocks": int(area)},
                           explanation=f"A {area}-block region shows a compression 'ghost' at quality ≈{q1}, while the "
                                       f"rest of the image does not (final quality {q_final}). This is the classic "
                                       "signature of content pasted from a different JPEG.",
                           innocent=["Very smooth or saturated regions can mimic ghosts",
                                     "Local filters (blur, denoise) applied to part of the image"],
                           artifacts=[art]))
    else:
        out.append(Finding(code="IMG_GHOST_LOCAL", module="image", family="compression",
                           title="JPEG ghost: no localised compression inconsistency", status="pass",
                           llr=-0.4, reliability=0.5, explanation="No region shows a distinct earlier compression.",
                           artifacts=[art]))
    if best_dip[0] and best_dip[1] > 0.06:
        out.append(Finding(code="IMG_RECOMPRESSED", module="image", family="compression",
                           title=f"Whole image appears recompressed (earlier quality ≈{best_dip[0]})",
                           status="info", llr=0.3, reliability=0.4, raw_score=float(best_dip[1]),
                           explanation="The image-wide error curve dips at a lower quality than the final one, "
                                       "indicating at least two JPEG compressions. This shows re-saving, NOT "
                                       "necessarily content manipulation.",
                           innocent=["Forwarding through messaging apps", "Gallery apps re-saving after rotation"],
                           data={"qualities": qualities, "curve": [float(x) for x in glob]}))
    return out


def _scale(ctx: Context, rgb: np.ndarray) -> float:
    w = Image.open(ctx.path).width
    return w / rgb.shape[1]
