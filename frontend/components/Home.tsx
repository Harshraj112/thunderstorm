"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import Lamp from "./Lamp";
import { api, postJSON, url, type ChainStatus, type LedgerEvent, type Sample } from "@/lib/api";
import { toast, usePoll } from "@/lib/hooks";
import { VerdictChip } from "./bits";

export default function Home() {
  return (
    <>
      <Hero />
      <TwoQuestions />
      <DemoKit />
    </>
  );
}

/* ------------------------------------------------------------------ hero */
function Hero() {
  const [stats, setStats] = useState<any>(null);
  usePoll(async () => setStats(await api("/api/stats")), 10000);
  return (
    <section className="hero">
      <div>
        <img className="hero-logo" src="/logo-192.png" alt="" width={96} height={96} />
        <h1>See what was changed. Prove what wasn&rsquo;t.</h1>
        <p className="lede">
          PRAMAAN examines photos, CCTV and audio for splices, deleted footage and synthetic content, keeps a
          tamper-evident custody record from the moment a file arrives, and signs a report ready for a
          Section&nbsp;63 certificate. Everything runs on this machine.
        </p>
        <div className="cta">
          <a className="btn primary" href="#kit">Try a test exhibit</a>
          <Link className="btn" href="/cases/">Open a case</Link>
        </div>
        <div className="hero-facts">
          <div><b>{stats ? stats.exhibits : "–"}</b>exhibits received</div>
          <div><b>{stats ? stats.flagged : "–"}</b>flagged for an examiner</div>
          <div><b>{stats ? stats.reports : "–"}</b>signed reports</div>
        </div>
      </div>
      <figure className="hero-fig">
        <Lamp
          before="/hero/wisteria.jpg"
          after="/hero/wisteria-ela.jpg"
          width={1600}
          height={1200}
          boxes={[]}
          rightLabel="Error-level map"
        />
        <figcaption className="spread small">
          <span className="muted">Drag across the photo. The error-level map shows how every region reacts to re-compression.</span>
          <a href="#kit" className="linkish">Examine your own</a>
        </figcaption>
      </figure>
    </section>
  );
}

