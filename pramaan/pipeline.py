"""Ingest -> integrity -> modality routing -> analyzers -> fusion -> persisted findings.

Originals are written once into a content-addressed, read-only store and never opened for
analysis; every job works on a copy, and the original is re-hashed after analysis to prove it
was not altered (custody event HASH_VERIFIED).
"""
from __future__ import annotations

import json
import mimetypes
import shutil
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO, Callable

from . import caselog, config, db, fusion
from .analyzers.base import Context, Finding
from .custody import hashing, ledger

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".heic"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".amr", ".wma"}

_pool = ThreadPoolExecutor(max_workers=config.WORKERS)


# --------------------------------------------------------------------------- ingest
def ingest(case_id: str, filename: str, stream: BinaryIO, actor: str, exhibit_no: str | None = None,
           acquisition_source: str | None = None, device_details: str | None = None,
           seized_at: str | None = None) -> dict:
    config.ensure_dirs()
    tmp = config.EVIDENCE_DIR / f".upload_{db.new_id('u')}"
    with open(tmp, "wb") as f:
        shutil.copyfileobj(stream, f, length=1024 * 1024)
    h = hashing.hash_file(tmp)
    ext = Path(filename).suffix.lower()
    dest = config.EVIDENCE_DIR / f"{h['sha256']}{ext}"
    if dest.exists():
        tmp.unlink()
    else:
        tmp.rename(dest)
        dest.chmod(0o444)   # write-once
    eid = db.new_id("ev")
    modality = detect_modality(dest)
    db.insert("evidence", {"id": eid, "case_id": case_id, "exhibit_no": exhibit_no, "original_filename": filename,
                           "mime": mimetypes.guess_type(filename)[0], "modality": modality, "size": h["size"],
                           "sha256": h["sha256"], "sha1": h["sha1"], "md5": h["md5"], "storage_path": str(dest),
                           "acquisition_source": acquisition_source, "device_details": device_details,
                           "seized_at": seized_at, "ingested_at": db.now(), "ingested_by": actor})
    ledger.append(eid, actor, "INGESTED", {"filename": filename, "case_id": case_id, "exhibit_no": exhibit_no,
                                           "acquisition_source": acquisition_source})
    ledger.append(eid, "system", "HASH_COMPUTED", {"sha256": h["sha256"], "sha1": h["sha1"], "md5": h["md5"],
                                                   "size": h["size"], "algorithm": "SHA-256 (primary), SHA-1, MD5"})
    ev = db.q1("SELECT * FROM evidence WHERE id=?", (eid,))
    caselog.evidence_added(ev, actor)  # type: ignore[arg-type]
    return ev  # type: ignore[return-value]


def detect_modality(path: Path) -> str:
    """By the file's signature bytes first (a renamed file is analysed as what it really is), then by extension."""
    from .analyzers import filetype
    real = filetype.sniff(path)
    if real in filetype.MODALITY:
        return filetype.MODALITY[real]
    if real == "empty":
        return "empty"
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    try:
        from PIL import Image
        Image.open(path).verify()
        return "image"
    except Exception:
        pass
    try:
        from .analyzers.video.probe import probe
        info = probe(path)
        kinds = {s.get("codec_type") for s in info.get("streams", [])
                 if not s.get("disposition", {}).get("attached_pic")}
        if "video" in kinds:
            return "video"
        if "audio" in kinds:
            return "audio"
    except Exception:
        pass
    return "unknown"


def verify_hash(evidence_id: str, actor: str = "system") -> dict:
    ev = db.q1("SELECT * FROM evidence WHERE id=?", (evidence_id,))
    if not ev:
        raise KeyError(evidence_id)
    h = hashing.hash_file(ev["storage_path"])
    ok = h["sha256"] == ev["sha256"]
    ledger.append(evidence_id, actor, "HASH_VERIFIED" if ok else "HASH_MISMATCH",
                  {"expected": ev["sha256"], "actual": h["sha256"]})
    return {"ok": ok, "expected": ev["sha256"], "actual": h["sha256"]}


# --------------------------------------------------------------------------- analysis
def analyzers_for(modality: str) -> list[tuple[str, Callable[[Context], list[Finding]]]]:
    from .analyzers.metadata import standard
    return _analyzers(modality) + [("metadata_table", standard.run)]


