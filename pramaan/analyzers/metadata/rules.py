"""Metadata consistency rule engine.

Design principle: metadata is trivially editable and is routinely stripped by messaging apps,
so ABSENCE of metadata is neutral. Rules look for INTERNAL INCONSISTENCIES and editing traces.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

from ... import config
from ..base import FAIL, INFO, PASS, WARN, Context, Finding, status_from_llr
from .extract import extract, parse_exif_dt, public_view
from .solar import solar_elevation

MOD = "metadata"

EDITORS = ["photoshop", "gimp", "lightroom", "snapseed", "picsart", "pixlr", "affinity", "paint.net",
           "canva", "facetune", "meitu", "photoscape", "capture one", "luminar", "fotor", "inshot",
           "remini", "photo editor", "photoroom", "pixelmator", "darktable", "rawtherapee", "acdsee"]
AI_MARKERS = ["trainedalgorithmicmedia", "compositewithtrainedalgorithmicmedia", "stable diffusion",
              "midjourney", "dall-e", "dall·e", "firefly", "imagen", "gemini", "openai", "comfyui",
              "automatic1111", "novelai", "leonardo.ai", "flux", "ideogram", "sdxl", "negative prompt",
              "steps:", "cfg scale", "sampler:", "seed:"]
BRANDS_WITH_MAKERNOTES = ["apple", "samsung", "canon", "nikon", "sony", "fujifilm", "olympus", "panasonic",
                          "google", "xiaomi", "oneplus", "oppo", "vivo", "huawei", "motorola", "realme"]


def _f(code, title, llr, rel, expl, innocent=None, status=None, data=None, family="metadata"):
    return Finding(code=code, module=MOD, family=family, title=title, llr=llr, reliability=rel,
                   status=status or status_from_llr(llr), explanation=expl, innocent=innocent or [],
                   data=data or {})


def run(ctx: Context) -> list[Finding]:
    meta = extract(ctx.path)
    ctx.shared["meta"] = meta
    ex, gps = meta["exif"], meta["gps"]
    out: list[Finding] = []
    blob = " ".join([str(ex), str(meta.get("png_text")), meta.get("xmp") or "", str(meta.get("exiftool") or "")]).lower()

    # ---------- inventory ----------
    has_exif = bool({"Make", "Model", "DateTimeOriginal"} & set(ex))
    out.append(Finding(code="META_INVENTORY", module=MOD, family="metadata", title="Metadata inventory",
                       status=INFO, reliability=0.0,
                       explanation=("Camera metadata present: " + ", ".join(
                           f"{k}={ex[k]}" for k in ("Make", "Model", "Software", "DateTimeOriginal") if k in ex))
                       if has_exif else
                       "No camera EXIF found. This is normal for images sent via WhatsApp/Telegram/social media "
                       "and screenshots, and is NOT by itself an indication of tampering.",
                       data={"metadata": public_view(meta)}))

    # ---------- AI generation markers (strong) ----------
    hits = sorted({m for m in AI_MARKERS if m in blob})
    strong = [h for h in hits if h not in ("steps:", "seed:", "sampler:", "gemini", "openai", "flux")]
    if strong or len(hits) >= 3:
        out.append(_f("META_AI_GENERATOR", "AI-generation / provenance marker in metadata", 3.5, 0.9,
                      f"Metadata contains generator markers: {', '.join(hits)}. Under the IT (Intermediary) "
                      "Amendment Rules 2026, synthetic content should carry such labels.",
                      ["Marker copied into metadata of a genuine photo (rare, deliberate)"],
                      family="synthetic-image"))

    # ---------- editing software ----------
    sw = str(ex.get("Software", "")) + " " + str(ex.get("ProcessingSoftware", ""))
    ed = [e for e in EDITORS if e in sw.lower()]
    xmp = (meta.get("xmp") or "").lower()
    xmp_hist = bool(re.search(r"stevt:action>(saved|converted|derived|edited)|photoshop:history|xmpmm:history", xmp))
    if ed:
        out.append(_f("META_EDITOR_SOFTWARE", f"Editing software recorded: {sw.strip()}", 1.5, 0.8,
                      "The Software tag names an image editor, so the file was processed after capture.",
                      ["Benign adjustments (crop, exposure, rotate) or export for printing"]))
    if xmp_hist or meta["jpeg"].get("photoshop_irb"):
        out.append(_f("META_EDIT_HISTORY", "Editing history (XMP/Photoshop) present", 1.2, 0.7,
                      "XMP history or Photoshop resource blocks record save/derive actions by editing software.",
                      ["Lossless re-save or metadata-only edit"]))

    # ---------- timestamp chain ----------
    dto, dtd, dtm = (parse_exif_dt(ex.get(k)) for k in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"))
    now = datetime.now()
    if dto:
        problems = []
        if dto > now + timedelta(days=1):
            problems.append(f"DateTimeOriginal {dto} is in the future")
        if dto.year < 1995:
            problems.append(f"DateTimeOriginal {dto} predates consumer digital cameras")
        if dtd and abs((dtd - dto).total_seconds()) > 2:
            problems.append(f"DateTimeDigitized ({dtd}) differs from DateTimeOriginal ({dto})")
        if dtm and (dtm - dto).total_seconds() < -2:
            problems.append(f"ModifyDate ({dtm}) is earlier than capture time ({dto})")
        modified_later = dtm and (dtm - dto).total_seconds() > 60
        if problems:
            out.append(_f("META_TIME_CHAIN", "Timestamp chain inconsistent", 1.8, 0.75, "; ".join(problems) + ".",
                          ["Camera clock reset or battery change", "Scanner/film workflow"]))
        elif modified_later:
            out.append(_f("META_TIME_MODIFIED", "File modified after capture", 0.8 if ed else 0.5, 0.6,
                          f"ModifyDate {dtm} is {(dtm - dto)} after capture {dto}.",
                          ["Re-saved by gallery app, rotation, or metadata edit"]))
        else:
            out.append(_f("META_TIME_CHAIN", "Timestamp chain consistent", -0.3, 0.5,
                          "Capture, digitisation and modification times agree.", status=PASS))

    # ---------- GPS ----------
    pos = meta.get("gps_decimal")
    if pos:
        lat, lon = pos["lat"], pos["lon"]
        la0, la1, lo0, lo1 = config.REGION_BBOX
        if abs(lat) < 0.01 and abs(lon) < 0.01:
            out.append(_f("META_GPS_NULL", "GPS is (0,0) 'null island'", 1.0, 0.6,
                          "Coordinates 0,0 are a classic placeholder written by editors/spoofing tools.",
                          ["GPS fix not acquired and firmware wrote zeros"]))
        elif not (la0 <= lat <= la1 and lo0 <= lon <= lo1):
            out.append(_f("META_GPS_REGION", "GPS outside expected jurisdiction", 0.4, 0.4,
                          f"Coordinates ({lat}, {lon}) fall outside the configured region.",
                          ["Photo genuinely taken elsewhere; travel"]))
        # GPS UTC time vs local capture time
        gdt = _gps_datetime(gps)
        if gdt and dto:
            out.append(_gps_time_rule(dto, gdt.replace(tzinfo=None),
                                      _offset_minutes(ex.get("OffsetTimeOriginal") or ex.get("OffsetTime"))))
        # solar plausibility
        when = gdt or ((dto - timedelta(minutes=_offset_minutes(ex.get("OffsetTimeOriginal"))
                                        or config.DEFAULT_TZ_OFFSET_MIN)).replace(tzinfo=timezone.utc) if dto else None)
        if when is not None:
            elev = solar_elevation(lat, lon, when)
            lum = _mean_luma(ctx.path)
            f = None
            if elev < -8 and lum is not None and lum > 150:
                f = _f("META_SOLAR", "Bright image but sun below horizon at GPS/time", 0.8, 0.35,
                       f"Sun elevation {elev:.1f}° at the claimed place/time, yet mean brightness is {lum:.0f}/255.",
                       ["Indoor or floodlit scene", "Long exposure / night mode"])
            elif elev > 20 and lum is not None and lum < 35:
                f = _f("META_SOLAR", "Very dark image while sun is high at GPS/time", 0.5, 0.25,
                       f"Sun elevation {elev:.1f}° yet mean brightness is {lum:.0f}/255.",
                       ["Indoor scene", "Underexposed shot"])
            if f:
                out.append(f)
            else:
                out.append(Finding(code="META_SOLAR", module=MOD, family="metadata",
                                   title="Day/night consistent with GPS + time", status=INFO, llr=0.0,
                                   reliability=0.2, explanation=f"Sun elevation {elev:.1f}° at claimed place/time."))

    # ---------- thumbnail vs main image ----------
    thumb = meta.get("thumbnail")
    if thumb:
        out.append(_thumbnail_rule(ctx.path, thumb, ctx))

    # ---------- dimensions vs EXIF dimensions ----------
    ew, eh = ex.get("ExifImageWidth"), ex.get("ExifImageHeight")
    if isinstance(ew, (int, float)) and isinstance(eh, (int, float)) and meta.get("width"):
        if {int(ew), int(eh)} != {meta["width"], meta["height"]}:
            out.append(_f("META_DIMENSIONS", "Pixel size differs from EXIF-recorded size", 1.2, 0.7,
                          f"EXIF says {ew}x{eh}, actual {meta['width']}x{meta['height']}: cropped or resized after capture.",
                          ["Resize by a sharing app that preserved EXIF"]))

    # ---------- device plausibility ----------
    make = str(ex.get("Make", "")).lower()
    if make and any(b in make for b in BRANDS_WITH_MAKERNOTES) and "MakerNote" not in ex and not ed:
        out.append(_f("META_MAKERNOTE", "Camera brand present but MakerNote missing", 0.5, 0.4,
                      f"{ex.get('Make')} devices normally write a MakerNote block; it is absent, which happens when "
                      "metadata is rewritten by a tool.", ["Some apps/firmware omit MakerNotes"]))

    # ---------- forged / planted metadata (C2PA itself is verified by the provenance module) ----------
    out += forgery_rules(meta, ctx)
    return out


CAMERA_VENDORS = [
    # phones and cameras
    "apple", "samsung", "canon", "nikon", "sony", "fujifilm", "olympus", "om digital", "panasonic", "leica", "pentax",
    "ricoh", "google", "xiaomi", "redmi", "poco", "oneplus", "oppo", "vivo", "realme", "huawei", "honor", "motorola",
    "nokia", "hmd", "lg", "htc", "asus", "lenovo", "zte", "tecno", "infinix", "itel", "lava", "micromax", "nothing",
    "gopro", "dji", "insta360", "kodak", "casio", "sigma", "hasselblad", "phase one", "blackmagic", "microsoft",
    "meizu", "sharp", "kyocera", "alcatel", "tcl", "fairphone", "polaroid", "minolta", "konica", "epson", "hp",
    # CCTV / DVR / NVR
    "hikvision", "hikvision digital", "dahua", "cp plus", "cp-plus", "uniview", "unv", "axis", "hanwha", "wisenet",
    "bosch", "honeywell", "avigilon", "godrej", "zkteco", "ezviz", "imou", "tp-link", "tapo", "reolink", "eufy",
    "wyze", "arlo", "ring", "nest", "blink", "d-link", "foscam", "amcrest", "lorex", "swann", "annke", "qubo",
    "trueview", "secureye", "vivotek", "mobotix", "pelco", "milesight", "tiandy", "hi-focus", "hifocus", "sparsh",
    "prama", "infinova", "geovision", "provision", "zicom",
]
CAMERA_KEYS = ("make", "model", "datetimeoriginal", "createdate", "gpslatitude", "gpslongitude", "lensmodel",
               "exposuretime", "fnumber", "iso")
EXPORT_WORDS = ("export", "processing", "suite", "utility", "converter", "editor", "studio", "toolkit")
# Native output sizes of popular image generators (w, h); a camera or DVR never produces most of these.
AI_SIZES = {(1024, 1024), (1536, 1024), (1024, 1536), (1792, 1024), (1024, 1792), (1672, 941), (1672, 940),
            (941, 1672), (940, 1672), (1344, 768), (768, 1344), (1216, 832), (832, 1216), (1152, 896), (896, 1152),
            (1248, 832), (832, 1248), (1184, 864), (864, 1184), (1536, 640), (640, 1536), (512, 512), (768, 768),
            (2048, 2048), (1408, 768), (768, 1408), (1536, 672), (672, 1536), (1824, 1024), (1024, 1824)}


def forgery_rules(meta: dict, ctx: Context) -> list[Finding]:
    """Signs that camera-style metadata was written by a tool rather than by the device it names."""
    out: list[Finding] = []
    ex = meta.get("exif", {})
    pt = {str(k).lower(): v for k, v in (meta.get("png_text") or {}).items()}
    w, h, mode = meta.get("width"), meta.get("height"), meta.get("mode")
    make = str(ex.get("Make") or pt.get("make") or "").strip()
    model = str(ex.get("Model") or pt.get("model") or "").strip()
    software = str(ex.get("Software") or pt.get("software") or "").strip()

    cam_in_png = [k for k in CAMERA_KEYS if k in pt]
    if meta.get("format") == "PNG" and len(cam_in_png) >= 2:
        out.append(_f("META_PNG_CAMERA_TEXT", f"Camera metadata typed into PNG text chunks ({', '.join(cam_in_png)})",
                      2.5, 0.9,
                      "Fields such as Make, Model, capture time and GPS are stored here as PNG text chunks. Cameras, "
                      "phones and CCTV recorders record these as EXIF inside a JPEG/HEIC or in the video container; no "
                      "capture device writes them as PNG text. They were added by a tool after the image existed, so "
                      "the 'camera', time and place they describe cannot be trusted.",
                      ["An evidence-management system that stores case notes in PNG text (check with the source)"],
                      data={"png_camera_fields": {k: pt[k] for k in cam_in_png}}))
    if make and not any(v in make.lower() for v in CAMERA_VENDORS):
        out.append(_f("META_UNKNOWN_MAKE", f"Camera maker '{make}' is not a known device manufacturer", 1.0, 0.6,
                      f"Make='{make}', Model='{model}'. This does not match any phone, camera or CCTV manufacturer in "
                      "PRAMAAN's list, which is typical of hand-written metadata.",
                      ["Rare or rebranded OEM device: confirm with the seized hardware"]))
    if make and mode in ("RGBA", "LA", "PA"):
        out.append(_f("META_ALPHA_CAMERA", "Image claims a camera source but has a transparency channel", 1.2, 0.7,
                      f"The file is {mode} (with an alpha channel) while its metadata names a camera ({make} {model}). "
                      "Camera and DVR images never contain transparency; the picture was converted or composed in an "
                      "editor.", ["Converted to PNG by an archiving tool"]))
    comment = str(meta.get("info_comment") or "")
    if make and comment.lower().startswith("lavc"):
        out.append(_f("META_FFMPEG_CAMERA", "JPEG written by FFmpeg, not by the camera it names", 1.0, 0.65,
                      f"The JPEG comment '{comment}' shows it was encoded by FFmpeg (libavcodec), i.e. a frame grab or "
                      f"conversion, while EXIF claims Make='{make}'.",
                      ["Frame legitimately exported from CCTV footage with FFmpeg, then tagged by the lab"]))
    if software and any(wd in software.lower() for wd in EXPORT_WORDS) and not any(e in software.lower() for e in EDITORS):
        out.append(_f("META_PROCESSING_SOFTWARE", f"Processed by '{software}' after capture", 0.8, 0.6,
                      "The Software field names an export/processing tool rather than camera firmware, so the file is "
                      "not the first-generation original.", ["Routine export from a DVR/NVR or evidence system"]))
    # time chain for PNG-text dates (EXIF dates are handled above)
    dto = parse_exif_dt(pt.get("datetimeoriginal"))
    dtm = parse_exif_dt(pt.get("modifydate"))
    if dto and dtm and (dtm - dto).total_seconds() > 3600:
        out.append(_f("META_TIME_MODIFIED", "File modified after capture", 0.6, 0.6,
                      f"ModifyDate {dtm} is {dtm - dto} after DateTimeOriginal {dto}.",
                      ["Re-saved by an archive or gallery app"]))
    if w and h and any(abs(w - a) <= 2 and abs(h - b) <= 2 for a, b in AI_SIZES):
        out.append(_f("IMG_AI_NATIVE_SIZE", f"{w}×{h}: a native output size of AI image generators", 1.2, 0.55,
                      "This exact pixel size is what ChatGPT/DALL·E, Gemini, Midjourney or Stable Diffusion produce by "
                      "default, and is not a sensor resolution of common phones or CCTV cameras.",
                      ["Photo cropped or resized to this size by coincidence"], family="synthetic-image"))
    info = meta.get("png_info") or {}
    if meta.get("format") == "PNG" and not make and not cam_in_png and info.get("srgb") is not None and \
            info.get("gamma") and abs(float(info.get("dpi_x") or 0) - 96) < 0.2:
        out.append(Finding(code="IMG_SCREENSHOT", module=MOD, family="metadata", status=INFO, reliability=0.0,
                           title="Looks like a screenshot or Snipping Tool export",
                           explanation="PNG with sRGB, gamma 0.45455 and 96 dpi and no capture metadata: the signature of "
                                       "a Windows screenshot. It records what was on a screen, not what a camera saw, so "
                                       "camera-based tests have little to work with."))
    return out


def _gps_time_rule(dto: datetime, gps_utc: datetime, off: int | None) -> Finding:
    """Camera clock (local) vs GPS satellite clock (UTC).
    With a recorded UTC offset the two must agree within minutes. Without one, any whole/half-hour
    difference up to 14 h is just the time zone; anything else – especially days – means the capture
    date/time was changed after the fact."""
    if off is not None:
        diff = abs((dto - timedelta(minutes=off) - gps_utc).total_seconds())
        if diff <= 120:
            return _f("META_GPS_TIME", "GPS clock agrees with capture time", -0.5, 0.65,
                      f"Within {diff:.0f} s after applying the recorded UTC offset ({off:+d} min).", status=PASS)
        big = diff > 6 * 3600
        return _f("META_GPS_TIME", "GPS clock disagrees with capture time", 2.2 if big else 1.4, 0.75 if big else 0.7,
                  f"GPS UTC {gps_utc:%Y-%m-%d %H:%M:%S} vs capture {dto} (offset {off:+d} min) differ by "
                  + (f"{diff / 86400:.1f} days. The capture date appears to have been edited." if diff > 86400
                     else f"{diff / 60:.0f} min."),
                  ["Stale GPS fix cached by the phone", "Camera clock set wrongly by the owner"])
    d = (dto - gps_utc).total_seconds()                 # = local UTC offset if the clocks are honest
    tz_err = abs(d - round(d / 1800) * 1800)
    if abs(d) <= 14 * 3600 and tz_err <= 120:
        return _f("META_GPS_TIME", f"GPS clock consistent with capture time (time zone UTC{d / 3600:+.1f} h)", -0.4, 0.55,
                  f"Capture time minus GPS UTC time is {d / 3600:+.2f} h, a valid time-zone offset.", status=PASS)
    if abs(d) > 14 * 3600:
        return _f("META_GPS_TIME", "GPS clock disagrees with capture date", 2.2, 0.75,
                  f"Capture time {dto} is {abs(d) / 86400:.1f} days {'after' if d > 0 else 'before'} the GPS satellite "
                  f"time {gps_utc:%Y-%m-%d %H:%M:%S} UTC. No time zone explains more than 14 hours, so the capture "
                  "date was most likely changed with a metadata editor (the GPS block is often forgotten).",
                  ["GPS block copied from another photo", "Camera clock badly wrong (battery reset)"])
    return _f("META_GPS_TIME", "GPS clock offset is not a whole time zone", 0.9, 0.5,
              f"Capture time minus GPS UTC is {d / 3600:+.2f} h – {tz_err / 60:.0f} min away from any valid time-zone "
              "offset.", ["Stale GPS fix", "Camera clock drift over months"])


def _thumbnail_rule(path, thumb: bytes, ctx: Context) -> Finding:
    from skimage.metrics import structural_similarity as ssim
    try:
        t = np.asarray(Image.open(io.BytesIO(thumb)).convert("L"), dtype=float)
        m_img = Image.open(path)
        m_img.draft("L", (t.shape[1] * 2, t.shape[0] * 2))
        m = np.asarray(m_img.convert("L").resize((t.shape[1], t.shape[0]), Image.LANCZOS), dtype=float)
    except Exception as e:
        return Finding(code="META_THUMBNAIL", module=MOD, family="metadata", title="Thumbnail unreadable",
                       status=INFO, explanation=str(e))
    ar_t = t.shape[1] / t.shape[0]
    mw, mh = Image.open(path).size
    ar_m = mw / mh
    s = float(ssim(t, m, data_range=255))
    diff = np.abs(t - m)
    from ..base import save_heatmap
    rgb = np.stack([m] * 3, -1).astype(np.uint8)
    art = save_heatmap(rgb, diff / max(diff.max(), 1), ctx.artifact("thumbnail_diff.png"), 0.6)
    if abs(ar_t - ar_m) > 0.05 or s < 0.6:
        return _f("META_THUMBNAIL", "Embedded thumbnail does not match the image", 2.2, 0.8,
                  f"The EXIF preview thumbnail differs from the main image (SSIM {s:.2f}, aspect "
                  f"{ar_t:.2f} vs {ar_m:.2f}). Many editors change pixels without regenerating the thumbnail, "
                  "so the thumbnail may show the ORIGINAL content.",
                  ["Rotation applied by viewer only", "Thumbnail regenerated at different crop by the phone"],
                  data={"ssim": s}) | _art(art)
    return _f("META_THUMBNAIL", "Embedded thumbnail matches image", -0.4, 0.6,
              f"Thumbnail and main image agree (SSIM {s:.2f}).", status=PASS, data={"ssim": s}) | _art(art)


class _art:  # tiny helper so `finding | _art(name)` attaches an artifact
    def __init__(self, name):
        self.name = name

    def __ror__(self, f: Finding) -> Finding:
        f.artifacts.append(self.name)
        return f


def _c2pa_rule(path) -> Finding:
    try:
        import c2pa  # type: ignore
        reader = c2pa.Reader.from_file(str(path))
        manifest = reader.json()
        ai = "trainedalgorithmicmedia" in manifest.lower()
        return _f("META_C2PA", "C2PA Content Credentials verified", 3.0 if ai else -1.0, 0.85,
                  "A cryptographically signed C2PA manifest is present" +
                  (" and declares AI-generated content." if ai else "; provenance chain verified."),
                  family="synthetic-image" if ai else "provenance")
    except ImportError:
        return Finding(code="META_C2PA", module=MOD, family="provenance", title="C2PA manifest detected (not verified)",
                       status=INFO, reliability=0.2,
                       explanation="A C2PA/JUMBF block is present. Install c2pa-python to verify its signature.")
    except Exception as e:
        return _f("META_C2PA", "C2PA manifest present but invalid", 1.5, 0.6,
                  f"Manifest failed validation: {e}", ["Manifest damaged by re-encoding"], family="provenance")


def _gps_datetime(gps: dict) -> datetime | None:
    try:
        d = str(gps["GPSDateStamp"]).replace("-", ":")
        h, m, s = (float(x) for x in gps["GPSTimeStamp"])
        base = datetime.strptime(d, "%Y:%m:%d").replace(tzinfo=timezone.utc)
        return base + timedelta(hours=h, minutes=m, seconds=s)
    except Exception:
        return None


def _offset_minutes(s) -> int | None:
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"([+-])(\d{2}):?(\d{2})", s.strip())
    if not m:
        return None
    v = int(m.group(2)) * 60 + int(m.group(3))
    return v if m.group(1) == "+" else -v


def _mean_luma(path) -> float | None:
    try:
        im = Image.open(path)
        im.draft("L", (256, 256))
        return float(np.asarray(im.convert("L").resize((128, 128))).mean())
    except Exception:
        return None
