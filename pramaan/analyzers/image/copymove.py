"""Copy-move (clone) detection: SIFT keypoints matched against themselves (g2NN test),
then a RANSAC-consistent geometric transform between source and target clusters."""
from __future__ import annotations

import cv2
import numpy as np

from ..base import Context, Finding, calibrated, save_rgb, status_from_llr
from .common import load_rgb


def run(ctx: Context) -> list[Finding]:
    rgb = load_rgb(ctx.path, max_side=1600)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sift = cv2.SIFT_create(nfeatures=6000, contrastThreshold=0.01)
    kps, des = sift.detectAndCompute(gray, None)
    if des is None or len(kps) < 20:
        return [Finding(code="IMG_COPYMOVE", module="image", family="pixel", title="Copy-move: too few keypoints",
                        status="info", reliability=0.0, explanation="Image has too little texture for keypoint matching.")]
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    knn = matcher.knnMatch(des, des, k=4)
    pts = np.array([k.pt for k in kps])
    min_dist = max(20, 0.03 * max(gray.shape))
    src, dst = [], []
    for ms in knn:
        ms = [m for m in ms if m.trainIdx != m.queryIdx]
        for i in range(len(ms) - 1):          # generalised 2NN
            if ms[i].distance < 0.5 * ms[i + 1].distance:
                a, b = pts[ms[i].queryIdx], pts[ms[i].trainIdx]
                if np.linalg.norm(a - b) > min_dist:
                    src.append(a)
                    dst.append(b)
            else:
                break
    best_inliers, M, extra_clusters = 0, None, 0
    if len(src) >= 6:
        s, d = np.float32(src), np.float32(dst)
        # each pair appears twice (a->b, b->a); keep one orientation
        keep = (s[:, 0] < d[:, 0]) | ((s[:, 0] == d[:, 0]) & (s[:, 1] < d[:, 1]))
        s, d = s[keep], d[keep]
        if len(s) >= 6:
            M, inl = cv2.estimateAffinePartial2D(s, d, method=cv2.RANSAC, ransacReprojThreshold=4.0)
            if inl is not None:
                inl = inl.ravel().astype(bool)
                best_inliers = int(inl.sum())
                # Repeated designs (logo walls, tiles, windows) give SEVERAL strong clusters at different
                # offsets; a clone gives one. Count further clusters among the remaining pairs.
                rs, rd = s[~inl], d[~inl]
                for _ in range(3):
                    if len(rs) < 6:
                        break
                    M2, in2 = cv2.estimateAffinePartial2D(rs, rd, method=cv2.RANSAC, ransacReprojThreshold=4.0)
                    if in2 is None:
                        break
                    in2 = in2.ravel().astype(bool)
                    if in2.sum() >= max(6, 0.4 * best_inliers):
                        extra_clusters += 1
                    rs, rd = rs[~in2], rd[~in2]
                s, d = s[inl], d[inl]
    vis = rgb.copy()
    if best_inliers >= 6:
        for a, b in zip(s, d):
            cv2.line(vis, tuple(int(x) for x in a), tuple(int(x) for x in b), (0, 255, 255), 1)
            cv2.circle(vis, tuple(int(x) for x in a), 4, (255, 60, 60), -1)
            cv2.circle(vis, tuple(int(x) for x in b), 4, (60, 255, 60), -1)
    art = save_rgb(vis, ctx.artifact("copymove.png"))
    llr = calibrated("copymove_inliers", best_inliers, a=0.18, b=-1.4, cap=3.2)
    if best_inliers >= 8:
        shift = M[:, 2].tolist() if M is not None else None
        if extra_clusters >= 1:
            return [Finding(code="IMG_COPYMOVE", module="image", family="pixel",
                            title=f"Repeated pattern, not a clone ({1 + extra_clusters} matching groups)",
                            status="info", raw_score=best_inliers, llr=0.0, reliability=0.0, location={"shift_px": shift},
                            explanation=f"Matching keypoints form {1 + extra_clusters} separate groups with different "
                                        "offsets. That is how a repeated design (logo backdrop, tiles, windows, text) "
                                        "looks; a cloned region produces one group. Not counted as evidence.",
                            artifacts=[art])]
        return [Finding(code="IMG_COPYMOVE", module="image", family="pixel",
                        title=f"Cloned region detected ({best_inliers} consistent keypoint pairs)",
                        status=status_from_llr(llr), raw_score=best_inliers, llr=llr, reliability=0.75,
                        location={"shift_px": shift},
                        explanation=f"{best_inliers} keypoint pairs map one part of the image onto another with a single "
                                    "consistent transform – the signature of copy-move cloning (e.g. hiding an object "
                                    "or duplicating one). Red = source, green = destination.",
                        innocent=["Genuinely repeated structures: tiles, windows, printed patterns, crowds in uniform"],
                        artifacts=[art])]
    return [Finding(code="IMG_COPYMOVE", module="image", family="pixel", title="No cloned regions detected",
                    status="pass", llr=-0.3, reliability=0.5, raw_score=best_inliers,
                    explanation=f"Best geometric cluster had {best_inliers} pairs (< 8).", artifacts=[art])]
