"""Acoustic-environment consistency and editing traces.

* background-noise floor / spectrum steps   -> segment from another room or another recording
* digital silence (exact zeros)             -> material removed and gap zero-filled by an editor
* bandwidth vs sample rate                  -> upsampled content (narrowband/TTS output re-wrapped)
* container tags                             -> exported by an audio editor
"""
from __future__ import annotations

import json

import numpy as np
from scipy import signal

from ..base import Context, Finding, status_from_llr
from .io import decode, frame_energy_db

EDITORS = ["audacity", "audition", "garageband", "logic pro", "reaper", "fl studio", "wavepad", "ocenaudio",
           "lexis", "elevenlabs", "resemble", "murf", "descript", "adobe podcast", "kinemaster", "vn ", "capcut"]


def run(ctx: Context) -> list[Finding]:
    x, sr = decode(ctx.path, ctx.work_dir)
    ctx.shared["audio_native"] = (x, sr)
    out: list[Finding] = []
    dur = len(x) / sr
    out.append(Finding(code="AUD_SUMMARY", module="audio", family="metadata", title="Audio summary", status="info",
                       explanation=f"{dur:.1f}s, {sr} Hz mono-mixed, peak {20 * np.log10(np.abs(x).max() + 1e-9):.1f} dBFS"))
    out += _tags(ctx)                      # also loads ctx.shared["probe"]
    out += _digital_silence(x, sr)
    codec = next((st.get("codec_name") for st in (ctx.shared.get("probe") or {}).get("streams", [])
                  if st.get("codec_type") == "audio"), "")
    out += _bandwidth(x, sr, lossy=codec in ("mp3", "aac", "opus", "vorbis", "amr_nb", "amr_wb", "wmav2"))
    out += _noise_floor(ctx, x, sr)
    out += _studio_clean(x, sr)
    return out


def _studio_clean(x, sr) -> list[Finding]:
    """Speech with near-silent gaps (no room noise at all between words). Every real room, street, phone call or
    CCTV microphone records a background; generated (TTS/cloned) speech and studio/noise-gated audio do not."""
    import numpy as np
    if x.ndim > 1:
        x = x.mean(axis=1)
    fr, hop = int(0.025 * sr), int(0.01 * sr)
    n = (len(x) - fr) // hop
    if n < 300:
        return []
    e = np.array([np.mean(x[i * hop:i * hop + fr].astype(np.float64) ** 2) for i in range(n)]) + 1e-12
    db = 10 * np.log10(e)
    p10, p95 = np.percentile(db, [10, 95])
    gaps = float((db < p95 - 35).mean())
    rng = float(p95 - p10)
    if rng >= 40 and gaps >= 0.12 and p10 < -55:
        return [Finding(code="AUD_STUDIO_CLEAN", module="audio", family="synthetic-voice", status="warn",
                        title=f"Studio-clean gaps: {rng:.0f} dB between speech and silence, no room noise",
                        llr=0.9, reliability=0.5, raw_score=rng,
                        explanation=f"In {gaps:.0%} of the recording the level falls to {p10:.0f} dBFS, i.e. there is no "
                                    "background (room tone, traffic, hum, hiss) between words. Phone calls, room, street and "
                                    "CCTV recordings always carry one. This is typical of AI-generated (TTS / cloned) speech, "
                                    "and also of studio recordings and apps with aggressive noise suppression.",
                        innocent=["Studio or podcast recording", "Noise suppression / noise gate in the recording app"])]
    return []


def _tags(ctx: Context) -> list[Finding]:
    info = ctx.shared.get("probe")
    if info is None:
        from ..video.probe import probe
        try:
            info = probe(ctx.path)
            ctx.shared["probe"] = info
        except Exception:
            return []
    blob = json.dumps(info.get("format", {}).get("tags", {})).lower() + json.dumps(
        [s.get("tags", {}) for s in info.get("streams", [])]).lower()
    raw = ctx.path.read_bytes()[:4096].lower()
    hits = [e for e in EDITORS if e in blob or e.encode() in raw]
    if hits:
        return [Finding(code="AUD_EDITOR_TAG", module="audio", family="metadata",
                        title=f"Audio editing/synthesis software tag: {', '.join(hits)}", status="fail", llr=1.6,
                        reliability=0.75, explanation="The file's metadata names audio-editing or voice-synthesis "
                        "software, so it is not a native recorder output.",
                        innocent=["Format conversion or loudness normalisation for playback"])]
    return []


