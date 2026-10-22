"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  api, findingTimes, fmtBytes, fmtTime, postJSON, url,
  type ChainStatus, type EvidenceDetail, type Finding, type LedgerEvent,
} from "@/lib/api";
import { toast, usePoll } from "@/lib/hooks";
import Lamp, { type Box } from "./Lamp";
import Timeline from "./Timeline";
import { VerdictPanel } from "./Verdict";
import { Progress, StatusChip } from "./bits";

const STAGES: Record<string, string[]> = {
  image: ["filetype", "c2pa", "metadata", "compare", "jpeg", "noise", "copymove", "ela", "trufor", "catnet", "aigi", "prnu"],
  video: ["filetype", "c2pa", "container", "double_compression", "pixels", "grade", "osd", "lipsync", "face_deepfake", "audio_track", "enf", "spoof", "partial_spoof"],
  audio: ["filetype", "c2pa", "acoustic", "enf", "spoof", "partial_spoof"],
  document: ["filetype", "c2pa", "document"],
  empty: ["filetype"],
  unknown: ["filetype", "c2pa", "document"],
};
const STAGE_LABEL: Record<string, string> = {
  filetype: "File signature vs extension", c2pa: "Content Credentials (C2PA)", compare: "Compare with other exhibits",
  grade: "Colour filter & framing", document: "Document properties", metadata_table: "Metadata table",
  metadata: "Metadata consistency", jpeg: "Compression history", noise: "Sensor-noise map", copymove: "Cloned regions",
  ela: "Error levels", trufor: "TruFor localiser", catnet: "CAT-Net localiser", aigi: "AI-generated image", prnu: "Camera fingerprint",
  container: "File timestamps & keyframes", double_compression: "Re-encoding footprint", pixels: "Frame continuity",
  osd: "On-screen clock", lipsync: "Lips vs speech", face_deepfake: "Face deepfake", audio_track: "Sound-track environment",
  enf: "Mains hum (ENF)", spoof: "Synthetic voice", partial_spoof: "Synthetic segments", acoustic: "Acoustic environment",
};

export default function EvidenceView() {
  const id = useSearchParams()?.get("id") || "";
  const [d, setD] = useState<EvidenceDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const done = !!d?.verdict;
  usePoll(async () => {
    try {
      setD(await api<EvidenceDetail>(`/api/evidence/${id}`));
    } catch (e: any) {
      setErr(e.message);
    }
  }, done ? 60000 : 1500, !!id);

  if (err) return <div className="empty">This exhibit could not be opened: {err}. <Link href="/">Back to the demo kit</Link></div>;
  if (!d) return <div className="empty scanning">Opening exhibit…</div>;
  return done ? <Workbench d={d} reload={async () => setD(await api(`/api/evidence/${id}`))} /> : <Examining d={d} />;
}

