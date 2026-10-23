"""Electrical Network Frequency (ENF) analysis – India runs one synchronous 50 Hz grid.

Mains-powered recorders (and battery devices near mains wiring/lights) pick up a faint hum at
50 Hz and its harmonics. Its frequency wanders slowly and its PHASE is continuous. Cutting out or
inserting audio breaks that continuity (blind tamper detection, no reference database needed;
cf. Rodríguez/Apolinário/Biscainho IEEE TIFS 2010; ESPRIT-Hilbert IEEE TIFS 2017).

Also: the presence of a 60 Hz hum in audio claimed to be recorded in India is a strong provenance
red flag. With a reference ENF logger (scripts/enf_logger.py) the frequency trace can additionally
be matched against logged grid frequency to verify WHEN a recording was made.
"""
from __future__ import annotations

import numpy as np
from scipy import signal

from ... import config
from ..base import Context, Finding, robust_z, status_from_llr

FS = 1000  # analysis rate


def _peak_snr(f: np.ndarray, p: np.ndarray, f0: float, bw: float = 0.6) -> float:
    band = (f > f0 - bw) & (f < f0 + bw)
    ref = ((f > f0 - 4) & (f < f0 - 1.5)) | ((f > f0 + 1.5) & (f < f0 + 4))
    if not band.any() or not ref.any():
        return -99.0
    return float(10 * np.log10(p[band].max() / (np.median(p[ref]) + 1e-20)))


