"""Metadata in the fixed output format required by PS-05 ("Metadata details" sheet), for every file type.

    File Name, File Type, Image Width, Image Height, Date Time Original, CreateDate, ModifyDate,
    GPSLatitude, GPSLongitude, Make, Model, Software, Description, Artist, Copyright

Sources, in priority order: EXIF (JPEG/TIFF/HEIC), PNG text chunks (tEXt/iTXt/zTXt), XMP, container tags
(MP4/MOV/MP3 ID3/WAV INFO via ffprobe), Office core properties, PDF Info dictionary. Everything else that
was found is returned as "additional" fields, so nothing is hidden from the examiner.
"""
from __future__ import annotations

import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from ..base import INFO, Context, Finding

FIELDS = ["File Name", "File Type", "Image Width", "Image Height", "Date Time Original", "CreateDate", "ModifyDate",
          "GPSLatitude", "GPSLongitude", "Make", "Model", "Software", "Description", "Artist", "Copyright"]
MISSING = "—"


def _first(*vals) -> Any:
    for v in vals:
        if v not in (None, "", [], {}) and str(v).strip() not in ("", "0000:00:00 00:00:00"):
            return v
    return None


def _ci(d: dict, *keys) -> Any:
    """Case-insensitive lookup in a tag dict."""
    low = {str(k).lower(): v for k, v in (d or {}).items()}
    for k in keys:
        if k.lower() in low and low[k.lower()] not in ("", None):
            return low[k.lower()]
    return None


def _dms(v, ref) -> str | None:
    try:
        d, m, s = (float(x) for x in v)
        dec = d + m / 60 + s / 3600
        return f"{dec:.6f} {ref or ''}".strip() + f"  ({int(d)}° {int(m)}' {s:.2f}\")"
    except Exception:
        return None


def _xmp(xmp: str | None, *names) -> str | None:
    if not xmp:
        return None
    for n in names:
        m = re.search(rf'{n}="([^"]+)"', xmp) or re.search(rf"<{n}>(.*?)</{n}>", xmp, re.S)
        if m:
            txt = re.sub(r"<[^>]+>", " ", m.group(1))
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt:
                return txt
    return None


def _ffprobe(path: Path) -> dict:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}


def _iso6709(s: str | None) -> tuple[str | None, str | None]:
    m = re.match(r"([+-]\d+\.?\d*)([+-]\d+\.?\d*)", s or "")
    if not m:
        return None, None
    la, lo = float(m.group(1)), float(m.group(2))
    return f"{abs(la):.6f} {'N' if la >= 0 else 'S'}", f"{abs(lo):.6f} {'E' if lo >= 0 else 'W'}"


def office_props(path: Path) -> dict:
    out = {}
    try:
        z = zipfile.ZipFile(path)
        for name in ("docProps/core.xml", "docProps/app.xml"):
            if name in z.namelist():
                x = z.read(name).decode("utf-8", "ignore")
                for tag, val in re.findall(r"<(?:\w+:)?(\w+)[^>]*>([^<]+)</", x):
                    out[tag] = val.strip()
    except Exception:
        pass
    return out


def pdf_info(path: Path) -> dict:
    out = {}
    try:
        b = path.read_bytes()[:3_000_000]
        for k in ("Title", "Author", "Subject", "Creator", "Producer", "CreationDate", "ModDate", "Keywords"):
            m = re.search(rb"/" + k.encode() + rb"\s*\(((?:\\.|[^\\)])*)\)", b)
            if m:
                out[k] = m.group(1).decode("latin1", "ignore")
    except Exception:
        pass
    return out


def _pdf_date(s: str | None) -> str | None:
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", s or "")
    if not m:
        return s
    y, mo, d, h, mi, se = (x or "00" for x in m.groups())
    return f"{y}:{mo}:{d} {h}:{mi}:{se}"


