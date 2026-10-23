"""Face-swap / face-reenactment deepfake detection.

PRAMAAN extracts face crops itself (OpenCV, no download) at ~2 fps, then hands them to a
DeepfakeBench detector (NeurIPS 2023; 36 detectors incl. Xception, EfficientNet-B4, UCF, SBI,
CLIP-based) through a thin command contract:

    PRAMAAN_DEEPFAKE_CMD="python models/deepfakebench_score.py --detector ucf --weights models/ucf_best.pth --list {list}"

The command reads one face-crop path per line and prints one fake-probability (0..1) per line.
models/deepfakebench_score.py (bundled) implements this with DeepfakeBench's own model zoo.

Scores are aggregated over time so the report can say WHEN the face looks synthetic. Without the
model, faces are still extracted and shown (contact sheet) and the check reports N/A.
Cross-dataset generalisation of face deepfake detectors is weak (DeepfakeBench reports large drops
from FF++ to Celeb-DF/DFDC), so this family is capped and must be corroborated.
"""
from __future__ import annotations

import os
import shlex
import subprocess

import cv2
import numpy as np

from ..base import Context, Finding, calibrated, save_rgb, status_from_llr
from .lipsync import face_detector

MAX_CROPS = 64


def extract_faces(ctx: Context, fps: float) -> list[tuple[float, np.ndarray]]:
    det = face_detector()
    if det is None:
        return []
    cap = cv2.VideoCapture(str(ctx.path))
    step = max(1, int(round(fps)))          # ~1 face crop per second
    crops, i = [], 0
    while len(crops) < MAX_CROPS:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, fr = cap.retrieve()
            if not ok:
                break
            h, w = fr.shape[:2]
            s = 320 / max(w, 1)
            g = cv2.cvtColor(cv2.resize(fr, (320, int(h * s))), cv2.COLOR_BGR2GRAY)
            faces = det.detectMultiScale(g, 1.15, 3, minSize=(32, 32))
            if len(faces):
                x, y, bw, bh = (int(v / s) for v in max(faces, key=lambda f: f[2] * f[3]))
                m = int(0.3 * max(bw, bh))   # DeepfakeBench-style 1.3x margin
                x0, y0, x1, y1 = max(0, x - m), max(0, y - m), min(w, x + bw + m), min(h, y + bh + m)
                crops.append((i / fps, cv2.resize(fr[y0:y1, x0:x1], (256, 256))))
        i += 1
    cap.release()
    return crops


def run(ctx: Context) -> list[Finding]:
    fps = ctx.shared.get("fps") or 25.0
    crops = extract_faces(ctx, fps)
    if not crops:
        return []
    d = ctx.work_dir / "faces"
    d.mkdir(exist_ok=True)
    paths = []
    for k, (t, c) in enumerate(crops):
        p = d / f"{k:03d}_{t:.2f}.png"
        cv2.imwrite(str(p), c)
        paths.append(str(p))
    # contact sheet
    sheet_crops = [cv2.resize(c, (96, 96)) for _, c in crops[:24]]
    rows = [np.hstack(sheet_crops[i:i + 8] + [np.zeros((96, 96, 3), np.uint8)] * (8 - len(sheet_crops[i:i + 8])))
            for i in range(0, len(sheet_crops), 8)]
    sheet = save_rgb(cv2.cvtColor(np.vstack(rows), cv2.COLOR_BGR2RGB), ctx.artifact("faces.png"))
    cmd = os.environ.get("PRAMAAN_DEEPFAKE_CMD")
    if not cmd:
        return [Finding(code="VID_FACE_DEEPFAKE", module="video", family="synthetic-face",
                        title=f"Face-deepfake detector not installed ({len(crops)} faces extracted)",
                        status="unavailable", reliability=0.0, artifacts=[sheet],
                        explanation="Faces were located and cropped (see contact sheet) but no DeepfakeBench weights "
                                    "are configured. Set PRAMAAN_DEEPFAKE_CMD (see models/deepfakebench_score.py). "
                                    "Lip-sync, ENF and container checks still run.")]
    lst = d / "list.txt"
    lst.write_text("\n".join(paths))
    try:
        r = subprocess.run(shlex.split(cmd.format(list=lst)), capture_output=True, text=True, check=True, timeout=3600)
        probs = np.array([float(v) for v in r.stdout.split()][:len(paths)])
    except Exception as e:
        return [Finding(code="VID_FACE_DEEPFAKE", module="video", family="synthetic-face",
                        title="Face-deepfake detector failed", status="unavailable", explanation=str(e)[:400],
                        artifacts=[sheet])]
    times = [t for t, _ in crops][:len(probs)]
    ctx.shared["timeline_face"] = {"t": [round(t, 2) for t in times], "fake_prob": [round(float(p), 3) for p in probs]}
    top = float(np.mean(np.sort(probs)[-max(3, len(probs) // 4):]))
    logit = float(np.log(top / (1 - top + 1e-6) + 1e-6))
    llr = calibrated("face_deepfake", logit, a=0.7, b=-0.7, cap=2.5)
    hot = [round(t, 1) for t, p in zip(times, probs) if p > 0.7]
    return [Finding(code="VID_FACE_DEEPFAKE", module="video", family="synthetic-face",
                    title=f"Face-manipulation score {top:.2f}" + (f" (peaks at {hot[:4]} s)" if hot else ""),
                    status=status_from_llr(llr), llr=llr, reliability=0.5, raw_score=top,
                    location={"times": hot[:20]}, artifacts=[sheet],
                    explanation=f"{len(probs)} face crops scored by the DeepfakeBench detector; the top-quartile mean "
                                f"fake probability is {top:.2f}. Face-swap and re-enactment leave blending and "
                                "texture artefacts these detectors learn.",
                    innocent=["Beauty filters / heavy compression", "Faces from a population or camera unseen in training"])]
