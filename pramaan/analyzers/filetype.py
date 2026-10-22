"""File signature (magic bytes) vs. extension, empty files, and the real type used to route analysis.

Criminals rename files to hide them ("holiday.jpg" that is really a spreadsheet, a video saved as ".xls"),
and a file that does not open can be mistaken for corrupt evidence. The real type comes from the bytes.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from .base import FAIL, INFO, PASS, WARN, Context, Finding

# extension -> canonical kind
EXT_KIND = {
    ".jpg": "jpeg", ".jpeg": "jpeg", ".jpe": "jpeg", ".jfif": "jpeg", ".png": "png", ".gif": "gif", ".webp": "webp",
    ".bmp": "bmp", ".tif": "tiff", ".tiff": "tiff", ".heic": "heif", ".heif": "heif", ".avif": "avif",
    ".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".3gp": "3gp", ".avi": "avi", ".mkv": "mkv", ".webm": "webm",
    ".dav": "dav", ".h264": "h264", ".ts": "mpegts",
    ".mp3": "mp3", ".wav": "wav", ".m4a": "m4a", ".aac": "aac", ".ogg": "ogg", ".opus": "ogg", ".flac": "flac",
    ".amr": "amr", ".wma": "asf", ".wmv": "asf",
    ".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx", ".doc": "ole", ".xls": "ole", ".ppt": "ole",
    ".zip": "zip", ".txt": "text", ".csv": "text", ".json": "text", ".xml": "text", ".html": "text",
}
LABEL = {
    "jpeg": "JPEG image", "png": "PNG image", "gif": "GIF image", "webp": "WebP image", "bmp": "BMP image",
    "tiff": "TIFF image", "heif": "HEIF/HEIC image", "avif": "AVIF image", "mp4": "MP4 video", "mov": "QuickTime video",
    "3gp": "3GP video", "m4a": "M4A audio", "avi": "AVI video", "mkv": "Matroska video", "webm": "WebM video",
    "mpegts": "MPEG transport stream", "asf": "Windows Media file", "dav": "Dahua DVR video", "h264": "raw H.264 stream",
    "mp3": "MP3 audio", "wav": "WAV audio", "aac": "AAC audio", "ogg": "Ogg audio", "flac": "FLAC audio",
    "amr": "AMR audio", "pdf": "PDF document", "docx": "Word document (.docx)", "xlsx": "Excel workbook (.xlsx)",
    "pptx": "PowerPoint (.pptx)", "ole": "legacy Office document (.doc/.xls/.ppt)", "zip": "ZIP archive",
    "text": "plain text", "empty": "empty file (0 bytes)", "unknown": "unrecognised binary data",
}
MODALITY = {
    **{k: "image" for k in ("jpeg", "png", "gif", "webp", "bmp", "tiff", "heif", "avif")},
    **{k: "video" for k in ("mp4", "mov", "3gp", "avi", "mkv", "webm", "mpegts", "asf", "dav", "h264")},
    **{k: "audio" for k in ("mp3", "wav", "m4a", "aac", "ogg", "flac", "amr")},
    **{k: "document" for k in ("pdf", "docx", "xlsx", "pptx", "ole", "zip", "text")},
}
# kinds that are the same container family (no mismatch if confused): .mp4 holding a MOV/M4A etc.
SAME = [{"mp4", "mov", "3gp", "m4a"}, {"jpeg"}, {"tiff"}, {"ole"}, {"asf"}, {"ogg"}, {"text"}, {"zip", "docx", "xlsx", "pptx"}]


def sniff(path: str | Path) -> str:
    p = Path(path)
    size = p.stat().st_size
    if size == 0:
        return "empty"
    with open(p, "rb") as f:
        h = f.read(4096)
    if h[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "webp"
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "wav"
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return "avi"
    if h[:2] == b"BM":
        return "bmp"
    if h[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"):
            return "heif"
        if brand == b"avif":
            return "avif"
        if brand.startswith(b"qt"):
            return "mov"
        if brand.startswith(b"3g"):
            return "3gp"
        if brand in (b"M4A ", b"M4B "):
            return "m4a"
        return "mp4"
    if h[4:8] in (b"moov", b"mdat", b"wide", b"free"):
        return "mov"
    if h[:4] == b"\x1aE\xdf\xa3":
        return "webm" if b"webm" in h[:64] else "mkv"
    if h[:3] == b"ID3":
        return "mp3"
    if len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0:     # MPEG audio frame sync
        return "aac" if (h[1] >> 1) & 3 == 0 else "mp3"             # layer bits 00 = ADTS AAC
    if h[:4] == b"OggS":
        return "ogg"
    if h[:4] == b"fLaC":
        return "flac"
    if h[:6] == b"#!AMR\n":
        return "amr"
    if h[:16] == bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"):
        return "asf"
    if h[0] == 0x47 and len(h) > 376 and h[188] == 0x47 and h[376] == 0x47:
        return "mpegts"
    if h[:4] == b"%PDF":
        return "pdf"
    if h[:8] == bytes.fromhex("d0cf11e0a1b11ae1"):
        return "ole"
    if h[:4] == b"PK\x03\x04":
        try:
            names = zipfile.ZipFile(p).namelist()
            if any(n.startswith("word/") for n in names):
                return "docx"
            if any(n.startswith("xl/") for n in names):
                return "xlsx"
            if any(n.startswith("ppt/") for n in names):
                return "pptx"
        except Exception:
            pass
        return "zip"
    if b"DHAV" in h[:64]:
        return "dav"
    try:
        h.decode("utf-8")
        if all(c >= 9 for c in h[:512]):
            return "text"
    except UnicodeDecodeError:
        pass
    return "unknown"


def same_family(a: str, b: str) -> bool:
    return a == b or any(a in s and b in s for s in SAME)


def run(ctx: Context) -> list[Finding]:
    real = sniff(ctx.path)
    ext = Path(ctx.shared.get("original_filename") or ctx.path.name).suffix.lower()
    claimed = EXT_KIND.get(ext)
    ctx.shared["real_type"] = real
    data = {"extension": ext or "(none)", "claimed_type": LABEL.get(claimed or "", "unknown extension"),
            "real_type": LABEL.get(real, real), "size": ctx.path.stat().st_size}
    if real == "empty":
        return [Finding(code="FILE_EMPTY", module="file", family="file-integrity",
                        title=f"Empty file: 0 bytes behind a '{ext}' name", status=FAIL, llr=0.0, reliability=0.0,
                        explanation="The file contains no data at all, so there is no "
                                    f"{LABEL.get(claimed or '', 'content')} to examine. Either the copy failed, the "
                                    "content was wiped, or an empty placeholder was submitted as evidence. Ask for the "
                                    "original media; this exhibit cannot support any conclusion.",
                        innocent=["Interrupted transfer or download", "Storage error on the source device"], data=data)]
    if real == "heif":
        from .. import HEIF_SUPPORT
        if not HEIF_SUPPORT:
            return [Finding(code="FILE_HEIF_UNSUPPORTED", module="file", family="file-integrity", status="unavailable",
                            title="HEIC/HEIF photo: decoder not installed",
                            explanation="This is an iPhone/Android HEIC photo. Install the decoder with "
                                        "'pip install pillow-heif' and examine it again; until then the pixel and "
                                        "metadata tests cannot read it.", data=data)]
    if claimed is None:
        return [Finding(code="FILE_TYPE", module="file", family="file-integrity",
                        title=f"File content is {LABEL.get(real, real)}", status=INFO,
                        explanation=f"The extension '{ext}' is not a common evidence format; analysed as "
                                    f"{LABEL.get(real, real)} based on its signature bytes.", data=data)]
    if real == "unknown":
        return [Finding(code="FILE_TYPE", module="file", family="file-integrity",
                        title="Unrecognised file signature", status=WARN, llr=0.8, reliability=0.5,
                        explanation=f"The name says {LABEL[claimed]}, but the first bytes do not match any known "
                                    "format. The file may be corrupt, encrypted or a proprietary DVR format.",
                        innocent=["Proprietary CCTV/DVR container", "Truncated download"], data=data)]
    if not same_family(real, claimed):
        return [Finding(code="FILE_EXT_MISMATCH", module="file", family="file-integrity",
                        title=f"Disguised file: named '{ext}' but is a {LABEL.get(real, real)}",
                        status=FAIL, llr=3.0, reliability=0.97,
                        explanation=f"The extension claims {LABEL[claimed]}, but the file's signature bytes identify "
                                    f"it as a {LABEL.get(real, real)}. The extension was changed after the file was "
                                    "created. Renaming is a common way to hide a file's real nature from a casual look "
                                    "or from automated scanning. PRAMAAN analysed it as what it really is.",
                        innocent=["Saved with the wrong extension by mistake", "Messaging app renamed the attachment"],
                        data=data)]
    return [Finding(code="FILE_EXT_OK", module="file", family="file-integrity",
                    title=f"Extension matches content ({LABEL.get(real, real)})", status=PASS, llr=0.0, reliability=0.0,
                    explanation="The file's signature bytes agree with its extension.", data=data)]
