"use client";
import { CATEGORY_HI, tone, type Status } from "@/lib/api";

const STATUS_WORD: Record<Status, string> = {
  fail: "Suspicious", warn: "Caution", pass: "Consistent", info: "Info", unavailable: "Not installed",
};

export function StatusChip({ s }: { s: Status }) {
  return <span className={`chip ${s}`}>{STATUS_WORD[s] || s}</span>;
}

const SHORT: Record<string, string> = {
  "Strong indications of manipulation": "Strong indications",
  "Indications of manipulation": "Indications",
  "No indications of manipulation detected": "No indications",
  Inconclusive: "Inconclusive",
};

export function VerdictChip({ category, hi = false }: { category: string; hi?: boolean }) {
  return <span className={`chip ${tone(category)}`} title={category}>{hi ? CATEGORY_HI[category] || category : SHORT[category] || category}</span>;
}

export function Progress({ value }: { value: number }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <i style={{ width: `${Math.max(4, value * 100)}%` }} />
    </div>
  );
}
