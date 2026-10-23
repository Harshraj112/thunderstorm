"""PRAMAAN – court-ready digital evidence integrity & authenticity platform."""
from .config import VERSION  # noqa: F401

# iPhone/Android HEIC/HEIF photos: Pillow reads them once pillow-heif is installed (requirements.txt).
try:
    from pillow_heif import register_heif_opener as _reg
    _reg()
    HEIF_SUPPORT = True
except Exception:
    HEIF_SUPPORT = False
