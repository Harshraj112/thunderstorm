"""Signed forensic report (PDF).

Structure follows SWGDE authentication-report practice and pre-fills a draft of the
Bharatiya Sakshya Adhiniyam (BSA) 2023 Section 63 certificate, Part B (expert), for a HUMAN
examiner to review and sign. The software never claims to be the expert.

Integrity of the report itself:
  * manifest = canonical JSON of {report id, exhibit hashes, verdict, every finding's code/LLR,
    custody head hash, Merkle root} – signed with the lab's Ed25519 key.
  * the PDF's own SHA-256 is recorded in the database; the QR code links to /verify/<id>.
  * scripts/verify_report.py verifies a PDF + manifest offline with the lab public key.
"""
from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.graphics.barcode.qr import QrCodeWidget  # noqa: E402
from reportlab.graphics.shapes import Drawing  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,  # noqa: E402
                                Table, TableStyle)

from .. import config, db, pipeline  # noqa: E402
from ..custody import hashing, ledger, signing  # noqa: E402
from .narrator import narrate  # noqa: E402

INK = colors.HexColor("#1b2430")
MUTED = colors.HexColor("#5b6675")
LINE = colors.HexColor("#d5dae1")
STATUS_COL = {"fail": colors.HexColor("#b42318"), "warn": colors.HexColor("#b54708"),
              "pass": colors.HexColor("#067647"), "info": colors.HexColor("#475467"),
              "unavailable": colors.HexColor("#98a2b3")}
VERDICT_COL = {"Strong indications of manipulation": "#b42318", "Indications of manipulation": "#b54708",
               "No indications of manipulation detected": "#067647", "Inconclusive": "#475467"}


def _styles():
    ss = getSampleStyleSheet()
    base = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=9, leading=12.5, textColor=INK)
    return {
        "base": base,
        "small": ParagraphStyle("s", parent=base, fontSize=7.5, leading=10, textColor=MUTED),
        "mono": ParagraphStyle("m", parent=base, fontName="Courier", fontSize=7.5, leading=9.5),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=20, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=10,
                             spaceAfter=5),
        "h3": ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=9.5, leading=12.5, spaceBefore=4),
        "center": ParagraphStyle("c", parent=base, alignment=TA_CENTER),
    }


def _esc(s) -> str:
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _strength(llr: float, rel: float) -> str:
    w = llr * rel
    if w >= 1.5:
        return "Strong support for manipulation"
    if w >= 0.5:
        return "Moderate support for manipulation"
    if w > 0.15:
        return "Weak support for manipulation"
    if w <= -0.3:
        return "Supports authenticity"
    return "Neutral"


def _table(rows, widths, header=True, font=8):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    st = [("FONT", (0, 0), (-1, -1), "Helvetica", font), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
          ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", font), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f4f7"))]
    t.setStyle(TableStyle(st))
    return t


def _qr(text: str, size=28 * mm) -> Drawing:
    w = QrCodeWidget(text)
    b = w.getBounds()
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(w)
    return d


