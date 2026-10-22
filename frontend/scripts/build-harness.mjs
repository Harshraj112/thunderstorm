// Bundle the dashboard WITHOUT Next.js (offline fallback + headless tests): node scripts/build-harness.mjs
// Uses esbuild + React from frontend/node_modules, or from NODE_PATH. Output: ../pramaan/web_dist/
import { cpSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// esbuild from frontend/node_modules, else from a NODE_PATH entry (ES modules ignore NODE_PATH themselves)
async function loadEsbuild() {
  try {
    return await import("esbuild");
  } catch {
    for (const p of (process.env.NODE_PATH || "").split(":").filter(Boolean)) {
      const f = resolve(p, "esbuild/lib/main.js");
      if (existsSync(f)) return (await import(pathToFileURL(f).href)).default;
    }
    throw new Error("esbuild not found: run `npm install` in frontend/ or set NODE_PATH");
  }
}
const { build } = await loadEsbuild();

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const out = resolve(root, "../pramaan/web_dist");
mkdirSync(out, { recursive: true });
await build({
  entryPoints: [resolve(root, "harness/entry.tsx")],
  bundle: true, minify: true, sourcemap: false, format: "esm", target: "es2020",
  outfile: resolve(out, "app.js"),
  jsx: "automatic",
  loader: { ".woff2": "file" },
  external: ["/fonts/*"],
  alias: {
    "next/link": resolve(root, "harness/next-link.tsx"),
    "next/navigation": resolve(root, "harness/next-navigation.ts"),
    "@": root,
  },
  nodePaths: (process.env.NODE_PATH || "").split(":").filter(Boolean),
  define: { "process.env.NEXT_PUBLIC_API": '""', "process.env.NODE_ENV": '"production"' },
  logLevel: "warning",
});
cpSync(resolve(root, "public/fonts"), resolve(out, "fonts"), { recursive: true });
cpSync(resolve(root, "public/hero"), resolve(out, "hero"), { recursive: true });
for (const f of ["favicon.ico", "logo-64.png", "logo-192.png", "logo-512.png"]) cpSync(resolve(root, "public", f), resolve(out, f));
writeFileSync(resolve(out, "index.html"), `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#3a1538">
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/logo-192.png"><link rel="apple-touch-icon" href="/logo-192.png">
<title>PRAMAAN · Evidence verification</title><link rel="stylesheet" href="/app.css"></head>
<body><div id="root"></div><script type="module" src="/app.js"></script></body></html>`);
console.log("built", out);
