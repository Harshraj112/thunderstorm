#!/usr/bin/env python3
"""Reference ENF logger – records Indian grid frequency at 1 s resolution.

Grid-India publishes only 15-minute averages, which is too coarse to timestamp recordings. A lab can
log its own reference: feed a SAFE low-voltage signal (the secondary of a small isolated 230V→9V AC
transformer, through a resistor divider) into a sound-card line input. NEVER connect mains directly.

    arecord -f S16_LE -r 1000 -c 1 -t raw | python scripts/enf_logger.py > enf_log.csv

Output: unix_time,frequency_hz  (one line per second). Each hourly file can be hashed into the custody
ledger so the reference itself is tamper-evident.
"""
import sys
import time

import numpy as np

FS = 1000
WIN = 4  # seconds of signal per estimate (hop 1 s)


def estimate(x: np.ndarray, f0: float = 50.0) -> float:
    x = x - x.mean()
    n = len(x) * 16
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
    f = np.fft.rfftfreq(n, 1 / FS)
    band = (f > f0 - 1) & (f < f0 + 1)
    i = np.argmax(spec * band)
    a, b, c = np.log(spec[i - 1:i + 2] + 1e-12)
    return float(f[i] + (a - c) / (2 * (a - 2 * b + c)) * (f[1] - f[0]))


def main():
    buf = np.zeros(0, dtype=np.float32)
    print("unix_time,frequency_hz", flush=True)
    while True:
        raw = sys.stdin.buffer.read(FS * 2)
        if not raw:
            break
        buf = np.concatenate([buf, np.frombuffer(raw, "<i2").astype(np.float32)])[-FS * WIN:]
        if len(buf) == FS * WIN:
            print(f"{time.time():.0f},{estimate(buf):.4f}", flush=True)


if __name__ == "__main__":
    main()
