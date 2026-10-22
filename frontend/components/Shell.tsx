"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type ChainStatus } from "@/lib/api";
import { usePoll, useToast } from "@/lib/hooks";

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname() || "/";
  const [chain, setChain] = useState<ChainStatus | null>(null);
  const [who, setWho] = useState("examiner");
  const msg = useToast();

  useEffect(() => {
    try {
      setWho(localStorage.getItem("pramaan.actor") || "examiner");
    } catch {}
  }, []);
  usePoll(async () => setChain(await api<ChainStatus>("/api/ledger/verify")), 15000);

  const nav = [
    { href: "/", label: "Demo kit", on: path === "/" },
    { href: "/cases/", label: "Cases", on: path.startsWith("/case") },
    { href: "/log/", label: "Case log", on: path.startsWith("/log") },
  ];
  return (
    <>
      <header className="topbar">
        <div className="wrap">
          <Link href="/" className="brand" aria-label="PRAMAAN home">
            <img className="brand-mark" src="/logo-64.png" alt="" width={34} height={34} />
            <span className="brand-text">PRAMAAN</span>
          </Link>
          <nav className="nav" aria-label="Main">
            {nav.map((n) => (
              <Link key={n.href} href={n.href} aria-current={n.on ? "page" : undefined}>{n.label}</Link>
            ))}
          </nav>
          <div className="top-right">
            {chain && (
              <span className={`pill ${chain.ok ? "ok" : "bad"}`} title="Chain-of-custody ledger">
                <span className="dot" />
                {chain.ok ? `Custody ledger intact · ${chain.events} entries` : `Ledger broken at entry ${chain.broken_at}`}
              </span>
            )}
            <span className="pill ok hide-sm" title="Every check runs on this machine; nothing is sent out">
              <span className="dot" />Offline
            </span>
            <label className="examiner hide-sm">
              Examiner
              <input
                value={who}
                onChange={(e) => {
                  setWho(e.target.value);
                  try {
                    localStorage.setItem("pramaan.actor", e.target.value || "examiner");
                  } catch {}
                }}
                aria-label="Examiner name written to the custody ledger"
              />
            </label>
          </div>
        </div>
      </header>
      <main>
        <div className="wrap">{children}</div>
      </main>
      <footer className="foot">
        <div className="wrap spread">
          <span>PRAMAAN · digital evidence integrity &amp; authenticity · PS-05</span>
          <span>Analytical support for a qualified examiner, not a substitute for one.</span>
        </div>
      </footer>
      {msg && <div className="toast" role="status">{msg}</div>}
    </>
  );
}
