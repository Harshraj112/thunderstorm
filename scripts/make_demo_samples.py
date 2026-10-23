#!/usr/bin/env python3
"""Generate a labelled golden set of pristine and tampered evidence for demos and regression tests.

    python scripts/make_demo_samples.py  [out_dir=demo_samples]

Everything is synthetic and clearly labelled (expected.json). For the real pitch, ALSO record
your own CCTV/phone footage and apply the same tampering recipes (see README §Demo).
"""
from __future__ import annotations

import io
import json
import struct
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.io import wavfile
from scipy import signal

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_samples")
EXPECTED: dict[str, dict] = {}


# ============================================================ minimal EXIF (TIFF) writer
ASCII, SHORT, LONG, RATIONAL = 2, 3, 4, 5
SIZES = {ASCII: 1, SHORT: 2, LONG: 4, RATIONAL: 8}


def _enc(typ, val) -> tuple[bytes, int]:
    if typ == ASCII:
        b = val.encode() + b"\0"
        return b, len(b)
    if typ == SHORT:
        val = val if isinstance(val, list) else [val]
        return b"".join(struct.pack("<H", v) for v in val), len(val)
    if typ == LONG:
        val = val if isinstance(val, list) else [val]
        return b"".join(struct.pack("<I", v) for v in val), len(val)
    if typ == RATIONAL:
        return b"".join(struct.pack("<II", n, d) for n, d in val), len(val)
    raise ValueError(typ)


def _ifd(entries, base, next_off=0) -> bytes:
    entries = sorted(entries, key=lambda e: e[0])
    head = struct.pack("<H", len(entries))
    data_start = base + 2 + 12 * len(entries) + 4
    body, data = b"", b""
    for tag, typ, val in entries:
        raw, count = _enc(typ, val)
        if len(raw) <= 4:
            body += struct.pack("<HHI", tag, typ, count) + raw.ljust(4, b"\0")
        else:
            body += struct.pack("<HHII", tag, typ, count, data_start + len(data))
            data += raw + (b"\0" if len(raw) % 2 else b"")
    return head + body + struct.pack("<I", next_off) + data


def _size(entries):
    return len(_ifd(entries, 0))


def make_exif(ifd0, exif_ifd, gps_ifd, thumb: bytes | None) -> bytes:
    ifd0 = list(ifd0) + [(0x8769, LONG, 0), (0x8825, LONG, 0)]
    ifd1 = [(0x0103, SHORT, 6), (0x0201, LONG, 0), (0x0202, LONG, len(thumb) if thumb else 0)]
    o0 = 8
    oe = o0 + _size(ifd0)
    og = oe + _size(exif_ifd)
    o1 = og + _size(gps_ifd)
    ot = o1 + _size(ifd1)
    ifd0 = [e for e in ifd0 if e[0] not in (0x8769, 0x8825)] + [(0x8769, LONG, oe), (0x8825, LONG, og)]
    ifd1 = [(0x0103, SHORT, 6), (0x0201, LONG, ot), (0x0202, LONG, len(thumb) if thumb else 0)]
    tiff = b"II*\0" + struct.pack("<I", o0)
    tiff += _ifd(ifd0, o0, o1 if thumb else 0) + _ifd(exif_ifd, oe) + _ifd(gps_ifd, og)
    if thumb:
        tiff += _ifd(ifd1, o1) + thumb
    return b"Exif\0\0" + tiff


def put_exif(jpeg: bytes, exif: bytes) -> bytes:
    assert jpeg[:2] == b"\xff\xd8"
    i = 2
    while jpeg[i] == 0xFF and jpeg[i + 1] in (0xE0, 0xE1):  # drop APP0/APP1
        i += 2 + int.from_bytes(jpeg[i + 2:i + 4], "big")
    return b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[i:]


def dms(v: float):
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    s = round(((v - d) * 60 - m) * 60 * 100)
    return [(d, 1), (m, 1), (s, 100)]


