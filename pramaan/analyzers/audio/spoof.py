"""Synthetic-voice (TTS / voice-clone) detection with segment-level scores.

Two back-ends, used in this order when available:

1. SSL + AASIST (recommended): a wav2vec2-XLS-R / WavLM front end with an AASIST head, the
   architecture behind the strongest ASVspoof 5 systems. Point PRAMAAN_SSL_SPOOF_CMD at a script
   that prints one score per line for a list of 16 kHz WAV chunks (e.g. the official
   SSL_Anti-spoofing repo's eval script). Scores > 0 = bona fide by ASVspoof convention.
2. LFCC + logistic regression (models/lfcc_spoof.npz) – the classic ASVspoof LFCC baseline,
   trainable on CPU in minutes with scripts/train_lfcc_spoof.py (ASVspoof 2019 LA, In-the-Wild,
   IndicSynth / IndicTTS-deepfake for Indian languages).

The detector is run on 2 s windows with 0.5 s hop over voiced audio, producing a score curve so
PARTIAL fakes (one cloned sentence spliced into a real call) are highlighted, not averaged away.
Cross-domain error rates are high (see README), so this family is capped in fusion and always
reported alongside ENF/acoustic evidence.
"""
from __future__ import annotations

import os
import shlex
import subprocess

import numpy as np
from scipy import signal
from scipy.fft import dct

from ... import config
from ..base import Context, Finding, calibrated, status_from_llr, unavailable

SR = 16000


def lfcc(x: np.ndarray, sr: int = SR, n_filt: int = 20, n_ceps: int = 20) -> np.ndarray:
    """Linear-frequency cepstral coefficients + deltas (frames x 3*n_ceps)."""
    f, t, Z = signal.stft(x, sr, nperseg=512, noverlap=352, nfft=512)
    P = np.abs(Z) ** 2
    edges = np.linspace(0, sr / 2, n_filt + 2)
    fb = np.zeros((n_filt, len(f)))
    for i in range(n_filt):
        l, c, r = edges[i:i + 3]
        fb[i] = np.clip(np.minimum((f - l) / (c - l + 1e-9), (r - f) / (r - c + 1e-9)), 0, None)
    E = np.log(fb @ P + 1e-10)
    C = dct(E, type=2, axis=0, norm="ortho")[:n_ceps].T
    d1 = np.gradient(C, axis=0)
    d2 = np.gradient(d1, axis=0)
    return np.concatenate([C, d1, d2], axis=1)


def window_features(x: np.ndarray) -> np.ndarray:
    F = lfcc(x)
    return np.concatenate([F.mean(0), F.std(0)])


def windows(x: np.ndarray, win: float = 2.0, hop: float = 0.5):
    n, h = int(win * SR), int(hop * SR)
    e = np.array([np.sqrt(np.mean(x[i:i + n] ** 2) + 1e-12) for i in range(0, max(1, len(x) - n + 1), h)])
    thr = min(float(np.median(e)), max(float(np.percentile(e, 10)) * 1.5, 1e-3))  # skip near-silent windows
    for k, i in enumerate(range(0, max(1, len(x) - n + 1), h)):
        if e[k] >= thr:  # voiced/active windows only
            yield i / SR, x[i:i + n]


def run(ctx: Context) -> list[Finding]:
    from .io import decode
    model = config.MODELS_DIR / "lfcc_spoof.npz"
    ssl_cmd = os.environ.get("PRAMAAN_SSL_SPOOF_CMD")
    hf_ok = hf_voice_available()
    if not model.exists() and not ssl_cmd and not hf_ok:
        return [unavailable("AUD_SPOOF", "audio", "synthetic-voice", "Synthetic-voice detector",
                            "No anti-spoofing model installed. Run: python scripts/install_models.py --voice "
                            "(pretrained wav2vec2 detector), or train one with scripts/train_lfcc_spoof.py, or set "
                            "PRAMAAN_SSL_SPOOF_CMD for an SSL+AASIST model. Content Credentials, ENF and acoustic "
                            "checks still run.")]
    x, _ = decode(ctx.path, ctx.work_dir, SR)
    wins = list(windows(x))
    if len(wins) < 2:
        return [Finding(code="AUD_SPOOF", module="audio", family="synthetic-voice", title="Too little speech",
                        status="info", reliability=0.0, explanation="Less than ~2.5 s of active audio.")]
    cal = None
    if ssl_cmd:
        scores = _ssl_scores(ctx, wins, ssl_cmd)
        name, backend = "ssl_aasist", "wav2vec2/WavLM + AASIST"
    elif hf_ok:
        wins = list(windows(x, win=4.0, hop=2.0)) or wins
        try:
            p_fake = _hf_voice_scores([w for _, w in wins])
        except Exception as e:
            return [unavailable("AUD_SPOOF", "audio", "synthetic-voice", "Synthetic-voice detector",
                                f"The pretrained voice model failed to run ({e}).")]
        p_fake = np.clip(np.asarray(p_fake, float), 1e-4, 1 - 1e-4)
        scores = -np.log(p_fake / (1 - p_fake))          # bona-fide-positive convention
        name, backend = "hf_voice", f"pretrained detector {VOICE_MODEL}"
        cal = _voice_calibration()
    else:
        p = np.load(model)
        Xf = np.stack([window_features(w) for _, w in wins])
        Xf = (Xf - p["mu"]) / p["sd"]
        scores = -(Xf @ p["w"] + p["b"])  # model predicts spoof logit; flip to bona-fide-positive convention
        name, backend = "lfcc_lr", "LFCC + logistic regression"
    spoof_logit = -np.asarray(scores, dtype=float)
    wlen = 4.0 if name == "hf_voice" else 2.0
    times = [t for t, _ in wins]
    hi = spoof_logit > 0
    segs = []
    for t, h in zip(times, hi):
        if h and segs and t - segs[-1][1] <= 0.6:
            segs[-1][1] = t + wlen
        elif h:
            segs.append([t, t + wlen])
    frac = float(hi.mean())
    agg = float(np.percentile(spoof_logit, 90))
    if name == "hf_voice":
        # Unvalidated off-the-shelf model: median over windows, conservative mapping; a calibration fitted on
        # labelled clips from your own cases (scripts/eval_dataset.py --fit-voice) replaces these defaults.
        agg = float(np.median(spoof_logit))
        c = cal or {"a": 0.6, "b": -1.2, "cap": 2.5, "reliability": 0.6}
        llr = float(np.clip(c["a"] * agg + c["b"], -1.0, c["cap"]))
    else:
        llr = calibrated(f"spoof_{name}", agg, a=0.8, b=-0.5, cap=2.5)
    ctx.shared["timeline_spoof"] = {"t": [round(t, 2) for t in times], "spoof_logit": [round(float(s), 3) for s in spoof_logit]}
    title = (f"Synthetic-voice indicators in {len(segs)} segment(s)" if segs else "No synthetic-voice indicators")
    return [Finding(code="AUD_SPOOF", module="audio", family="synthetic-voice", title=title,
                    status=status_from_llr(llr), llr=llr,
                    reliability=(cal or {}).get("reliability", 0.6) if name == "hf_voice" else 0.45 if name == "lfcc_lr" else 0.6,
                    raw_score=agg, location={"segments": [{"start": round(a, 2), "end": round(b, 2)} for a, b in segs[:20]],
                                             "fraction_flagged": round(frac, 2)},
                    explanation=f"{backend} scored {len(wins)} windows of active speech; "
                                f"{frac:.0%} look synthetic. Highlighted segments are where a cloned/TTS voice is most "
                                "likely; compare with ENF and noise-floor breaks at the same times.",
                    innocent=["Heavy noise suppression / codec artefacts (WhatsApp, VoIP) resemble vocoder traces",
                              "Language or accent not represented in the detector's training data"])]


