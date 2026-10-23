"""Pixel / motion forensics on decoded frames.

* frozen runs   : consecutive identical frames while the scene is otherwise moving
* loop inserts  : a block of moving frames that re-appears later (copy-paste to hide an event).
                  Candidates come from a perceptual hash; each is then VERIFIED at pixel level:
                  real cameras add fresh sensor noise to every frame, so a natural repeat of the same
                  scene never matches as closely as a copied frame does.
* discontinuity : spikes in frame-difference + optical-flow energy (cuts / inserts on a fixed camera)
"""
from __future__ import annotations

import cv2
import numpy as np

from ... import config
from ..base import Context, Finding, robust_z, status_from_llr

TINY = (96, 54)
KERNEL = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)


def _crop(g: np.ndarray) -> np.ndarray:
    """Central 80x120 crop at analysis resolution – keeps per-frame sensor noise for loop verification."""
    h, w = g.shape
    return g[max(0, h // 2 - 40):h // 2 + 40, max(0, w // 2 - 60):w // 2 + 60]


def _dhash(g: np.ndarray) -> int:
    s = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (s[:, 1:] > s[:, :-1]).flatten()
    return int(np.packbits(bits).view(">u8")[0])


def decode(ctx: Context) -> dict:
    cap = cv2.VideoCapture(str(ctx.path))
    fps = ctx.shared.get("fps") or cap.get(cv2.CAP_PROP_FPS) or 25.0
    W = config.VIDEO_ANALYSIS_WIDTH
    hashes, diffs, flows, times, tiny, crops = [], [], [], [], [], []
    prev = prev_small = None
    frames_meta = ctx.shared.get("frames") or []
    i = 0
    while i < config.VIDEO_MAX_FRAMES:
        ok, fr = cap.read()
        if not ok:
            break
        h, w = fr.shape[:2]
        g = cv2.cvtColor(cv2.resize(fr, (W, int(h * W / w))), cv2.COLOR_BGR2GRAY)
        small = cv2.resize(g, (W // 2, int(h * W / w) // 2))
        hashes.append(_dhash(g))
        tiny.append(cv2.resize(g, TINY, interpolation=cv2.INTER_AREA))
        crops.append(_crop(g).copy())
        if prev is None:
            diffs.append(0.0)
            flows.append(0.0)
        else:
            diffs.append(float(np.mean(np.abs(g.astype(np.int16) - prev.astype(np.int16)))))
            fl = cv2.calcOpticalFlowFarneback(prev_small, small, None, 0.5, 2, 9, 2, 5, 1.1, 0)
            flows.append(float(np.mean(np.hypot(fl[..., 0], fl[..., 1]))))
        times.append(frames_meta[i]["t"] if i < len(frames_meta) else i / fps)
        prev, prev_small = g, small
        i += 1
        if i % 500 == 0:
            ctx.progress(f"decoded {i} frames")
    cap.release()
    res = {"hash": hashes, "tiny": np.array(tiny, dtype=np.int16), "crop": np.array(crops, dtype=np.int16),
           "diff": np.array(diffs),
           "flow": np.array(flows), "t": np.array(times), "fps": fps}
    ctx.shared["pixels"] = res
    return res


def _runs(mask: np.ndarray, min_len: int, max_gap: int = 2) -> list[tuple[int, int]]:
    """Runs of True allowing short interruptions (I-frame requantisation breaks exact equality)."""
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return []
    runs, s, last = [], idx[0], idx[0]
    for k in idx[1:]:
        if k - last > max_gap + 1:
            if last - s + 1 >= min_len:
                runs.append((int(s), int(last)))
            s = k
        last = k
    if last - s + 1 >= min_len:
        runs.append((int(s), int(last)))
    return runs


def run(ctx: Context) -> list[Finding]:
    px = decode(ctx)
    n = len(px["hash"])
    out: list[Finding] = []
    if n < 10:
        return out
    diff, flow, t, fps, tiny = px["diff"], px["flow"], px["t"], px["fps"], px["tiny"]
    duration_all = float(t[-1] - t[0]) or 1.0
    # Native frame rate: how often a genuinely NEW picture appears. DVRs often record at 1-8 fps and exports
    # repeat each picture to reach 25/30 fps; then most "frames" are duplicates by design.
    p95 = float(np.percentile(diff[1:], 95))
    change = diff > max(0.15, 0.25 * p95)          # a genuinely new picture, not re-encoding noise on a repeat
    change[0] = False
    ch_idx = np.nonzero(change)[0]
    native_fps = len(ch_idx) / duration_all
    ch_gaps = np.diff(t[ch_idx]) if len(ch_idx) > 3 else np.array([])
    low_native_fps = bool(len(ch_gaps) >= 5 and native_fps < 0.6 * fps and
                          float(np.std(ch_gaps) / (np.mean(ch_gaps) + 1e-9)) < 0.75)
    ctx.shared["low_native_fps"] = low_native_fps
    moving_level = float(np.median(diff[1:]))
    static_scene = moving_level < 0.3 and not low_native_fps

    # ---- frozen runs (identical consecutive frames in a moving scene) ----
    if low_native_fps:
        # only a stretch with no new picture far longer than the normal repeat interval can be padding
        frozen = ~change
        frozen[0] = False
        min_run = max(int(3 * float(np.median(ch_gaps)) * fps), int(2 * fps))
        runs = [(a - 1, b) for a, b in _runs(frozen, min_run, max_gap=0)]
    else:
        frozen = diff < max(0.15, 0.05 * moving_level)
        frozen[0] = False
        runs = [(a - 1, b) for a, b in _runs(frozen, max(3, int(fps * 0.5)))]
    frozen_mask = np.zeros(n, bool)
    for a, b in runs:
        frozen_mask[a:b + 1] = True
    frame_dropping = False
    if runs and not low_native_fps:
        lens = np.array([t[b] - t[a] for a, b in runs])
        med_len = float(np.median(lens))
        # Many short repeats spread over the whole clip = the recorder dropping frames under load (normal on
        # DVRs). Padding over a deletion is a long freeze that stands out from the rest.
        if len(runs) >= 8 and lens.sum() / duration_all > 0.2 and med_len < 1.5:
            frame_dropping = True
            out.append(Finding(code="VID_FRAME_DROPPING", module="video", family="temporal-pixel", status="info",
                               reliability=0.0,
                               title=f"Recorder dropped frames throughout ({len(runs)} short repeats, typical {med_len:.1f} s)",
                               location={"segments": [{"start": round(float(t[a]), 2), "end": round(float(t[b]), 2)}
                                                      for a, b in runs[:30]]},
                               explanation="Short runs of repeated frames occur all through the clip. That is how a DVR "
                                           "under load or with motion-based recording behaves, so these repeats are not "
                                           "treated as padding. Only a freeze much longer than the others would be."))
            runs = [r for r, ln in zip(runs, lens) if ln >= max(2.0, 3 * med_len)]
    ctx.shared["frame_dropping"] = frame_dropping
    ctx.shared["frozen_runs"] = [(float(t[a]), float(t[b])) for a, b in runs]
    if low_native_fps:
        out.append(Finding(code="VID_LOW_FPS", module="video", family="temporal-pixel", status="info",
                           title=f"Recorded at ≈{native_fps:.1f} pictures/s, stored at {fps:.0f} fps",
                           reliability=0.0,
                           explanation=f"A new picture appears every {float(np.median(ch_gaps)):.2f} s on average, and "
                                       "the frames in between repeat it. This is how a low-frame-rate DVR recording looks "
                                       "after conversion to a standard frame rate, so repeated frames are not treated as "
                                       "padding; only a freeze much longer than this interval would be."))
    if runs and not static_scene:
        dur = sum(t[b] - t[a] for a, b in runs)
        llr = float(min(2.2, 1.2 + 0.4 * len(runs)))
        out.append(Finding(code="VID_FROZEN", module="video", family="temporal-pixel",
                           title=f"{len(runs)} frozen segment(s), {dur:.1f}s of identical frames",
                           status=status_from_llr(llr), llr=llr, reliability=0.65,
                           location={"segments": [{"start": round(float(t[a]), 2), "end": round(float(t[b]), 2)}
                                                  for a, b in runs[:30]]},
                           explanation="Consecutive frames are pixel-identical while the rest of the footage shows "
                                       "motion and sensor noise. Frame duplication is used to pad over removed footage.",
                           innocent=["Recorder freezing under load", "Genuinely static scene with noise reduction"]))

    # ---- loop inserts ----
    # Sensor-noise level (Immerkaer) on the full-resolution crop. Two frames of identical content but
    # independent noise differ by ~1.13*sigma on average; a copied frame differs only by re-encoding error.
    crop = px["crop"]
    sig = [float(np.abs(cv2.filter2D(c.astype(np.float32), -1, KERNEL)).mean() * np.sqrt(np.pi / 2) / 6)
           for c in crop[:: max(1, n // 50)]]
    sigma = float(np.median(sig))
    noise_floor = 1.128 * sigma
    loop_thr = max(0.4 * noise_floor, 0.6)
    pos: dict[int, list[int]] = {}
    for i, h in enumerate(px["hash"]):
        pos.setdefault(h, []).append(i)
    min_off, min_len = int(fps * 2), max(5, int(fps))
    offsets: dict[int, int] = {}
    for idxs in pos.values():
        if len(idxs) > 1:
            for a in idxs:
                for b in idxs:
                    if b - a > min_off:
                        offsets[b - a] = offsets.get(b - a, 0) + 1
    loops = []
    reliability_loop = 0.8 if sigma > 1.5 else 0.45   # noiseless (heavily denoised) video: weaker test
    for off, cnt in sorted(offsets.items(), key=lambda kv: -kv[1])[:40]:
        if cnt < min_len // 2:
            continue
        d = np.abs(crop[off:] - crop[:-off]).mean(axis=(1, 2))      # frame i vs frame i+off, full-res noise
        match = d < loop_thr
        for a, b in _runs(match, min_len, max_gap=0):
            moving = (flow[a + 1:b + 1].mean() > 0.05 and flow[a + off + 1:min(b + off + 1, n)].mean() > 0.05
                      and frozen_mask[a:b + 1].mean() < 0.3 and frozen_mask[a + off:min(b + off + 1, n)].mean() < 0.3)
            if moving and not any(a <= la + 2 and lo == off for la, _, lo in loops):
                loops.append((a, b, off))
    if loops:
        loops.sort(key=lambda x: -(x[1] - x[0]))
        segs = [{"source_start": round(float(t[a]), 2), "source_end": round(float(t[b]), 2),
                 "repeat_start": round(float(t[a + off]), 2), "repeat_end": round(float(t[min(b + off, n - 1)]), 2)}
                for a, b, off in loops[:10]]
        ctx.shared["loops"] = segs
        out.append(Finding(code="VID_LOOP", module="video", family="temporal-pixel",
                           title=f"Repeated footage: {segs[0]['source_start']}–{segs[0]['source_end']}s re-appears at "
                                 f"{segs[0]['repeat_start']}s" + (f" (+{len(loops) - 1} more)" if len(loops) > 1 else ""),
                           status="fail", llr=2.6, reliability=reliability_loop, location={"segments": segs},
                           explanation="A run of moving frames re-appears later, matching even in sensor-noise detail "
                                       f"(mean difference below {loop_thr:.2f} grey levels, versus ≈{noise_floor:.2f} expected "
                                       "between two genuine captures of the same scene). Real cameras never record "
                                       "identical noise twice, so the "
                                       "later segment is a copy – typically pasted to hide an event.",
                           innocent=["Video already contained an intentional replay (e.g. compiled footage)"]))
    else:
        out.append(Finding(code="VID_LOOP", module="video", family="temporal-pixel", title="No repeated footage",
                           status="pass", llr=-0.4, reliability=0.6,
                           explanation="No stretch of frames re-appears with matching sensor noise."))

    # ---- discontinuities ----
    energy = robust_z(diff) + 0.5 * robust_z(flow)
    cand = [int(i) for i in np.nonzero(energy > 8)[0] if i > 1]
    merged: list[int] = []
    for i in cand:
        if not merged or i - merged[-1] > fps:
            merged.append(i)
    ctx.shared["discontinuities"] = [float(t[i]) for i in merged]
    gaps = np.diff([t[i] for i in merged]) if len(merged) > 2 else np.array([])
    periodic = len(gaps) >= 3 and np.std(gaps) / (np.mean(gaps) + 1e-9) < 0.25
    duration = float(t[-1] - t[0]) or 1.0
    if merged and not periodic and not low_native_fps and not frame_dropping and len(merged) <= max(3, duration / 30):
        out.append(Finding(code="VID_DISCONTINUITY", module="video", family="temporal-pixel",
                           title=f"{len(merged)} abrupt visual discontinuity(ies)", status="warn",
                           llr=float(min(1.2, 0.6 + 0.2 * len(merged))), reliability=0.45 if not static_scene else 0.6,
                           location={"times": [round(float(t[i]), 2) for i in merged]},
                           explanation="Frame-difference and optical-flow energy spike far above normal at these "
                                       "instants. On a fixed CCTV camera this usually marks a cut or an insert.",
                           innocent=["Lights switched on/off, IR mode change", "Large object passing close to the lens"]))
    elif merged:
        out.append(Finding(code="VID_DISCONTINUITY", module="video", family="temporal-pixel",
                           title=f"{len(merged)} scene changes ({'periodic' if periodic else 'frequent'})", status="info",
                           reliability=0.1, location={"times": [round(float(t[i]), 2) for i in merged[:50]]},
                           explanation="Visual jumps are regular or frequent, i.e. a property of the scene/camera "
                                       "(PTZ tour, flashing lights), so they are not treated as splice indicators."))
    step = max(1, n // 3000)
    ctx.shared["timeline_pixels"] = {"t": [round(float(x), 3) for x in t[::step]],
                                     "diff": [round(float(x), 3) for x in diff[::step]],
                                     "flow": [round(float(x), 3) for x in flow[::step]]}
    return out