def camera_exif(rgb: np.ndarray, when: datetime, lat: float, lon: float, software: str = "S911BXXU3CXH1",
                modify: datetime | None = None, thumb_rgb: np.ndarray | None = None, gps_when: datetime | None = None):
    h, w = rgb.shape[:2]
    t = Image.fromarray(thumb_rgb if thumb_rgb is not None else rgb).resize((160, int(160 * h / w)))
    buf = io.BytesIO()
    t.save(buf, "JPEG", quality=85)
    fmt = "%Y:%m:%d %H:%M:%S"
    g = (gps_when or when) - timedelta(hours=5, minutes=30)
    ifd0 = [(0x010F, ASCII, "samsung"), (0x0110, ASCII, "SM-S911B"), (0x0131, ASCII, software),
            (0x0132, ASCII, (modify or when).strftime(fmt)), (0x0112, SHORT, 1)]
    exif_ifd = [(0x9003, ASCII, when.strftime(fmt)), (0x9004, ASCII, when.strftime(fmt)),
                (0x9011, ASCII, "+05:30"), (0xA002, LONG, w), (0xA003, LONG, h),
                (0x927C, LONG, [1, 2, 3, 4, 5, 6, 7, 8])]  # stand-in MakerNote blob
    gps = [(0x0001, ASCII, "N" if lat >= 0 else "S"), (0x0002, RATIONAL, dms(lat)),
           (0x0003, ASCII, "E" if lon >= 0 else "W"), (0x0004, RATIONAL, dms(lon)),
           (0x0007, RATIONAL, [(g.hour, 1), (g.minute, 1), (g.second, 1)]),
           (0x001D, ASCII, g.strftime("%Y:%m:%d"))]
    return make_exif(ifd0, exif_ifd, gps, buf.getvalue())


# ============================================================ images
def scene(seed, h=900, w=1200, noise=4.0):
    r = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3))
    for c in range(3):
        img[..., c] = 80 + 60 * np.sin(x / (120 + 30 * c) + seed) + 50 * np.cos(y / (90 + 20 * c))
    for _ in range(40):
        cx, cy, rad = r.integers(0, w), r.integers(0, h), r.integers(15, 80)
        img[(x - cx) ** 2 + (y - cy) ** 2 < rad ** 2] += r.normal(0, 40, 3)
    img += (cv2.GaussianBlur(r.normal(0, 1, (h, w)), (0, 0), 1.5) * 25)[..., None]
    for _ in range(60):
        x0, y0 = int(r.integers(0, w - 60)), int(r.integers(0, h - 40))
        cv2.rectangle(img, (x0, y0), (x0 + int(r.integers(10, 60)), y0 + int(r.integers(10, 40))),
                      r.uniform(0, 255, 3).tolist(), -1)
    img = cv2.GaussianBlur(img, (0, 0), 1.0) + r.normal(0, noise, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def jpeg_bytes(rgb, q, camera=False):
    b = io.BytesIO()
    if camera:  # vendor-tuned tables (not the IJG standard), as written by real phone ISPs
        luma = [max(1, min(255, int(round(v * 0.16 + (i % 3))))) for i, v in enumerate(_STD)]
        chroma = [max(1, min(255, int(round(v * 0.22 + (i % 2))))) for i, v in enumerate(_STD_C)]
        Image.fromarray(rgb).save(b, "JPEG", qtables=[luma, chroma], subsampling=2)
    else:
        Image.fromarray(rgb).save(b, "JPEG", quality=q)
    return b.getvalue()


_STD = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29,
        51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78, 87, 103, 121,
        120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
_STD_C = [17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99, 24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99,
          99, 99, 99, 99, 99] + [99] * 32


def images():
    d = OUT / "images"
    d.mkdir(parents=True, exist_ok=True)
    when = datetime(2026, 9, 14, 16, 42, 10)
    lat, lon = 28.6139, 77.2090
    a = scene(1)
    base = jpeg_bytes(a, 92, camera=True)
    (d / "01_pristine_photo.jpg").write_bytes(put_exif(base, camera_exif(a, when, lat, lon)))
    EXPECTED["01_pristine_photo.jpg"] = {"tampered": False, "note": "camera original"}

    # splice: object from another, lower-quality JPEG pasted; saved by Photoshop; OLD thumbnail kept
    donor = np.asarray(Image.open(io.BytesIO(jpeg_bytes(scene(7, noise=0.5), 60))))
    s = a.copy()
    s[300:556, 400:720] = donor[100:356, 200:520]
    ex = camera_exif(s, when, lat, lon, software="Adobe Photoshop 25.0 (Windows)",
                     modify=when + timedelta(days=3, hours=2), thumb_rgb=a)
    (d / "02_spliced_photoshop.jpg").write_bytes(put_exif(jpeg_bytes(s, 92), ex))
    EXPECTED["02_spliced_photoshop.jpg"] = {"tampered": True, "region": [400, 300, 320, 256]}

    # copy-move: a region cloned elsewhere (hiding an object), metadata stripped like a WhatsApp forward
    c = a.copy()
    c[600:760, 800:1000] = a[150:310, 100:300]
    (d / "03_copy_move.jpg").write_bytes(jpeg_bytes(c, 90))
    EXPECTED["03_copy_move.jpg"] = {"tampered": True, "region": [800, 600, 200, 160]}

    # metadata forgery: pixels untouched, DateTimeOriginal shifted by 2 days (GPS clock betrays it)
    forged = camera_exif(a, when - timedelta(days=2), lat, lon, gps_when=when)
    (d / "04_metadata_backdated.jpg").write_bytes(put_exif(base, forged))
    EXPECTED["04_metadata_backdated.jpg"] = {"tampered": True, "note": "capture date edited"}

    # AI generated: PNG carrying Stable-Diffusion style generation parameters
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a crowd near a police barricade, photorealistic\nNegative prompt: blurry\n"
                                "Steps: 30, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 1234, Model: sdxl_base_1.0")
    Image.fromarray(scene(11)).save(d / "05_ai_generated.png", pnginfo=info)
    EXPECTED["05_ai_generated.png"] = {"tampered": True, "note": "synthetic"}

    # WhatsApp-like forward of the pristine photo: no EXIF, resized, Q75. Must NOT be called tampered.
    wa = np.asarray(Image.fromarray(a).resize((1000, 750), Image.LANCZOS))
    (d / "06_whatsapp_forward.jpg").write_bytes(jpeg_bytes(wa, 75))
    EXPECTED["06_whatsapp_forward.jpg"] = {"tampered": False, "note": "benign recompression"}