def _ssl_scores(ctx: Context, wins, cmd: str) -> list[float]:
    from scipy.io import wavfile
    d = ctx.work_dir / "ssl_chunks"
    d.mkdir(exist_ok=True)
    paths = []
    for k, (_, w) in enumerate(wins):
        p = d / f"{k:05d}.wav"
        wavfile.write(p, SR, (np.clip(w, -1, 1) * 32767).astype(np.int16))
        paths.append(str(p))
    lst = d / "list.txt"
    lst.write_text("\n".join(paths))
    r = subprocess.run(shlex.split(cmd.format(list=lst)), capture_output=True, text=True, check=True, timeout=3600)
    return [float(v) for v in r.stdout.split()]


# --------------------------------------------------------------------------- pretrained Hugging Face back-end
VOICE_MODEL = os.environ.get("PRAMAAN_VOICE_MODEL", "MelodyMachine/Deepfake-audio-detection-V2")
_hfv = None
_hfv_lock = __import__("threading").Lock()


def hf_voice_available() -> bool:
    """True when torch+transformers are installed and the model is cached (scripts/install_models.py --voice)."""
    if os.environ.get("PRAMAAN_VOICE_MODEL", "x") == "":
        return False
    import importlib.util as iu
    if iu.find_spec("torch") is None or iu.find_spec("transformers") is None:
        return False
    from pathlib import Path
    hf_home = Path(os.environ.get("HF_HOME", str(config.MODELS_DIR / "hf")))
    return any(hf_home.glob(f"hub/models--{VOICE_MODEL.replace('/', '--')}"))


def _hf_voice():
    global _hfv
    with _hfv_lock:
        if _hfv is None:
            os.environ.setdefault("HF_HOME", str(config.MODELS_DIR / "hf"))
            import torch
            if hasattr(torch, "set_num_threads"):
                torch.set_num_threads(config.usable_cpus())
            from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
            offline = os.environ.get("HF_HUB_OFFLINE") == "1"
            fe = AutoFeatureExtractor.from_pretrained(VOICE_MODEL, local_files_only=offline)
            model = AutoModelForAudioClassification.from_pretrained(VOICE_MODEL, local_files_only=offline).eval()
            labels = {int(k): str(v).lower() for k, v in model.config.id2label.items()}
            fake = [i for i, l in labels.items() if any(t in l for t in ("fake", "spoof", "synth", "ai", "deepfake", "generated"))
                    and not any(t in l for t in ("real", "bona", "human", "genuine"))]
            if len(fake) != 1:
                raise RuntimeError(f"cannot tell which label means synthetic in {labels}")
            _hfv = (fe, model, fake[0], labels)
    return _hfv


def _hf_voice_scores(chunks: list) -> list[float]:
    import torch
    fe, model, fake, _ = _hf_voice()
    sr = getattr(fe, "sampling_rate", SR) or SR
    out = []
    for w in chunks:
        if sr != SR:
            w = signal.resample_poly(w, sr, SR)
        inp = fe(w.astype(np.float32), sampling_rate=sr, return_tensors="pt")
        with torch.no_grad():
            p = torch.softmax(model(**inp).logits[0], -1)[fake].item()
        out.append(float(p))
    return out


def _voice_calibration() -> dict | None:
    import json
    f = config.MODELS_DIR / "voice_calibration.json"
    try:
        return json.loads(f.read_text()) if f.exists() else None
    except Exception:
        return None
