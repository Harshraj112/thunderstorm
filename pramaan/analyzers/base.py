"""Common types for all analyzers.

Every analyzer returns Findings. A Finding carries:
  * llr          – calibrated log-likelihood ratio. >0 supports "manipulated", <0 supports "authentic".
  * reliability  – 0..1, how much this signal can be trusted ON THIS INPUT (drives abstention).
  * family       – correlated detectors share a family so fusion doesn't double count them.
  * explanation + innocent – plain-language reasoning and the alternative innocent explanations
                   an examiner must consider (SWGDE practice).
  * artifacts    – images/plots shown in the dashboard and embedded in the PDF.
  * data         – raw series for interactive charts (timelines, curves).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .. import config

PASS, INFO, WARN, FAIL, UNAVAILABLE = "pass", "info", "warn", "fail", "unavailable"


@dataclass
class Finding:
    code: str
    module: str
    family: str
    title: str
    status: str = INFO
    raw_score: float = 0.0
    llr: float = 0.0
    reliability: float = 0.0
    explanation: str = ""
    innocent: list[str] = field(default_factory=list)
    location: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["llr"] = float(round(self.llr, 3))
        d["reliability"] = float(round(self.reliability, 3))
        d["raw_score"] = float(round(self.raw_score, 5)) if math.isfinite(self.raw_score) else 0.0
        return d


@dataclass
class Context:
    evidence_id: str
    path: Path                 # working copy (never the original)
    work_dir: Path
    artifact_dir: Path
    modality: str
    shared: dict[str, Any] = field(default_factory=dict)   # cross-analyzer cache (exif, probe, timelines)
    progress: Callable[[str], None] = lambda s: None

    def artifact(self, name: str) -> Path:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        return self.artifact_dir / name


# ---------- calibration -----------------------------------------------------------------------
# Each detector's raw score is mapped to an LLR by a Platt-style affine map in log-odds space:
#   llr = a * raw + b,  clipped to [-cap, cap].
# Defaults below are conservative hand-set values; run scripts/calibrate.py on your labelled
# validation set to fit (a, b) and write models/calibration.json, which overrides these.
_DEFAULT_CAL: dict[str, tuple[float, float, float]] = {}
_cal_cache: dict[str, Any] | None = None


def _load_cal() -> dict[str, Any]:
    global _cal_cache
    if _cal_cache is None:
        p = config.MODELS_DIR / "calibration.json"
        _cal_cache = json.loads(p.read_text()) if p.exists() else {}
    return _cal_cache


def calibrated(name: str, raw: float, a: float, b: float, cap: float = 4.0) -> float:
    """Return llr for detector `name`. (a, b) are defaults if no fitted calibration exists."""
    c = _load_cal().get(name)
    if c:
        a, b, cap = c.get("a", a), c.get("b", b), c.get("cap", cap)
    return float(np.clip(a * raw + b, -cap, cap))


# ---------- helpers ---------------------------------------------------------------------------
def robust_z(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med)) * 1.4826
    if not np.isfinite(mad) or mad < 1e-9:
        mad = np.nanstd(x) + 1e-9
    return (x - med) / mad


def save_heatmap(base_rgb: np.ndarray, heat: np.ndarray, path: Path, alpha: float = 0.5) -> str:
    """Overlay a [0,1] heat map on an RGB image and write a PNG."""
    import cv2
    h, w = base_rgb.shape[:2]
    heat = np.clip(np.nan_to_num(heat), 0, 1)
    heat_u8 = cv2.resize((heat * 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
    colored = cv2.applyColorMap(heat_u8, cv2.COLORMAP_INFERNO)[:, :, ::-1]
    out = (base_rgb.astype(float) * (1 - alpha) + colored.astype(float) * alpha).astype(np.uint8)
    out = _limit(out)
    cv2.imwrite(str(path), out[:, :, ::-1])
    return path.name


def save_rgb(img: np.ndarray, path: Path) -> str:
    import cv2
    cv2.imwrite(str(path), _limit(img)[:, :, ::-1])
    return path.name


def _limit(img: np.ndarray, max_side: int = 1400) -> np.ndarray:
    import cv2
    h, w = img.shape[:2]
    s = max(h, w)
    if s > max_side:
        f = max_side / s
        img = cv2.resize(img, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)
    return img


def unavailable(code: str, module: str, family: str, title: str, why: str) -> Finding:
    return Finding(code=code, module=module, family=family, title=title, status=UNAVAILABLE,
                   reliability=0.0, explanation=why)


def status_from_llr(llr: float) -> str:
    if llr >= 1.5:
        return FAIL
    if llr >= 0.5:
        return WARN
    if llr <= -0.2:
        return PASS
    return INFO
