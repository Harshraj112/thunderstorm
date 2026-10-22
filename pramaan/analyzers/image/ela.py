"""Error Level Analysis – shown as a SUPPORTING visual only.

ELA is strongly content dependent (edges and texture light up in pristine images), so it is
given low reliability and a small LLR cap; the primary localisers are the noise map and JPEG ghosts.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

from ..base import Context, Finding, calibrated, save_rgb, status_from_llr
from .common import is_jpeg, load_rgb


def run(ctx: Context) -> list[Finding]:
    rgb = load_rgb(ctx.path)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=90)
    re = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), dtype=float)
    diff = np.abs(rgb.astype(float) - re).max(axis=2)
    scale = 255.0 / max(np.percentile(diff, 99.5), 1)
    ela = np.clip(diff * scale, 0, 255).astype(np.uint8)
    art = save_rgb(np.stack([ela] * 3, -1), ctx.artifact("ela.png"))

    # Region contrast: 32px block means, compare the brightest coherent region with the median.
    import cv2
    small = cv2.resize(diff, (max(diff.shape[1] // 32, 1), max(diff.shape[0] // 32, 1)), interpolation=cv2.INTER_AREA)
    # discount edges: divide by local gradient energy
    g = cv2.resize(cv2.Laplacian(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), cv2.CV_64F).__abs__(),
                   (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) + 1.0
    norm = small / np.sqrt(g)
    ratio = float(np.percentile(norm, 98) / (np.median(norm) + 1e-6))
    llr = max(calibrated("ela_ratio", ratio, a=0.25, b=-1.5, cap=1.0), -0.2)  # ELA is never strong evidence of authenticity
    return [Finding(code="IMG_ELA", module="image", family="compression", title="Error Level Analysis",
                    status=status_from_llr(llr), raw_score=ratio, llr=llr,
                    reliability=0.3 if is_jpeg(ctx.path) else 0.1,
                    explanation=f"Re-saved at JPEG Q90 and amplified the difference. Edge-normalised contrast "
                                f"ratio {ratio:.1f}. Uniform texture in the ELA map is expected; a region that is "
                                "much brighter or darker than similar content nearby is worth examining.",
                    innocent=["High-contrast edges and fine texture always appear bright",
                              "Images re-saved many times show flat, uninformative ELA"],
                    artifacts=[art])]
