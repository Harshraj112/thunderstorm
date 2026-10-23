from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np


def decode(path: Path, work: Path, sr: int | None = None) -> tuple[np.ndarray, int]:
    """Decode any audio/video file to mono float32 via ffmpeg (keeps native rate unless sr given)."""
    from scipy.io import wavfile
    out = work / f"_audio_{sr or 'native'}.wav"
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vn", "-ac", "1",
            "-c:a", "pcm_s16le"]
    if sr:
        args += ["-ar", str(sr)]
    subprocess.run(args + [str(out)], check=True, timeout=1800)
    rate, x = wavfile.read(out)
    x = x.astype(np.float32) / 32768.0
    return x, int(rate)


def frame_energy_db(x: np.ndarray, sr: int, win: float = 0.05) -> np.ndarray:
    n = int(sr * win)
    m = len(x) // n
    if m == 0:
        return np.array([])
    e = (x[:m * n].reshape(m, n) ** 2).mean(axis=1)
    return 10 * np.log10(e + 1e-12)
