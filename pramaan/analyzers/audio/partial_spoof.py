"""Partial-spoof localisation: find WHICH words/segments of a real recording were replaced by a
synthetic voice (the realistic attack: one cloned sentence inside a genuine phone call).

Model: frame-level classifier on LFCC frames with ±0.25 s context statistics, trained on the
PartialSpoof database (Zhang et al., IEEE/ACM TASLP 2023; segment labels at 20 ms resolution) with
scripts/train_partialspoof.py -> models/partialspoof_frame.npz. Output is a per-20 ms spoof
probability curve, smoothed and converted into time segments for the timeline and the report.
A neural multi-resolution model (PartialSpoof's own SSL-based CM) can replace it via
PRAMAAN_PARTIALSPOOF_CMD (prints one probability per 20 ms frame).
"""
from __future__ import annotations

import os
import shlex
import subprocess

import numpy as np
from scipy.ndimage import uniform_filter1d

from ... import config
from ..base import Context, Finding, calibrated, status_from_llr, unavailable
from .spoof import SR, lfcc

FRAME_S = 0.02        # 20 ms output resolution (PartialSpoof segment-label resolution)
CTX = 12              # ±12 LFCC frames (10 ms hop) ≈ ±0.12 s context


def frame_features(x: np.ndarray) -> np.ndarray:
    """LFCC (10 ms hop) -> context mean/std features at 20 ms resolution."""
    F = lfcc(x)                                       # (n10ms, 60)
    mu = uniform_filter1d(F, size=2 * CTX + 1, axis=0, mode="nearest")
    sq = uniform_filter1d(F ** 2, size=2 * CTX + 1, axis=0, mode="nearest")
    sd = np.sqrt(np.clip(sq - mu ** 2, 0, None))
    feats = np.concatenate([F, mu, sd], axis=1)
    return feats[::2]                                 # 20 ms


def segments(prob: np.ndarray, thr: float = 0.5, min_len: float = 0.3) -> list[tuple[float, float, float]]:
    on = prob > thr
    segs, s = [], None
    for i, v in enumerate(np.append(on, False)):
        if v and s is None:
            s = i
        if not v and s is not None:
            if (i - s) * FRAME_S >= min_len:
                segs.append((s * FRAME_S, i * FRAME_S, float(prob[s:i].mean())))
            s = None
    return segs


def run(ctx: Context) -> list[Finding]:
    from .io import decode
    model = config.MODELS_DIR / "partialspoof_frame.npz"
    cmd = os.environ.get("PRAMAAN_PARTIALSPOOF_CMD")
    if not model.exists() and not cmd:
        return [unavailable("AUD_PARTIAL_SPOOF", "audio", "synthetic-voice", "Partial-spoof (segment) localiser",
                            "No partial-spoof model installed. Train with scripts/train_partialspoof.py on the "
                            "PartialSpoof database. ENF and noise-floor checks still localise edits.")]
    x, _ = decode(ctx.path, ctx.work_dir, SR)
    if len(x) < SR * 2:
        return []
    if cmd:
        wav = ctx.work_dir / "_ps16k.wav"
        from scipy.io import wavfile
        wavfile.write(wav, SR, (np.clip(x, -1, 1) * 32767).astype(np.int16))
        r = subprocess.run(shlex.split(cmd.format(wav=wav)), capture_output=True, text=True, check=True, timeout=3600)
        prob = np.array([float(v) for v in r.stdout.split()])
        backend = "external multi-resolution CM"
    else:
        p = np.load(model)
        X = (frame_features(x) - p["mu"]) / p["sd"]
        prob = 1 / (1 + np.exp(-(X @ p["w"] + p["b"])))
        backend = "LFCC frame-level classifier"
    prob = uniform_filter1d(prob, size=15, mode="nearest")          # 0.3 s smoothing
    # only speech-active frames count
    e = np.array([np.sqrt(np.mean(x[int(i * FRAME_S * SR):int((i + 1) * FRAME_S * SR)] ** 2) + 1e-12)
                  for i in range(len(prob))])
    active = e > max(np.percentile(e, 30), 1e-3)
    prob = np.where(active, prob, 0.0)
    segs = segments(prob)
    step = max(1, len(prob) // 3000)
    ctx.shared["timeline_partialspoof"] = {"t": [round(i * FRAME_S, 2) for i in range(0, len(prob), step)],
                                           "spoof_prob": [round(float(v), 3) for v in prob[::step]]}
    frac = float(sum(b - a for a, b, _ in segs) / max(active.sum() * FRAME_S, 1e-6))
    raw = max((s[2] for s in segs), default=float(np.percentile(prob[active], 95)) if active.any() else 0.0)
    llr = calibrated("partial_spoof", raw, a=6.0, b=-3.5, cap=2.5)
    if segs and 0 < frac < 0.8:
        title = f"{len(segs)} segment(s) look synthetic inside otherwise natural speech"
    elif segs:
        title = "Most of the speech looks synthetic"
    else:
        title = "No synthetic segments localised"
    return [Finding(code="AUD_PARTIAL_SPOOF", module="audio", family="synthetic-voice", title=title,
                    status=status_from_llr(llr), llr=llr, reliability=0.45, raw_score=raw,
                    location={"segments": [{"start": round(a, 2), "end": round(b, 2), "p": round(c, 2)} for a, b, c in segs[:20]]},
                    explanation=f"{backend} scored every 20 ms of active speech. Segments above 0.5 for at least "
                                f"0.3 s are listed ({frac:.0%} of speech). Check them against ENF and noise-floor breaks.",
                    innocent=["Codec artefacts (VoIP, WhatsApp voice notes)", "Languages/speakers unseen in training"])]
