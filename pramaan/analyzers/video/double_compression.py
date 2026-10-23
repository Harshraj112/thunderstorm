"""Video double compression & frame deletion from the prediction footprint.

Principle (Wang & Farid 2006; Vázquez-Padín et al., G-VPF, IEEE TIFS 2020): when an H.264/MPEG video
is decoded and re-encoded with a different GOP, frames that were I-frames in the FIRST encoding are
encoded as P-frames the second time, and their prediction residual (hence their bit cost) jumps.
The P-frame size sequence therefore shows spikes with the period of the ORIGINAL GOP.

* periodic spikes with period T ≠ current GOP      -> the file was re-encoded (not a native DVR export)
* the spike phase shifts at frame k                -> frames were deleted or inserted near k, and
                                                      (phase shift mod T) estimates how many.

We use per-frame packet sizes from ffprobe as the residual proxy (no patched decoder needed). The
full G-VPF uses macroblock-type statistics from a patched decoder (roadmap) for stronger results.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter

from ..base import Context, Finding, robust_z, status_from_llr


def _ratios(frames: list[dict]) -> tuple[np.ndarray, int]:
    """log(frame bits / local median of same-type frames) for P and B frames; NaN for key-frames."""
    sizes = np.array([f["size"] for f in frames], float)
    types = np.array([f["type"] for f in frames])
    keys = np.array([bool(f["key"]) for f in frames]) | (types == "I")
    iidx = np.nonzero(keys)[0]
    gop = int(np.bincount(np.diff(iidx)).argmax()) if len(iidx) > 2 else 0
    r = np.full(len(frames), np.nan)
    for ty in ("P", "B"):
        idx = np.nonzero((types == ty) & ~keys)[0]
        if len(idx) >= 10:
            base = median_filter(sizes[idx], size=9, mode="nearest")
            r[idx] = np.log((sizes[idx] + 1) / (base + 1))
    return r, gop


def _profile(r: np.ndarray, start: int, end: int, T: int) -> np.ndarray:
    k = np.arange(start, end)
    seg = r[start:end]
    prof = np.full(T, np.nan)
    for ph in range(T):
        v = seg[(k % T) == ph]
        v = v[np.isfinite(v)]
        if len(v):
            prof[ph] = v.mean()
    return prof


def _windows(n: int, T: int) -> list[tuple[int, int]]:
    W = max(4 * T, 60)
    return [(s, min(n, s + W)) for s in range(0, max(1, n - W // 2), W // 2) if min(n, s + W) - s >= 3 * T]


def _score_T(r: np.ndarray, T: int):
    res = []
    for s, e in _windows(len(r), T):
        prof = _profile(r, s, e, T)
        if np.isnan(prof).all():
            continue
        ph = int(np.nanargmax(prof))
        others = np.delete(prof, [ph, (ph - 1) % T, (ph + 1) % T])
        height = float(prof[ph] - np.nanmedian(others))
        res.append((s, e, ph, height, height / (np.nanstd(others) + 0.02)))
    if len(res) < 6:   # need enough windows for phase consistency to mean something
        return None
    contrast = float(np.median([x[4] for x in res])) / np.sqrt(2 * np.log(T))
    # a genuine footprint keeps the same phase from window to window (until a cut shifts it)
    tol = max(1, round(T * 0.06))
    cons = float(np.mean([_circ(res[i][2], res[i + 1][2], T) <= tol for i in range(len(res) - 1)]))
    height = float(np.median([x[3] for x in res]))
    return contrast * (0.4 + cons), height, cons, res


def _period(r: np.ndarray, gop: int):
    """Earlier-GOP period T. Returns (T, score, height, consistency, windows)."""
    n = len(r)
    scored = {}
    for T in range(6, max(7, min(300, n // 8))):
        if gop and (T % gop == 0 or gop % T == 0 or abs(T - gop) <= 1):
            continue
        v = _score_T(r, T)
        if v and v[1] > 0.2:
            scored[T] = v
    if not scored:
        return None, 0.0, 0.0, 0.0, []
    T = max(scored, key=lambda k: scored[k][0])
    # harmonics: a footprint with period P also peaks at 2P, 3P… – fold back to the smallest good divisor
    for d in sorted(x for x in scored if T % x == 0 and x < T):
        if scored[d][0] >= 0.6 * scored[T][0]:
            T = d
            break
    sc, h, cons, res = scored[T]
    return T, sc, h, cons, res


def _circ(a: int, b: int, T: int) -> int:
    x = abs(a - b) % T
    return min(x, T - x)


def run(ctx: Context) -> list[Finding]:
    frames = ctx.shared.get("frames") or []
    if len(frames) < 60:
        return []
    r, gop = _ratios(frames)
    n = len(frames)
    T, score, height, cons, wins = _period(r, gop)
    t = np.array([f["t"] for f in frames])
    step = max(1, n // 3000)
    ctx.shared["timeline_vpf"] = {"t": [round(float(x), 2) for x in t[::step]],
                                  "bit_ratio": [round(float(np.nan_to_num(v)), 3) for v in r[::step]]}
    if T is None or score < 0.9 or height < 0.3 or cons < 0.5:
        return [Finding(code="VID_DOUBLE_COMP", module="video", family="temporal-container",
                        title="No double-compression footprint", status="pass", llr=-0.3, reliability=0.45,
                        explanation=f"Frame bit costs show no periodic excess besides the current GOP ({gop}); "
                                    "consistent with a single (native) encoding.")]
    on = [w for w in wins if w[4] >= 2.0]
    out = [Finding(code="VID_DOUBLE_COMP", module="video", family="temporal-container",
                   title=f"Re-encoded video: earlier GOP ≈ {T} frames (current {gop})", status="warn",
                   llr=0.9, reliability=0.6, raw_score=score,
                   location={"previous_gop": T, "current_gop": gop, "windows": len(on)},
                   explanation=f"Every {T} frames, one frame costs far more bits than its neighbours (contrast "
                               f"{score:.1f}σ across {len(wins)} windows). "
                               "Those were key-frames of an earlier encoding, so this file was decoded and re-encoded "
                               "after recording. A native DVR export is normally encoded once.",
                   innocent=["Format conversion by the DVR vendor's export/backup tool",
                             "Transcoding for playback by police or a social-media platform"])]
    br = None
    phases = [(w[0], w[1], w[2]) for w in wins if w[4] >= 2.0]
    tol = max(1, round(T * 0.06))
    for i in range(2, len(phases)):
        p0 = int(np.bincount([p[2] for p in phases[:i]], minlength=T).argmax())   # stable phase before
        p1 = phases[i][2]
        later = [p[2] for p in phases[i:]]
        if (_circ(p0, p1, T) > tol and sum(_circ(x, p1, T) <= tol for x in later) >= max(2, len(later) * 0.7)
                and sum(_circ(p[2], p0, T) <= tol for p in phases[:i]) >= max(2, i * 0.7)):
            k = (phases[i - 1][1] + phases[i][0]) // 2      # between the windows that disagree
            br = (min(k, n - 1), (p1 - p0) % T)
            break
    if br:
        k, d = br
        removed = (T - d) % T   # deleting m frames moves later spikes m frames earlier in the cycle
        out.append(Finding(
            code="VID_VPF_DELETION", module="video", family="temporal-container",
            title=f"Prediction-footprint phase shift near {t[k]:.1f}s: ≈{removed} (+n×{T}) frames removed or inserted",
            status="fail", llr=1.8, reliability=0.6, raw_score=float(d),
            location={"time": round(float(t[k]), 2), "frames_mod_gop": removed, "previous_gop": T},
            explanation=f"Before {t[k]:.1f}s the earlier encoding's key-frames fall on one phase of the {T}-frame "
                        f"lattice; after it they fall on another (shift {d}). Removing or inserting frames between "
                        f"two encodings shifts this lattice – the classic frame-deletion trace ({removed} mod {T} frames).",
            innocent=["The original recording itself restarted (new segment) at this point"]))
    return out
