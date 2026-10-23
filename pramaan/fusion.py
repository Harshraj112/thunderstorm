"""Evidence fusion with abstention.

1. Findings are grouped into families of correlated detectors (e.g. ELA + JPEG ghost = 'compression').
   Within a family we take the strongest reliability-weighted evidence for and against, not the sum,
   so two views of the same artefact are not double counted.
2. Family contributions (capped) are summed into a total log-likelihood ratio.
3. Coverage = how much reliable evidence was actually available. Low coverage => "Inconclusive"
   rather than a guess (abstention).
4. The total maps to SWGDE-style ordinal conclusions. We never print a bare "87% fake".
"""
from __future__ import annotations

from collections import defaultdict

FAMILY_CAP = {"synthetic-voice": 2.0, "synthetic-image": 4.0, "synthetic-face": 2.0, "metadata": 2.5,
              "provenance": 4.6, "cross-modal": 2.5, "file-integrity": 3.0, "colour-filter": 2.5, "framing": 1.5,
              "derivative": 4.0}

# What KIND of problem a flagged finding points to, in words a court understands (shown beside the verdict).
NATURE_BY_CODE = {
    "PROV_C2PA_AI": "AI-generated (signed Content Credentials)",
    "PROV_C2PA_ALTERED": "Changed after it was signed",
    "PROV_C2PA_BADSIG": "Forged or broken Content Credentials",
    "FILE_EXT_MISMATCH": "Disguised file type",
    "FILE_EMPTY": "Empty file (no content)",
    "DERIV_COPY": "Modified copy of another exhibit",
    "DERIV_C2PA_STRIPPED": "AI-generated, provenance removed",
    "VID_COLOUR_FILTER": "Colour filter applied",
    "VID_REFRAMED": "Re-framed in an editor",
    "VID_OSD_FORWARD": "Footage removed (clock jump)",
    "VID_OSD_BACKWARD": "Footage reordered (clock goes back)",
    "VID_VPF_DELETION": "Frames deleted",
    "VID_LOOP": "Footage repeated (loop)",
    "VID_FROZEN": "Frozen frames inserted",
    "IMG_AI_NATIVE_SIZE": "Likely AI-generated",
    "AUD_EDITOR_TAG": "Re-encoded by software",
    "AUD_STUDIO_CLEAN": "Studio-clean audio (possible TTS)",
}
NATURE_BY_FAMILY = {
    "synthetic-image": "Likely AI-generated", "synthetic-voice": "Likely synthetic voice",
    "synthetic-face": "Possible face deepfake", "metadata": "Metadata altered", "pixel": "Edited region",
    "compression": "Re-saved / edited", "enf": "Audio cut or spliced", "acoustic": "Audio edited",
    "temporal-container": "Video timeline edited", "temporal-pixel": "Video timeline edited",
    "osd": "Footage removed (clock jump)", "cross-modal": "Audio and video out of sync",
    "colour-filter": "Colour filter applied", "framing": "Re-framed in an editor", "derivative": "Modified copy",
}
DEFAULT_CAP = 3.0

EXPECTED_FAMILIES = {
    "image": ["metadata", "compression", "pixel", "synthetic-image"],
    "video": ["metadata", "temporal-container", "temporal-pixel", "osd"],
    "audio": ["metadata", "enf", "acoustic", "synthetic-voice"],
}

# Plain-language reasons shown when a verdict is Inconclusive, so the examiner knows what would settle it.
MISSING_WHY = {
    "metadata": "the file carries no camera metadata (date, GPS, device), which WhatsApp, Instagram and screenshots strip",
    "synthetic-image": "no AI-image detector result",
    "compression": "no usable JPEG compression history (PNG, screenshot or heavily recompressed)",
    "pixel": "too little texture or resolution for the sensor-noise and clone tests",
    "osd": "no readable on-screen clock in the footage",
    "temporal-container": "the file's own frame timestamps were unusable",
    "temporal-pixel": "too few frames for continuity tests",
    "enf": "no clean mains hum in the audio to test for cuts",
    "acoustic": "the background noise was too uneven to compare",
    "synthetic-voice": "no synthetic-voice detector result",
}
INSTALL_HINT = {
    "synthetic-image": "install it with: pip install -r requirements-ml.txt, then python scripts/install_models.py",
    "synthetic-voice": "install one: python scripts/install_models.py --voice",
}

CATEGORIES = [
    (4.0, "Strong indications of manipulation"),
    (1.5, "Indications of manipulation"),
]


