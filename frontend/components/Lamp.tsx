"use client";
import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "@/lib/hooks";

export interface Box { x: number; y: number; w: number; h: number; label: string }

/**
 * The UV lamp: the evidence as received on the left of the beam, the forensic map on the right.
 * One orchestrated sweep when it first appears, then it follows the pointer / arrow keys.
 * Flagged regions light up once the beam has passed over them.
 */
export default function Lamp({
  before, after, width, height, boxes = [], leftLabel = "As received", rightLabel = "Under analysis",
  sweep = true, className = "",
}: {
  before: string; after?: string | null; width: number; height: number; boxes?: Box[];
  leftLabel?: string; rightLabel?: string; sweep?: boolean; className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState(sweep ? 100 : 45);   // beam position, % from left
  const [touched, setTouched] = useState(false);
  const reduced = useReducedMotion();

  useEffect(() => {
    if (!sweep || touched || !after) return;
    if (reduced) {
      setPos(45);
      return;
    }
    // 100% -> 18% -> 45%: reveal the map, then settle so both halves are visible
    const keys = [[0, 100], [1500, 18], [2300, 45]] as const;
    const t0 = performance.now() + 350;
    let raf = 0;
    const step = (now: number) => {
      const t = now - t0;
      if (t < 0) {
        raf = requestAnimationFrame(step);
        return;
      }
      let v = keys[keys.length - 1][1] as number;
      for (let i = 0; i < keys.length - 1; i++) {
        const [ta, va] = keys[i], [tb, vb] = keys[i + 1];
        if (t <= tb) {
          const k = (t - ta) / (tb - ta);
          const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
          v = va + (vb - va) * e;
          break;
        }
      }
      setPos(v);
      if (t < keys[keys.length - 1][0]) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [sweep, touched, after, reduced]);

  const move = (clientX: number) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    setTouched(true);
    setPos(Math.max(0, Math.min(100, ((clientX - r.left) / r.width) * 100)));
  };

  return (
    <div
      ref={ref}
      className={`lamp ${className}`}
      style={{ aspectRatio: `${width} / ${height}`, ["--lamp" as any]: `${pos}%`, ["--ar" as any]: width / height }}
      onPointerMove={(e) => (e.pointerType === "mouse" || e.buttons) && after && move(e.clientX)}
      onPointerDown={(e) => after && move(e.clientX)}
      role={after ? "slider" : undefined}
      aria-label={after ? "Drag to compare the evidence with the forensic map" : undefined}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pos)}
      tabIndex={after ? 0 : undefined}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          setTouched(true);
          setPos((p) => Math.max(0, Math.min(100, p + (e.key === "ArrowLeft" ? -5 : 5))));
          e.preventDefault();
        }
      }}
    >
      <img src={before} alt="Evidence as received" draggable={false} />
      {after && (
        <>
          <img className="uv-layer" src={after} alt="Forensic localisation map" draggable={false} />
          <div className="uv-tint" />
          <div className="beam" aria-hidden />
        </>
      )}
      {boxes.map((b, i) => {
        const lit = !after || pos <= ((b.x + b.w) / width) * 100;
        return (
          <div key={i} className={`found ${lit ? "on" : ""}`}
            style={{ left: `${(b.x / width) * 100}%`, top: `${(b.y / height) * 100}%`,
                     width: `${(b.w / width) * 100}%`, height: `${(b.h / height) * 100}%` }}>
            <b style={{ top: -26 - i * 24 }}>{b.label}</b>
          </div>
        );
      })}
      {after && (
        <div className="tag" aria-hidden>
          <span>{leftLabel}</span>
          <span>{rightLabel}</span>
        </div>
      )}
    </div>
  );
}