def _timeline_png(timeline: dict, modality: str) -> bytes | None:
    panels = []
    if "osd" in timeline and timeline["osd"].get("pts"):
        panels.append("osd")
    if "pixels" in timeline:
        panels.append("pixels")
    if "enf" in timeline:
        panels.append("enf")
    if "audio" in timeline:
        panels.append("audio")
    if not panels:
        return None
    fig, axes = plt.subplots(len(panels), 1, figsize=(7.2, 1.7 * len(panels)), sharex=False)
    axes = [axes] if len(panels) == 1 else axes
    for ax, p in zip(axes, panels):
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=7)
        if p == "osd":
            o = timeline["osd"]
            ax.plot(o["pts"], o["offset"], color="#1f4e79", lw=1.4, marker=".", ms=3)
            for e in o.get("events", []):
                ax.axvline(e["at"], color="#b42318", lw=1, ls="--")
            ax.set_ylabel("OSD − file\nclock (s)", fontsize=7)
            ax.set_title("On-screen clock vs container timestamps (flat = consistent)", fontsize=8, loc="left")
        elif p == "pixels":
            x = timeline["pixels"]
            ax.plot(x["t"], x["diff"], color="#475467", lw=0.8)
            ax.set_ylabel("frame diff", fontsize=7)
            ax.set_title("Frame-to-frame change", fontsize=8, loc="left")
        elif p == "enf":
            e = timeline["enf"]
            ax.plot(e["t"], e["phase_jump"], color="#7a2e8e", lw=1)
            ax.axhline(0.6, color="#b42318", lw=0.8, ls=":")
            ax.set_ylabel("phase step\n(rad)", fontsize=7)
            ax.set_title(f"Mains hum ({e['grid']:.0f} Hz × {e['harmonic']}) phase continuity", fontsize=8, loc="left")
        elif p == "audio":
            a = timeline["audio"]
            ax.plot(a["t"], a["noise_floor_db"], color="#1f6f43", lw=1.2)
            ax.set_ylabel("noise floor\n(dBFS)", fontsize=7)
            ax.set_title("Background noise floor", fontsize=8, loc="left")
        ax.set_xlabel("file time (s)", fontsize=7)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160)
    plt.close(fig)
    return buf.getvalue()


def _img(path: Path, max_w=170 * mm, max_h=70 * mm):
    from PIL import Image as PImage
    try:
        with PImage.open(path) as im:
            w, h = im.size
    except Exception:
        return None
    s = min(max_w / w, max_h / h)
    return Image(str(path), width=w * s, height=h * s)


ANNEX_HI = {
    "Electronic record": "इलेक्ट्रॉनिक अभिलेख", "Produced from (device / system)": "उत्पत्ति (उपकरण / प्रणाली)",
    "Mode of acquisition": "प्राप्ति का तरीका", "Hash algorithm": "हैश एल्गोरिद्म",
    "Hash value (SHA-256)": "हैश मान (SHA-256)", "Hash value (SHA-1)": "हैश मान (SHA-1)",
    "Hash value (MD5)": "हैश मान (MD5)", "Hash re-verified on": "हैश पुनः सत्यापन",
    "Examination report reference": "परीक्षण रिपोर्ट संदर्भ", "Name / designation of expert": "विशेषज्ञ का नाम / पद",
    "Signature, date, place": "हस्ताक्षर, दिनांक, स्थान",
}


def _hi_img(text: str, width_mm: float, size: float = 10, bold: bool = False, color=(27, 36, 48)):
    from . import hindi
    png, w, h = hindi.render(text, width_mm, size, bold, color)
    return Image(io.BytesIO(png), width=w * mm, height=h * mm)