def fuse(findings: list[dict], modality: str) -> dict:
    fam: dict[str, dict] = defaultdict(lambda: {"pos": 0.0, "neg": 0.0, "pos_code": None, "neg_code": None,
                                                "neg_min": 0.0, "rel": 0.0})
    for f in findings:
        if f.get("status") == "unavailable" or f.get("reliability", 0) <= 0:
            continue
        w = f["llr"] * f["reliability"]
        d = fam[f["family"]]
        d["rel"] = max(d["rel"], f["reliability"])
        if w > d["pos"]:
            d["pos"], d["pos_code"] = w, f["code"]
        if w < 0:
            d["neg"] += w                      # distinct passing checks within a family add up (capped below)
            if w < d["neg_min"]:
                d["neg_min"], d["neg_code"] = w, f["code"]
    contributions = []
    for name, d in fam.items():
        cap = FAMILY_CAP.get(name, DEFAULT_CAP)
        # Within a family the strongest positive evidence wins: a clean ELA does not cancel a JPEG ghost.
        c = d["pos"] if d["pos"] >= 0.3 else d["pos"] + max(d["neg"], -1.0)
        c = max(-1.0, min(cap, c))
        contributions.append({"family": name, "llr": round(c, 2), "driver": d["pos_code"] if c > 0 else d["neg_code"],
                              "reliability": round(d["rel"], 2)})
    contributions.sort(key=lambda c: -abs(c["llr"]))
    pos = sum(c["llr"] for c in contributions if c["llr"] > 0)
    neg = sum(c["llr"] for c in contributions if c["llr"] < 0)
    # Families test DIFFERENT manipulation types. "No splice found" does not argue against "metadata was
    # back-dated", so once any family flags a specific manipulation, other families' passes are discounted.
    flagged = any(c["llr"] >= 0.5 for c in contributions)
    total = pos + (0.25 if flagged else 1.0) * neg
    # Independent corroboration: two or more families pointing the same way is worth more than one.
    strong = [c for c in contributions if c["llr"] >= 1.0]
    if len(strong) >= 2:
        total += 0.5 * (len(strong) - 1)

    expected = EXPECTED_FAMILIES.get(modality, [])
    covered = [f for f in expected if fam.get(f, {}).get("rel", 0) >= 0.3]
    coverage = len(covered) / len(expected) if expected else 1.0

    if total >= CATEGORIES[0][0]:
        cat = CATEGORIES[0][1]
    elif total >= CATEGORIES[1][0]:
        cat = CATEGORIES[1][1]
    elif total <= -0.75 and coverage >= 0.5 and not flagged:
        cat = "No indications of manipulation detected"
    else:
        cat = "Inconclusive"
    abstained = cat == "Inconclusive"
    mag = abs(total)
    confidence = "High" if mag >= 4 else "Moderate" if mag >= 2 else "Low"
    if abstained:
        confidence = "—"
    supporting = [c for c in contributions if c["llr"] > 0.2]
    contradicting = [c for c in contributions if c["llr"] < -0.2]
    missing = [f for f in expected if f not in covered]
    nature: list[str] = []
    for f in sorted(findings, key=lambda f: -(f.get("llr") or 0) * (f.get("reliability") or 0)):
        if f.get("status") not in ("fail", "warn") or (f.get("llr") or 0) * (f.get("reliability") or 0) < 0.6:
            continue
        lab = NATURE_BY_CODE.get(f["code"]) or NATURE_BY_FAMILY.get(f.get("family"))
        if f["code"] == "DERIV_COPY" and f.get("status") == "warn":
            lab = "Converted copy of another exhibit"
        if lab and lab not in nature and not (lab == "Likely AI-generated" and any(n.startswith("AI-generated") for n in nature)):
            nature.append(lab)
    empty = any(f.get("code") == "FILE_EMPTY" for f in findings)
    if empty:
        nature = ["Empty file (no content)"]
    reason, gaps = None, []
    if empty:
        cat, abstained, confidence = "Inconclusive", True, "—"
        reason = ("The file is empty (0 bytes): there is nothing to examine. Ask for the original media from the "
                  "source device.")
    elif abstained:
        unavailable = {f["family"] for f in findings if f.get("status") == "unavailable"}
        for fam_name in missing:
            why = MISSING_WHY.get(fam_name, f"no reliable signal from the {fam_name} tests")
            if fam_name in unavailable and fam_name in INSTALL_HINT:
                why += f" ({INSTALL_HINT[fam_name]})"
            gaps.append({"family": fam_name, "why": why})
        if flagged:
            lead = "One test found something, but it is not strong enough on its own and nothing corroborates it."
        elif total < 0:
            lead = "Every test that could run came back clean, but clean results from these tests alone cannot prove the content is original."
        else:
            lead = "The available evidence is weak or points both ways."
        reason = lead + (" Missing: " + "; ".join(g["why"] for g in gaps) + "." if gaps else "")
    return {"category": cat, "confidence": confidence, "total_llr": round(total, 2), "coverage": round(coverage, 2),
            "abstained": abstained, "abstain_reason": reason, "contributions": contributions, "nature": nature[:4],
            "supporting": supporting, "contradicting": contradicting, "families_missing": missing, "gaps": gaps,
            "note": "Integrity (hash) is verified separately. This verdict concerns AUTHENTICITY of the content."}