# ============================================================ CCTV
def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def cctv():
    d = OUT / "cctv"
    d.mkdir(parents=True, exist_ok=True)
    raw = d / "_master.mp4"
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
    start = int(datetime(2026, 9, 14, 22, 15, 0).timestamp())
    fps, dur = 10, 60
    # moving scene + burned-in DVR clock (like a real OSD)
    # testsrc2 = moving scene; temporal noise = real sensor noise (every frame differs, as in real CCTV)
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=size=640x360:rate={fps}:duration={dur}",
           "-vf", f"noise=alls=10:allf=t,drawbox=x=0:y=0:w=iw:h=40:color=black:t=fill,"
                  f"drawtext=fontfile={font}:text='%{{pts\\:localtime\\:{start}}}':x=12:y=8:fontsize=24:fontcolor=white,"
                  f"drawtext=fontfile={font}:text='CAM-03 GATE':x=w-tw-12:y=8:fontsize=20:fontcolor=white",
           "-c:v", "libx264", "-g", "25", "-keyint_min", "25", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
           "-metadata", "creation_time=" + datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), str(raw))
    cap = cv2.VideoCapture(str(raw))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()

    def write(name, seq):
        tmp = d / "_tmp.avi"
        vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"MJPG"), fps, (640, 360))
        for f in seq:
            vw.write(f)
        vw.release()
        ffmpeg("-i", str(tmp), "-c:v", "libx264", "-g", "20", "-keyint_min", "20", "-sc_threshold", "0",
               "-pix_fmt", "yuv420p", "-metadata", "creation_time=" + datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               str(d / name))
        tmp.unlink()

    import shutil
    shutil.copy(raw, d / "01_cctv_pristine.mp4")   # the DVR original: encoded once (GOP 25)
    EXPECTED["01_cctv_pristine.mp4"] = {"tampered": False}
    write("02_cctv_frames_deleted.mp4", frames[:200] + frames[320:])           # 12 s removed at t=20 s, re-timed
    EXPECTED["02_cctv_frames_deleted.mp4"] = {"tampered": True, "at_s": 20, "kind": "deletion 12s"}
    write("03_cctv_reordered.mp4", frames[:200] + frames[400:] + frames[200:400])
    EXPECTED["03_cctv_reordered.mp4"] = {"tampered": True, "at_s": [20, 40], "kind": "reorder"}
    loop = frames[:300] + frames[100:180] + frames[380:]                        # earlier 8 s pasted over 30–38 s
    write("04_cctv_loop_insert.mp4", loop)
    EXPECTED["04_cctv_loop_insert.mp4"] = {"tampered": True, "at_s": 30, "kind": "loop insert"}
    frz = frames[:250] + [frames[250]] * 60 + frames[310:]                      # freeze 6 s
    write("05_cctv_frozen.mp4", frz)
    EXPECTED["05_cctv_frozen.mp4"] = {"tampered": True, "at_s": 25, "kind": "freeze"}
    raw.unlink()


