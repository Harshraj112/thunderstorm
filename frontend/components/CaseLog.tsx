"use client";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtBytes, fmtTime, type ChainStatus } from "@/lib/api";
import { toast, usePoll } from "@/lib/hooks";

export interface CaseLogEntry {
  seq: number;
  ts: number;
  action: "CASE_CREATED" | "EVIDENCE_ADDED" | "CASE_DELETED" | string;
  case_id: string;
  case_label: string | null;
  actor: string;
  details: any;
  entry_hash: string;
}

/* ------------------------------------------------------------------ delete dialog */
export function DeleteCaseDialog({
  caseId, label, exhibits, onClose, onDeleted,
}: { caseId: string; label: string; exhibits: number; onClose: () => void; onDeleted: () => void }) {
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    box.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const ok = reason.trim().length >= 3 && typed.trim() === label.trim();
  const go = async () => {
    setBusy(true);
    try {
      await api(`/api/cases/${caseId}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason.trim() }),
      });
      toast(`Case ${label} deleted. Its record stays in the case log.`);
      onDeleted();
    } catch (e: any) {
      toast(e.message);
      setBusy(false);
    }
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="dlg" role="dialog" aria-modal="true" aria-labelledby="del-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="del-title">Delete case {label}?</h2>
        <ul>
          <li>{exhibits} exhibit{exhibits === 1 ? "" : "s"}, their analysis results, heatmaps and signed reports will be removed.</li>
          <li>The case log keeps a permanent record: when the case was opened, every file it held with its SHA-256, the verdicts, and who deleted it, when and why.</li>
          <li>The custody ledger is not touched; each exhibit gets a final &ldquo;deleted&rdquo; entry.</li>
        </ul>
        <label><span>Reason for deletion (written to the log)</span>
          <textarea ref={box} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Duplicate of FIR 0457/2026, opened by mistake" />
        </label>
        <label><span>Type <b className="mono">{label}</b> to confirm</span>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
        </label>
        <div className="row">
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn danger solid" disabled={!ok || busy} onClick={go}>{busy ? "Deleting…" : "Delete permanently"}</button>
        </div>
      </div>
    </div>
  );
}

export function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6" />
    </svg>
  );
}

/* ------------------------------------------------------------------ log page */
const ACTION = {
  CASE_CREATED: { ico: "+", text: "Case opened" },
  EVIDENCE_ADDED: { ico: "⇣", text: "Exhibit received" },
  CASE_DELETED: { ico: "×", text: "Case deleted" },
} as Record<string, { ico: string; text: string }>;

export function CaseLogView() {
  const [log, setLog] = useState<{ entries: CaseLogEntry[]; chain: ChainStatus & { entries?: number } } | null>(null);
  const [only, setOnly] = useState<string>("all");
  const [q, setQ] = useState("");
  usePoll(async () => setLog(await api("/api/caselog")), 15000);
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (log?.entries || []).filter((e) =>
      (only === "all" || e.action === only) &&
      (!s || JSON.stringify(e).toLowerCase().includes(s)));
  }, [log, only, q]);
  const gone = useMemo(() => new Set((log?.entries || []).filter((e) => e.action === "CASE_DELETED").map((e) => e.case_id)), [log]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    (log?.entries || []).forEach((e) => (c[e.action] = (c[e.action] || 0) + 1));
    return c;
  }, [log]);
  const download = () => {
    const blob = new Blob([JSON.stringify(log?.entries || [], null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `pramaan-case-log-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="stack">
      <div className="spread">
        <div>
          <h1 className="page-title">Case log</h1>
          <p className="muted">Every case opened, every file received, and every case deleted, with what it held. Entries can&rsquo;t be edited or removed.</p>
        </div>
        {log && (
          <span className={`pill ${log.chain.ok ? "ok" : "bad"}`} title="Each entry is hash-chained to the one before it">
            <span className="dot" />
            {log.chain.ok ? `Log intact · ${log.chain.entries} entries` : `Log broken at entry ${log.chain.broken_at}`}
          </span>
        )}
      </div>
      <div className="log-filter">
        <div className="seg" role="group" aria-label="Filter by event">
          {[["all", "All"], ["CASE_CREATED", "Opened"], ["EVIDENCE_ADDED", "Exhibits"], ["CASE_DELETED", "Deleted"]].map(([k, t]) => (
            <button key={k} aria-pressed={only === k} onClick={() => setOnly(k)}>
              {t}{k !== "all" && counts[k] ? ` · ${counts[k]}` : ""}
            </button>
          ))}
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search FIR, file name, hash, examiner…" style={{ minWidth: 240 }} />
        <button className="btn ghost" onClick={download} disabled={!log}>Download log (JSON)</button>
      </div>
      {!log ? <div className="empty scanning">Reading the case log…</div> : rows.length === 0 ? (
        <div className="empty">Nothing in the log matches.</div>
      ) : (
        <div className="log-list">
          {rows.slice(0, 300).map((e, i) => <LogItem key={e.seq} e={e} i={Math.min(i, 12)} gone={gone.has(e.case_id)} />)}
        </div>
      )}
    </div>
  );
}

function LogItem({ e, i, gone }: { e: CaseLogEntry; i: number; gone: boolean }) {
  const a = ACTION[e.action] || { ico: "•", text: e.action };
  const d = e.details || {};
  const c = d.case || {};
  return (
    <div className={`log-item ${e.action}`} style={{ ["--i" as any]: i }}>
      <div className="ico" aria-hidden>{a.ico}</div>
      <div>
        <div className="head">
          <b>{a.text}</b>
          <span>{e.case_label || e.case_id}</span>
          <span className="small muted">{fmtTime(e.ts)} · by {e.actor}</span>
          <span className="mono faint" title={e.entry_hash}>#{e.seq} {e.entry_hash.slice(0, 10)}…</span>
        </div>
        {e.action === "CASE_CREATED" && (
          <div className="small muted">
            {[c.police_station, c.io_name && `IO ${c.io_name}`, c.title].filter(Boolean).join(" · ") || "No details recorded"}
            {d.backfilled && " · recorded when the case log was switched on"}
            {gone ? <span className="chip fail" style={{ marginLeft: 8 }}>deleted later</span> : <OpenLink id={e.case_id} />}
          </div>
        )}
        {e.action === "EVIDENCE_ADDED" && (
          <div className="small muted">
            {d.exhibit_no} · {d.filename} · {d.modality} · {fmtBytes(d.size || 0)} · <span className="mono" title={d.sha256}>SHA-256 {String(d.sha256 || "").slice(0, 16)}…</span>
          </div>
        )}
        {e.action === "CASE_DELETED" && (
          <>
            <div className="small">Reason: <i>{d.reason}</i></div>
            <div className="small muted">Opened {c.created_at ? fmtTime(c.created_at) : "?"} by {c.created_by || "?"} · held {d.exhibits?.length || 0} exhibit(s)</div>
          </>
        )}
        {(d.exhibits?.length || d.exhibits_at_backfill?.length) ? (
          <details>
            <summary>What it {e.action === "CASE_DELETED" ? "held when deleted" : "held"} ({(d.exhibits || d.exhibits_at_backfill).length} files)</summary>
            <ExhibitTable rows={d.exhibits || d.exhibits_at_backfill} />
          </details>
        ) : null}
      </div>
    </div>
  );
}

function OpenLink({ id }: { id: string }) {
  return <> · <Link className="linkish" href={`/case/?id=${id}`}>open</Link></>;
}

function ExhibitTable({ rows }: { rows: any[] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table>
        <thead><tr><th>Exhibit</th><th>File</th><th>SHA-256</th><th>Verdict</th><th>Reports</th></tr></thead>
        <tbody>
          {rows.map((x) => (
            <tr key={x.evidence_id}>
              <td>{x.exhibit_no}</td>
              <td>{x.filename}<div className="faint">{x.modality} · {fmtBytes(x.size || 0)}</div></td>
              <td className="mono" style={{ wordBreak: "break-all" }}>{x.sha256}</td>
              <td>{x.verdict?.category || <span className="faint">not examined</span>}</td>
              <td>{x.reports?.length || 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
