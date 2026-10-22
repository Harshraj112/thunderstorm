"""Metadata extraction that works with no external tools, and merges ExifTool output if installed."""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

EXIF_IFD, GPS_IFD = 0x8769, 0x8825


def _clean(v: Any) -> Any:
    if isinstance(v, bytes):
        try:
            s = v.decode("utf-8", "ignore").strip("\x00 ").strip()
            return s if s.isprintable() else f"<{len(v)} bytes>"
        except Exception:
            return f"<{len(v)} bytes>"
    if isinstance(v, tuple):
        return [_clean(x) for x in v]
    if isinstance(v, (int, float, str)):
        return v
    if hasattr(v, "numerator") and hasattr(v, "denominator"):
        return float(v) if v.denominator else 0.0
    return v


def parse_exif_dt(s: Any) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.strip()[:19] if "%z" not in fmt else s.strip(), fmt)
        except ValueError:
            continue
    return None


def _gps_decimal(gps: dict) -> tuple[float, float] | None:
    try:
        def dms(v):
            d, m, s = (float(x) for x in v)
            return d + m / 60 + s / 3600
        lat = dms(gps["GPSLatitude"]) * (-1 if gps.get("GPSLatitudeRef") in ("S", b"S") else 1)
        lon = dms(gps["GPSLongitude"]) * (-1 if gps.get("GPSLongitudeRef") in ("W", b"W") else 1)
        return lat, lon
    except Exception:
        return None


def _jpeg_segments(data: bytes) -> list[tuple[int, bytes]]:
    """Return (marker, payload) of JPEG header segments up to SOS."""
    segs, i = [], 2
    if data[:2] != b"\xff\xd8":
        return segs
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xDA:  # SOS
            break
        ln = int.from_bytes(data[i + 2:i + 4], "big")
        segs.append((marker, data[i + 4:i + 2 + ln]))
        i += 2 + ln
    return segs


def _exif_thumbnail(app1: bytes) -> bytes | None:
    """Embedded EXIF thumbnail = a full JPEG (FFD8..FFD9) inside APP1, after the main IFDs."""
    start = app1.find(b"\xff\xd8", 8)
    if start < 0:
        return None
    end = app1.rfind(b"\xff\xd9")
    if end <= start:
        return None
    return app1[start:end + 2]


def extract(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"exif": {}, "gps": {}, "xmp": None, "png_text": {}, "thumbnail": None,
                           "c2pa_present": False, "jpeg": {}, "exiftool": None, "file": {}}
    data = path.read_bytes()
    st = path.stat()
    out["file"] = {"size": st.st_size, "mtime": datetime.fromtimestamp(st.st_mtime).isoformat()}
    try:
        img = Image.open(io.BytesIO(data))
        out["format"], out["width"], out["height"], out["mode"] = img.format, img.width, img.height, img.mode
        exif = img.getexif()
        for k, v in exif.items():
            out["exif"][ExifTags.TAGS.get(k, str(k))] = _clean(v)
        try:
            for k, v in exif.get_ifd(EXIF_IFD).items():
                name = ExifTags.TAGS.get(k, str(k))
                if name == "MakerNote":
                    out["exif"]["MakerNote"] = f"<{len(v) if isinstance(v, bytes) else 0} bytes>"
                else:
                    out["exif"][name] = _clean(v)
        except Exception:
            pass
        try:
            for k, v in exif.get_ifd(GPS_IFD).items():
                out["gps"][ExifTags.GPSTAGS.get(k, str(k))] = _clean(v)
        except Exception:
            pass
        if img.info.get("comment"):
            out["info_comment"] = _clean(img.info["comment"])
        if img.format == "PNG":
            dpi = img.info.get("dpi") or (0, 0)
            out["png_info"] = {"srgb": img.info.get("srgb"), "gamma": img.info.get("gamma"),
                               "dpi_x": float(dpi[0]) if dpi else 0.0}
            txt = getattr(img, "text", None) or {k: v for k, v in img.info.items() if isinstance(v, str)}
            out["png_text"] = {k: str(v)[:2000] for k, v in txt.items() if k not in ("XML:com.adobe.xmp",)}
        if img.format == "JPEG":
            q = getattr(img, "quantization", None) or {}
            out["jpeg"]["quantization"] = {int(k): list(v) for k, v in q.items()}
            out["jpeg"]["subsampling"] = _subsampling(img)
            out["jpeg"]["progressive"] = bool(img.info.get("progressive") or img.info.get("progression"))
    except Exception as e:  # not an image
        out["error"] = str(e)

    if data[:2] == b"\xff\xd8":
        for marker, payload in _jpeg_segments(data):
            if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
                thumb = _exif_thumbnail(payload)
                if thumb:
                    out["thumbnail"] = thumb
            if marker == 0xEB and (b"c2pa" in payload or b"jumb" in payload):
                out["c2pa_present"] = True
            if marker == 0xEE and payload.startswith(b"Adobe"):
                out["jpeg"]["adobe_app14"] = True
            if marker == 0xED and payload.startswith(b"Photoshop 3.0"):
                out["jpeg"]["photoshop_irb"] = True
    elif b"caBX" in data[:200000] or b"c2pa" in data[:200000]:
        out["c2pa_present"] = True

    m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", data[:2_000_000], re.S)
    if m:
        out["xmp"] = m.group(0).decode("utf-8", "ignore")[:20000]

    pos = _gps_decimal(out["gps"]) if out["gps"] else None
    if pos:
        out["gps_decimal"] = {"lat": round(pos[0], 6), "lon": round(pos[1], 6)}

    if shutil.which("exiftool"):
        try:
            r = subprocess.run(["exiftool", "-a", "-G1", "-s", "-json", str(path)],
                               capture_output=True, text=True, timeout=60)
            out["exiftool"] = json.loads(r.stdout)[0]
        except Exception:
            pass
    return out


def _subsampling(img) -> str:
    try:
        from PIL import JpegImagePlugin
        s = JpegImagePlugin.get_sampling(img)
        return {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}.get(s, str(s))
    except Exception:
        return "unknown"


def public_view(meta: dict) -> dict:
    """JSON-safe subset for storage/display."""
    m = {k: v for k, v in meta.items() if k not in ("thumbnail", "exiftool", "xmp")}
    m["thumbnail_present"] = meta.get("thumbnail") is not None
    m["xmp_present"] = meta.get("xmp") is not None
    if meta.get("jpeg", {}).get("quantization"):
        m["jpeg"] = {**meta["jpeg"], "quantization": {k: v[:16] for k, v in meta["jpeg"]["quantization"].items()}}
    return m
