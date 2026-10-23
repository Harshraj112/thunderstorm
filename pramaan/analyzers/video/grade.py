"""Colour filters ('night', 'noir', vintage) and re-framing applied in an editor.

A camera sensor records colour and brightness independently: in any real scene, two pixels of the same
brightness can have very different colours. A colour grade breaks that:
  * tint / duotone filters compute colour FROM brightness (colour becomes a function of luma);
  * 'night'/'noir' looks suppress colour while keeping or boosting contrast, and crush shadows to black.
Genuine infrared night footage is fully monochrome (no colour at all), so it is reported, not flagged.

Re-framing: a picture that fills only part of the frame with constant black bars was placed onto a new canvas
(e.g. a portrait phone or CCTV clip exported as 16:9), which only editing/export software does.
"""
from __future__ import annotations

import subprocess

import numpy as np

from ..base import FAIL, INFO, PASS, WARN, Context, Finding

W, H = 320, 180


def _frames(path, fps: float = 1.0, limit: int = 120) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={fps},scale={W}:{H}:flags=area",
                          "-frames:v", str(limit), "-f", "rawvideo", "-pix_fmt", "yuv444p", "-"],
                         capture_output=True, timeout=600).stdout
    n = len(raw) // (3 * W * H)
    return np.frombuffer(raw[:n * 3 * W * H], np.uint8).reshape(n, 3, H, W).astype(np.float32)


def measure(path) -> dict | None:
    a = _frames(path)
    if len(a) < 3:
        return None
    Y, U, V = a[:, 0], a[:, 1] - 128, a[:, 2] - 128
    colm = (Y.mean((0, 1)) > 20) | (Y.std((0, 1)) > 3)
    rowm = (Y.mean((0, 2)) > 20) | (Y.std((0, 2)) > 3)
    bars = 1 - colm.mean() * rowm.mean()
    m = np.outer(rowm, colm)
    r2, resid, chroma, ystd, crushed = [], [], [], [], []
    for i in range(len(a)):
        y, u, v = Y[i][m], U[i][m], V[i][m]
        if y.size < 500:
            continue
        b = np.clip((y // 8).astype(int), 0, 31)
        cnt = np.maximum(np.bincount(b, minlength=32), 1)
        mu = np.bincount(b, weights=u, minlength=32) / cnt
        mv = np.bincount(b, weights=v, minlength=32) / cnt
        tot = u.var() + v.var() + 1e-9
        r2.append(float((mu[b].var() + mv[b].var()) / tot))
        resid.append(float(np.sqrt(((u - mu[b]) ** 2 + (v - mv[b]) ** 2).mean())))
        chroma.append(float(np.hypot(u, v).mean()))
        ystd.append(float(y.std()))
        crushed.append(float((y <= 18).mean()))
    if not r2:
        return None
    med = lambda x: float(np.median(x))
    c, ys = med(chroma), med(ystd)
    return {"frames": len(a), "bars": float(bars), "r2": med(r2), "resid": med(resid), "chroma": c, "ystd": ys,
            "ratio": c / (ys + 1e-6), "crushed": med(crushed),
            "content_w": float(colm.mean()), "content_h": float(rowm.mean())}


def run(ctx: Context) -> list[Finding]:
    s = measure(ctx.path)
    if not s:
        return []
    ctx.shared["grade"] = s
    out: list[Finding] = []
    data = {k: round(v, 3) for k, v in s.items()}
    if s["chroma"] < 1.0:
        out.append(Finding(code="VID_MONOCHROME", module="video", family="colour-filter", status=INFO,
                           title="Monochrome footage (IR night mode or black-and-white camera)", data=data,
                           explanation="The picture carries no colour at all, as infrared night mode does. A full "
                                       "black-and-white filter would look the same, so this is not counted either way."))
    else:
        tint = s["r2"] >= 0.5 and s["resid"] < 2.0
        muted = s["ratio"] < 0.08
        crushed = s["crushed"] > 0.08
        if tint or muted:
            why = []
            if tint:
                why.append(f"{s['r2'] * 100:.0f}% of the remaining colour is predictable from brightness alone (a "
                           f"real scene shows ≈5–25%), with only {s['resid']:.1f} levels of independent colour: the "
                           "colour was computed from brightness by a tint/duotone filter")
            if muted:
                why.append(f"colour strength is only {s['ratio'] * 100:.1f}% of the contrast (genuine colour footage, "
                           "day or night, shows 10–35%): colour was suppressed while contrast was kept")
            if crushed:
                why.append(f"{s['crushed'] * 100:.0f}% of the picture is crushed to pure black, typical of a "
                           "'night'/'noir' contrast curve")
            llr = 2.5 if tint else 1.6 + (0.6 if crushed else 0.0)
            out.append(Finding(code="VID_COLOUR_FILTER", module="video", family="colour-filter", status=FAIL if llr >= 2 else WARN,
                               title="Colour filter applied ('night'/tinted look added after recording)",
                               llr=llr, reliability=0.75 if tint else 0.6, data=data,
                               explanation="The colour statistics are not those of a camera sensor: " + "; ".join(why) +
                                           ". A filter changes how the scene looks (time of day, lighting, identifiability) "
                                           "without changing the file's timeline.",
                               innocent=["Camera's own 'colour/mono' image setting or low-light desaturation",
                                         "A lab enhancement applied for viewing (should be documented)"]))
        else:
            out.append(Finding(code="VID_COLOUR_FILTER", module="video", family="colour-filter", status=PASS,
                               title="Colour behaves like camera output", llr=-0.3, reliability=0.4, data=data,
                               explanation=f"Colour is independent of brightness ({s['r2'] * 100:.0f}% predictable) and "
                                           f"in proportion to contrast ({s['ratio'] * 100:.0f}%): no sign of a colour grade."))
    if s["bars"] >= 0.15:
        side = "left/right (pillarbox)" if s["content_w"] < s["content_h"] else "top/bottom (letterbox)"
        out.append(Finding(code="VID_REFRAMED", module="video", family="framing", status=WARN, llr=1.4, reliability=0.7,
                           title=f"Picture placed on a larger canvas: {s['bars'] * 100:.0f}% of the frame is black bars",
                           data=data,
                           explanation=f"Constant black bars on the {side} surround the picture in every frame. Cameras "
                                       "and DVRs record the full sensor area; bars are added when a clip is exported into a "
                                       "different shape by editing software (e.g. a portrait clip placed in a 16:9 video).",
                           innocent=["DVR multi-camera layout or aspect-ratio correction during export"]))
    return out
