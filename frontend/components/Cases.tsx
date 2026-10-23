"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useRef, useState } from "react";
import { api, fmtBytes, fmtTime, postJSON, url, type Evidence, type Job } from "@/lib/api";
import { toast, usePoll } from "@/lib/hooks";
import { Progress, VerdictChip } from "./bits";
import { DeleteCaseDialog, TrashIcon } from "./CaseLog";

interface CaseRow { id: string; fir_no: string; police_station?: string; io_name?: string; title?: string; created_at: number; n_evidence: number }

export function CasesView() {
  const router = useRouter();
  const [cases, setCases] = useState<CaseRow[] | null>(null);
  const [del, setDel] = useState<CaseRow | null>(null);
  const load = async () => setCases(await api<CaseRow[]>("/api/cases"));
  usePoll(load, 10000);
  const create = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const c = await postJSON<CaseRow>("/api/cases", Object.fromEntries(new FormData(e.currentTarget)));
    toast(`Case ${c.fir_no} opened`);
    router.push(`/case/?id=${c.id}`);
  };
  return (
    <div className="stack">
      <div>
        <h1 className="page-title">Cases</h1>
        <p className="muted">Each case holds its exhibits and their chain of custody.</p>
      </div>
      <div className="split">
        <div className="panel flush">
          {!cases ? <div className="empty scanning">Loading cases…</div> : cases.length === 0 ? (
            <div className="empty">No cases yet. Open one on the right, or try the test exhibits on the demo kit page.</div>
          ) : (
            <table className="list">
              <thead><tr><th>FIR / description</th><th>Police station</th><th>Exhibits</th><th>Opened</th><th><span className="sr-only">Delete</span></th></tr></thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.id} className="click" onClick={() => router.push(`/case/?id=${c.id}`)}>
                    <td><Link href={`/case/?id=${c.id}`}><b>{c.fir_no || "Untitled"}</b></Link><div className="small faint">{c.title}</div></td>
                    <td>{c.police_station}</td>
                    <td>{c.n_evidence}</td>
                    <td className="small muted">{fmtTime(c.created_at)}</td>
                    <td>
                      <button className="icon-btn" title={`Delete case ${c.fir_no || ""}`} aria-label={`Delete case ${c.fir_no || ""}`}
                        onClick={(e) => { e.stopPropagation(); setDel(c); }}><TrashIcon /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <form className="panel form" onSubmit={create}>
          <h2>Open a case</h2>
          <label>FIR number<input name="fir_no" placeholder="0457/2026" required /></label>
          <label>Police station<input name="police_station" placeholder="Connaught Place" /></label>
          <label>Investigating officer<input name="io_name" placeholder="Insp. A. Khan" /></label>
          <label>Short description<input name="title" placeholder="Shop CCTV and voice note" /></label>
          <button className="btn primary">Open case</button>
          <p className="small faint">Opening, receiving files and deleting are all recorded in the <Link className="linkish" href="/log/">case log</Link>.</p>
        </form>
      </div>
      {del && (
        <DeleteCaseDialog caseId={del.id} label={del.fir_no || "Untitled"} exhibits={del.n_evidence}
          onClose={() => setDel(null)} onDeleted={() => { setDel(null); load(); }} />
      )}
    </div>
  );
}

interface CaseEvidence extends Evidence { job: Job | null; verdict: { category: string; confidence: string; total_llr: number } | null }
interface CaseDetail extends CaseRow { evidence: CaseEvidence[] }

