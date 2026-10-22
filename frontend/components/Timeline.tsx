"use client";
import { useEffect, useRef, useState } from "react";
import { findingTimes, type Finding } from "@/lib/api";

interface Track { key: string; name: string; x: number[]; y: number[]; color: string }

const SPECS: [string, string, string, string, string][] = [
  // timeline key, x field, y field, label, colour
  ["osd", "pts", "offset", "On-screen clock minus file clock (s)", "var(--uv)"],
  ["pixels", "t", "diff", "Frame-to-frame change", "#b896bd"],
  ["vpf", "t", "bit_ratio", "Re-encoding footprint (P-frame size)", "#5fb7ff"],
  ["lipsync", "t", "sync_corr", "Lips vs speech agreement", "var(--amber)"],
  ["face", "t", "fake_prob", "Face-manipulation score", "#ff8fb1"],
  ["enf", "t", "phase_jump", "Mains-hum phase step (rad)", "#c08bff"],
  ["audio", "t", "noise_floor_db", "Background noise floor (dBFS)", "var(--teal)"],
  ["spoof", "t", "spoof_logit", "Synthetic-voice score", "#ffb36b"],
  ["partialspoof", "t", "spoof_prob", "Synthetic speech, per 20 ms", "#ff7a6b"],
];

export function tracksOf(tl: Record<string, any> | undefined): Track[] {
  if (!tl) return [];
  const out: Track[] = [];
  for (const [k, xf, yf, name, color] of SPECS) {
    const d = tl[k];
    if (d && Array.isArray(d[xf]) && Array.isArray(d[yf]) && d[xf].length > 1) {
      out.push({ key: k, name, x: d[xf], y: d[yf], color });
    }
  }
  return out;
}

/** Stacked signal tracks on one time axis; flagged findings shade their time spans; click to seek. */
export default function Timeline({ tl, findings, media, onSeek }: {
  tl?: Record<string, any>; findings: Finding[]; media: HTMLMediaElement | null; onSeek: (t: number) => void;
}) {
  const tracks = tracksOf(tl);
  const svg = useRef<SVGSVGElement>(null);
  const [cur, setCur] = useState(0);
  useEffect(() => {
    if (!media) return;
    const on = () => setCur(media.currentTime);
    media.addEventListener("timeupdate", on);
    return () => media.removeEventListener("timeupdate", on);
  }, [media]);
  if (!tracks.length) return <p className="small faint">No time-based signals for this exhibit.</p>;
  const tmax = Math.max(...tracks.map((t) => t.x[t.x.length - 1] || 0), media?.duration || 0, 1);
  const H = 54, GAP = 26, W = 1000, L = 8, PW = 984;
  const total = tracks.length * (H + GAP) + 22;
  const X = (t: number) => L + (t / tmax) * PW;
  const flagged = findings.filter((f) => (f.status === "fail" || f.status === "warn") && findingTimes(f).length);
  const click = (e: React.MouseEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const t = (((e.clientX - r.left) / r.width) * W - L) / PW * tmax;
    if (t >= 0 && t <= tmax) onSeek(t);
  };
  return (
    <svg ref={svg} className="tl" viewBox={`0 0 ${W} ${total}`} onClick={click} role="img"
      aria-label="Signal timeline. Shaded spans are where tests found problems; click to jump there.">
      {flagged.flatMap((f) => findingTimes(f).map(([a, b], i) => {
        const x1 = X(a), x2 = Math.max(X(b), x1 + 4);
        return (
          <rect key={f.id + i} className="flag-span" x={x1 - 2} y={0} width={x2 - x1 + 4} height={total - 20}>
            <title>{f.title}</title>
          </rect>
        );
      }))}
      {tracks.map((tr, k) => {
        const y0 = 16 + k * (H + GAP);
        const ys = tr.y.filter(Number.isFinite);
        let lo = Math.min(...ys), hi = Math.max(...ys);
        if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
        const Y = (v: number) => y0 + H - ((v - lo) / (hi - lo)) * H;
        const pts = tr.x.map((x, i) => `${X(x).toFixed(1)},${Y(tr.y[i]).toFixed(1)}`).join(" ");
        return (
          <g key={tr.key}>
            <text className="lab" x={L} y={y0 - 4}>{tr.name}</text>
            <line x1={L} x2={L + PW} y1={y0 + H} y2={y0 + H} stroke="var(--line)" />
            {/* pathLength=1 lets the draw-in animation cover lines of any length */}
            <polyline className="draw" points={pts} fill="none" stroke={tr.color} strokeWidth="1.6" pathLength={1}
              style={{ animationDelay: `${k * 120}ms` }} />
          </g>
        );
      })}
      {Array.from({ length: 11 }).map((_, i) => {
        const t = (tmax * i) / 10;
        return <text key={i} x={X(t)} y={total - 4} textAnchor={i === 0 ? "start" : i === 10 ? "end" : "middle"}>
          {t < 60 ? `${t.toFixed(t < 10 ? 1 : 0)}s` : `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}`}
        </text>;
      })}
      <line x1={X(cur)} x2={X(cur)} y1={0} y2={total - 18} className="cursor" />
    </svg>
  );
}
