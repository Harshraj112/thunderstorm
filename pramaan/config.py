"""Central configuration. Everything is overridable with PRAMAAN_* environment variables."""
from __future__ import annotations

import os
from pathlib import Path

VERSION = "1.9.2"

# Settings written by scripts/setup_all.py (e.g. which Python runs TruFor). Real environment variables win.
_LOCAL = Path(os.environ.get("PRAMAAN_MODELS", Path(__file__).resolve().parent.parent / "models")) / "local_settings.json"
if _LOCAL.exists():
    try:
        import json as _json
        for _k, _v in _json.loads(_LOCAL.read_text()).items():
            if _k.startswith(("PRAMAAN_", "HF_")) and _v not in (None, ""):
                os.environ.setdefault(_k, str(_v))
    except Exception:
        pass

DATA_DIR = Path(os.environ.get("PRAMAAN_DATA", Path(__file__).resolve().parent.parent / "data"))
EVIDENCE_DIR = DATA_DIR / "evidence"      # write-once originals (chmod 0444)
WORK_DIR = DATA_DIR / "work"              # per-job working copies
ARTIFACT_DIR = DATA_DIR / "artifacts"     # heatmaps, plots, overlays
REPORT_DIR = DATA_DIR / "reports"
KEY_DIR = DATA_DIR / "keys"
DEVICE_DIR = DATA_DIR / "devices"         # PRNU fingerprints / ENF reference logs
DB_PATH = DATA_DIR / "pramaan.db"
MODELS_DIR = Path(os.environ.get("PRAMAAN_MODELS", Path(__file__).resolve().parent.parent / "models"))

LAB_NAME = os.environ.get("PRAMAAN_LAB", "State Forensic Science Laboratory (Demo)")
PUBLIC_BASE_URL = os.environ.get("PRAMAAN_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000"

# India: single synchronous 50 Hz grid. Used by ENF analysis.
GRID_HZ = float(os.environ.get("PRAMAAN_GRID_HZ", "50"))
# Default timezone offset (minutes) assumed when EXIF has no OffsetTime: IST = +05:30
DEFAULT_TZ_OFFSET_MIN = int(os.environ.get("PRAMAAN_TZ_OFFSET_MIN", "330"))
# Expected jurisdiction bounding box (lat_min, lat_max, lon_min, lon_max) – India by default
REGION_BBOX = tuple(float(x) for x in os.environ.get("PRAMAAN_REGION_BBOX", "6.5,37.5,68.0,97.5").split(","))

# Optional local LLM narrator (Ollama). Leave empty for fully deterministic template narration.
OLLAMA_URL = os.environ.get("PRAMAAN_OLLAMA_URL", "")
OLLAMA_MODEL = os.environ.get("PRAMAAN_OLLAMA_MODEL", "llama3.1:8b")

# Video analysis limits (keep CPU demos fast; raise on GPU boxes)
VIDEO_ANALYSIS_WIDTH = int(os.environ.get("PRAMAAN_VIDEO_WIDTH", "320"))
VIDEO_MAX_FRAMES = int(os.environ.get("PRAMAAN_VIDEO_MAX_FRAMES", "20000"))
OCR_EVERY_SECONDS = float(os.environ.get("PRAMAAN_OCR_EVERY", "1.0"))

WORKERS = int(os.environ.get("PRAMAAN_WORKERS", "2"))


def usable_cpus() -> int:
    """CPUs this process may really use. In a container os.cpu_count() reports the HOST's cores (often 32+),
    while the plan allows 1-2; oversubscribing threads makes PyTorch crawl. Order: PRAMAAN_THREADS, cgroup quota,
    CPU affinity, cpu_count."""
    if os.environ.get("PRAMAAN_THREADS"):
        return max(1, int(os.environ["PRAMAAN_THREADS"]))
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            return max(1, int(float(quota) / float(period)))
    except Exception:
        pass
    try:
        q = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        p = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if q > 0:
            return max(1, q // p)
    except Exception:
        pass
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except Exception:
        return os.cpu_count() or 2


def ensure_dirs() -> None:
    for d in (DATA_DIR, EVIDENCE_DIR, WORK_DIR, ARTIFACT_DIR, REPORT_DIR, KEY_DIR, DEVICE_DIR):
        d.mkdir(parents=True, exist_ok=True)
