"""HTTP API + dashboard (Starlette/ASGI; runs offline under uvicorn).

    uvicorn pramaan.api.server:app --host 0.0.0.0 --port 8000

Every request may carry `X-Actor: <badge/name>`; it is written to the custody ledger.
Set PRAMAAN_API_KEY to require `X-API-Key` on /api routes.
"""
from __future__ import annotations

import base64
import hmac
import io
import json
import os
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import caselog, config, db, pipeline
from ..custody import ledger, signing

WEB = Path(__file__).resolve().parent.parent / "web"                      # legacy single-file dashboard
NEXT_OUT = Path(__file__).resolve().parents[2] / "frontend" / "out"        # `npm run build` (Next.js static export)
WEB_DIST = Path(__file__).resolve().parent.parent / "web_dist"             # prebuilt bundle of the same React UI
API_KEY = os.environ.get("PRAMAAN_API_KEY")
CORS = os.environ.get("PRAMAAN_CORS", "http://localhost:3000")             # `next dev` origin


def actor(req: Request) -> str:
    return req.headers.get("x-actor") or "examiner"


def J(obj, status=200):
    return Response(json.dumps(obj, default=str), status_code=status, media_type="application/json")


PASSWORD = os.environ.get("PRAMAAN_PASSWORD")                              # set this when the site is online
USERNAME = os.environ.get("PRAMAAN_USER", "pramaan")
# Reachable without the password: the health check (Render pings it) and the report-verification page that a
# report's QR code opens, so a court can check a report without an account.
PUBLIC = re.compile(r"^/(api/health|verify/[\w-]+|api/reports/[\w-]+/verify|favicon\.ico|logo-\d+\.png|fonts/.*)$")


