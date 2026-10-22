"""Cross-exhibit comparison: is this exhibit a modified copy of another exhibit in the same case?

Investigators often receive the same picture twice: a forwarded/'processed' version and, later, the original
from the device or the generator. Comparing them settles questions that neither file can answer alone:
which one came first, what was changed (size, colour channels, metadata fields, Content Credentials), and
whether provenance was stripped. Matching uses a 32x32 brightness signature (robust to resizing, re-saving,
a 1-pixel crop and alpha conversion); the diff is then computed on the real files.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import config, db
from .base import FAIL, INFO, Context, Finding

MATCH = 0.93


def signature(path: str | Path, sha: str | None = None) -> tuple[np.ndarray, float] | None:
    cache = config.DATA_DIR / "sig" / f"{sha}.npz" if sha else None
    if cache and cache.exists():
        z = np.load(cache)
        return z["s"], float(z["ar"])
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.draft("L", (256, 256))
            ar = im.width / max(im.height, 1)
            g = np.asarray(im.convert("RGB").convert("L").resize((32, 32), Image.BILINEAR, reducing_gap=2.0), float)
    except Exception:
        return None
    s = (g - g.mean()) / (g.std() + 1e-6)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, s=s, ar=ar)
    return s, ar


def jpeg_blocking(path: Path) -> float:
    """How strongly the pixels show JPEG's 8x8 block grid (≈1.0 = none). A PNG/BMP/TIFF/WebP that was made from a
    JPEG keeps this grid, which proves it is the later generation."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            g = np.asarray(im.convert("L"), np.float32)
    except Exception:
        return 1.0
    g = g[: g.shape[0] // 8 * 8, : g.shape[1] // 8 * 8]
    if g.shape[0] < 32 or g.shape[1] < 32:
        return 1.0
    dx = np.abs(np.diff(g, axis=1))
    col = dx.mean(axis=0)
    at = col[7::8].mean()                                   # differences across 8-px block borders
    inside = np.delete(col, np.s_[7::8]).mean() + 1e-6
    return float(at / inside)


def _facts(path: Path, filename: str) -> dict:
    from PIL import Image
    from .metadata.standard import build, MISSING
    from .provenance.c2pa_lite import read
    with Image.open(path) as im:
        w, h, mode = im.width, im.height, im.mode
    try:
        c = read(path)
    except Exception:
        c = None
    std = dict(build(path, filename)["standard"])
    filled = {k: v for k, v in std.items() if v != MISSING and k not in ("File Name", "File Type", "Image Width",
                                                                         "Image Height")}
    from .image.common import is_jpeg
    jpeg = is_jpeg(path)
    return {"w": w, "h": h, "mode": mode, "c2pa": c, "std": std, "filled": filled, "jpeg": jpeg,
            "blocking": 1.0 if jpeg else jpeg_blocking(path)}


def _derived_score(me: dict, other: dict) -> tuple[float, list[str]]:
    s, why = 0.0, []
    oc, mc = other["c2pa"], me["c2pa"]
    if oc and oc.get("signature_valid") and not mc:
        s += 2
        why.append(f"has no Content Credentials, while the other carries valid ones signed by {oc.get('signer')}")
    if me["mode"] in ("RGBA", "LA") and other["mode"] not in ("RGBA", "LA"):
        s += 1
        why.append(f"gained an alpha channel ({other['mode']} → {me['mode']})")
    if not me["jpeg"] and other["jpeg"] and me["blocking"] > 1.15:
        s += 2
        why.append(f"is a {me['std'].get('File Type', 'lossless file')} that still shows JPEG's 8×8 block pattern "
                   f"(border/inside ratio {me['blocking']:.2f}), so it was converted from a JPEG")
    added = [k for k in me["filled"] if k not in other["filled"]]
    # Extra metadata only marks the later copy when both files are the same kind (a JPEG -> PNG conversion simply
    # drops metadata, so there it would point the wrong way).
    if added and me["jpeg"] == other["jpeg"]:
        s += 1
        why.append("has metadata fields the other lacks: " + ", ".join(added))
    a_me, a_ot = me["w"] * me["h"], other["w"] * other["h"]
    if a_me > a_ot * 1.05:
        s += 1
        why.append(f"was enlarged from {other['w']}×{other['h']} to {me['w']}×{me['h']} (upscaling adds no detail)")
    elif a_me < a_ot * 0.97:
        s += 0.5
        why.append(f"is smaller ({me['w']}×{me['h']} vs {other['w']}×{other['h']}): cropped or downscaled")
    return s, why


def run(ctx: Context) -> list[Finding]:
    case_id, me_id = ctx.shared.get("case_id"), ctx.evidence_id
    me_ev = db.q1("SELECT * FROM evidence WHERE id=?", (me_id,))
    if not case_id or not me_ev:
        return []
    mine = signature(ctx.path, me_ev["sha256"])
    if mine is None:
        return []
    others = db.q("SELECT * FROM evidence WHERE case_id=? AND id<>? AND modality='image' ORDER BY ingested_at LIMIT 200",
                  (case_id, me_id))
    out: list[Finding] = []
    me_facts = None
    for o in others:
        if o["sha256"] == me_ev["sha256"]:
            out.append(Finding(code="DERIV_IDENTICAL", module="compare", family="derivative", status=INFO,
                               title=f"Identical file to {o.get('exhibit_no') or o['original_filename']}",
                               explanation="Byte-for-byte the same file (same SHA-256) was received twice.",
                               data={"other_id": o["id"], "other_exhibit": o["exhibit_no"]}))
            continue
        sig = signature(o["storage_path"], o["sha256"])
        if sig is None or abs(np.log(sig[1] / mine[1])) > 0.06:
            continue
        corr = float((sig[0] * mine[0]).mean())
        if corr < MATCH:
            continue
        me_facts = me_facts or _facts(ctx.path, me_ev["original_filename"])
        ot = _facts(Path(o["storage_path"]), o["original_filename"])
        s_me, why_me = _derived_score(me_facts, ot)
        s_ot, why_ot = _derived_score(ot, me_facts)
        changed = [f"{k}: '{ot['std'].get(k)}' → '{me_facts['std'].get(k)}'" for k in me_facts["std"]
                   if k not in ("File Name",) and me_facts["std"].get(k) != ot["std"].get(k)]
        data = {"other_id": o["id"], "other_exhibit": o["exhibit_no"], "other_file": o["original_filename"],
                "similarity": round(corr, 3), "changes": changed[:20]}
        label = f"{o['exhibit_no']} ({o['original_filename']})" if o.get("exhibit_no") else o["original_filename"]
        if s_me > s_ot:
            oc = ot["c2pa"]
            if oc and oc.get("signature_valid") and not me_facts["c2pa"]:
                ai = bool(oc.get("ai_generated"))
                out.append(Finding(
                    code="DERIV_C2PA_STRIPPED", module="compare", family="provenance", status=FAIL,
                    llr=3.6 if ai else 2.4, reliability=0.92,
                    title=(f"Same picture as {label}, which is signed as AI-generated by {oc.get('signer')}"
                           if ai else f"Same picture as {label}, whose Content Credentials were removed here"),
                    explanation=(f"This exhibit shows the same image as {label} (signature similarity {corr:.2f}). "
                                 f"That file carries valid Content Credentials from {oc.get('claim_generator')}, signed by "
                                 f"{oc.get('signer')}" + (", declaring the image AI-generated" if ai else "") +
                                 ". This copy has none: the credentials were stripped, which only happens when the "
                                 "file is re-saved or deliberately cleaned." +
                                 (" So this exhibit is AI-generated too, whatever its metadata now claims." if ai else "")),
                    innocent=["Re-saved by an app that drops C2PA (most messaging apps do) — the image is still "
                              "the generator's output"], data=data))
            serious = any(k in w for w in why_me for k in ("alpha", "metadata fields", "enlarged", "Content Credentials"))
            out.append(Finding(
                code="DERIV_COPY", module="compare", family="derivative",
                status=FAIL if serious else "warn", llr=2.5 if serious else 0.8, reliability=0.85,
                title=f"Modified copy of {label}" if serious else f"Converted / re-saved copy of {label}",
                explanation=(f"The picture matches {label} (signature similarity {corr:.2f}), and this file is the "
                             "later generation: it " + "; it ".join(why_me) + ". Differences: " +
                             ("; ".join(changed[:12]) or "none in the standard fields") + "."),
                innocent=["Both files legitimately exported by the same lab at different stages"] +
                         ([] if serious else ["A format conversion for sharing or viewing (changes no content)"]),
                data=data))
        elif s_ot > s_me:
            out.append(Finding(code="DERIV_SOURCE", module="compare", family="derivative", status=INFO,
                               title=f"{label} is a modified copy of this exhibit",
                               explanation=f"{label} shows the same picture (similarity {corr:.2f}) and it " +
                                           "; it ".join(why_ot) + ". This exhibit is the earlier generation.",
                               data=data))
            ctx.shared.setdefault("reanalyze", []).append(o["id"])
        else:
            out.append(Finding(code="DERIV_RELATED", module="compare", family="derivative", status=INFO,
                               title=f"Same picture as {label}",
                               explanation=f"Signature similarity {corr:.2f}. Differences: " +
                                           ("; ".join(changed[:12]) or "none in the standard fields") + ".",
                               data=data))
    return out