function Head({ d, children }: { d: EvidenceDetail; children?: React.ReactNode }) {
  const e = d.evidence;
  return (
    <>
      <div className="crumbs">
        <Link href="/cases/">Cases</Link> / <Link href={`/case/?id=${e.case_id}`}>{d.case?.fir_no}</Link> / {e.exhibit_no}
      </div>
      <div className="ev-head">
        <div>
          <h1>{e.original_filename}</h1>
          <p className="muted small">
            Exhibit {e.exhibit_no}, {e.modality}, {fmtBytes(e.size)}. Received {fmtTime(e.ingested_at)} by {e.ingested_by}.
          </p>
        </div>
        <div className="row">{children}</div>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ live analysis */
function Examining({ d }: { d: EvidenceDetail }) {
  const e = d.evidence;
  const stages = STAGES[e.modality] || [];
  const cur = (d.job?.stage || "").split(":")[0];
  const idx = stages.indexOf(cur);
  return (
    <div className="stack">
      <Head d={d} />
      <div className="split">
        <div className="panel flush examining">
          {e.modality === "image" ? (
            <img src={url(`/api/evidence/${e.id}/original`)} alt="Exhibit under examination" />
          ) : e.modality === "video" ? (
            <video src={url(`/api/evidence/${e.id}/original`)} muted autoPlay loop playsInline />
          ) : (
            <div className="audio-wait"><span /><span /><span /><span /><span /></div>
          )}
          <div className="sweep" aria-hidden />
        </div>
        <div className="panel">
          <h2>Examining a working copy</h2>
          <p className="small muted" style={{ margin: "6px 0 14px" }}>
            The original was hashed on arrival and is never opened for writing. Results appear here as soon as the
            tests finish.
          </p>
          <ol className="stages">
            {stages.map((s, i) => (
              <li key={s} className={i < idx ? "done" : i === idx ? "now" : ""}>
                <span className="tick" aria-hidden>{i < idx ? "✓" : ""}</span>
                {STAGE_LABEL[s] || s}
                {i === idx && d.job?.stage?.includes(":") && <span className="faint small"> {d.job.stage.split(":")[1]}</span>}
              </li>
            ))}
          </ol>
          <Progress value={d.job?.progress || 0} />
        </div>
      </div>
      <Integrity d={d} />
    </div>
  );
}

/* ------------------------------------------------------------------ results */
function Workbench({ d, reload }: { d: EvidenceDetail; reload: () => Promise<void> }) {
  const e = d.evidence;
  const v = d.verdict!.rationale;
  const [tab, setTab] = useState<"findings" | "metadata" | "custody">("findings");
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const [media, setMedia] = useState<HTMLMediaElement | null>(null);
  const [making, setMaking] = useState(false);
  const seek = (t: number) => {
    const m = mediaRef.current;
    if (!m) return;
    m.currentTime = t;
    m.play?.().catch(() => {});
    m.scrollIntoView({ block: "center", behavior: "smooth" });
  };
  const reanalyse = async () => {
    await api(`/api/evidence/${e.id}/analyze`, { method: "POST" });
    toast("Examination restarted");
    await reload();
  };
  const report = async () => {
    setMaking(true);
    try {
      const r = await postJSON<{ report_id: string }>(`/api/evidence/${e.id}/report`, { examiner: undefined, lang: "bi" });
      toast("Report signed. Opening the PDF.");
      window.open(url(`/api/reports/${r.report_id}/pdf`), "_blank");
      await reload();
    } catch (err: any) {
      toast(err.message);
    } finally {
      setMaking(false);
    }
  };
  return (
    <div className="stack">
      <Head d={d}>
        <button className="btn ghost" onClick={reanalyse}>Examine again</button>
        <button className="btn primary" onClick={report} disabled={making}>{making ? "Signing…" : "Sign report (English + हिन्दी)"}</button>
      </Head>
      <div className="split">
        <VerdictPanel v={v} narration={d.narration} />
        <Integrity d={d} />
      </div>
      {e.modality !== "image" && e.modality !== "video" && e.modality !== "audio" ? (
        <FileBench d={d} />
      ) : (
      <section className="panel bench">
        {e.modality === "image" ? (
          <ImageBench d={d} />
        ) : (
          <>
            <div className="spread">
              <h2>{e.modality === "video" ? "Footage and its timelines" : "Recording and its signals"}</h2>
              <span className="small faint">Shaded spans mark problems. Click any track to jump there.</span>
            </div>
            {e.modality === "video" ? (
              <video className="player" controls preload="metadata" src={url(`/api/evidence/${e.id}/original`)}
                ref={(el) => { mediaRef.current = el; if (el !== media) setMedia(el); }} />
            ) : (
              <audio className="player" controls preload="metadata" src={url(`/api/evidence/${e.id}/original`)}
                ref={(el) => { mediaRef.current = el; if (el !== media) setMedia(el); }} />
            )}
            <Timeline tl={v.timeline} findings={d.findings} media={media} onSeek={seek} />
          </>
        )}
      </section>
      )}
      <section className="panel">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "findings"} onClick={() => setTab("findings")}>
            What the tests found
          </button>
          <button role="tab" aria-selected={tab === "metadata"} onClick={() => setTab("metadata")}>Metadata</button>
          <button role="tab" aria-selected={tab === "custody"} onClick={() => setTab("custody")}>Chain of custody</button>
        </div>
        {tab === "findings" ? <Findings d={d} onSeek={seek} canSeek={e.modality === "video" || e.modality === "audio"} />
          : tab === "metadata" ? <MetadataTab d={d} /> : <Custody id={e.id} />}
      </section>
    </div>
  );
}

function ImageBench({ d }: { d: EvidenceDetail }) {
  const e = d.evidence, job = d.job!;
  const layers = useMemo(() => {
    const out: { file: string; label: string }[] = [];
    d.findings.forEach((f) => (f.artifacts || []).forEach((a) => {
      if (!out.some((o) => o.file === a)) out.push({ file: a, label: LAYER_LABEL[a] || f.title });
    }));
    return out;
  }, [d]);
  const first = layers.find((l) => /ghost|noise|trufor|catnet/.test(l.file)) || layers[0];
  const [layer, setLayer] = useState<string | null>(first?.file || null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const boxes: Box[] = d.findings
    .filter((f) => (f.status === "fail" || f.status === "warn") && f.location?.bbox)
    .map((f) => ({ ...f.location.bbox, label: f.title.split(":")[0] }));
  const src = url(`/api/evidence/${e.id}/original`);
  return (
    <>
      <div className="spread">
        <h2>The photo under the lamp</h2>
        <span className="small faint">Drag across the image to compare it with the selected map.</span>
      </div>
      <div className="layer-bar" role="group" aria-label="Forensic maps">
        <button aria-pressed={layer === null} onClick={() => setLayer(null)}>Photo only</button>
        {layers.map((l) => (
          <button key={l.file} aria-pressed={layer === l.file} onClick={() => setLayer(l.file)}>{l.label}</button>
        ))}
      </div>
      {/* natural size is needed so flagged boxes land on the right pixels */}
      <img src={src} alt="" style={{ display: "none" }} onLoad={(ev) => setSize({ w: ev.currentTarget.naturalWidth, h: ev.currentTarget.naturalHeight })} />
      {size && (
        <Lamp key={layer || "none"} className="bench-img" before={src} after={layer ? url(`/artifacts/${job.id}/${layer}`) : null}
          width={size.w} height={size.h} boxes={boxes} rightLabel={layers.find((l) => l.file === layer)?.label || ""} sweep />
      )}
    </>
  );
}

const LAYER_LABEL: Record<string, string> = {
  "jpeg_ghost.png": "Compression ghosts", "noise_map.png": "Sensor-noise map", "ela.png": "Error levels",
  "copymove.png": "Cloned regions", "thumbnail_diff.png": "Thumbnail vs photo", "trufor_map.png": "TruFor map",
  "trufor_conf.png": "TruFor confidence", "catnet_map.png": "CAT-Net map",
};

/* ------------------------------------------------------------------ findings */
function Findings({ d, onSeek, canSeek }: { d: EvidenceDetail; onSeek: (t: number) => void; canSeek: boolean }) {
  const [zoom, setZoom] = useState<string | null>(null);
  const groups: [string, Finding[], boolean][] = [
    ["Problems found", d.findings.filter((f) => f.status === "fail" || f.status === "warn"), true],
    ["Consistent with an original", d.findings.filter((f) => f.status === "pass"), false],
    ["Background and tests not installed", d.findings.filter((f) => (f.status === "info" || f.status === "unavailable") && f.code !== "META_TABLE"), false],
  ];
  return (
    <div className="findings">
      {groups.map(([title, list, open]) => list.length > 0 && (
        <div key={title}>
          <h3 className="group-title">{title} <span className="faint">({list.length})</span></h3>
          {list.map((f) => (
            <details key={f.id} className={`finding ${f.status}`} open={open}>
              <summary>
                <StatusChip s={f.status} />
                <span className="t">{f.title}</span>
                {f.status !== "info" && f.status !== "unavailable" && (
                  <span className="meta">weight {f.llr > 0 ? "+" : ""}{f.llr.toFixed(1)}, trust {Math.round(f.reliability * 100)}%</span>
                )}
              </summary>
              <div className="bd">
                <p>{f.explanation}</p>
                {canSeek && findingTimes(f).length > 0 && (
                  <div className="row" style={{ marginTop: 10 }}>
                    {findingTimes(f).slice(0, 8).map(([a], i) => (
                      <button key={i} className="seek" onClick={() => onSeek(a)}>▶ {a.toFixed(1)} s</button>
                    ))}
                  </div>
                )}
                {f.innocent?.length > 0 && (
                  <p className="inn">Innocent explanations to rule out: {f.innocent.join("; ")}.</p>
                )}
                {f.artifacts?.length > 0 && (
                  <div className="arts">
                    {f.artifacts.map((a) => (
                      <img key={a} src={url(`/artifacts/${d.job!.id}/${a}`)} alt={a} loading="lazy"
                        onClick={() => setZoom(url(`/artifacts/${d.job!.id}/${a}`))} />
                    ))}
                  </div>
                )}
              </div>
            </details>
          ))}
        </div>
      ))}
      {zoom && <div className="modal" onClick={() => setZoom(null)}><img src={zoom} alt="Enlarged forensic map" /></div>}
    </div>
  );
}

/* ------------------------------------------------------------------ metadata (standard PS-05 format) */
interface MetaTable { standard: [string, string][]; additional: Record<string, Record<string, string>> }

function MetadataTab({ d }: { d: EvidenceDetail }) {
  const [t, setT] = useState<MetaTable | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const f = d.findings.find((x) => x.code === "META_TABLE") as any;
    if (f?.data?.standard) setT(f.data);
    else api<MetaTable>(`/api/evidence/${d.evidence.id}/metadata`).then(setT).catch((e) => setErr(e.message));
  }, [d.findings, d.evidence.id]);
  if (err) return <div className="empty">Metadata could not be read: {err}</div>;
  if (!t) return <div className="empty scanning">Reading metadata…</div>;
  const asText = t.standard.map(([k, v]) => `${k.padEnd(20)} ${v}`).join("\n");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(asText);
      toast("Metadata copied");
    } catch {
      toast("Copy is blocked here; use Download CSV");
    }
  };
  const json = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(t, null, 2)], { type: "application/json" }));
    a.download = `${d.evidence.exhibit_no || d.evidence.id}_metadata.json`;
    a.click();
  };
  return (
    <div className="meta-tab">
      <div className="spread">
        <p className="small muted">Output format as specified for PS-05. "—" means the field is not present in the file.</p>
        <div className="row">
          <button className="btn ghost" onClick={copy}>Copy</button>
          <a className="btn ghost" href={url(`/api/evidence/${d.evidence.id}/metadata?format=csv`)}>Download CSV</a>
          <button className="btn ghost" onClick={json}>JSON</button>
        </div>
      </div>
      <table className="kv">
        <tbody>
          {t.standard.map(([k, v]) => (
            <tr key={k} className={v === "—" ? "missing" : ""}><th>{k}</th><td>{v}</td></tr>
          ))}
        </tbody>
      </table>
      {Object.entries(t.additional || {}).map(([g, kv]) => (
        <details key={g} className="meta-group" open={g.startsWith("Content Credentials")}>
          <summary>{g} <span className="faint">({Object.keys(kv).length})</span></summary>
          <table className="kv small">
            <tbody>{Object.entries(kv).map(([k, v]) => <tr key={k}><th>{k}</th><td>{String(v)}</td></tr>)}</tbody>
          </table>
        </details>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ documents, empty and unrecognised files */
function FileBench({ d }: { d: EvidenceDetail }) {
  const ft = d.findings.find((f) => f.code.startsWith("FILE_")) as any;
  const data = ft?.data || {};
  return (
    <section className="panel bench">
      <h2>What this file really is</h2>
      <table className="kv">
        <tbody>
          <tr><th>Name</th><td>{d.evidence.original_filename}</td></tr>
          <tr><th>Extension says</th><td>{data.claimed_type || "—"}</td></tr>
          <tr className={ft?.status === "fail" ? "bad" : ""}><th>Content is</th><td>{data.real_type || d.evidence.modality}</td></tr>
          <tr><th>Size</th><td>{fmtBytes(d.evidence.size)}</td></tr>
        </tbody>
      </table>
      <p className="small muted" style={{ marginTop: 10 }}>
        Photos, video and audio are examined with the full forensic suite. Other content (documents, archives, empty files)
        gets the file-signature, Content Credentials and document-property checks.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ integrity + reports */
function Integrity({ d }: { d: EvidenceDetail }) {
  const e = d.evidence;
  const [check, setCheck] = useState<null | "busy" | boolean>(null);
  const verify = async () => {
    setCheck("busy");
    const r = await api<{ ok: boolean }>(`/api/evidence/${e.id}/verify-hash`, { method: "POST" });
    setTimeout(() => setCheck(r.ok), 600);
  };
  return (
    <section className="panel integrity" aria-label="Integrity">
      <div className="spread">
        <h2>Integrity</h2>
        <span className={`chip ${check === false ? "fail" : "pass"}`}>
          {check === false ? "Hash mismatch" : check === true ? "Re-checked just now" : "Matches intake hash"}
        </span>
      </div>
      <dl className="hashes">
        <dt>SHA-256</dt><dd>{e.sha256}</dd>
        <dt>SHA-1</dt><dd>{e.sha1}</dd>
        <dt>MD5</dt><dd>{e.md5}</dd>
      </dl>
      <p className="small muted">{e.acquisition_source || "Acquisition not recorded"}{e.device_details ? `. ${e.device_details}` : ""}</p>
      <div className="row" style={{ marginTop: 12 }}>
        <button className={`btn small ${check === "busy" ? "scanning" : ""}`} onClick={verify} disabled={check === "busy"}>
          {check === "busy" ? "Re-hashing…" : "Re-check the hash now"}
        </button>
        <a className="btn small ghost" href={url(`/api/evidence/${e.id}/original?download=1`)}>Export a copy</a>
      </div>
      {d.reports?.length > 0 && (
        <div className="reports">
          <h3 className="group-title">Signed reports</h3>
          {d.reports.map((r) => (
            <div key={r.id} className="spread small">
              <a href={url(`/api/reports/${r.id}/pdf`)} target="_blank" rel="noreferrer">Version {r.version}, {fmtTime(r.created_at)}</a>
              <a href={url(`/verify/${r.id}`)} target="_blank" rel="noreferrer" className="faint">Check signature</a>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ custody */
function Custody({ id }: { id: string }) {
  const [data, setData] = useState<{ events: LedgerEvent[]; chain: ChainStatus } | null>(null);
  const [run, setRun] = useState(0);
  usePoll(async () => setData(await api(`/api/evidence/${id}/custody`)), 20000);
  if (!data) return <div className="empty scanning">Reading the ledger…</div>;
  return (
    <div>
      <div className="spread" style={{ marginBottom: 10 }}>
        <p className="small muted" style={{ maxWidth: "60ch" }}>
          Each entry stores the hash of the entry before it. Changing any past entry, even directly in the database,
          breaks every link after it.
        </p>
        <button className="btn small" onClick={async () => { setRun((r) => r + 1); setData(await api(`/api/evidence/${id}/custody`)); }}>
          Verify every link
        </button>
      </div>
      <div className={`custody ${run ? "sealing" : ""}`} key={run}>
        {data.events.map((ev, i) => (
          <div className="ev" key={ev.seq} style={{ ["--i" as any]: i }}>
            <span className="node">{ev.seq}</span>
            <div>
              <b>{ev.action.toLowerCase().replace(/_/g, " ")}</b> <span className="muted">by {ev.actor}</span>
              <div className="small faint">{fmtTime(ev.ts)}</div>
            </div>
            <span className="mono faint" title={ev.entry_hash}>{ev.entry_hash.slice(0, 16)}…</span>
          </div>
        ))}
      </div>
      {run > 0 && (
        <p className={`chip ${data.chain.ok ? "pass" : "fail"} seal-result`} style={{ ["--n" as any]: data.events.length }}>
          {data.chain.ok ? `Verified: all ${data.chain.events} ledger entries link correctly` : `Broken at entry ${data.chain.broken_at}`}
        </p>
      )}
    </div>
  );
}