class KeyAuth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if PASSWORD and not PUBLIC.match(path) and request.method != "OPTIONS":
            ok = False
            h = request.headers.get("authorization", "")
            if h.lower().startswith("basic "):
                try:
                    user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                    ok = hmac.compare_digest(pw, PASSWORD) and hmac.compare_digest(user, USERNAME)
                except Exception:
                    ok = False
            if not ok:
                return Response("Password required", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="PRAMAAN", charset="UTF-8"'})
        if API_KEY and path.startswith("/api") and request.headers.get("x-api-key") != API_KEY:
            return JSONResponse({"error": "unauthorised"}, status_code=401)
        return await call_next(request)


# ------------------------------------------------------------------ demo kit & stats
async def samples_list(req: Request):
    from .. import samples
    return J(samples.with_status(samples.list_samples()))


async def showcase(req: Request):
    from .. import samples
    return J(samples.showcase())


async def ledger_recent(req: Request):
    n = min(int(req.query_params.get("n", 6)), 50)
    rows = db.q("SELECT seq, actor, action, ts, entry_hash FROM custody_events ORDER BY seq DESC LIMIT ?", (n,))
    return J({"events": rows[::-1], "chain": ledger.verify_chain()})


async def sample_thumb(req: Request):
    from .. import samples
    p = samples.thumbnail(req.query_params.get("id", ""))
    return FileResponse(p) if p else J({"error": "not found"}, 404)


async def sample_file(req: Request):
    from .. import samples
    p = samples.path_of(req.query_params.get("id", ""))
    return FileResponse(p) if p else J({"error": "not found"}, 404)


async def samples_analyze(req: Request):
    from .. import samples
    b = await req.json()
    ids = b.get("ids") or []
    if not ids:
        return J({"error": "choose at least one sample"}, 400)
    return J(samples.analyze(ids, actor(req)), 202)


async def demo(req: Request):
    from .. import samples
    cid = samples.demo_case()
    return J({"case_id": cid})


async def stats(req: Request):
    one = lambda sql: (db.q1(sql) or {}).get("n", 0)
    return J({"cases": one("SELECT COUNT(*) n FROM cases"), "exhibits": one("SELECT COUNT(*) n FROM evidence"),
              "analysed": one("SELECT COUNT(DISTINCT evidence_id) n FROM verdicts"),
              "flagged": one("SELECT COUNT(DISTINCT evidence_id) n FROM verdicts WHERE category LIKE '%ndications of "
                             "manipulation' AND category NOT LIKE 'No%'"),
              "reports": one("SELECT COUNT(*) n FROM reports"), "ledger": ledger.verify_chain()})


# ------------------------------------------------------------------ cases
async def create_case(req: Request):
    b = await req.json()
    cid = db.new_id("case")
    db.insert("cases", {"id": cid, "fir_no": b.get("fir_no"), "police_station": b.get("police_station"),
                        "io_name": b.get("io_name"), "title": b.get("title"), "created_by": actor(req),
                        "created_at": db.now()})
    ledger.append(None, actor(req), "CASE_CREATED", {"case_id": cid, "fir_no": b.get("fir_no")})
    caselog.case_created(cid, actor(req))
    return J(db.q1("SELECT * FROM cases WHERE id=?", (cid,)), 201)


async def delete_case(req: Request):
    """Permanently delete a case. A reason is required; the case log keeps a snapshot of what was deleted."""
    try:
        b = await req.json()
    except Exception:
        b = {}
    reason = (b.get("reason") or "").strip()
    if len(reason) < 3:
        return J({"error": "give a reason for deleting this case (it is written to the case log)"}, 400)
    try:
        return J(caselog.delete_case(req.path_params["cid"], actor(req), reason))
    except KeyError:
        return J({"error": "case not found"}, 404)
    except caselog.CaseBusy as e:
        return J({"error": str(e)}, 409)


async def case_log(req: Request):
    cid = req.query_params.get("case")
    return J({"entries": caselog.entries(cid, min(int(req.query_params.get("n", 500)), 2000)),
              "chain": caselog.verify()})


async def list_cases(req: Request):
    rows = db.q("SELECT c.*, (SELECT COUNT(*) FROM evidence e WHERE e.case_id=c.id) AS n_evidence "
                "FROM cases c ORDER BY created_at DESC")
    return J(rows)


def _evidence_summary(e: dict) -> dict:
    job = pipeline.latest_job(e["id"])
    v = db.q1("SELECT category, confidence, total_llr FROM verdicts WHERE job_id=?", (job["id"],)) if job else None
    return {**e, "storage_path": None, "job": job and {k: job[k] for k in ("id", "status", "progress", "stage")},
            "verdict": v}


async def get_case(req: Request):
    c = db.q1("SELECT * FROM cases WHERE id=?", (req.path_params["cid"],))
    if not c:
        return J({"error": "not found"}, 404)
    ev = db.q("SELECT * FROM evidence WHERE case_id=? ORDER BY ingested_at", (c["id"],))
    return J({**c, "evidence": [_evidence_summary(e) for e in ev]})


async def upload(req: Request):
    cid = req.path_params["cid"]
    if not db.q1("SELECT id FROM cases WHERE id=?", (cid,)):
        return J({"error": "case not found"}, 404)
    form = await req.form(max_files=500, max_fields=50)
    files = form.getlist("files") or form.getlist("file")
    analyze = str(form.get("analyze", "true")).lower() != "false"
    out = []
    base_no = (db.q1("SELECT COUNT(*) AS n FROM evidence WHERE case_id=?", (cid,)) or {}).get("n", 0)
    for k, f in enumerate(files, 1):
        ev = pipeline.ingest(cid, f.filename, f.file, actor(req),
                             exhibit_no=form.get("exhibit_no") or f"EX-{base_no + k:03d}",
                             acquisition_source=form.get("acquisition_source"),
                             device_details=form.get("device_details"), seized_at=form.get("seized_at"))
        job = pipeline.submit(ev["id"], actor(req)) if analyze else None
        out.append({"evidence_id": ev["id"], "sha256": ev["sha256"], "modality": ev["modality"], "job_id": job})
    return J(out, 201)


# ------------------------------------------------------------------ evidence
async def get_evidence(req: Request):
    e = db.q1("SELECT * FROM evidence WHERE id=?", (req.path_params["eid"],))
    if not e:
        return J({"error": "not found"}, 404)
    res = pipeline.results(e["id"])
    reports = db.q("SELECT id, version, pdf_sha256, signer, created_at FROM reports WHERE evidence_id=? "
                   "ORDER BY created_at DESC", (e["id"],))
    case = db.q1("SELECT * FROM cases WHERE id=?", (e["case_id"],))
    narration = None
    if res.get("verdict"):
        from ..report.narrator import narrate
        narration = narrate(e, res["verdict"]["rationale"], res["findings"], True)
    return J({"evidence": {**e, "storage_path": None}, "case": case, **res, "reports": reports, "narration": narration})


def _metadata_table(e: dict) -> dict:
    job = pipeline.latest_job(e["id"])
    f = db.q1("SELECT data FROM findings WHERE job_id=? AND code='META_TABLE'", (job["id"],)) if job else None
    if f and isinstance(f.get("data"), dict) and f["data"].get("standard"):
        return f["data"]
    from ..analyzers.metadata.standard import build
    return build(Path(e["storage_path"]), e["original_filename"])


def _csv(rows: list[list]) -> str:
    import csv
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


async def evidence_metadata(req: Request):
    e = db.q1("SELECT * FROM evidence WHERE id=?", (req.path_params["eid"],))
    if not e:
        return J({"error": "not found"}, 404)
    t = _metadata_table(e)
    if req.query_params.get("format") == "csv":
        rows = [["Field", "Value"]] + t["standard"] + [[f"{g}: {k}", v] for g, kv in t.get("additional", {}).items()
                                                      for k, v in kv.items()]
        return Response(_csv(rows), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="{e.get("exhibit_no") or e["id"]}_metadata.csv"'})
    return J(t)


async def case_metadata(req: Request):
    """One row per exhibit, columns = the standard metadata fields + verdict (for the whole case, as CSV)."""
    from ..analyzers.metadata.standard import FIELDS
    c = db.q1("SELECT * FROM cases WHERE id=?", (req.path_params["cid"],))
    if not c:
        return J({"error": "not found"}, 404)
    rows = [["Exhibit"] + FIELDS + ["SHA-256", "Verdict", "What was found"]]
    for e in db.q("SELECT * FROM evidence WHERE case_id=? ORDER BY ingested_at", (c["id"],)):
        t = dict(_metadata_table(e)["standard"])
        res = pipeline.results(e["id"])
        v = res.get("verdict") or {}
        rat = v.get("rationale") if isinstance(v.get("rationale"), dict) else {}
        rows.append([e.get("exhibit_no")] + [t.get(k, "") for k in FIELDS] +
                    [e["sha256"], v.get("category", "not examined"), "; ".join(rat.get("nature") or [])])
    name = (c.get("fir_no") or c["id"]).replace("/", "-")
    return Response(_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="case_{name}_metadata.csv"'})


async def analyze(req: Request):
    eid = req.path_params["eid"]
    return J({"job_id": pipeline.submit(eid, actor(req))}, 202)


async def job_status(req: Request):
    j = db.q1("SELECT * FROM jobs WHERE id=?", (req.path_params["jid"],))
    return J(j or {"error": "not found"}, 200 if j else 404)


async def verify_hash(req: Request):
    return J(pipeline.verify_hash(req.path_params["eid"], actor(req)))


async def custody(req: Request):
    return J({"events": ledger.events(req.path_params["eid"]), "chain": ledger.verify_chain()})


async def original(req: Request):
    e = db.q1("SELECT * FROM evidence WHERE id=?", (req.path_params["eid"],))
    if not e:
        return J({"error": "not found"}, 404)
    if req.query_params.get("download"):
        ledger.append(e["id"], actor(req), "EXPORTED", {"to": req.client.host if req.client else None})
    return FileResponse(e["storage_path"], filename=e["original_filename"],
                        content_disposition_type="attachment" if req.query_params.get("download") else "inline")


async def artifact(req: Request):
    p = (config.ARTIFACT_DIR / req.path_params["jid"] / req.path_params["name"]).resolve()
    if not str(p).startswith(str(config.ARTIFACT_DIR.resolve())) or not p.exists():
        return J({"error": "not found"}, 404)
    return FileResponse(p)


# ------------------------------------------------------------------ reports
async def make_report(req: Request):
    from ..report import pdf
    try:
        body = await req.json()
    except Exception:
        body = {}
    try:
        r = pdf.build(req.path_params["eid"], examiner=body.get("examiner") or actor(req), lang=body.get("lang", "bi"))
    except ValueError as e:
        return J({"error": str(e)}, 409)
    return J({k: v for k, v in r.items() if k != "pdf_path"}, 201)


async def report_pdf(req: Request):
    r = db.q1("SELECT * FROM reports WHERE id=?", (req.path_params["rid"],))
    if not r:
        return J({"error": "not found"}, 404)
    return FileResponse(r["pdf_path"], media_type="application/pdf", filename=f"{r['id']}.pdf",
                        content_disposition_type="inline")


async def report_verify(req: Request):
    from ..report import pdf
    data = None
    if req.method == "POST":
        form = await req.form()
        f = form.get("file")
        data = await f.read() if f else None
    return J(pdf.verify(req.path_params["rid"], data))


async def verify_page(req: Request):
    from ..report import pdf
    r = pdf.verify(req.path_params["rid"])
    ok = r.get("ok")
    rows = "".join(f"<tr><th>{k.replace('_', ' ')}</th><td>{v}</td></tr>" for k, v in r.items())
    html = f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Report verification</title><style>body{{font:15px system-ui;margin:0;background:#f5f6f8;color:#1b2430}}
main{{max-width:640px;margin:24px auto;padding:0 16px}}.b{{padding:18px;border-radius:12px;color:#fff;
background:{'#067647' if ok else '#b42318'};font-size:20px;font-weight:600}}table{{width:100%;margin-top:16px;
border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden}}th,td{{text-align:left;padding:8px 12px;
border-bottom:1px solid #e5e7eb;font-size:13px;word-break:break-all;vertical-align:top}}th{{width:38%;color:#5b6675;
font-weight:500;text-transform:capitalize}}</style><main><div class=b>{'✓ Authentic report — signature and PDF hash valid'
if ok else '✗ Verification failed'}</div><table>{rows}</table><p style="color:#5b6675;font-size:12px">
Verified against the laboratory's Ed25519 key {signing.fingerprint()}. To check a PDF you hold, POST it to
/api/reports/{req.path_params['rid']}/verify.</p></main>"""
    return HTMLResponse(html)


# ------------------------------------------------------------------ ledger / devices / misc
async def ledger_verify(req: Request):
    return J(ledger.verify_chain())


async def ledger_anchor(req: Request):
    return J(ledger.anchor())


async def devices(req: Request):
    if req.method == "GET":
        return J(db.q("SELECT id, name, kind, meta, created_at FROM devices ORDER BY created_at DESC"))
    from ..analyzers.image.prnu import register_device
    form = await req.form(max_files=200)
    tmp = config.WORK_DIR / db.new_id("devreg")
    tmp.mkdir(parents=True)
    paths = []
    for f in form.getlist("files"):
        p = tmp / Path(f.filename).name
        p.write_bytes(await f.read())
        paths.append(p)
    if len(paths) < 5:
        return J({"error": "upload at least 5 (ideally 20-50) flat reference images"}, 400)
    r = register_device(form.get("name") or "device", paths)
    ledger.append(None, actor(req), "DEVICE_REGISTERED", r)
    return J(r, 201)


def _voice_ready() -> bool:
    try:
        from ..analyzers.audio import spoof
        return spoof.hf_voice_available()
    except Exception:
        return False


def _capabilities() -> dict:
    """Which optional detectors can run right now (shown in the dashboard so 'Inconclusive' is never a mystery)."""
    import importlib.util as iu
    from ..analyzers.image import dl_plugins
    have_ml = iu.find_spec("torch") is not None and iu.find_spec("transformers") is not None
    hf_home = Path(os.environ.get("HF_HOME", str(config.MODELS_DIR / "hf")))
    cached = any(hf_home.glob(f"hub/models--{dl_plugins.AIGI_MODEL.replace('/', '--')}")) if dl_plugins.AIGI_MODEL else False
    probe = (config.MODELS_DIR / "aigi_probe.npz").exists()
    if have_ml and (cached or probe):
        ai = "ready"
    elif have_ml:
        ai = "downloads on first use"
    else:
        ai = "not installed"
    return {
        "ai_image": {"status": ai, "model": dl_plugins.AIGI_MODEL,
                     "install": "pip install -r requirements-ml.txt && python scripts/install_models.py"},
        "trufor": "ready" if dl_plugins.trufor_status()[0] else "not installed",
        "catnet": "ready" if os.environ.get("PRAMAAN_CATNET_CMD") else "not installed",
        "face_deepfake": "ready" if os.environ.get("PRAMAAN_DEEPFAKE_CMD") else "not installed",
        "synthetic_voice": "ready" if (config.MODELS_DIR / "lfcc_spoof.npz").exists() or os.environ.get("PRAMAAN_SSL_SPOOF_CMD")
        or _voice_ready() else "not installed",
        "content_credentials": "ready (offline C2PA reader)",
    }


async def health(req: Request):
    return J({"ok": True, "version": config.VERSION, "lab": config.LAB_NAME, "key": signing.fingerprint(),
              "grid_hz": config.GRID_HZ, "detectors": _capabilities()})


def _ui_root() -> Path:
    for d in (NEXT_OUT, WEB_DIST):
        if (d / "index.html").exists():
            return d
    return WEB


async def index(req: Request):
    return FileResponse(_ui_root() / "index.html")


async def spa(req: Request):
    """Next.js export has one HTML file per route; the prebuilt bundle is a single-page app."""
    root = _ui_root()
    page = req.path_params["page"].strip("/")
    for cand in (root / page / "index.html", root / f"{page}.html"):
        if cand.exists():
            return FileResponse(cand)
    return FileResponse(root / "index.html")


routes = [
    Route("/", index),
    Route("/verify/{rid}", verify_page),
    Route("/api/health", health),
    Route("/api/cases", list_cases, methods=["GET"]),
    Route("/api/cases", create_case, methods=["POST"]),
    Route("/api/cases/{cid}", get_case, methods=["GET"]),
    Route("/api/cases/{cid}", delete_case, methods=["DELETE"]),
    Route("/api/caselog", case_log),
    Route("/api/cases/{cid}/evidence", upload, methods=["POST"]),
    Route("/api/evidence/{eid}", get_evidence),
    Route("/api/evidence/{eid}/metadata", evidence_metadata),
    Route("/api/cases/{cid}/metadata.csv", case_metadata),
    Route("/api/evidence/{eid}/analyze", analyze, methods=["POST"]),
    Route("/api/evidence/{eid}/verify-hash", verify_hash, methods=["POST"]),
    Route("/api/evidence/{eid}/custody", custody),
    Route("/api/evidence/{eid}/original", original),
    Route("/api/evidence/{eid}/report", make_report, methods=["POST"]),
    Route("/api/jobs/{jid}", job_status),
    Route("/api/reports/{rid}/pdf", report_pdf),
    Route("/api/reports/{rid}/verify", report_verify, methods=["GET", "POST"]),
    Route("/api/ledger/verify", ledger_verify),
    Route("/api/ledger/anchor", ledger_anchor, methods=["POST"]),
    Route("/api/devices", devices, methods=["GET", "POST"]),
    Route("/artifacts/{jid}/{name}", artifact),
    Route("/api/samples", samples_list),
    Route("/api/samples/thumb", sample_thumb),
    Route("/api/samples/file", sample_file),
    Route("/api/samples/analyze", samples_analyze, methods=["POST"]),
    Route("/api/demo", demo),
    Route("/api/demo/showcase", showcase),
    Route("/api/ledger/recent", ledger_recent),
    Route("/api/stats", stats),
    Mount("/static", StaticFiles(directory=WEB), name="static"),
]
_ui = _ui_root()
if _ui != WEB:
    for sub in ("_next", "assets", "fonts", "hero"):
        if (_ui / sub).exists():
            routes.append(Mount(f"/{sub}", StaticFiles(directory=_ui / sub), name=sub))
    for f in _ui.glob("*.*"):
        if f.name != "index.html":
            routes.append(Route(f"/{f.name}", (lambda p: (lambda req: FileResponse(p)))(f)))
routes.append(Route("/{page:path}", spa))

config.ensure_dirs()
db.conn()
signing.public_pem()
app = Starlette(routes=routes, middleware=[
    Middleware(CORSMiddleware, allow_origins=[o for o in CORS.split(",") if o], allow_methods=["*"], allow_headers=["*"]),
    Middleware(KeyAuth)])
_b = caselog.backfill()
if _b:
    print(f"PRAMAAN: case log started; recorded {_b} existing case(s)")
_n = pipeline.recover_interrupted()
if _n:
    print(f"PRAMAAN: re-queued {_n} analysis job(s) interrupted by the last shutdown")
if os.environ.get("PRAMAAN_SEED_DEMO", "1") == "1":
    from .. import samples as _samples
    _samples.seed_in_background()
