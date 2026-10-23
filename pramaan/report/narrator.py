"""Plain-language narration of findings.

Default: deterministic templates (English + Hindi) – reproducible, auditable, no model needed.
Optional: a LOCAL LLM via Ollama (PRAMAAN_OLLAMA_URL) rewrites the template text for readability.
The LLM receives only the structured findings and is instructed not to add facts; its output is
checked so every number it mentions already appears in the input, otherwise the template is used.
"""
from __future__ import annotations

import json
import re

from .. import config

CAT_HI = {
    "Strong indications of manipulation": "छेड़छाड़ के प्रबल संकेत",
    "Indications of manipulation": "छेड़छाड़ के संकेत",
    "No indications of manipulation detected": "छेड़छाड़ का कोई संकेत नहीं मिला",
    "Inconclusive": "अनिर्णायक",
}
CONF_HI = {"High": "उच्च", "Moderate": "मध्यम", "Low": "निम्न", "—": "—"}
MOD_HI = {"image": "छवि", "video": "वीडियो/सीसीटीवी", "audio": "ऑडियो", "unknown": "फ़ाइल"}


def template_en(ev: dict, verdict: dict, findings: list[dict], integrity_ok: bool) -> str:
    fails = [f for f in findings if f["status"] in ("fail", "warn")]
    passes = [f for f in findings if f["status"] == "pass"]
    ran = [f for f in findings if f["status"] != "unavailable"]
    s = [f"Exhibit {ev.get('exhibit_no') or ev['id']} ({ev['original_filename']}, {ev['modality']}) was examined with "
         f"{len(ran)} automated forensic checks."]
    s.append("Integrity: the stored original still matches the SHA-256 recorded at intake, so the file has not changed "
             "since it entered the laboratory." if integrity_ok else
             "WARNING: the stored file no longer matches its intake SHA-256 hash.")
    s.append(f"Authenticity conclusion: {verdict['category']}" +
             (f" (confidence: {verdict['confidence']})." if verdict["confidence"] != "—" else "."))
    if fails:
        s.append("Main observations: " + "; ".join(f["title"] for f in fails[:4]) + ".")
    if passes:
        s.append("Checks consistent with authenticity: " + "; ".join(f["title"] for f in passes[:3]) + ".")
    if verdict.get("abstain_reason"):
        s.append(verdict["abstain_reason"])
    inn = sorted({i for f in fails for i in (f.get("innocent") or [])})[:4]
    if inn:
        s.append("Innocent explanations that the examiner must rule out: " + "; ".join(inn) + ".")
    s.append("These are analytical indicators to support, not replace, the opinion of a qualified examiner.")
    return " ".join(s)


def template_hi(ev: dict, verdict: dict, findings: list[dict], integrity_ok: bool) -> str:
    fails = [f for f in findings if f["status"] in ("fail", "warn")]
    ran = [f for f in findings if f["status"] != "unavailable"]
    times = []
    for f in fails:
        loc = f.get("location") or {}
        for k in ("time", "times", "at"):
            v = loc.get(k)
            if isinstance(v, (int, float)):
                times.append(v)
            elif isinstance(v, list):
                times += [x for x in v if isinstance(x, (int, float))]
    s = [f"प्रदर्श {ev.get('exhibit_no') or ev['id']} ({MOD_HI.get(ev['modality'], 'फ़ाइल')}) की {len(ran)} स्वचालित "
         "फ़ॉरेंसिक जाँचें की गईं।"]
    s.append("अखंडता: प्रयोगशाला में प्राप्ति के समय दर्ज SHA-256 हैश अब भी मेल खाता है, अतः फ़ाइल में कोई बदलाव नहीं हुआ।"
             if integrity_ok else "चेतावनी: संग्रहीत फ़ाइल का हैश प्राप्ति के समय के हैश से मेल नहीं खाता।")
    s.append(f"प्रामाणिकता निष्कर्ष: {CAT_HI.get(verdict['category'], verdict['category'])}"
             + (f" (विश्वास स्तर: {CONF_HI.get(verdict['confidence'], verdict['confidence'])})।"
                if verdict["confidence"] != "—" else "।"))
    if fails:
        s.append(f"{len(fails)} जाँचों में संदिग्ध संकेत मिले" +
                 (f", मुख्यतः फ़ाइल के समय {', '.join(f'{t:.1f}' for t in sorted(set(times))[:5])} सेकंड पर।" if times else "।"))
    s.append("ये विश्लेषणात्मक संकेत हैं; अंतिम राय योग्य परीक्षक द्वारा दी जानी चाहिए।")
    return " ".join(s)


def narrate(ev: dict, verdict: dict, findings: list[dict], integrity_ok: bool) -> dict:
    en = template_en(ev, verdict, findings, integrity_ok)
    hi = template_hi(ev, verdict, findings, integrity_ok)
    out = {"en": en, "hi": hi, "engine": "template"}
    if config.OLLAMA_URL:
        llm = _ollama(en)
        if llm and _numbers_subset(llm, en):
            out["en_llm"] = llm
            out["engine"] = f"template + {config.OLLAMA_MODEL} (verified)"
    return out


def _numbers_subset(candidate: str, source: str) -> bool:
    nums = lambda s: set(re.findall(r"\d+(?:\.\d+)?", s))
    return nums(candidate) <= nums(source)


def _ollama(text: str) -> str | None:
    try:
        import requests
        prompt = ("Rewrite the following forensic summary for a police investigating officer in clear, neutral English. "
                  "Do NOT add any fact, number, cause or opinion that is not in the text. Keep every caveat.\n\n" + text)
        r = requests.post(config.OLLAMA_URL.rstrip("/") + "/api/generate",
                          json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False,
                                "options": {"temperature": 0}}, timeout=120)
        return r.json().get("response", "").strip() or None
    except Exception:
        return None
