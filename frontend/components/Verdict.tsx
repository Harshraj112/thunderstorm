"use client";
import { useEffect, useState } from "react";
import { CATEGORY_HI, FAMILY_LABEL, tone, type Contribution, type Verdict } from "@/lib/api";
import { useCountUp } from "@/lib/hooks";

/** Evidence-weight dial: the arc fills towards the verdict's combined log-likelihood ratio. */
export function Dial({ llr, category }: { llr: number; category: string }) {
  const v = useCountUp(llr, 1100);
  const t = tone(category);
  const R = 62, C = 2 * Math.PI * R, span = 0.75;           // 270° gauge
  const frac = Math.max(0, Math.min(1, (v + 2) / 8));        // −2 … +6
  const col = { strong: "var(--coral)", ind: "var(--amber)", none: "var(--teal)", inc: "var(--muted)" }[t];
  return (
    <svg className="ring" viewBox="0 0 150 150" role="img" aria-label={`Combined evidence weight ${llr.toFixed(1)}`}>
      <g transform="rotate(135 75 75)">
        <circle cx="75" cy="75" r={R} fill="none" stroke="var(--room-2)" strokeWidth="10"
          strokeDasharray={`${C * span} ${C}`} strokeLinecap="round" />
        <circle cx="75" cy="75" r={R} fill="none" stroke={col} strokeWidth="10" strokeLinecap="round"
          strokeDasharray={`${C * span * frac} ${C}`} style={{ filter: `drop-shadow(0 0 6px ${col})` }} />
        {/* threshold ticks: indications (+1.5) and strong (+4) */}
        {[1.5, 4].map((th) => {
          const a = ((th + 2) / 8) * span * 2 * Math.PI;
          return <line key={th} x1={75 + (R - 9) * Math.cos(a)} y1={75 + (R - 9) * Math.sin(a)}
            x2={75 + (R + 9) * Math.cos(a)} y2={75 + (R + 9) * Math.sin(a)} stroke="var(--line-2)" strokeWidth="2" />;
        })}
      </g>
      <text x="75" y="76" textAnchor="middle" fontSize="32" fontWeight="700">{v >= 0 ? "+" : ""}{v.toFixed(1)}</text>
      <text x="75" y="98" textAnchor="middle" fontSize="11" style={{ fill: "var(--faint)" }}>evidence weight</text>
    </svg>
  );
}

export function FamilyBars({ items }: { items: Contribution[] }) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setOn(true), 120);
    return () => clearTimeout(t);
  }, []);
  return (
    <div className="bars" aria-label="Evidence by family of tests">
      {items.map((c) => {
        const pos = c.llr > 0;
        const w = Math.min(Math.abs(c.llr) / 4, 1) * (pos ? 75 : 25);
        return (
          <div className="bar" key={c.family}>
            <span>{FAMILY_LABEL[c.family] || c.family}</span>
            <span className="track">
              <i style={pos ? { left: "25%", width: on ? `${w}%` : 0, background: "var(--coral)" }
                : { right: "75%", width: on ? `${w}%` : 0, background: "var(--teal)" }} />
            </span>
            <span className="mono">{pos ? "+" : ""}{c.llr.toFixed(1)}</span>
          </div>
        );
      })}
      <p className="small faint">Right of the line: evidence of manipulation. Left: evidence the content is original.</p>
    </div>
  );
}

export function VerdictPanel({ v, narration }: { v: Verdict; narration: { en: string; hi: string; en_llm?: string } | null }) {
  const [lang, setLang] = useState<"en" | "hi">("en");
  const [more, setMore] = useState(false);
  const t = tone(v.category);
  const full = narration ? (lang === "hi" ? narration.hi : narration.en_llm || narration.en) : "";
  // first two sentences; the rest is one click away (and always in the signed report)
  const parts = full.split(/(?<=[.।])\s+/);
  const short = parts.slice(0, 2).join(" ");
  return (
    <section className={`panel verdict ${t}`} aria-label="Authenticity assessment">
      <Dial llr={v.total_llr} category={v.category} />
      <div>
        <div className="spread">
          <span className="small faint">Authenticity assessment</span>
          <div className="seg" role="group" aria-label="Language">
            <button aria-pressed={lang === "en"} onClick={() => setLang("en")}>English</button>
            <button aria-pressed={lang === "hi"} onClick={() => setLang("hi")} lang="hi">हिन्दी</button>
          </div>
        </div>
        <div className="cat" lang={lang}>{lang === "hi" ? CATEGORY_HI[v.category] || v.category : v.category}</div>
        {(v.nature || []).length > 0 && (
          <div className="nature" aria-label="What was found">
            {(v.nature || []).map((n) => <span key={n} className={`chip ${n.startsWith("AI") || n.startsWith("Likely AI") ? "fail" : "warn"}`}>{n}</span>)}
          </div>
        )}
        <div className="small muted">
          {v.confidence !== "—" && <>Confidence {v.confidence.toLowerCase()}. </>}
          Evidence available from {Math.round(v.coverage * 100)}% of the expected test families.
        </div>
        {narration && (
          <p className="narr" lang={lang}>
            {more || parts.length <= 2 ? full : short}{" "}
            {parts.length > 2 && (
              <button className="more" onClick={() => setMore(!more)}>{more ? "Show less" : "Read the full summary"}</button>
            )}
          </p>
        )}
      </div>
      {v.abstained && v.abstain_reason && (
        <div className="why">
          <h3>Why this is inconclusive</h3>
          <p>{v.abstain_reason.split(" Missing:")[0]}</p>
          {(v.gaps || []).length > 0 && (
            <>
              <p className="small muted">What would settle it:</p>
              <ul>{(v.gaps || []).map((g) => <li key={g.family}>{g.why.charAt(0).toUpperCase() + g.why.slice(1)}.</li>)}</ul>
            </>
          )}
        </div>
      )}
      <div className="verdict-bars"><FamilyBars items={v.contributions} /></div>
    </section>
  );
}
