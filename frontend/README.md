# PRAMAAN dashboard (Next.js 15, App Router)

Pages: `/` (UV-lamp hero, integrity vs authenticity, one-click demo kit), `/cases/`, `/case/?id=`, `/evidence/?id=`.
No UI dependencies beyond React and Next.js. Animations are CSS and `requestAnimationFrame`, fonts are self-hosted IBM Plex (Latin + Devanagari), and everything works offline. `prefers-reduced-motion` is respected.

```bash
npm install
npm run build          # static export → out/ (served by `uvicorn pramaan.api.server:app`)
npm run typecheck
NEXT_PUBLIC_API=http://localhost:8000 npm run dev
```

`scripts/build-harness.mjs` bundles the same components with esbuild into `../pramaan/web_dist`, standing in for `next/link` and `next/navigation` (see `harness/`). That bundle is the offline fallback and what the automated screenshot tests use.

| File | What it is |
|---|---|
| `components/Lamp.tsx` | The signature compare slider: one sweep on load, then follows the pointer or arrow keys; flagged boxes light up as the beam passes |
| `components/Home.tsx` | Hero, live custody chain, demo kit with filters and multi-select dock |
| `components/Evidence.tsx` | Live examination view, verdict, integrity, media bench, findings, custody sealing |
| `components/Verdict.tsx` | Evidence-weight dial and per-family bars |
| `components/Timeline.tsx` | Stacked signal tracks with flagged spans; click to seek |