def build(path: Path, filename: str, real_type: str | None = None, meta: dict | None = None,
          c2pa: dict | None = None) -> dict:
    """{'standard': [[field, value], ...15], 'additional': {group: {k: v}}}"""
    from .. import filetype
    from .extract import extract
    real = real_type or filetype.sniff(path)
    ext = Path(filename).suffix.lower()
    claimed = filetype.EXT_KIND.get(ext)
    ftype = filetype.LABEL.get(real, real)
    if claimed and not filetype.same_family(real, claimed) and real != "empty":
        ftype += f" (named '{ext}', which claims {filetype.LABEL.get(claimed, claimed)})"
    std = dict.fromkeys(FIELDS)
    std["File Name"], std["File Type"] = filename, ftype
    add: dict[str, dict] = {}
    mod = filetype.MODALITY.get(real)

    if mod == "image":
        m = meta or extract(path)
        ex, gps, pt, xmp = m.get("exif", {}), m.get("gps", {}), m.get("png_text", {}) or {}, m.get("xmp")
        pt = {k: v for k, v in pt.items() if k.lower() not in ("xml:com.adobe.xmp",)}
        std["Image Width"], std["Image Height"] = m.get("width"), m.get("height")
        std["Date Time Original"] = _first(ex.get("DateTimeOriginal"), _ci(pt, "DateTimeOriginal"),
                                           _xmp(xmp, "exif:DateTimeOriginal", "photoshop:DateCreated"))
        std["CreateDate"] = _first(ex.get("DateTimeDigitized"), _ci(pt, "CreateDate", "Creation Time"),
                                   _xmp(xmp, "xmp:CreateDate"))
        std["ModifyDate"] = _first(ex.get("DateTime"), _ci(pt, "ModifyDate"), _xmp(xmp, "xmp:ModifyDate"))
        std["GPSLatitude"] = _first(_dms(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef")) if gps.get("GPSLatitude") else None,
                                    _ci(pt, "GPSLatitude"), _xmp(xmp, "exif:GPSLatitude"))
        std["GPSLongitude"] = _first(_dms(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef")) if gps.get("GPSLongitude") else None,
                                     _ci(pt, "GPSLongitude"), _xmp(xmp, "exif:GPSLongitude"))
        std["Make"] = _first(ex.get("Make"), _ci(pt, "Make"), _xmp(xmp, "tiff:Make"))
        std["Model"] = _first(ex.get("Model"), _ci(pt, "Model"), _xmp(xmp, "tiff:Model"))
        std["Software"] = _first(ex.get("Software"), ex.get("ProcessingSoftware"), _ci(pt, "Software"),
                                 _xmp(xmp, "xmp:CreatorTool"))
        std["Description"] = _first(ex.get("ImageDescription"), _ci(pt, "Description", "Comment", "Title"),
                                    _xmp(xmp, "dc:description"))
        std["Artist"] = _first(ex.get("Artist"), _ci(pt, "Artist", "Author"), _xmp(xmp, "dc:creator"))
        std["Copyright"] = _first(ex.get("Copyright"), _ci(pt, "Copyright"), _xmp(xmp, "dc:rights"))
        used = {"DateTimeOriginal", "CreateDate", "ModifyDate", "GPSLatitude", "GPSLongitude", "Make", "Model",
                "Software", "Description", "Artist", "Copyright", "Author", "Comment", "Title"}
        add["Image"] = {"Colour mode": m.get("mode"), "Format": m.get("format")}
        if m.get("jpeg", {}).get("subsampling"):
            add["Image"]["JPEG subsampling"] = m["jpeg"]["subsampling"]
        extra_ex = {k: v for k, v in ex.items() if k not in ("MakerNote", "GPSInfo", "ExifOffset") and
                    not (isinstance(v, str) and v.startswith("<"))}
        if extra_ex:
            add["EXIF"] = {k: str(v)[:200] for k, v in extra_ex.items()}
        extra_pt = {k: str(v)[:300] for k, v in pt.items() if k not in used and k not in
                    ("dpi", "gamma", "srgb", "exif", "icc_profile", "chromaticity", "transparency")}
        if extra_pt:
            add["PNG text chunks"] = extra_pt
        if m.get("info_comment"):
            add["Image"]["JPEG comment"] = m["info_comment"]
    elif mod in ("video", "audio"):
        pr = _ffprobe(path)
        fmt = pr.get("format", {})
        tags = {**{k: v for s in pr.get("streams", []) for k, v in (s.get("tags") or {}).items()}, **(fmt.get("tags") or {})}
        v = next((s for s in pr.get("streams", []) if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
        a = next((s for s in pr.get("streams", []) if s.get("codec_type") == "audio"), None)
        if v:
            std["Image Width"], std["Image Height"] = v.get("width"), v.get("height")
        lat, lon = _iso6709(_ci(tags, "location", "com.apple.quicktime.location.ISO6709", "location-eng"))
        std["Date Time Original"] = _ci(tags, "com.apple.quicktime.creationdate", "date_recorded", "date")
        std["CreateDate"] = _ci(tags, "creation_time", "date", "creation_date", "ICRD")
        std["ModifyDate"] = _ci(tags, "modification_time", "com.apple.quicktime.modifydate")
        std["GPSLatitude"], std["GPSLongitude"] = lat, lon
        std["Make"] = _ci(tags, "com.apple.quicktime.make", "com.android.manufacturer", "make", "manufacturer")
        std["Model"] = _ci(tags, "com.apple.quicktime.model", "com.android.model", "model")
        std["Software"] = _ci(tags, "encoder", "com.apple.quicktime.software", "software", "encoded_by", "ISFT",
                              "com.android.version")
        std["Description"] = _ci(tags, "description", "comment", "title", "synopsis", "ICMT")
        std["Artist"] = _ci(tags, "artist", "author", "album_artist", "IART")
        std["Copyright"] = _ci(tags, "copyright", "ICOP")
        add["Media"] = {"Container": fmt.get("format_long_name"), "Duration (s)": fmt.get("duration"),
                        "Overall bit rate": fmt.get("bit_rate")}
        if v:
            add["Media"].update({"Video codec": v.get("codec_name"), "Frame rate": v.get("r_frame_rate"),
                                 "Frames": v.get("nb_frames"), "Pixel format": v.get("pix_fmt")})
        if a:
            add["Media"].update({"Audio codec": a.get("codec_name"), "Sample rate": a.get("sample_rate"),
                                 "Channels": a.get("channels"), "Audio bit rate": a.get("bit_rate")})
        add["Container tags"] = {k: str(val)[:300] for k, val in tags.items() if k != "iTunMOVI"}
    elif real in ("docx", "xlsx", "pptx"):
        p = office_props(path)
        std["CreateDate"], std["ModifyDate"] = p.get("created"), p.get("modified")
        std["Software"] = _first(" ".join(x for x in (p.get("Application"), p.get("AppVersion")) if x) or None)
        std["Description"] = _first(p.get("title"), p.get("subject"), p.get("description"))
        std["Artist"] = p.get("creator")
        add["Document"] = p
    elif real == "pdf":
        p = pdf_info(path)
        std["CreateDate"], std["ModifyDate"] = _pdf_date(p.get("CreationDate")), _pdf_date(p.get("ModDate"))
        std["Software"] = _first(p.get("Producer"), p.get("Creator"))
        std["Description"] = _first(p.get("Title"), p.get("Subject"))
        std["Artist"] = p.get("Author")
        add["Document"] = p
    if c2pa:
        add["Content Credentials (C2PA)"] = {
            "Generator": c2pa.get("claim_generator"), "Signed by": c2pa.get("signer"),
            "Certificate issuer": c2pa.get("issuer"), "Source type": c2pa.get("source_meaning"),
            "Signature": {True: "valid", False: "INVALID", None: "not checked"}[c2pa.get("signature_valid")],
            "Content hash": {True: "matches", False: "DOES NOT MATCH", None: "not checked"}[c2pa.get("content_hash_valid")]}
    rows = [[k, MISSING if std[k] in (None, "") else str(std[k])] for k in FIELDS]
    add = {g: {k: v for k, v in d.items() if v not in (None, "")} for g, d in add.items()}
    return {"standard": rows, "additional": {g: d for g, d in add.items() if d}}


def run(ctx: Context) -> list[Finding]:
    table = build(ctx.path, ctx.shared.get("original_filename") or ctx.path.name, ctx.shared.get("real_type"),
                  ctx.shared.get("meta"), ctx.shared.get("c2pa"))
    ctx.shared["metadata_table"] = table
    present = [k for k, v in table["standard"] if v != MISSING and k not in ("File Name", "File Type")]
    return [Finding(code="META_TABLE", module="metadata", family="metadata", title="Metadata (standard format)",
                    status=INFO, reliability=0.0,
                    explanation=f"{len(present)} of 13 descriptive fields present"
                                + (f": {', '.join(present)}." if present else ". (No embedded metadata.)"),
                    data=table)]