def _analyzers(modality: str) -> list[tuple[str, Callable[[Context], list[Finding]]]]:
    from .analyzers import filetype, provenance
    common = [("filetype", filetype.run), ("c2pa", provenance.c2pa)]
    if modality == "image":
        from .analyzers.image import copymove, dl_plugins, ela, jpeg, noise, prnu
        from .analyzers.metadata import rules
        from .analyzers import derivative
        return common + [("metadata", rules.run), ("compare", derivative.run), ("jpeg", jpeg.run), ("noise", noise.run), ("copymove", copymove.run),
                ("ela", ela.run), ("trufor", dl_plugins.trufor), ("catnet", dl_plugins.catnet), ("aigi", dl_plugins.aigi),
                ("prnu", prnu.run)]
    if modality == "video":
        from .analyzers.video import double_compression, face_deepfake, grade, lipsync, osd, pixels, probe
        from .analyzers.audio import acoustic, enf, partial_spoof, spoof
        return common + [("container", probe.run), ("double_compression", double_compression.run), ("pixels", pixels.run),
                ("grade", grade.run), ("osd", osd.run), ("lipsync", lipsync.run), ("face_deepfake", face_deepfake.run),
                ("audio_track", _audio_track(acoustic.run)), ("enf", _audio_track(enf.run)),
                ("spoof", _audio_track(spoof.run)), ("partial_spoof", _audio_track(partial_spoof.run))]
    if modality == "audio":
        from .analyzers.audio import acoustic, enf, partial_spoof, spoof
        return common + [("acoustic", acoustic.run), ("enf", enf.run), ("spoof", spoof.run), ("partial_spoof", partial_spoof.run)]
    from .analyzers.metadata import documents
    return common + [("document", documents.run)]       # documents, empty and unknown files


def _audio_track(fn):
    """Run an audio analyzer on a video's audio track, if it has one (cross-modal: ENF in CCTV audio)."""
    def wrapped(ctx: Context) -> list[Finding]:
        info = ctx.shared.get("probe") or {}
        if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
            return []
        fs = fn(ctx)
        for f in fs:
            f.module = "video-audio"
        return [f for f in fs if f.code != "AUD_SUMMARY"]
    return wrapped


def recover_interrupted(actor: str = "system") -> int:
    """Jobs left 'queued'/'running' when the server stopped will never finish (their thread is gone).
    On start-up, mark them interrupted and run them again."""
    stale = db.q("SELECT * FROM jobs WHERE status IN ('queued', 'running')")
    for j in stale:
        db.update("jobs", "id", j["id"], {"status": "interrupted", "stage": "server restarted", "finished_at": db.now()})
        ledger.append(j["evidence_id"], actor, "ANALYSIS_INTERRUPTED", {"job_id": j["id"], "resubmitted": True})
        submit(j["evidence_id"], actor)
    return len(stale)


def submit(evidence_id: str, actor: str = "system") -> str:
    job_id = db.new_id("job")
    db.insert("jobs", {"id": job_id, "evidence_id": evidence_id, "status": "queued", "progress": 0.0,
                       "stage": "queued", "started_at": db.now()})
    _pool.submit(run_job, job_id, evidence_id, actor)
    return job_id