def _hindi_summary(ev, verdict, findings, integrity_ok, narration_hi, S) -> list:
    from .hindi import CAT_HI, CONF_HI, STATUS_HI, TITLE_HI
    col = {"fail": (180, 35, 24), "warn": (161, 92, 7), "pass": (6, 118, 71)}
    out = [Spacer(1, 6), _hi_img("1क. सारांश (हिन्दी)", 172, 11.5, bold=True)]
    v = CAT_HI.get(verdict["category"], verdict["category"])
    c = CONF_HI.get(verdict["confidence"], verdict["confidence"])
    vc = {"Strong indications of manipulation": (180, 35, 24), "Indications of manipulation": (161, 92, 7),
          "No indications of manipulation detected": (6, 118, 71)}.get(verdict["category"], (71, 84, 103))
    out.append(_hi_img(f"प्रामाणिकता निष्कर्ष: {v}" + (f"  ·  विश्वास स्तर: {c}" if c != "—" else ""), 172, 10.5,
                       bold=True, color=vc))
    out.append(_hi_img(narration_hi, 172, 9.5))
    rows = []
    for f in findings:
        if f["status"] not in ("fail", "warn", "pass"):
            continue
        loc = f.get("location") or {}
        t = loc.get("time", loc.get("start"))
        if t is None and loc.get("times"):
            t = loc["times"][0]
        rows.append([_hi_img(TITLE_HI.get(f["code"], f["title"]), 112, 9),
                     _hi_img(STATUS_HI[f["status"]], 26, 9, bold=True, color=col[f["status"]]),
                     Paragraph(f"{t:.1f} s" if isinstance(t, (int, float)) else "—", S["small"])])
    if rows:
        head = [_hi_img("जाँच", 30, 8.5, bold=True), _hi_img("परिणाम", 26, 8.5, bold=True),
                _hi_img("समय", 20, 8.5, bold=True)]
        out.append(_table([head] + rows, [118 * mm, 32 * mm, 22 * mm], font=7.5))
    out.append(_hi_img("ये स्वचालित विश्लेषण के संकेत हैं; अंतिम राय योग्य विशेषज्ञ देंगे। भारतीय साक्ष्य अधिनियम, 2023 "
                       "की धारा 63 के प्रमाण-पत्र का प्रारूप परिशिष्ट A में है।", 172, 8, color=(91, 102, 117)))
    return out