/* ------------------------------------------------------------------ integrity vs authenticity */
function TwoQuestions() {
  const [events, setEvents] = useState<LedgerEvent[]>([]);
  const [chain, setChain] = useState<ChainStatus | null>(null);
  const [sealing, setSealing] = useState(0);
  usePoll(async () => {
    const r = await api<{ events: LedgerEvent[]; chain: ChainStatus }>("/api/ledger/recent?n=6");
    setEvents(r.events);
    setChain(r.chain);
  }, 8000);
  const verify = async () => {
    setSealing((s) => s + 1);
    const r = await api<ChainStatus>("/api/ledger/verify");
    setChain(r);
    setTimeout(() => toast(r.ok ? `All ${r.events} custody entries verified` : `Chain broken at entry ${r.broken_at}`), 900);
  };
  return (
    <section className="two-q" aria-label="Integrity and authenticity">
      <div>
        <h3>Has the file changed since it reached us?</h3>
        <p>
          Every exhibit is hashed on arrival (SHA-256, SHA-1, MD5), stored write-once, and analysed only as a copy.
          Each custody entry includes the hash of the one before it, so editing any past entry breaks every later link.
        </p>
        <div className="chainline" key={sealing}>
          {events.map((e, i) => (
            <span key={e.seq} className="chain-item" style={{ ["--i" as any]: i }}>
              {i > 0 && <span className="lnk" />}
              <span className="blk" title={`${e.action} by ${e.actor}`}>
                <em>{e.action.toLowerCase().replace(/_/g, " ")}</em>
                {e.entry_hash.slice(0, 8)}
              </span>
            </span>
          ))}
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn small" onClick={verify}>Verify the whole chain</button>
          {chain && <span className={`chip ${chain.ok ? "pass" : "fail"}`}>{chain.ok ? "Intact" : "Broken"}</span>}
        </div>
      </div>
      <div>
        <h3>Is the content genuine?</h3>
        <p>
          A matching hash cannot tell you whether the scene is real. That takes independent evidence: compression
          history, sensor noise, the CCTV on-screen clock against the file&rsquo;s own timestamps, the 50&nbsp;Hz mains
          hum inside audio, lips against speech. Each test explains itself and lists innocent explanations. When the
          evidence is thin, PRAMAAN says so instead of guessing.
        </p>
        <ul className="families">
          {["On-screen clock", "Compression ghosts", "Sensor noise", "Mains hum (ENF)", "Lips vs speech",
            "Re-encoding footprint", "Metadata consistency", "Synthetic voice & face"].map((f) => <li key={f}>{f}</li>)}
        </ul>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ demo kit */
const KINDS = [["all", "Everything"], ["image", "Photos"], ["video", "CCTV & video"], ["audio", "Audio"]] as const;
const SETS = [["all", "All sources"], ["real", "Real-world"], ["synthetic", "Generated"]] as const;

function DetectorNotice() {
  const [h, setH] = useState<any>(null);
  useEffect(() => {
    api("/api/health").then(setH).catch(() => {});
  }, []);
  const ai = h?.detectors?.ai_image;
  if (!ai || ai.status === "ready") return null;
  return (
    <div className="notice" role="note">
      <b>AI-image detection is {ai.status === "not installed" ? "off" : "not downloaded yet"} on this machine.</b>{" "}
      Your own photos will usually come back Inconclusive: forwarded and social-media copies carry no camera
      metadata, and without the detector an AI image has nothing to give it away. Turn it on with{" "}
      <code>{ai.install}</code> and restart the server.
    </div>
  );
}

function DemoKit() {
  const router = useRouter();
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [kind, setKind] = useState<string>("all");
  const [set, setSet] = useState<string>("real");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const running = !!samples?.some((s) => s.evidence_id && !s.verdict);
  usePoll(async () => setSamples(await api<Sample[]>("/api/samples")), running ? 3000 : 12000);

  const shown = useMemo(
    () => (samples || []).filter((s) => (kind === "all" || s.kind === kind) && (set === "all" || s.set === set)),
    [samples, kind, set],
  );
  useEffect(() => setPicked((p) => p.filter((id) => shown.some((s) => s.id === id))), [kind, set]); // eslint-disable-line

  const run = async (ids: string[]) => {
    setBusy(true);
    try {
      const r = await postJSON<{ case_id: string; items: { evidence_id: string }[] }>("/api/samples/analyze", { ids });
      toast(ids.length === 1 ? "Exhibit received and hashed. Analysis started." : `${ids.length} exhibits received. Analysis started.`);
      router.push(ids.length === 1 ? `/evidence/?id=${r.items[0].evidence_id}` : `/case/?id=${r.case_id}`);
    } catch (e: any) {
      toast(e.message);
      setBusy(false);
    }
  };
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  return (
    <section id="kit" className="kit">
      <DetectorNotice />
      <div className="kit-head">
        <div>
          <h2>Test exhibits, ready to examine</h2>
          <p className="muted">
            Real-world material from public research datasets (camera photos, street CCTV, a recorded statement, room
            recordings, a voice clone) and generated cases with known answers. Pick one to open it, or select several
            and run them together. Nothing to upload.
          </p>
        </div>
        <div className="filters">
          <div className="seg" role="group" aria-label="Source">
            {SETS.map(([k, l]) => <button key={k} aria-pressed={set === k} onClick={() => setSet(k)}>{l}</button>)}
          </div>
          <div className="seg" role="group" aria-label="Type">
            {KINDS.map(([k, l]) => <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{l}</button>)}
          </div>
        </div>
      </div>
      {!samples ? (
        <div className="tags">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="tagcard ghost scanning" />)}</div>
      ) : shown.length === 0 ? (
        <div className="empty">
          No test exhibits in this view. Run <code>python scripts/fetch_real_samples.py</code> and{" "}
          <code>python scripts/make_demo_samples.py</code> to install them.
        </div>
      ) : (
        <div className="tags">
          {shown.map((s, i) => (
            <SampleCard key={s.id} s={s} n={i} picked={picked.includes(s.id)} onToggle={() => toggle(s.id)}
              onOpen={() => (s.evidence_id ? router.push(`/evidence/?id=${s.evidence_id}`) : run([s.id]))} />
          ))}
        </div>
      )}
      <div className={`dock ${picked.length ? "show" : ""}`} aria-hidden={!picked.length}>
        <span><b>{picked.length}</b> selected</span>
        <button className="btn ghost small" onClick={() => setPicked([])}>Clear</button>
        <button className="btn primary" disabled={busy || !picked.length} onClick={() => run(picked)}>
          {busy ? "Starting…" : `Examine ${picked.length} exhibit${picked.length === 1 ? "" : "s"}`}
        </button>
      </div>
    </section>
  );
}

function SampleCard({ s, n, picked, onToggle, onOpen }: {
  s: Sample; n: number; picked: boolean; onToggle: () => void; onOpen: () => void;
}) {
  const kindLabel = { image: "Photo", video: "Video", audio: "Audio" }[s.kind] || s.kind;
  const analysing = s.evidence_id && !s.verdict;
  return (
    <article className={`tagcard ${picked ? "picked" : ""}`} style={{ ["--n" as any]: n }}>
      <button className={`thumb ${s.kind}`} onClick={onOpen} aria-label={`Examine ${s.title}`}>
        <img src={url(`/api/samples/thumb?id=${encodeURIComponent(s.id)}`)} alt="" loading="lazy" />
        <span className="chip uv kind">{kindLabel}</span>
        {s.verdict && (
          <span className="result"><VerdictChip category={s.verdict.category} /></span>
        )}
        {analysing && <span className="result scanning"><span className="small">Examining…</span></span>}
      </button>
      <div className="body">
        <div className="spread">
          <span className={`truth ${s.tampered ? "t-edit" : s.tampered === false ? "t-orig" : ""}`}>
            {s.tampered ? "Known: edited" : s.tampered === false ? "Known: original" : "Unknown"}
          </span>
          <label className="pick">
            <input type="checkbox" checked={picked} onChange={onToggle} aria-label={`Select ${s.title}`} />
            Select
          </label>
        </div>
        <h3>{s.title}</h3>
        <p className="story">{s.story}</p>
        {s.expect && <p className="expect">{s.expect.charAt(0).toUpperCase() + s.expect.slice(1)}.</p>}
        <p className="src">{s.real ? `Source: ${s.source}` : "Generated test case"}</p>
        <button className="btn small open" onClick={onOpen}>
          {s.verdict ? "Open result" : analysing ? "Watch the analysis" : "Examine"}
        </button>
      </div>
    </article>
  );
}

