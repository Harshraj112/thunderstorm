"""Cross-modal consistency: lip movement vs speech, and audio/video stream alignment.

Lip-sync: the speaker's mouth-region motion is cross-correlated with the speech energy envelope.
In genuine footage they align (lag ≈ 0). Dubbed / re-voiced / audio-swapped clips show either a
constant offset or no consistent alignment; partial replacement shows the lag CHANGING mid-clip.
This is the classical precursor of SyncNet (Chung & Zisserman 2016); a SyncNet/LatentSync model can be
a natural next step (roadmap) for talking-head deepfakes.

Faces: see faces.py (YuNet → OpenCV cascade → NumPy cascade; no download required).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..base import Context, Finding, save_rgb

MAX_SECONDS = 120


def face_detector():
    from .faces import detector
    det, _ = detector()
    return det


def track_mouth(path: Path, fps: float, det) -> tuple[np.ndarray, np.ndarray, list]:
    """Per-frame mouth-motion energy (nan where no face) and face boxes on sampled frames.
    Motion is measured with the SAME box on the previous and current frame, so re-detection jitter
    does not create false motion."""
    from .faces import NumpyCascade
    every = 6 if isinstance(det, NumpyCascade) else 3
    cap = cv2.VideoCapture(str(path))
    motion, have, boxes = [], [], []
    prev_g, box, miss = None, None, 0
    i = 0
    while i < int(fps * MAX_SECONDS):
        ok, fr = cap.read()
        if not ok:
            break
        h, w = fr.shape[:2]
        s = 320 / w
        g = cv2.cvtColor(cv2.resize(fr, (320, int(h * s))), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if i % every == 0 or (box is None and i % 2 == 0):   # re-detect periodically, track in between
            faces = det.detectMultiScale(g.astype(np.uint8), 1.15, 3, minSize=(32, 32))
            if len(faces):
                nb = np.array(max(faces, key=lambda f: f[2] * f[3]), float)
                box = nb if box is None else 0.6 * box + 0.4 * nb       # smooth the track
                miss = 0
                if len(boxes) < 400:
                    boxes.append((i, tuple(int(v / s) for v in box)))
            else:
                miss += 1
                if miss > 2:
                    box = None
        val = np.nan
        if box is not None and prev_g is not None:
            x, y, bw, bh = (int(v) for v in box)
            ys, xs = slice(y + int(bh * 0.62), y + int(bh * 0.98)), slice(x + int(bw * 0.22), x + int(bw * 0.78))
            a_, b_ = g[ys, xs], prev_g[ys, xs]
            if a_.size > 20:
                val = float(np.mean(np.abs(a_ - b_)))
        motion.append(val)
        have.append(np.isfinite(val))
        prev_g = g
        i += 1
    cap.release()
    m = np.array(motion)
    if np.isfinite(m).any():
        m = np.minimum(m, np.nanpercentile(m, 99))
    return m, np.array(have), boxes


def audio_envelope(ctx: Context, fps: float, n: int) -> np.ndarray | None:
    """Smoothed log-energy of the audio track, one value per video frame, on the VIDEO timeline
    (stream start offsets are honoured by padding, so a delayed track really appears delayed)."""
    import subprocess
    from scipy.io import wavfile
    wav = ctx.work_dir / "_av16k.wav"
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(ctx.path), "-vn", "-ac", "1",
                        "-ar", "16000", "-af", "aresample=async=1:first_pts=0", str(wav)], check=True, timeout=600)
        _, x = wavfile.read(wav)
    except Exception:
        return None
    x = x.astype(np.float32) / 32768
    hop = 16000 / fps
    env = np.array([np.sqrt(np.mean(x[int(k * hop):int((k + 1) * hop)] ** 2) + 1e-9) if int(k * hop) < len(x) - 1
                    else np.nan for k in range(n)])
    env = np.log(env)
    env = np.where(np.isfinite(env), env, np.nanmedian(env))
    return np.convolve(env, np.ones(3) / 3, mode="same")


def robust_motion(m: np.ndarray) -> np.ndarray:
    """Clip shot-change / re-detection spikes (median + 4·MAD) and smooth over 3 frames."""
    med = np.nanmedian(m)
    mad = np.nanmedian(np.abs(m - med)) * 1.4826 + 1e-6
    m = np.minimum(m, med + 4 * mad)
    filled = np.where(np.isfinite(m), m, med)
    out = np.convolve(filled, np.ones(3) / 3, mode="same")
    return np.where(np.isfinite(m), out, np.nan)


def best_lag(a: np.ndarray, m: np.ndarray, max_lag: int) -> tuple[int, float, float]:
    """Return (lag in frames, peak correlation, sharpness = peak − median correlation)."""
    ok = np.isfinite(m) & np.isfinite(a)
    if ok.sum() < 30:
        return 0, 0.0, 0.0
    cs = []
    for lag in range(-max_lag, max_lag + 1):
        mm = np.roll(m, -lag)          # positive lag: audio leads video
        o = ok & np.roll(ok, -lag)
        if lag > 0:
            o[-lag:] = False
        elif lag < 0:
            o[:-lag] = False
        if o.sum() < 30:
            cs.append(0.0)
            continue
        x, y = a[o] - a[o].mean(), mm[o] - mm[o].mean()
        cs.append(float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-9)))
    cs = np.array(cs)
    k = int(np.argmax(cs))
    return k - max_lag, float(cs[k]), float(cs[k] - np.median(cs))


def run(ctx: Context) -> list[Finding]:
    info = ctx.shared.get("probe") or {}
    streams = info.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    au = next((s for s in streams if s.get("codec_type") == "audio"), None)
    out: list[Finding] = []
    if not vs or not au:
        return out
    # ---- stream alignment (container level) ----
    try:
        vd, ad = float(vs.get("duration") or 0), float(au.get("duration") or 0)
        vst, ast = float(vs.get("start_time") or 0), float(au.get("start_time") or 0)
        if vd and ad and abs(vd - ad) > 1.0:
            out.append(Finding(code="AV_DURATION", module="cross-modal", family="cross-modal",
                               title=f"Audio and video durations differ by {abs(vd - ad):.1f}s", status="warn",
                               llr=0.8, reliability=0.5,
                               explanation=f"Video stream {vd:.1f}s vs audio stream {ad:.1f}s. Editing one track "
                                           "(cutting or replacing audio) often leaves the tracks with unequal lengths.",
                               innocent=["Recorder started audio slightly later", "Trailing silence trimmed by encoder"]))
        if abs(vst - ast) > 0.3:
            out.append(Finding(code="AV_START", module="cross-modal", family="cross-modal",
                               title=f"Audio starts {ast - vst:+.2f}s relative to video", status="info",
                               llr=0.3, reliability=0.3, explanation="Container start times differ."))
    except (TypeError, ValueError):
        pass
    det = face_detector()
    if det is None:
        return out
    fps = ctx.shared.get("fps") or 25.0
    motion, have, boxes = track_mouth(ctx.path, fps, det)
    motion = robust_motion(motion)
    n = len(motion)
    if have.sum() < fps * 4:
        out.append(Finding(code="AV_LIPSYNC", module="cross-modal", family="cross-modal",
                           title="Lip-sync: no speaking face long enough", status="info", reliability=0.0,
                           explanation=f"A face was tracked in {have.sum() / fps:.1f}s of footage; at least 4 s is "
                                       "needed for audio-visual synchronisation analysis."))
        return out
    env = audio_envelope(ctx, fps, n)
    if env is None:
        return out
    max_lag = int(fps * 1.0)
    lag, corr, conf = best_lag(env, motion, max_lag)
    # windowed lags (8 s windows, 2 s hop) to catch partial re-voicing
    W, H = int(8 * fps), int(2 * fps)
    wins = []   # (start_s, lag_s, corr, sharpness)
    for s0 in range(0, max(1, n - W), H):
        sl = slice(s0, s0 + W)
        if have[sl].mean() > 0.6:
            l2, c2, f2 = best_lag(env[sl], motion[sl], max_lag)
            wins.append((s0 / fps, l2 / fps, c2, f2))
    art = _face_snapshot(ctx, boxes)
    arts = [art] if art else []
    ctx.shared["timeline_lipsync"] = {"t": [round(w[0] + 4, 1) for w in wins],
                                      "sync_corr": [round(w[2], 3) for w in wins]}
    lag_s = lag / fps
    TOL = 0.24
    trusted = [w for w in wins if w[3] >= 0.25 and w[2] >= 0.3]
    ref = float(np.median([w[1] for w in trusted[:max(2, len(trusted) // 2)]])) if trusted else lag_s

    def state(w):   # +1 agree, -1 disagree, 0 uncertain
        if w[2] < 0.25:
            return -1
        if w[3] < 0.25:
            return 0
        return 1 if abs(w[1] - ref) <= TOL else -1
    states = [state(w) for w in wins]
    agree = [w for w, st in zip(wins, states) if st == 1]
    # partial re-voicing: an early run of agreeing windows, then (ignoring uncertain ones) only disagreement
    break_at = None
    decided = [(w, st) for w, st in zip(wins, states) if st != 0]
    for i in range(2, len(decided) - 1):
        if all(st == 1 for _, st in decided[:i]) and all(st == -1 for _, st in decided[i:]) and len(decided) - i >= 2:
            break_at = decided[i - 1][0][0] + 8.0        # end of the last window where lips and speech agree
            break
    lags_t = np.array([w[1] for w in trusted])
    drift = float(np.subtract(*np.percentile(lags_t, [75, 25]))) if len(lags_t) >= 5 else 0.0
    near_overall = np.mean([abs(w[1] - lag_s) <= TOL for w in trusted]) if trusted else 0.0
    constant_offset = abs(lag_s) > 0.25 and near_overall >= 0.6 and corr >= 0.25

    if constant_offset:
        break_at = None
    if break_at is not None and len(agree) >= 2:
        early = np.mean([w[2] for w in agree])
        late = np.mean([w[2] for w, st in zip(wins, states) if st == -1])
        out.append(Finding(code="AV_LIPSYNC_BREAK", module="cross-modal", family="cross-modal",
                           title=f"Lips stop matching the speech after ≈{break_at:.0f}s", status="fail", llr=2.6,
                           reliability=0.7 if early >= 0.4 else 0.6, raw_score=break_at,
                           location={"time": round(break_at, 1),
                                     "windows": [[round(w[0], 1), round(w[1], 2), round(w[2], 2)] for w in wins]},
                           explanation=f"Mouth movement follows the speech closely at first (correlation {early:.2f}, "
                                       f"stable offset {ref * 1000:+.0f} ms) but after about {break_at:.0f}s the speech no "
                                       f"longer corresponds to the lips (correlation {late:.2f}, offset wandering). The "
                                       "words heard there were probably not spoken on camera – audio replaced or re-voiced.",
                           innocent=["Speaker turns away or covers the mouth", "A second, off-screen speaker starts talking"],
                           artifacts=arts))
    elif corr < 0.25 or conf < 0.1:
        out.append(Finding(code="AV_LIPSYNC", module="cross-modal", family="cross-modal",
                           title="Lip-sync: alignment undetermined", status="info", reliability=0.15, raw_score=conf,
                           explanation=f"Mouth motion and speech energy show no clear alignment (peak correlation "
                                       f"{corr:.2f}). This happens with off-screen speakers, music or low-resolution "
                                       "faces – and also with fully re-voiced footage.", artifacts=arts))
    elif constant_offset:
        out.append(Finding(code="AV_LIPSYNC", module="cross-modal", family="cross-modal",
                           title=f"Lip-sync mismatch: speech {abs(lag_s) * 1000:.0f} ms "
                                 f"{'ahead of' if lag_s > 0 else 'behind'} the lips",
                           # broadcast tolerance is about -60/+40 ms; >350 ms is far outside anything a single camera produces
                           status="fail", llr=2.6 if abs(lag_s) >= 0.35 else 1.8,
                           reliability=min(0.75, 0.35 + corr), raw_score=lag_s,
                           location={"offset_s": round(lag_s, 3)},
                           explanation=f"Across the whole clip the speech is consistently offset from the mouth movement "
                                       f"by {lag_s * 1000:+.0f} ms ({near_overall:.0%} of reliable windows agree). Genuine "
                                       "single-camera recordings stay within about ±100 ms; a constant larger offset "
                                       "indicates the audio track was replaced or re-laid.",
                           innocent=["Broadcast/stream encoder A/V delay", "External or Bluetooth microphone latency"],
                           artifacts=arts))
    elif drift > 0.25:
        out.append(Finding(code="AV_LIPSYNC", module="cross-modal", family="cross-modal",
                           title=f"Lip-sync offset varies by {drift * 1000:.0f} ms across the clip", status="warn",
                           llr=1.0, reliability=0.5, raw_score=drift,
                           explanation="The offset between speech and lips is not stable over time, which genuine "
                                       "recordings rarely show; parts of the audio may have been edited separately.",
                           innocent=["Variable-frame-rate phone recording", "Audio clock drift in cheap recorders"],
                           artifacts=arts))
    else:
        out.append(Finding(code="AV_LIPSYNC", module="cross-modal", family="cross-modal",
                           title=f"Lip movement consistent with speech (offset {lag_s * 1000:+.0f} ms)", status="pass",
                           llr=-0.7, reliability=min(0.7, 0.3 + corr), raw_score=lag_s,
                           explanation=f"Mouth motion of the tracked face aligns with speech energy (peak correlation "
                                       f"{corr:.2f}) with a stable offset across {len(agree)} reliable windows.",
                           artifacts=arts))
    return out


def _face_snapshot(ctx: Context, boxes: list) -> str | None:
    """Frame with the tracked face and the mouth region drawn, for the report."""
    if not boxes:
        return None
    cap = cv2.VideoCapture(str(ctx.path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, boxes[len(boxes) // 2][0])
    ok, fr = cap.read()
    cap.release()
    if not ok:
        return None
    x, y, bw, bh = boxes[len(boxes) // 2][1]
    cv2.rectangle(fr, (x, y), (x + bw, y + bh), (0, 220, 255), 2)
    cv2.rectangle(fr, (x + int(bw * 0.22), y + int(bh * 0.62)), (x + int(bw * 0.78), y + int(bh * 0.98)), (60, 60, 255), 2)
    return save_rgb(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), ctx.artifact("lipsync_face.png"))