# ============================================================ audio
SR = 16000


def speechlike(seconds, seed):
    r = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    f0 = 140 + 40 * np.sin(2 * np.pi * 0.3 * t + seed) + 15 * np.sin(2 * np.pi * 1.7 * t)
    ph = 2 * np.pi * np.cumsum(f0) / SR
    voiced = sum(np.sin(k * ph) / k for k in range(1, 25))
    env = np.clip(np.sin(2 * np.pi * 3.8 * t + r.uniform(0, 6)) + 0.2 * r.normal(size=1), 0, None) ** 1.5
    pauses = (np.sin(2 * np.pi * 0.21 * t + seed) > -0.6).astype(float)
    env = signal.filtfilt(*signal.butter(2, 20 / (SR / 2)), env * pauses)
    b, a = signal.butter(2, [400 / (SR / 2), 3200 / (SR / 2)], "band")
    return signal.lfilter(b, a, voiced * env) * 0.3


def enf_hum(seconds, seed, grid=50.0, amp=0.004):
    r = np.random.default_rng(seed)
    n = int(seconds * SR)
    drift = np.cumsum(r.normal(0, 0.0015, int(seconds) + 2))
    drift = np.clip(drift, -0.06, 0.06)
    f = grid + np.interp(np.arange(n) / SR, np.arange(len(drift)), drift)
    ph = 2 * np.pi * np.cumsum(f) / SR + r.uniform(0, 2 * np.pi)
    return amp * (np.sin(ph) + 0.6 * np.sin(2 * ph + 0.3))


def room(seconds, seed, level):
    r = np.random.default_rng(seed)
    w = r.normal(0, 1, int(seconds * SR))
    b, a = signal.butter(1, 1500 / (SR / 2))
    return signal.lfilter(b, a, w) * level


def wav(path, x, sr=SR):
    x = np.clip(x, -1, 1)
    wavfile.write(path, sr, (x * 32767).astype(np.int16))


def audio():
    d = OUT / "audio"
    d.mkdir(parents=True, exist_ok=True)
    T = 40
    base = speechlike(T, 1) + room(T, 2, 0.004) + enf_hum(T, 3)
    wav(d / "01_audio_pristine.wav", base)
    EXPECTED["01_audio_pristine.wav"] = {"tampered": False}
    cut = np.concatenate([base[:18 * SR], base[int(21.37 * SR):]])           # 3.37 s removed -> ENF phase jump
    wav(d / "02_audio_words_deleted.wav", cut)
    EXPECTED["02_audio_words_deleted.wav"] = {"tampered": True, "at_s": 18.0}
    other = speechlike(5, 9) + room(5, 10, 0.02) + enf_hum(5, 11)            # louder room, different ENF phase
    ins = np.concatenate([base[:15 * SR], other, base[15 * SR:]])
    wav(d / "03_audio_insert_other_room.wav", ins)
    EXPECTED["03_audio_insert_other_room.wav"] = {"tampered": True, "at_s": [15.0, 20.0]}
    us = speechlike(T, 1) + room(T, 2, 0.004) + enf_hum(T, 3, grid=60.0)
    wav(d / "04_audio_60hz_grid.wav", us)
    EXPECTED["04_audio_60hz_grid.wav"] = {"tampered": True, "note": "60 Hz mains: not recorded on Indian grid"}
    sil = base.copy()
    sil[int(25 * SR):int(25.6 * SR)] = 0.0
    wav(d / "05_audio_digital_silence.wav", sil)
    EXPECTED["05_audio_digital_silence.wav"] = {"tampered": True, "at_s": 25.0}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    images()
    print("images done")
    audio()
    print("audio done")
    cctv()
    print("cctv done")
    (OUT / "expected.json").write_text(json.dumps(EXPECTED, indent=2))
    print(f"Wrote {len(EXPECTED)} samples to {OUT}/")
