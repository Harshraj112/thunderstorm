// Thin client for the PRAMAAN API. Same origin in production; NEXT_PUBLIC_API points at :8000 in `next dev`.
export const API = (process.env.NEXT_PUBLIC_API || "").replace(/\/$/, "");

export type Status = "fail" | "warn" | "pass" | "info" | "unavailable";

export interface Finding {
  id: string;
  code: string;
  module: string;
  family: string;
  title: string;
  status: Status;
  llr: number;
  reliability: number;
  explanation: string;
  innocent: string[];
  location: Record<string, any>;
  artifacts: string[];
}

export interface Contribution { family: string; llr: number; driver: string | null; reliability: number }

export interface Verdict {
  category: string;
  confidence: string;
  total_llr: number;
  coverage: number;
  abstained: boolean;
  abstain_reason: string | null;
  contributions: Contribution[];
  nature?: string[];
  gaps?: { family: string; why: string }[];
  timeline?: Record<string, any>;
}

export interface Evidence {
  id: string;
  case_id: string;
  exhibit_no: string;
  original_filename: string;
  modality: "image" | "video" | "audio" | "document" | "empty" | "unknown";
  size: number;
  sha256: string;
  sha1: string;
  md5: string;
  acquisition_source?: string;
  device_details?: string;
  ingested_at: number;
  ingested_by: string;
}

export interface Job { id: string; status: string; progress: number; stage: string }

export interface EvidenceDetail {
  evidence: Evidence;
  case: any;
  job: Job | null;
  findings: Finding[];
  verdict: { category: string; confidence: string; rationale: Verdict } | null;
  reports: { id: string; version: number; created_at: number }[];
  narration: { en: string; hi: string; en_llm?: string } | null;
}

export interface Sample {
  id: string;
  set: "real" | "synthetic";
  file: string;
  kind: "image" | "video" | "audio";
  tampered: boolean | null;
  title: string;
  story: string;
  source: string;
  expect?: string;
  real: boolean;
  evidence_id?: string;
  job_status?: string | null;
  verdict?: { category: string; confidence: string; total_llr: number } | null;
}

export interface Showcase {
  pending: boolean;
  evidence_id?: string;
  job_id?: string;
  layer?: string | null;
  boxes?: { x: number; y: number; w: number; h: number; label: string }[];
  width?: number;
  height?: number;
  verdict?: string;
  filename?: string;
  sha256?: string;
}

export interface LedgerEvent { seq: number; actor: string; action: string; ts: number; entry_hash: string; details?: any }
export interface ChainStatus { ok: boolean; events?: number; head?: string; broken_at?: number; reason?: string }

export function actor(): string {
  try {
    return localStorage.getItem("pramaan.actor") || "examiner";
  } catch {
    return "examiner";
  }
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const r = await fetch(API + path, { ...opts, headers: { "X-Actor": actor(), ...(opts.headers || {}) } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as any).error || `${r.status} ${r.statusText}`);
  return j as T;
}

export const postJSON = <T = any>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const url = (path: string) => API + path;

export type Tone = "strong" | "ind" | "none" | "inc";
export function tone(category?: string): Tone {
  if (!category) return "inc";
  if (category.startsWith("Strong")) return "strong";
  if (category.startsWith("Indications")) return "ind";
  if (category.startsWith("No ind")) return "none";
  return "inc";
}

export const CATEGORY_HI: Record<string, string> = {
  "Strong indications of manipulation": "छेड़छाड़ के प्रबल संकेत",
  "Indications of manipulation": "छेड़छाड़ के संकेत",
  "No indications of manipulation detected": "छेड़छाड़ का कोई संकेत नहीं",
  Inconclusive: "अनिर्णायक",
};

export const FAMILY_LABEL: Record<string, string> = {
  metadata: "Metadata",
  compression: "Compression history",
  pixel: "Pixel & noise",
  "synthetic-image": "AI-generated image",
  "synthetic-face": "Face deepfake",
  "synthetic-voice": "Synthetic voice",
  "file-integrity": "File type",
  "colour-filter": "Colour filter",
  framing: "Framing",
  derivative: "Copy of another exhibit",
  "temporal-container": "File timeline",
  "temporal-pixel": "Frame continuity",
  osd: "On-screen clock",
  enf: "Mains hum (ENF)",
  acoustic: "Acoustic environment",
  provenance: "Content Credentials",
  "cross-modal": "Lips vs speech",
};

export function fmtBytes(n: number): string {
  if (n > 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n > 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}

export function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

/** Seconds referenced by a finding (for timeline marks and seek buttons). */
export function findingTimes(f: Finding): [number, number][] {
  const L = f.location || {};
  const out: [number, number][] = [];
  const num = (v: any) => typeof v === "number" && Number.isFinite(v);
  if (num(L.time)) out.push([L.time, L.time]);
  (L.times || []).forEach((t: any) => num(t) && out.push([t, t]));
  if (num(L.start)) out.push([L.start, num(L.end) ? L.end : L.start]);
  (L.segments || []).forEach((s: any) => {
    const a = s.start ?? s.repeat_start ?? s.source_start;
    const b = s.end ?? s.repeat_end ?? s.source_end ?? a;
    if (num(a)) out.push([a, b]);
  });
  (L.gaps || []).forEach((g: any) => num(g.at) && out.push([g.at, g.at]));
  return out;
}