def _digital_silence(x: np.ndarray, sr: int) -> list[Finding]:
    zero = np.abs(x) < 1.5 / 32768
    min_len = int(0.05 * sr)
    edges = np.diff(np.concatenate([[0], zero.astype(np.int8), [0]]))
    starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
    segs = [(s / sr, e / sr) for s, e in zip(starts, ends) if e - s >= min_len]
    if not segs:
        return []
    floor = np.percentile(frame_energy_db(x[~zero] if (~zero).any() else x, sr), 10)
    if floor < -85:  # recording is itself digitally clean (e.g. studio/gated) – zeros are expected
        return []
    segs = [s for s in segs if s[0] > 0.05 and s[1] < len(x) / sr - 0.05]  # leading/trailing silence is normal
    if not segs:
        return []
    return [Finding(code="AUD_DIGITAL_SILENCE", module="audio", family="acoustic",
                    title=f"{len(segs)} stretch(es) of exact digital silence", status="fail",
                    llr=float(min(2.8, 2.2 + 0.3 * len(segs))), reliability=0.75,
                    location={"segments": [{"start": round(a, 2), "end": round(b, 2)} for a, b in segs[:20]]},
                    explanation=f"The recording has a background noise floor of ≈{floor:.0f} dBFS, yet contains "
                                "stretches of samples that are exactly zero. A microphone always records some noise; "
                                "exact zeros are written by editing software when audio is deleted or muted, and by "
                                "speech-synthesis (TTS / voice-clone) engines that pad sentences with pure silence.",
                    innocent=["Recorder's automatic gain/noise gate (check device model)",
                              "Noise suppression in a messaging/VoIP app muting pauses",
                              "Dropouts in a VoIP/phone-call recording"])]


def _bandwidth(x: np.ndarray, sr: int, lossy: bool = False) -> list[Finding]:
    if sr < 32000 or len(x) < sr:
        return []
    f, p = signal.welch(x, sr, nperseg=4096)
    pdb = 10 * np.log10(p + 1e-20)
    ref = np.percentile(pdb[(f > 300) & (f < 3000)], 90)
    above = f[pdb > ref - 60]
    cutoff = float(above.max()) if len(above) else 0.0
    if cutoff < 0.3 * sr:
        typical = min((8000, 11025, 12000, 16000), key=lambda c: abs(c - cutoff))
        return [Finding(code="AUD_BANDWIDTH", module="audio", family="acoustic",
                        title=f"Content band-limited to ≈{cutoff / 1000:.1f} kHz in a {sr / 1000:.1f} kHz file",
                        status="warn" if not lossy else "info", llr=0.8 if not lossy else 0.2,
                        reliability=0.5 if not lossy else 0.2, raw_score=cutoff,
                        explanation=f"Energy stops near {cutoff / 1000:.1f} kHz although the file could carry up to "
                                    f"{sr / 2000:.1f} kHz. The audio was generated or captured at ≈{2 * typical / 1000:.0f} kHz "
                                    "and upsampled – common for TTS/voice-clone output and for phone-call audio.",
                        innocent=["Genuine telephone / VoIP call recording", "Low-bitrate codec low-pass"] +
                                 (["This file is lossy-compressed; MP3/AAC encoders low-pass by themselves"] if lossy else []))]
    return []


def _noise_floor(ctx: Context, x: np.ndarray, sr: int) -> list[Finding]:
    """Track the background floor (10th percentile energy in 1 s windows) and the spectral shape of the
    quietest frames; look for a persistent step between the 3 s before and after each point."""
    e = frame_energy_db(x, sr, 0.05)            # 20 frames / s
    if len(e) < 20 * 10:
        return []
    per = 20
    k = len(e) // per
    floor = np.array([np.percentile(e[i * per:(i + 1) * per], 10) for i in range(k)])
    W = 3
    steps = np.zeros(k)
    for i in range(W, k - W):
        L, R = floor[i - W:i], floor[i:i + W]
        spread = max(np.std(L), np.std(R), 0.75)
        steps[i] = (np.median(R) - np.median(L)) / spread
    ctx.shared["timeline_audio"] = {"t": list(range(k)), "noise_floor_db": [round(float(v), 2) for v in floor]}
    cand = [i for i in range(W, k - W) if abs(steps[i]) > 4 and abs(np.median(floor[i:i + W]) - np.median(floor[i - W:i])) > 4]
    merged: list[int] = []
    for i in cand:
        if not merged or i - merged[-1] > 2:
            merged.append(i)
        elif abs(steps[i]) > abs(steps[merged[-1]]):
            merged[-1] = i
    if not merged:
        return [Finding(code="AUD_NOISE_FLOOR", module="audio", family="acoustic",
                        title="Background noise floor stable", status="pass", llr=-0.4, reliability=0.5,
                        explanation=f"Background level stays within ±{np.std(floor):.1f} dB with no abrupt steps.")]
    times = [float(i) for i in merged]
    deltas = [float(np.median(floor[i:i + W]) - np.median(floor[i - W:i])) for i in merged]
    llr = float(min(2.0, 1.0 + 0.4 * len(merged)))
    return [Finding(code="AUD_NOISE_FLOOR", module="audio", family="acoustic",
                    title=f"Background noise changes abruptly at {', '.join(f'{t:.0f}s' for t in times[:6])}",
                    status=status_from_llr(llr), llr=llr, reliability=0.6,
                    location={"times": times, "delta_db": [round(d, 1) for d in deltas]},
                    explanation="The room/background noise level steps by "
                                + ", ".join(f"{d:+.0f} dB" for d in deltas[:6])
                                + " and stays there. A different acoustic environment suggests material from another "
                                "recording was inserted at these points.",
                    innocent=["Fan/AC switched on or off", "Speaker moved closer to a noise source",
                              "Automatic gain control reacting to loud speech"])]