export function CaseView() {
  const id = useSearchParams()?.get("id") || "";
  const router = useRouter();
  const [c, setC] = useState<CaseDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [sending, setSending] = useState(0);
  const [del, setDel] = useState(false);
  const acq = useRef<HTMLInputElement>(null);
  const dev = useRef<HTMLInputElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const running = !!c?.evidence.some((e) => e.job && !String(e.job.status).startsWith("done"));
  usePoll(async () => {
    try {
      setC(await api<CaseDetail>(`/api/cases/${id}`));
    } catch (e: any) {
      setErr(e.message);
    }
  }, running || sending ? 2000 : 10000, !!id);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    fd.append("acquisition_source", acq.current?.value || "");
    fd.append("device_details", dev.current?.value || "");
    setSending(files.length);
    try {
      await api(`/api/cases/${id}/evidence`, { method: "POST", body: fd });
      toast(`${files.length} file${files.length > 1 ? "s" : ""} received and hashed. Analysis started.`);
      setC(await api<CaseDetail>(`/api/cases/${id}`));
    } catch (e: any) {
      toast(e.message);
    } finally {
      setSending(0);
    }
  };

  if (err) return <div className="empty">This case could not be opened: {err}. <Link href="/cases/">Back to cases</Link></div>;
  if (!c) return <div className="empty scanning">Opening case…</div>;
  const done = c.evidence.filter((e) => e.verdict);
  const flagged = done.filter((e) => /ndications of manipulation/.test(e.verdict!.category) && !e.verdict!.category.startsWith("No"));
  return (
    <div className="stack">
      <div className="crumbs"><Link href="/cases/">Cases</Link> / {c.fir_no}</div>
      <div className="spread">
        <div>
          <h1 className="page-title">{c.fir_no}</h1>
          <p className="muted">{[c.police_station, c.io_name && `IO ${c.io_name}`, c.title].filter(Boolean).join(", ")}</p>
        </div>
        <div className="case-tally">
          <span><b>{c.evidence.length}</b> exhibits</span>
          <span><b>{done.length}</b> examined</span>
          <span className="tally-flag"><b>{flagged.length}</b> flagged</span>
          <a className="btn ghost" href={url(`/api/cases/${c.id}/metadata.csv`)} title="Standard metadata of every exhibit, one row each">Metadata CSV</a>
          <button className="btn danger" onClick={() => setDel(true)} disabled={running}
            title={running ? "Wait for the running analyses to finish" : "Delete this case"}><TrashIcon /> Delete case</button>
        </div>
      </div>
      <div className="split">
        <div className="panel flush">
          {c.evidence.length === 0 ? (
            <div className="empty">No exhibits yet. Drop files on the right to receive them into this case.</div>
          ) : (
            <table className="list">
              <thead><tr><th>Exhibit</th><th>Type</th><th>SHA-256</th><th>Result</th></tr></thead>
              <tbody>
                {c.evidence.map((e) => {
                  const live = e.job && !String(e.job.status).startsWith("done");
                  return (
                    <tr key={e.id} className="click" onClick={() => router.push(`/evidence/?id=${e.id}`)}>
                      <td><b>{e.exhibit_no}</b><div className="small faint ellipsis">{e.original_filename}</div></td>
                      <td className="small">{e.modality}<div className="faint">{fmtBytes(e.size)}</div></td>
                      <td className="mono faint" title={e.sha256}>{e.sha256.slice(0, 12)}…</td>
                      <td className={live ? "scanning" : ""}>
                        {live ? (<><span className="small muted">{e.job!.stage || "queued"}</span><Progress value={e.job!.progress || 0} /></>)
                          : e.verdict ? <VerdictChip category={e.verdict.category} /> : <span className="small faint">Not examined</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <div className="panel form">
          <h2>Receive evidence</h2>
          <div
            className={`drop ${over ? "over" : ""} ${sending ? "scanning" : ""}`}
            onClick={() => file.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && file.current?.click()}
          >
            {sending ? `Hashing ${sending} file${sending > 1 ? "s" : ""}…` : "Drop photos, CCTV exports or audio here, or click to choose"}
            <input ref={file} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
          </div>
          <label>How it was acquired<input ref={acq} placeholder="DVR export on sealed USB, SI Sharma" /></label>
          <label>Source device<input ref={dev} placeholder="Hikvision DS-7608, camera 3" /></label>
          <p className="small faint">Files are hashed on arrival, stored write-once and examined as copies.</p>
        </div>
      </div>
      {del && (
        <DeleteCaseDialog caseId={c.id} label={c.fir_no || "Untitled"} exhibits={c.evidence.length}
          onClose={() => setDel(false)} onDeleted={() => router.push("/log/")} />
      )}
    </div>
  );
}
