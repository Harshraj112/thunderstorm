"""Hindi (Devanagari) for the PDF report.

ReportLab cannot shape Indic scripts (conjuncts and matras would come out broken), so Hindi blocks
are laid out by HarfBuzz through Pillow's raqm engine and embedded as 300-dpi images. Font: Noto Sans
Devanagari (OFL), fetched by scripts/fetch_real_samples.py into pramaan/report/fonts/.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

FONT_DIR = Path(__file__).with_name("fonts")
SYSTEM_FONTS = ["/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
                "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
                "/usr/share/fonts/truetype/fonts-deva-extra/gargi.ttf"]
DPI = 300

STATUS_HI = {"fail": "संदिग्ध", "warn": "सावधानी", "pass": "सुसंगत", "info": "सूचना", "unavailable": "उपलब्ध नहीं"}
CAT_HI = {
    "Strong indications of manipulation": "छेड़छाड़ के प्रबल संकेत",
    "Indications of manipulation": "छेड़छाड़ के संकेत",
    "No indications of manipulation detected": "छेड़छाड़ का कोई संकेत नहीं मिला",
    "Inconclusive": "अनिर्णायक",
}
CONF_HI = {"High": "उच्च", "Moderate": "मध्यम", "Low": "निम्न", "—": "—"}

# Plain-language Hindi title for each check (technical detail stays in the English section)
TITLE_HI = {
    "META_AI_GENERATOR": "मेटाडेटा में एआई-जनरेटर का चिह्न",
    "META_EDITOR_SOFTWARE": "फ़ोटो-संपादन सॉफ़्टवेयर का नाम दर्ज",
    "META_EDIT_HISTORY": "संपादन का इतिहास मौजूद",
    "META_TIME_CHAIN": "समय-मुहरों का क्रम",
    "META_TIME_MODIFIED": "कैप्चर के बाद फ़ाइल बदली गई",
    "META_GPS_TIME": "जीपीएस घड़ी बनाम कैप्चर समय",
    "META_GPS_NULL": "जीपीएस निर्देशांक (0,0)",
    "META_GPS_REGION": "जीपीएस स्थान अपेक्षित क्षेत्र से बाहर",
    "META_SOLAR": "सूर्य की स्थिति बनाम चित्र की रोशनी",
    "META_THUMBNAIL": "अंतर्निहित थंबनेल बनाम मुख्य चित्र",
    "META_DIMENSIONS": "चित्र का आकार EXIF से भिन्न",
    "META_MAKERNOTE": "कैमरा MakerNote अनुपस्थित",
    "META_C2PA": "C2PA डिजिटल प्रमाण-पत्र",
    "IMG_QTABLE": "JPEG संपीड़न तालिका",
    "IMG_GHOST_LOCAL": "JPEG 'घोस्ट' – किसी हिस्से का अलग संपीड़न इतिहास",
    "IMG_RECOMPRESSED": "पूरा चित्र दोबारा सहेजा गया",
    "IMG_NOISE": "सेंसर-शोर में असंगति",
    "IMG_COPYMOVE": "चित्र के भीतर कॉपी-पेस्ट (क्लोनिंग)",
    "IMG_ELA": "त्रुटि-स्तर विश्लेषण (ELA)",
    "IMG_TRUFOR": "TruFor स्थानीयकरण",
    "IMG_CATNET": "CAT-Net (DCT) स्थानीयकरण",
    "IMG_AIGI": "एआई-निर्मित चित्र जाँच",
    "IMG_PRNU": "कैमरा सेंसर फ़िंगरप्रिंट (PRNU)",
    "VID_EDITOR_TAG": "वीडियो-संपादन सॉफ़्टवेयर का टैग",
    "VID_PTS_BACKWARD": "फ़ाइल की समय-मुहरें पीछे गईं",
    "VID_PTS_DUP": "दोहराई गई समय-मुहरें",
    "VID_PTS_GAP": "फ़्रेम समय-मुहरों में अंतराल",
    "VID_GOP_BREAK": "की-फ़्रेम क्रम टूटा",
    "VID_DOUBLE_COMP": "वीडियो दोबारा एनकोड किया गया",
    "VID_VPF_DELETION": "दोहरे संपीड़न में फ़ेज़-परिवर्तन (फ़्रेम हटाए/जोड़े)",
    "VID_FROZEN": "जमे हुए (एक जैसे) फ़्रेम",
    "VID_LOOP": "फ़ुटेज दोहराया गया (लूप)",
    "VID_DISCONTINUITY": "अचानक दृश्य-विच्छेद",
    "VID_OSD_FORWARD": "स्क्रीन-घड़ी आगे कूदी",
    "VID_OSD_BACKWARD": "स्क्रीन-घड़ी पीछे गई",
    "VID_OSD_STALL": "स्क्रीन-घड़ी रुकी रही",
    "VID_OSD_OVER_FROZEN": "जमे फ़्रेमों पर घड़ी चलती रही",
    "VID_OSD_RATE": "स्क्रीन-घड़ी की गति असामान्य",
    "VID_OSD": "स्क्रीन-घड़ी फ़ाइल समय से मेल खाती है",
    "VID_FACE_DEEPFAKE": "चेहरा-डीपफ़ेक जाँच",
    "AV_LIPSYNC": "होंठों की गति बनाम आवाज़ (लिप-सिंक)",
    "AV_LIPSYNC_BREAK": "बीच से होंठ और आवाज़ मेल नहीं खाते",
    "AV_START": "ऑडियो और वीडियो का आरंभ-समय भिन्न",
    "AV_DURATION": "ऑडियो और वीडियो की अवधि भिन्न",
    "AUD_ENF_GRID": "बिजली-ग्रिड आवृत्ति भारत (50 Hz) से भिन्न",
    "AUD_ENF_PHASE": "बिजली-गुंजन (ENF) की कला-निरंतरता",
    "AUD_NOISE_FLOOR": "पृष्ठभूमि शोर में अचानक बदलाव",
    "AUD_DIGITAL_SILENCE": "पूर्ण डिजिटल चुप्पी (संपादन का निशान)",
    "AUD_BANDWIDTH": "ऑडियो बैंडविड्थ सीमित (अपसैंपल)",
    "AUD_EDITOR_TAG": "ऑडियो-संपादन सॉफ़्टवेयर का टैग",
    "AUD_SPOOF": "कृत्रिम (एआई) आवाज़ जाँच",
    "AUD_PARTIAL_SPOOF": "आंशिक कृत्रिम आवाज़ – खंड स्तर",
}


@lru_cache(maxsize=1)
def font_path() -> str | None:
    for p in [FONT_DIR / "NotoSansDevanagari-Regular.ttf", *map(Path, SYSTEM_FONTS)]:
        if p.exists():
            return str(p)
    return None


def available() -> bool:
    return font_path() is not None and features.check("raqm")


@lru_cache(maxsize=8)
def _font(size_pt: float, bold: bool) -> ImageFont.FreeTypeFont:
    p = font_path()
    if bold and (FONT_DIR / "NotoSansDevanagari-Bold.ttf").exists():
        p = str(FONT_DIR / "NotoSansDevanagari-Bold.ttf")
    return ImageFont.truetype(p, int(size_pt * DPI / 72), layout_engine=ImageFont.Layout.RAQM)


LATIN = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
LATIN_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SHARED = set(" :-(),.;/'\"")   # glyphs present in the Devanagari font


@lru_cache(maxsize=8)
def _latin(size_pt: float, bold: bool):
    p = LATIN_BOLD if bold else LATIN
    try:
        return ImageFont.truetype(p, int(size_pt * DPI / 72 * 0.88))
    except OSError:
        return ImageFont.load_default()


def _is_deva(ch: str) -> bool:
    o = ord(ch)
    return 0x0900 <= o <= 0x097F or 0xA8E0 <= o <= 0xA8FF or o in (0x200C, 0x200D)


def _runs(text: str):
    """Split into (is_devanagari, chunk); shared punctuation sticks to the current run."""
    runs, cur, kind = [], "", None
    for ch in text:
        k = kind if ch in SHARED and kind is not None else _is_deva(ch)
        if kind is None or k == kind:
            cur += ch
            kind = k
        else:
            runs.append((kind, cur))
            cur, kind = ch, k
    if cur:
        runs.append((kind, cur))
    return runs


def _length(text: str, size: float, bold: bool) -> float:
    return sum((_font(size, bold) if d else _latin(size, bold)).getlength(c) for d, c in _runs(text))


def render(text: str, width_mm: float, size_pt: float = 10, bold: bool = False,
           color: tuple = (27, 36, 48), line: float = 1.5) -> tuple[bytes, float, float]:
    """Return (png bytes, width_mm, height_mm) of word-wrapped, correctly shaped Hindi text."""
    f = _font(size_pt, bold)
    max_px = int(width_mm / 25.4 * DPI)
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split(" "):
            cand = (cur + " " + w).strip()
            if _length(cand, size_pt, bold) <= max_px or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    lh = int(size_pt * DPI / 72 * line)
    used_w = max(int(_length(l, size_pt, bold)) for l in lines) + 8
    img = Image.new("RGBA", (max(used_w, 10), lh * len(lines) + int(lh * 0.3)), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    lat = _latin(size_pt, bold)
    for i, l in enumerate(lines):
        x = 2
        for deva, chunk in _runs(l):
            fnt = f if deva else lat
            dy = 0 if deva else int(lh * 0.05)        # align Latin baseline with Devanagari headline font
            d.text((x, i * lh + dy), chunk, font=fnt, fill=color + (255,))
            x += fnt.getlength(chunk)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), img.width / DPI * 25.4, img.height / DPI * 25.4