def run_job(job_id: str, evidence_id: str, actor: str = "system") -> dict:
    ev = db.q1("SELECT * FROM evidence WHERE id=?", (evidence_id,))
    assert ev, evidence_id
    work = config.WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    from .analyzers import filetype
    real = filetype.sniff(ev["storage_path"])
    real_ext = next((e for e, k in filetype.EXT_KIND.items() if k == real), None) or Path(ev["storage_path"]).suffix
    copy = work / ("working_copy" + real_ext)       # tools that look at extensions see the true type
    shutil.copyfile(ev["storage_path"], copy)
    copy.chmod(0o444)
    art_dir = config.ARTIFACT_DIR / job_id
    stages = analyzers_for(ev["modality"])
    versions = _versions()
    db.update("jobs", "id", job_id, {"status": "running", "versions": versions})
    ledger.append(evidence_id, actor, "ANALYSIS_STARTED", {"job_id": job_id, "modality": ev["modality"],
                                                          "modules": [s[0] for s in stages], "versions": versions})
    ctx = Context(evidence_id=evidence_id, path=copy, work_dir=work, artifact_dir=art_dir, modality=ev["modality"])
    ctx.shared["original_filename"] = ev["original_filename"]
    ctx.shared["case_id"] = ev["case_id"]
    findings: list[dict] = []
    errors = []
    for k, (name, fn) in enumerate(stages):
        db.update("jobs", "id", job_id, {"stage": name, "progress": k / max(len(stages), 1)})
        ctx.progress = lambda s, name=name: db.update("jobs", "id", job_id, {"stage": f"{name}: {s}"})
        t0 = time.time()
        try:
            for f in fn(ctx):
                d = f.to_dict()
                d["elapsed_s"] = round(time.time() - t0, 2)
                findings.append(d)
        except Exception as e:  # one failing module never kills the case
            errors.append({"module": name, "error": str(e), "trace": traceback.format_exc()[-1500:]})
            findings.append(Finding(code=f"ERR_{name.upper()}", module=name, family="error", title=f"{name} failed",
                                    status="unavailable", explanation=str(e)[:500]).to_dict())
    verdict = fusion.fuse(findings, ev["modality"])
    timeline = {k[len("timeline_"):]: v for k, v in ctx.shared.items() if k.startswith("timeline_")}
    with db.tx() as c:
        for d in findings:
            c.execute("INSERT INTO findings (id, job_id, evidence_id, code, module, family, title, status, raw_score, llr,"
                      " reliability, location, explanation, innocent, artifacts, data) VALUES"
                      " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (db.new_id("f"), job_id, evidence_id, d["code"], d["module"], d["family"], d["title"], d["status"],
                       d["raw_score"], d["llr"], d["reliability"], json.dumps(d["location"], default=str),
                       d["explanation"], json.dumps(d["innocent"]), json.dumps(d["artifacts"]),
                       json.dumps(d["data"], default=str)))
        c.execute("INSERT INTO verdicts (id, job_id, evidence_id, category, confidence, total_llr, coverage, abstained,"
                  " rationale, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (db.new_id("v"), job_id, evidence_id, verdict["category"], verdict["confidence"], verdict["total_llr"],
                   verdict["coverage"], int(verdict["abstained"]), json.dumps({**verdict, "timeline": timeline,
                                                                               "errors": errors}, default=str), db.now()))
    post = verify_hash(evidence_id)
    db.update("jobs", "id", job_id, {"status": "done" if not errors else "done_with_errors", "progress": 1.0,
                                     "stage": "complete", "finished_at": db.now(),
                                     "error": json.dumps(errors) if errors else None})
    db.update("evidence", "id", evidence_id, {"status": "analysed"})
    ledger.append(evidence_id, "system", "ANALYSIS_COMPLETED", {"job_id": job_id, "verdict": verdict["category"],
                                                               "total_llr": verdict["total_llr"],
                                                               "original_unchanged": post["ok"]})
    shutil.rmtree(work, ignore_errors=True)
    # A copy of this exhibit was analysed earlier without knowing this original: re-run it so it gets the comparison.
    for other in dict.fromkeys(ctx.shared.get("reanalyze", [])):
        j = latest_job(other)
        if j and str(j["status"]).startswith("done"):
            fs = db.q("SELECT data FROM findings WHERE job_id=? AND code LIKE 'DERIV_%'", (j["id"],))
            if not any(evidence_id in str(f["data"]) for f in fs):
                ledger.append(other, "system", "REANALYSIS_REQUESTED", {"reason": "related exhibit received",
                                                                        "related": evidence_id})
                submit(other, "system")
    return verdict


def latest_job(evidence_id: str) -> dict | None:
    return db.q1("SELECT * FROM jobs WHERE evidence_id=? ORDER BY started_at DESC LIMIT 1", (evidence_id,))


def results(evidence_id: str) -> dict:
    job = latest_job(evidence_id)
    if not job:
        return {"job": None}
    fs = db.q("SELECT * FROM findings WHERE job_id=?", (job["id"],))
    order = {"fail": 0, "warn": 1, "unavailable": 4, "info": 3, "pass": 2}
    fs.sort(key=lambda f: (order.get(f["status"], 5), -abs(f["llr"] or 0)))
    v = db.q1("SELECT * FROM verdicts WHERE job_id=?", (job["id"],))
    return {"job": job, "findings": fs, "verdict": v}


def _versions() -> dict:
    import cv2
    import numpy
    import scipy
    from . import config as c
    v = {"pramaan": c.VERSION, "opencv": cv2.__version__, "numpy": numpy.__version__, "scipy": scipy.__version__}
    try:
        import subprocess
        v["ffmpeg"] = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split()[2]
    except Exception:
        pass
    try:
        import pytesseract
        v["tesseract"] = str(pytesseract.get_tesseract_version())
    except Exception:
        pass
    return v
