"""OSD timeline reconciliation – PRAMAAN's flagship CCTV check.

CCTV/DVR footage carries a burned-in on-screen-display (OSD) clock. We read it with OCR and
reconcile THREE independent clocks on one axis:

    OSD clock (what the DVR painted)  vs  container PTS (what the file claims)  vs  frame/motion evidence

A frame deletion that was re-timed so PTS looks continuous still makes the OSD clock jump forward.
Reordering makes it jump backward. A freeze/loop insert makes it stall (or keeps it running over
identical pixels). Each finding comes with before/after frames and innocent explanations
(e.g. motion-triggered recording), because a jump is an indicator, not proof.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import cv2
import numpy as np

from ... import config
from ..base import Context, Finding, save_rgb, status_from_llr

PATTERNS = [
    (re.compile(r"(\d{4})\D{0,2}(\d{2})\D{0,2}(\d{2})\D{0,3}(\d{2})\D?(\d{2})\D?(\d{2})"), "ymd"),
    (re.compile(r"(\d{2})\D{0,2}(\d{2})\D{0,2}(\d{4})\D{0,3}(\d{2})\D?(\d{2})\D?(\d{2})"), "dmy"),
    (re.compile(r"(\d{2}):(\d{2}):(\d{2})"), "hms"),
]
WHITELIST = "0123456789-/:. "


def parse_osd(text: str) -> float | None:
    s = text.strip().replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    for rx, kind in PATTERNS:
        m = rx.search(s)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        try:
            if kind == "ymd":
                return datetime(g[0], g[1], g[2], g[3], g[4], g[5]).timestamp()
            if kind == "dmy":
                return datetime(g[2], g[1], g[0], g[3], g[4], g[5]).timestamp()
            if kind == "hms" and g[0] < 24 and g[1] < 60 and g[2] < 60:
                return float(g[0] * 3600 + g[1] * 60 + g[2])
        except ValueError:
            continue
    return None


def _ocr(img: np.ndarray) -> tuple[float | None, str]:
    import pytesseract
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    scale = max(1.0, 48.0 / max(g.shape[0], 1))
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    last = ""
    for cand in (th, 255 - th):
        cand = cv2.copyMakeBorder(cand, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=int(cand[0, 0]))
        txt = pytesseract.image_to_string(cand, config=f"--psm 7 -c tessedit_char_whitelist={WHITELIST!r}")
        v = parse_osd(txt)
        last = txt.strip()
        if v is not None:
            return v, last
    return None, last


def _bands(h: int, w: int) -> list[tuple[int, int, int, int]]:
    env = os.environ.get("PRAMAAN_OSD_ROI")  # fractions "x,y,w,h"
    if env:
        x, y, ww, hh = (float(v) for v in env.split(","))
        return [(int(x * w), int(y * h), int(ww * w), int(hh * h))]
    out = []
    # DVR overlays are a single text row; try several strip heights so the strip holds the text and little scene
    for frac in (0.05, 0.065, 0.09, 0.12):
        bh = max(int(h * frac), 22)
        for x, ww in ((0, w // 2), (w // 2, w - w // 2), (0, w)):
            out += [(x, 0, ww, bh), (x, h - bh, ww, bh)]
    return out


def _find_roi(samples: list[tuple[int, float, np.ndarray]]) -> tuple[tuple[int, int, int, int] | None, str]:
    """Pick the strip whose readings are readable AND self-consistent: consecutive OSD values must advance in step
    with the file's own timestamps (within the clock's 1 s resolution). Cheaper/smaller strips win ties."""
    h, w = samples[0][2].shape[:2]
    best, best_score, best_txt = None, 0.0, ""
    for x, y, ww, hh in _bands(h, w):
        reads, txt = [], ""
        for _, t, f in samples[:4]:
            v, tx = _ocr(f[y:y + hh, x:x + ww])
            if v is not None:
                reads.append((t, v))
                txt = tx
        if len(reads) < 2:
            continue
        consistent = sum(abs((v2 - v1) - (t2 - t1)) <= 1.5 for (t1, v1), (t2, v2) in zip(reads, reads[1:]))
        score = len(reads) + 2 * consistent - (ww * hh) / (w * h)
        if score > best_score:
            best, best_score, best_txt = (x, y, ww, hh), score, txt
        if len(reads) == 4 and consistent == 3:
            break   # perfect strip found – stop searching
    return best, best_txt


def run(ctx: Context) -> list[Finding]:
    try:
        import pytesseract  # noqa: F401
        pytesseract.get_tesseract_version()
    except Exception as e:
        return [Finding(code="VID_OSD", module="video", family="osd", title="OSD clock OCR unavailable",
                        status="unavailable", explanation=f"Tesseract OCR not installed: {e}")]
    fps = ctx.shared.get("fps") or 25.0
    frames_meta = ctx.shared.get("frames") or []
    step = max(1, int(round(fps * config.OCR_EVERY_SECONDS)))
    cap = cv2.VideoCapture(str(ctx.path))
    samples: list[tuple[int, float, np.ndarray]] = []
    i = 0
    while i < config.VIDEO_MAX_FRAMES:
        if i % step == 0:
            ok, fr = cap.read()
            if not ok:
                break
            t = frames_meta[i]["t"] if i < len(frames_meta) else i / fps
            samples.append((i, t, fr))
        else:
            if not cap.grab():
                break
        i += 1
    cap.release()
    if len(samples) < 4:
        return []
    roi, sample_txt = _find_roi(samples[:6])
    if roi is None:
        return [Finding(code="VID_OSD", module="video", family="osd", title="No on-screen clock detected",
                        status="info", reliability=0.0,
                        explanation="No readable date/time overlay was found in the top or bottom bands. Set "
                                    "PRAMAAN_OSD_ROI to the overlay position for unusual DVR layouts.")]
    x, y, ww, hh = roi
    roi_img = samples[0][2][y:y + hh, x:x + ww]
    roi_art = save_rgb(cv2.cvtColor(roi_img, cv2.COLOR_BGR2RGB), ctx.artifact("osd_roi.png"))
    reads = []
    for k, (fi, t, fr) in enumerate(samples):
        v, _ = _ocr(fr[y:y + hh, x:x + ww])
        reads.append((fi, t, v))
        if k % 20 == 0:
            ctx.progress(f"OSD OCR {k}/{len(samples)}")
    good = [(fi, t, v) for fi, t, v in reads if v is not None]
    read_rate = len(good) / len(reads)
    if len(good) < 4:
        return [Finding(code="VID_OSD", module="video", family="osd", title="OSD clock found but unreadable",
                        status="info", reliability=0.0, explanation=f"Only {len(good)} readable samples.")]

    pts = np.array([g[1] for g in good])
    osd = np.array([g[2] for g in good])
    off = osd - pts
    # reject isolated OCR errors: a real splice changes the offset PERSISTENTLY, an OCR error doesn't
    keep = np.ones(len(off), bool)
    for k in range(len(off)):
        nb = off[max(0, k - 2):k + 3]
        if abs(off[k] - np.median(nb)) > 1.5 and np.sum(np.abs(nb - off[k]) < 1.5) < 2:
            keep[k] = False
    pts, osd, off = pts[keep], osd[keep], off[keep]
    fidx = [g[0] for g, kp in zip(good, keep) if kp]
    frame_by_idx = {s[0]: s[2] for s in samples}

    tol = 1.0 + config.OCR_EVERY_SECONDS  # OSD has 1 s resolution
    events = []
    for k in range(1, len(off)):
        d_off = off[k] - off[k - 1]
        d_osd = osd[k] - osd[k - 1]
        d_pts = pts[k] - pts[k - 1]
        if d_osd < -1.0:
            events.append({"kind": "backward", "at": pts[k], "osd_from": osd[k - 1], "osd_to": osd[k],
                           "delta": d_off, "k": k})
        elif d_off > tol:
            events.append({"kind": "forward", "at": pts[k], "osd_from": osd[k - 1], "osd_to": osd[k],
                           "delta": d_off, "k": k})
    # stalls: OSD constant while PTS advances
    k0 = 0
    for k in range(1, len(osd) + 1):
        if k == len(osd) or osd[k] != osd[k0]:
            if pts[k - 1] - pts[k0] >= 2.5:
                events.append({"kind": "stall", "at": pts[k0], "end": pts[k - 1], "osd_from": osd[k0],
                               "osd_to": osd[k0], "delta": -(pts[k - 1] - pts[k0]), "k": k0})
            k0 = k

    # cross-checks with other modules
    frozen = ctx.shared.get("frozen_runs") or []
    gop_breaks = ctx.shared.get("gop_breaks") or []
    running_over_frozen = []
    for a, b in frozen:
        inside = [(p, o) for p, o in zip(pts, osd) if a <= p <= b]
        if len(inside) >= 2 and inside[-1][1] - inside[0][1] >= 2:
            running_over_frozen.append((a, b))

    findings: list[Finding] = []
    many = len([e for e in events if e["kind"] == "forward"]) > 6
    for e in sorted(events, key=lambda e: e["at"]):
        near_gop = any(abs(g - e["at"]) <= config.OCR_EVERY_SECONDS + 1 for g in gop_breaks)
        k = e["k"]
        pair = None
        if k >= 1 and fidx[k - 1] in frame_by_idx and fidx[k] in frame_by_idx:
            a = cv2.resize(frame_by_idx[fidx[k - 1]], (320, 180))
            b = cv2.resize(frame_by_idx[fidx[k]], (320, 180))
            sep = np.full((180, 6, 3), 255, np.uint8)
            pair = save_rgb(cv2.cvtColor(np.hstack([a, sep, b]), cv2.COLOR_BGR2RGB),
                            ctx.artifact(f"osd_{e['kind']}_{e['at']:.1f}.png"))
        fmt = lambda v: datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S") if v > 86400 * 365 else \
            f"{int(v // 3600):02d}:{int(v % 3600 // 60):02d}:{int(v % 60):02d}"
        if e["kind"] == "forward":
            retimed = not any(abs(g - e["at"]) <= tol for g in ctx.shared.get("pts_gaps", []))
            llr = (2.8 if retimed else 2.2) if not many else 0.8
            findings.append(Finding(
                code="VID_OSD_FORWARD", module="video", family="osd",
                title=f"On-screen clock skips ≈{e['delta']:.0f}s at file time {e['at']:.1f}s",
                status=status_from_llr(llr), llr=llr, reliability=0.8 if read_rate > 0.7 else 0.55,
                raw_score=float(e["delta"]), location={"time": round(float(e["at"]), 2), "missing_s": round(float(e["delta"]), 1)},
                explanation=f"The burned-in clock jumps from {fmt(e['osd_from'])} to {fmt(e['osd_to'])} while the "
                            f"file's own timestamps advance only {config.OCR_EVERY_SECONDS:.0f}s. About "
                            f"{e['delta']:.0f}s of recorded time is absent from this file"
                            + (". The container timestamps are continuous here, so the file was re-timed after "
                               "the cut to hide the gap." if not any(abs(g - e["at"]) <= tol for g in
                                                                     ctx.shared.get("pts_gaps", [])) else
                               "; the container also shows a timestamp gap here.")
                            + (" A GOP boundary coincides with this point." if near_gop else ""),
                innocent=["Motion-triggered / scheduled recording resumes after a pause",
                          "DVR clock synchronised by NTP during recording"] +
                         (["Many evenly spread jumps – recorder likely in motion-detection mode"] if many else []),
                artifacts=[pair] if pair else []))
        elif e["kind"] == "backward":
            findings.append(Finding(
                code="VID_OSD_BACKWARD", module="video", family="osd",
                title=f"On-screen clock goes BACK {abs(e['osd_to'] - e['osd_from']):.0f}s at file time {e['at']:.1f}s",
                status="fail", llr=2.8, reliability=0.8, raw_score=float(e["delta"]),
                location={"time": round(float(e["at"]), 2)},
                explanation=f"The clock reads {fmt(e['osd_from'])} and then {fmt(e['osd_to'])}. Within one continuous "
                            "recording the OSD clock cannot run backwards; segments have been reordered or footage from "
                            "an earlier time has been inserted.",
                innocent=["Daylight-saving / manual clock correction on the DVR (not applicable in India)",
                          "Two separate recordings concatenated for convenience (should be documented)"],
                artifacts=[pair] if pair else []))
        else:
            findings.append(Finding(
                code="VID_OSD_STALL", module="video", family="osd",
                title=f"On-screen clock frozen for {abs(e['delta']):.0f}s from file time {e['at']:.1f}s",
                status="fail", llr=1.8, reliability=0.7, raw_score=float(e["delta"]),
                location={"start": round(float(e["at"]), 2), "end": round(float(e["end"]), 2)},
                explanation="The clock shows the same second while the file advances – consistent with a still frame "
                            "or duplicated frames inserted to fill time.",
                innocent=["DVR overlay generator hung while video continued"], artifacts=[pair] if pair else []))
    for a, b in running_over_frozen:
        findings.append(Finding(
            code="VID_OSD_OVER_FROZEN", module="video", family="osd",
            title=f"Clock keeps running over identical frames ({a:.1f}–{b:.1f}s)", status="fail", llr=2.0,
            reliability=0.7, location={"start": round(a, 2), "end": round(b, 2)},
            explanation="The pixels are identical but the OSD seconds advance, meaning only the overlay was regenerated "
                        "over duplicated content, or the scene was frozen by the recorder.",
            innocent=["Truly static scene with no noise (rare; sensor noise normally changes pixels)"]))

    # rate check
    from scipy.stats import theilslopes
    slope = float(theilslopes(osd, pts)[0]) if len(pts) > 5 else 1.0
    if not events and abs(slope - 1) > 0.05:
        findings.append(Finding(code="VID_OSD_RATE", module="video", family="osd",
                                title=f"OSD clock runs at {slope:.2f}× file speed", status="warn", llr=1.4,
                                reliability=0.6, explanation="Footage appears re-timed (sped up or slowed) relative "
                                "to the recorder's clock.", innocent=["Low-fps DVR mislabelled with a wrong frame rate"]))
    if not findings:
        findings.append(Finding(code="VID_OSD", module="video", family="osd",
                                title="On-screen clock consistent with file timeline", status="pass", llr=-0.8,
                                reliability=0.75 if read_rate > 0.7 else 0.5,
                                explanation=f"{len(pts)} OSD readings advance in lock-step with the container "
                                            f"timestamps (rate {slope:.3f}×, no jumps, stalls or reversals)."))
    # container creation_time vs OSD
    ct = (ctx.shared.get("probe", {}).get("format", {}).get("tags", {}) or {}).get("creation_time")
    note = ""
    if ct and osd[0] > 86400 * 365:
        try:
            ctd = datetime.fromisoformat(ct.replace("Z", "+00:00")).timestamp()
            note = f"Container creation_time differs from first OSD reading by {abs(ctd - osd[0]) / 60:.0f} min."
        except ValueError:
            pass
    ctx.shared["timeline_osd"] = {"pts": [round(float(x), 2) for x in pts], "osd": [float(x) for x in osd],
                                  "offset": [round(float(x - off[0]), 2) for x in off],
                                  "events": [{k: (round(float(v), 2) if isinstance(v, (float, np.floating)) else v)
                                              for k, v in e.items() if k != "k"} for e in events],
                                  "read_rate": round(read_rate, 2), "roi": roi, "note": note}
    findings.insert(0, Finding(code="VID_OSD_READ", module="video", family="osd",
                               title=f"OSD clock read in {read_rate:.0%} of samples", status="info",
                               explanation=f"Overlay located at x={x}, y={y}, {ww}×{hh}px. Sample text: “{sample_txt}”. "
                                           + note, artifacts=[roi_art]))
    return findings