def detect_grid(x: np.ndarray) -> dict:
    nper = min(len(x), FS * 16)
    f, p = signal.welch(x, FS, nperseg=nper, noverlap=nper // 2)
    res = {}
    for grid in (50.0, 60.0):
        snrs = {h: _peak_snr(f, p, grid * h) for h in (1, 2, 3, 4, 5)}
        best_h = max(snrs, key=snrs.get)
        res[grid] = {"snr": snrs[best_h], "harmonic": best_h, "all": snrs}
    return res


def phase_track(x: np.ndarray, fc: float, half_bw: float):
    sos = signal.butter(4, [(fc - half_bw) / (FS / 2), (fc + half_bw) / (FS / 2)], "band", output="sos")
    nb = signal.sosfiltfilt(sos, x)
    t = np.arange(len(nb)) / FS
    z = signal.hilbert(nb) * np.exp(-2j * np.pi * fc * t)     # demodulate to baseband
    # average in 0.1 s bins -> robust complex phasor sequence
    m = FS // 10
    k = len(z) // m
    zb = z[:k * m].reshape(k, m).mean(axis=1)
    ph = np.unwrap(np.angle(zb))
    tb = (np.arange(k) + 0.5) * 0.1
    amp = np.abs(zb)
    return tb, ph, amp


def phase_jumps(tb: np.ndarray, ph: np.ndarray, win: float = 2.0, gap: float = 0.3) -> tuple[np.ndarray, np.ndarray]:
    """At each t, fit a line to phase in [t-gap-win, t-gap] and [t+gap, t+gap+win]; return the
    difference of their extrapolations at t (a pure phase step shows up as a sharp peak)."""
    n, w, g = len(tb), int(win / 0.1), int(gap / 0.1)
    jump = np.zeros(n)
    for i in range(w + g, n - w - g):
        L = slice(i - g - w, i - g)
        R = slice(i + g, i + g + w)
        pl = np.polyfit(tb[L], ph[L], 1)
        pr = np.polyfit(tb[R], ph[R], 1)
        jump[i] = np.polyval(pr, tb[i]) - np.polyval(pl, tb[i])
    return jump, tb


def run(ctx: Context) -> list[Finding]:
    from .io import decode
    x, sr = decode(ctx.path, ctx.work_dir, FS)
    dur = len(x) / FS
    if dur < 8:
        return [Finding(code="AUD_ENF", module="audio", family="enf", title="ENF: recording too short",
                        status="info", reliability=0.0, explanation="ENF analysis needs at least ~8 s of audio.")]
    g = detect_grid(x)
    s50, s60 = g[50.0]["snr"], g[60.0]["snr"]
    out: list[Finding] = []
    expected = config.GRID_HZ
    other = 60.0 if expected == 50.0 else 50.0
    s_exp, s_oth = g[expected]["snr"], g[other]["snr"]
    ctx.shared["enf_grid"] = {"snr50": round(s50, 1), "snr60": round(s60, 1)}
    if s_oth > 10 and s_oth > s_exp + 6:
        out.append(Finding(code="AUD_ENF_GRID", module="audio", family="provenance",
                           title=f"{other:.0f} Hz mains hum detected (expected {expected:.0f} Hz grid)",
                           status="fail", llr=2.6, reliability=0.75, raw_score=s_oth,
                           explanation=f"The recording carries electrical hum at {other:.0f} Hz (SNR {s_oth:.0f} dB) and "
                                       f"none at {expected:.0f} Hz. India's grid runs at {expected:.0f} Hz, so this audio "
                                       "was most likely recorded elsewhere (or the hum was synthesised/inserted).",
                           innocent=["Equipment with its own 60 Hz inverter / generator",
                                     "Hum from a device charger or fan motor at a coincidental frequency"]))
    fc_grid = expected if s_exp >= s_oth else other
    harm = sorted(((v, h) for h, v in g[fc_grid]["all"].items() if v >= 6), reverse=True)[:3]
    snr = harm[0][0] if harm else max(s_exp, s_oth)
    if not harm:
        out.append(Finding(code="AUD_ENF", module="audio", family="enf", title="No usable mains hum (ENF)",
                           status="info", reliability=0.0,
                           explanation=f"Mains hum SNR {snr:.0f} dB at every harmonic up to {5 * fc_grid:.0f} Hz – too weak for "
                                       "phase analysis (battery device far from mains, or hum filtered out). ENF test abstains."))
        return out
    # Multi-harmonic phase-continuity: each usable harmonic gives an independent phase track; their
    # discontinuity evidence is combined, weighted by SNR (spectrum-combining, cf. Hajj-Ahmad et al. 2013).
    curves, mags, wsum = None, [], 0.0
    for sv, h in harm:
        tb, ph, amp = phase_track(x, fc_grid * h, 1.0 * h)
        jump, _ = phase_jumps(tb, ph)
        mag = np.abs(np.angle(np.exp(1j * jump)))
        valid = np.abs(jump) > 0
        z = np.zeros_like(mag)
        z[valid] = robust_z(mag[valid])
        w = 10 ** (sv / 10)
        curves = z * w if curves is None else curves[:len(z)] + z[:len(curves)] * w
        mags.append(mag)
        wsum += w
        if h == harm[0][1]:
            freq = fc_grid + np.gradient(signal.savgol_filter(ph, 21, 2), tb) / (2 * np.pi * h)
            tb0 = tb
    score = curves / wsum
    n = len(score)
    maxmag = np.max(np.stack([m[:n] for m in mags]), axis=0)
    # Self-check: on a clean, continuous hum the left/right phase extrapolations agree to a few hundredths of a
    # radian. If they typically disagree by >0.4 rad the phase is dominated by noise and a cut would be invisible,
    # so the test abstains instead of reporting "continuous".
    noise_floor = float(np.median(np.min(np.stack([m[:n] for m in mags]), axis=0)[maxmag > 0]))
    if noise_floor > 0.4:
        out.append(Finding(code="AUD_ENF", module="audio", family="enf",
                           title=f"Mains hum present ({snr:.0f} dB) but too noisy for phase analysis", status="info",
                           reliability=0.0, raw_score=noise_floor,
                           explanation=f"Grid hum is visible at {'+'.join(f'{fc_grid * h:.0f}' for _, h in harm)} Hz, but "
                                       f"its phase wanders by {noise_floor:.1f} rad between neighbouring seconds even "
                                       "without any edit, so a cut could not be seen. The ENF test abstains. With a grid "
                                       "reference log (scripts/enf_logger.py) the hum frequency can still be matched "
                                       "over several minutes to verify when the recording was made."))
        return out
    peaks, _ = signal.find_peaks(score, height=5.0, distance=20)
    peaks = [p_ for p_ in peaks if maxmag[p_] > 0.5]
    hs = "+".join(f"{fc_grid * h:.0f}" for _, h in harm)
    rel = min(0.85, 0.3 + snr / 40) * (1.0 if len(harm) > 1 else 0.85)
    ctx.shared["timeline_enf"] = {"t": [round(float(v), 2) for v in tb0[:n:2]],
                                  "freq": [round(float(v), 4) for v in freq[:n:2]],
                                  "phase_jump": [round(float(v), 3) for v in maxmag[::2]],
                                  "score": [round(float(v), 2) for v in score[::2]],
                                  "grid": fc_grid, "harmonic": harm[0][1], "snr_db": round(snr, 1)}
    if peaks:
        times = [round(float(tb0[p_]), 2) for p_ in peaks]
        llr = float(min(2.8, 1.6 + 0.4 * len(peaks)))
        out.append(Finding(code="AUD_ENF_PHASE", module="audio", family="enf",
                           title=f"Mains-hum phase discontinuity at {', '.join(f'{v:.1f}s' for v in times[:6])}",
                           status=status_from_llr(llr), llr=llr, reliability=rel,
                           raw_score=float(max(score[p_] for p_ in peaks)), location={"times": times},
                           explanation=f"The grid hum ({hs} Hz, best SNR {snr:.0f} dB) is phase-continuous everywhere "
                                       f"except at these instants, where its phase jumps by up to "
                                       f"{max(maxmag[p_] for p_ in peaks):.1f} rad (combined evidence "
                                       f"{max(score[p_] for p_ in peaks):.0f}σ across {len(harm)} harmonic(s)). A continuous "
                                       "recording cannot do this; audio was cut out or inserted at these points.",
                           innocent=["Recorder paused and resumed (should be audible / documented)",
                                     "Device moved between rooms/power circuits mid-recording"]))
    else:
        out.append(Finding(code="AUD_ENF_PHASE", module="audio", family="enf",
                           title="Mains-hum phase continuous throughout", status="pass", llr=-0.9, reliability=rel,
                           explanation=f"The grid hum ({hs} Hz, best SNR {snr:.0f} dB) shows no phase discontinuity, "
                                       "which supports a single, uninterrupted recording."))
    return out
    h = g[fc_grid]["harmonic"]
    fc = fc_grid * h
    tb, ph, amp = phase_track(x, fc, 1.0 * h)
    jump, _ = phase_jumps(tb, ph)
    wrapped = np.angle(np.exp(1j * jump))
    mag = np.abs(wrapped)
    z = robust_z(mag[np.abs(jump) > 0])
    zfull = np.zeros_like(mag)
    zfull[np.abs(jump) > 0] = z
    freq = fc_grid + np.gradient(signal.savgol_filter(ph, 21, 2), tb) / (2 * np.pi * h)
    peaks, _ = signal.find_peaks(mag, height=0.6, distance=20)
    peaks = [p for p in peaks if zfull[p] > 6]
    ctx.shared["timeline_enf"] = {"t": [round(float(v), 2) for v in tb[::2]],
                                  "freq": [round(float(v), 4) for v in freq[::2]],
                                  "phase_jump": [round(float(v), 3) for v in mag[::2]],
                                  "grid": fc_grid, "harmonic": h, "snr_db": round(snr, 1)}
    if peaks:
        times = [round(float(tb[p]), 2) for p in peaks]
        llr = float(min(2.8, 1.6 + 0.4 * len(peaks)))
        out.append(Finding(code="AUD_ENF_PHASE", module="audio", family="enf",
                           title=f"Mains-hum phase discontinuity at {', '.join(f'{v:.1f}s' for v in times[:6])}",
                           status=status_from_llr(llr), llr=llr, reliability=min(0.85, 0.4 + snr / 50),
                           raw_score=float(max(mag[p] for p in peaks)), location={"times": times},
                           explanation=f"The {fc:.0f} Hz grid hum (harmonic {h}, SNR {snr:.0f} dB) is phase-continuous "
                                       "everywhere except at these instants, where its phase jumps by up to "
                                       f"{max(mag[p] for p in peaks):.1f} rad. Continuous recording cannot do this; "
                                       "audio was cut out or inserted at these points.",
                           innocent=["Recorder paused and resumed (should be audible / documented)",
                                     "Device moved between rooms/power circuits mid-recording"]))
    else:
        out.append(Finding(code="AUD_ENF_PHASE", module="audio", family="enf",
                           title="Mains-hum phase continuous throughout", status="pass", llr=-0.9,
                           reliability=min(0.8, 0.3 + snr / 50),
                           explanation=f"The {fc:.0f} Hz hum (SNR {snr:.0f} dB) shows no phase discontinuity, which "
                                       "supports a single, uninterrupted recording."))
    return out