def build(evidence_id: str, examiner: str = "Examiner (to sign)", lang: str = "bi") -> dict:
    """lang: "en" English only, "bi" English + Hindi summary/labels (default)."""
    ev = db.q1("SELECT * FROM evidence WHERE id=?", (evidence_id,))
    if not ev:
        raise KeyError(evidence_id)
    res = pipeline.results(evidence_id)
    if not res.get("job") or not res.get("verdict"):
        raise ValueError("evidence has not been analysed yet")
    case = db.q1("SELECT * FROM cases WHERE id=?", (ev["case_id"],)) or {}
    job, findings, vrow = res["job"], res["findings"], res["verdict"]
    verdict = vrow["rationale"]
    integ = pipeline.verify_hash(evidence_id, actor="report-service")
    chain = ledger.verify_chain()
    anchor = ledger.anchor()
    events = ledger.events(evidence_id)
    version = (db.q1("SELECT MAX(version) AS v FROM reports WHERE evidence_id=?", (evidence_id,)) or {}).get("v") or 0
    rid = db.new_id("rep")
    created = datetime.now()
    narration = narrate(ev, verdict, findings, integ["ok"])

    manifest = {"report_id": rid, "version": version + 1, "lab": config.LAB_NAME, "created_at": created.isoformat(),
                "evidence": {"id": ev["id"], "filename": ev["original_filename"], "sha256": ev["sha256"],
                             "sha1": ev["sha1"], "md5": ev["md5"], "size": ev["size"]},
                "integrity_reverified": integ["ok"],
                "verdict": {k: verdict[k] for k in ("category", "confidence", "total_llr", "coverage", "abstained")},
                "findings": [{"code": f["code"], "status": f["status"], "llr": f["llr"], "reliability": f["reliability"]}
                             for f in findings],
                "custody_head": chain.get("head"), "merkle_root": anchor["merkle_root"],
                "tool_versions": job.get("versions"), "public_key_fingerprint": signing.fingerprint()}
    manifest_bytes = ledger.canonical(manifest)
    signature = signing.sign(manifest_bytes)
    verify_url = f"{config.PUBLIC_BASE_URL}/verify/{rid}"

    S = _styles()
    out_path = config.REPORT_DIR / f"{rid}.pdf"
    art_dir = config.ARTIFACT_DIR / job["id"]
    story = []

    # ---------------- header
    head = Table([[
        [Paragraph("DIGITAL EVIDENCE EXAMINATION REPORT", S["h1"]),
         Paragraph(f"{_esc(config.LAB_NAME)} · Generated with PRAMAAN v{config.VERSION}", S["small"]),
         Spacer(1, 4),
         Paragraph(f"<b>Report</b> {rid} (v{version + 1}) · <b>Date</b> {created:%d %b %Y %H:%M}", S["base"]),
         Paragraph(f"<b>Case / FIR</b> {_esc(case.get('fir_no') or '—')} · <b>Police station</b> "
                   f"{_esc(case.get('police_station') or '—')} · <b>IO</b> {_esc(case.get('io_name') or '—')}", S["base"]),
         Paragraph(f"<b>Exhibit</b> {_esc(ev.get('exhibit_no') or ev['id'])} · <b>Examiner</b> {_esc(examiner)}", S["base"])],
        [_qr(verify_url), Paragraph("Scan to verify", S["small"])]]],
        colWidths=[140 * mm, 32 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1, INK)]))
    story += [head, Spacer(1, 8)]

    # ---------------- 1. summary
    vc = VERDICT_COL.get(verdict["category"], "#475467")
    story.append(Paragraph("1. Summary of findings", S["h2"]))
    nature = verdict.get("nature") or []
    box = Table([[Paragraph(f"<font color='{vc}'><b>{_esc(verdict['category'])}</b></font>"
                            + (f"<br/><font size=8.5>What was found: <b>{_esc('; '.join(nature))}</b></font>" if nature else "")
                            + (f" &nbsp;·&nbsp; confidence: <b>{verdict['confidence']}</b>" if verdict['confidence'] != '—' else "")
                            + f"<br/><font size=8 color='#5b6675'>Combined evidence weight (log-likelihood ratio) "
                              f"{verdict['total_llr']:+.2f}; evidence coverage {verdict['coverage']:.0%}</font>", S["base"]),
                  Paragraph(("<font color='#067647'><b>INTEGRITY VERIFIED</b></font><br/>" if integ["ok"] else
                             "<font color='#b42318'><b>INTEGRITY FAILURE</b></font><br/>") +
                            "<font size=7.5>SHA-256 at intake = SHA-256 now</font>", S["base"])]],
                colWidths=[125 * mm, 47 * mm])
    box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, LINE), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fafbfc")),
                             ("LINEAFTER", (0, 0), (0, 0), 0.5, LINE), ("TOPPADDING", (0, 0), (-1, -1), 6),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
    story += [box, Spacer(1, 5), Paragraph(_esc(narration.get("en_llm") or narration["en"]), S["base"]),
              Spacer(1, 3), Paragraph("<i>Integrity</i> (the file is bit-identical to what was received) and "
                                      "<i>authenticity</i> (the content truly depicts the event) are different questions; "
                                      "a matching hash proves only the former.", S["small"])]

    # ---------------- 1A. Hindi summary
    from . import hindi
    use_hi = lang in ("bi", "hi") and hindi.available()
    if use_hi:
        story += _hindi_summary(ev, verdict, findings, integ["ok"], narration["hi"], S)

    # ---------------- 2. evidence
    story.append(Paragraph("2. Evidence received", S["h2"]))
    rows = [["Field", "Value"],
            ["Original file name", Paragraph(_esc(ev["original_filename"]), S["base"])],
            ["Type / size", f"{ev['modality']} · {ev['size']:,} bytes"],
            ["SHA-256", Paragraph(ev["sha256"], S["mono"])], ["SHA-1", Paragraph(ev["sha1"], S["mono"])],
            ["MD5", Paragraph(ev["md5"], S["mono"])],
            ["Acquisition source", Paragraph(_esc(ev.get("acquisition_source") or "—"), S["base"])],
            ["Device details", Paragraph(_esc(ev.get("device_details") or "—"), S["base"])],
            ["Received in lab", datetime.fromtimestamp(ev["ingested_at"]).strftime("%d %b %Y %H:%M:%S")],
            ["Re-verified at report time", "MATCH" if integ["ok"] else f"MISMATCH ({integ['actual']})"]]
    story.append(_table(rows, [42 * mm, 130 * mm]))

    # ---------------- 2A. metadata in the standard output format
    mt = next((f for f in findings if f["code"] == "META_TABLE"), None)
    table = (mt or {}).get("data") if mt else None
    if not table:
        try:
            from ..analyzers.metadata.standard import build as _mbuild
            table = _mbuild(Path(ev["storage_path"]), ev["original_filename"])
        except Exception:
            table = None
    if table:
        story.append(Paragraph("2A. Metadata (standard format)", S["h2"]))
        rows = [["Field", "Value"]] + [[k, Paragraph(_esc(str(v)), S["base"])] for k, v in table["standard"]]
        story.append(_table(rows, [42 * mm, 130 * mm]))
        for group, kv in (table.get("additional") or {}).items():
            if group in ("Content Credentials (C2PA)", "PNG text chunks", "Media", "Document"):
                story.append(Paragraph(_esc(group), S["h3"]))
                story.append(_table([["Field", "Value"]] + [[_esc(str(k)), Paragraph(_esc(str(v))[:300], S["small"])]
                                                            for k, v in list(kv.items())[:20]], [42 * mm, 130 * mm], font=7.5))

    # ---------------- 3. custody
    story.append(Paragraph("3. Chain of custody (tamper-evident ledger)", S["h2"]))
    rows = [["#", "Time", "Actor", "Action", "Entry hash"]]
    for e in events[-25:]:
        rows.append([str(e["seq"]), datetime.fromtimestamp(e["ts"]).strftime("%d-%m-%Y %H:%M:%S"), _esc(e["actor"]),
                     e["action"], Paragraph(e["entry_hash"][:32] + "…", S["mono"])])
    story.append(_table(rows, [10 * mm, 32 * mm, 30 * mm, 38 * mm, 62 * mm], font=7.5))
    story.append(Paragraph(f"Ledger verification: <b>{'intact' if chain['ok'] else 'BROKEN at #' + str(chain.get('broken_at'))}</b>"
                           f" ({chain.get('events', '?')} events). Signed Merkle root over all events up to "
                           f"#{anchor['upto_seq']}: <font face='Courier' size=7>{anchor['merkle_root']}</font>", S["small"]))

    # ---------------- 4. methods
    story.append(Paragraph("4. Examination performed", S["h2"]))
    mods = sorted({f["module"] for f in findings})
    story.append(Paragraph(
        "Propositions considered: <b>H1</b> – the exhibit is an unaltered, original recording/image of the depicted "
        "event; <b>H2</b> – the exhibit has been edited, composited or synthetically generated. Each test reports a "
        "calibrated log-likelihood ratio (positive supports H2) and a reliability for this specific input. Correlated "
        "tests are grouped into families so they are not double counted; when coverage is insufficient the system "
        f"abstains (\"Inconclusive\"). Modules run: {', '.join(mods)}. All analysis was performed on a working copy "
        "inside an offline environment; the original was never opened for writing.", S["base"]))
    vers = job.get("versions") or {}
    story.append(Paragraph("Tool versions: " + "; ".join(f"{k} {v}" for k, v in vers.items()), S["small"]))

    # ---------------- 5. findings
    story.append(Paragraph("5. Detailed findings", S["h2"]))
    tl = verdict.get("timeline") or {}
    png = _timeline_png(tl, ev["modality"])
    if png:
        p = art_dir / "timeline_report.png"
        art_dir.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png)
        story += [_img(p, max_h=95 * mm), Spacer(1, 4)]
    shown = [f for f in findings if f["status"] in ("fail", "warn", "pass")]
    for k, f in enumerate(shown, 1):
        col = STATUS_COL.get(f["status"], MUTED)
        block = [Paragraph(f"<font color='{col.hexval()}'>■</font> <b>5.{k} {_esc(f['title'])}</b>", S["h3"]),
                 Paragraph(f"<font size=7.5 color='#5b6675'>{f['module']} · family {f['family']} · {f['code']} · "
                           f"LLR {f['llr']:+.2f} · reliability {f['reliability']:.2f} · "
                           f"<b>{_strength(f['llr'], f['reliability'])}</b></font>", S["base"]),
                 Paragraph(_esc(f["explanation"]), S["base"])]
        loc = f.get("location") or {}
        if loc:
            block.append(Paragraph("Location: " + _esc(json.dumps(loc, default=str))[:600], S["small"]))
        if f.get("innocent"):
            block.append(Paragraph("<i>Alternative (innocent) explanations:</i> " + _esc("; ".join(f["innocent"])), S["small"]))
        for a in (f.get("artifacts") or [])[:2]:
            ip = art_dir / a
            if ip.exists():
                im = _img(ip, max_w=120 * mm, max_h=65 * mm)
                if im:
                    block.append(im)
        block.append(Spacer(1, 4))
        story.append(KeepTogether(block))
    other = [f for f in findings if f["status"] in ("info", "unavailable") and f["code"] != "META_TABLE"]
    if other:
        story.append(Paragraph("Informational / not applicable", S["h3"]))
        story.append(_table([["Check", "Note"]] + [[Paragraph(_esc(f["title"]), S["small"]),
                                                    Paragraph(_esc(f["explanation"])[:300], S["small"])] for f in other],
                            [55 * mm, 117 * mm], font=7))

    # ---------------- 6. conclusion & limitations
    story.append(Paragraph("6. Conclusion", S["h2"]))
    sup = ", ".join(f"{c['family']} ({c['llr']:+.1f})" for c in verdict.get("supporting", [])) or "none"
    con = ", ".join(f"{c['family']} ({c['llr']:+.1f})" for c in verdict.get("contradicting", [])) or "none"
    story.append(Paragraph(f"On the basis of the tests above, the examination finds: <b>{_esc(verdict['category'])}</b>. "
                           f"Evidence families supporting manipulation: {sup}. Evidence families supporting "
                           f"authenticity: {con}." + (f" {verdict['abstain_reason']}" if verdict.get("abstain_reason") else ""),
                           S["base"]))
    story.append(Paragraph("7. Limitations", S["h2"]))
    story.append(Paragraph(
        "Automated detectors have known error rates that rise on heavily recompressed, low-resolution, low-light or "
        "very short material, and AI-generation detectors may not recognise generators released after their training. "
        "Absence of detected manipulation is not proof of authenticity. Metadata can be edited and is routinely "
        "stripped by messaging applications; its absence is neutral. Results must be interpreted by a qualified "
        "examiner together with case context (device seizure records, DVR configuration, witness accounts).", S["base"]))

    # ---------------- Annex A: Section 63
    story.append(PageBreak())
    story.append(Paragraph("Annex A – Draft particulars for the certificate under Section 63(4)(c), "
                           "Bharatiya Sakshya Adhiniyam, 2023 (Schedule, Part B – Expert)", S["h2"]))
    story.append(Paragraph("<b>DRAFT – auto-filled by software for review. To be verified, completed and signed by the "
                           "expert. Part A must be completed by the person in charge of the device.</b>", S["small"]))
    rows = [["Particular", "Value"],
            ["Electronic record", Paragraph(_esc(ev["original_filename"]), S["base"])],
            ["Produced from (device / system)", Paragraph(_esc(ev.get("device_details") or "[to be completed]"), S["base"])],
            ["Mode of acquisition", Paragraph(_esc(ev.get("acquisition_source") or "[to be completed]"), S["base"])],
            ["Hash algorithm", "SHA-256 (also SHA-1, MD5 listed)"],
            ["Hash value (SHA-256)", Paragraph(ev["sha256"], S["mono"])],
            ["Hash value (SHA-1)", Paragraph(ev["sha1"], S["mono"])],
            ["Hash value (MD5)", Paragraph(ev["md5"], S["mono"])],
            ["Hash re-verified on", f"{created:%d %b %Y %H:%M} – {'matches' if integ['ok'] else 'DOES NOT MATCH'}"],
            ["Examination report reference", f"{rid} v{version + 1}"],
            ["Name / designation of expert", "______________________________"],
            ["Signature, date, place", "______________________________"]]
    if use_hi:
        for r in rows[1:]:
            hi_label = ANNEX_HI.get(r[0])
            if hi_label:
                r[0] = [Paragraph(r[0], S["base"]), _hi_img(hi_label, 56, 8, color=(91, 102, 117))]
    story.append(_table(rows, [60 * mm, 112 * mm]))

    # ---------------- Annex B: signature
    story.append(Paragraph("Annex B – Digital signature of this report", S["h2"]))
    story.append(Paragraph("The manifest below (exhibit hashes, verdict, every finding's code and score, custody head and "
                           "Merkle root) is signed with the laboratory's Ed25519 key. Any change to these facts invalidates "
                           f"the signature. Verify online at {verify_url} or offline with scripts/verify_report.py.", S["base"]))
    story.append(Paragraph(f"Public key fingerprint: {signing.fingerprint()}<br/>Manifest SHA-256: "
                           f"{hashing.sha256_bytes(manifest_bytes)}<br/>Signature: {signature}", S["mono"]))

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"{rid} · exhibit SHA-256 {ev['sha256'][:16]}… · {config.LAB_NAME}")
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm,
                            bottomMargin=16 * mm, title=f"Forensic report {rid}", author=config.LAB_NAME)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    pdf_sha = hashing.hash_file(out_path)["sha256"]
    (config.REPORT_DIR / f"{rid}.manifest.json").write_bytes(manifest_bytes)
    (config.REPORT_DIR / f"{rid}.sig").write_text(signature)
    db.insert("reports", {"id": rid, "evidence_id": evidence_id, "job_id": job["id"], "version": version + 1,
                          "pdf_path": str(out_path), "pdf_sha256": pdf_sha, "manifest": manifest,
                          "signature": signature, "signer": examiner, "created_at": db.now()})
    ledger.append(evidence_id, examiner, "REPORT_SIGNED", {"report_id": rid, "pdf_sha256": pdf_sha,
                                                          "manifest_sha256": hashing.sha256_bytes(manifest_bytes)})
    return {"report_id": rid, "pdf_path": str(out_path), "pdf_sha256": pdf_sha, "signature": signature,
            "narration": narration}


def verify(report_id: str, pdf_bytes: bytes | None = None) -> dict:
    r = db.q1("SELECT * FROM reports WHERE id=?", (report_id,))
    if not r:
        return {"ok": False, "reason": "unknown report"}
    manifest_bytes = ledger.canonical(r["manifest"])
    sig_ok = signing.verify(manifest_bytes, r["signature"])
    stored = Path(r["pdf_path"])
    current = hashing.sha256_bytes(pdf_bytes) if pdf_bytes is not None else (
        hashing.hash_file(stored)["sha256"] if stored.exists() else None)
    ev = db.q1("SELECT sha256 FROM evidence WHERE id=?", (r["evidence_id"],))
    return {"ok": sig_ok and current == r["pdf_sha256"], "signature_valid": sig_ok,
            "pdf_hash_matches": current == r["pdf_sha256"], "pdf_sha256_recorded": r["pdf_sha256"],
            "pdf_sha256_checked": current, "evidence_sha256": ev["sha256"] if ev else None,
            "verdict": r["manifest"]["verdict"], "created_at": r["manifest"]["created_at"],
            "lab": r["manifest"]["lab"], "public_key_fingerprint": r["manifest"]["public_key_fingerprint"]}
