/* PRAMAAN dashboard – dependency-free so it runs on an air-gapped lab workstation. */
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const actor = () => $("#actor").value || "examiner";
let pollTimer = null;

try { $("#actor").value = localStorage.getItem("pramaan.actor") || "examiner"; } catch (e) {}
$("#actor").addEventListener("change", () => { try { localStorage.setItem("pramaan.actor", actor()); } catch (e) {} });

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "X-Actor": actor(), ...(opts.headers || {}) } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
}
const fmtTime = (ts) => new Date(ts * 1000).toLocaleString();
const verdictClass = (c = "") => c.startsWith("Strong") ? "v-strong" : c.startsWith("Indications") ? "v-ind" : c.startsWith("No ind") ? "v-none" : "";
const chip = (s) => `<span class="chip s-${s}">${{ fail: "Suspicious", warn: "Caution", pass: "Consistent", info: "Info", unavailable: "N/A" }[s] || s}</span>`;
const vchip = (v) => v ? `<span class="chip s-${verdictClass(v.category) === "v-strong" ? "fail" : verdictClass(v.category) === "v-ind" ? "warn" : verdictClass(v.category) === "v-none" ? "pass" : "info"}">${esc(v.category)}</span>` : `<span class="chip s-info">Not analysed</span>`;

async function ledgerBadge() {
  try {
    const l = await api("/api/ledger/verify");
    const b = $("#ledger-badge");
    b.className = "pill " + (l.ok ? "ok" : "bad");
    b.textContent = l.ok ? `Ledger intact · ${l.events} events` : `Ledger BROKEN at #${l.broken_at}`;
  } catch (e) {}
}

/* ------------------------------------------------------------------ router */
function route() {
  clearInterval(pollTimer);
  const h = location.hash.slice(1) || "/";
  const [, kind, id] = h.split("/");
  ledgerBadge();
  if (kind === "case") return viewCase(id);
  if (kind === "ev") return viewEvidence(id);
  return viewCases();
}
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ cases */
async function viewCases() {
  const cases = await api("/api/cases");
  app.innerHTML = `
  <div class="spread"><div><h1>Cases</h1><div class="sub">Each case holds exhibits with a hash-chained custody record.</div></div></div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card">${cases.length ? `<table class="list"><tr><th>FIR / title</th><th>Police station</th><th>IO</th><th>Exhibits</th><th>Created</th></tr>
      ${cases.map(c => `<tr class="click" data-id="${c.id}"><td><b>${esc(c.fir_no || "—")}</b><div class="small muted">${esc(c.title || "")}</div></td>
      <td>${esc(c.police_station || "")}</td><td>${esc(c.io_name || "")}</td><td>${c.n_evidence}</td><td class="small">${fmtTime(c.created_at)}</td></tr>`).join("")}</table>`
      : `<div class="empty">No cases yet. Create one to start receiving evidence.</div>`}</div>
    <form class="card grid" id="newcase" style="align-content:start">
      <h2>New case</h2>
      <input name="fir_no" placeholder="FIR no. e.g. 0123/2026" required>
      <input name="police_station" placeholder="Police station">
      <input name="io_name" placeholder="Investigating officer">
      <input name="title" placeholder="Short description">
      <button class="primary">Create case</button>
    </form>
  </div>`;
  app.querySelectorAll("tr.click").forEach(tr => tr.onclick = () => location.hash = `#/case/${tr.dataset.id}`);
  $("#newcase").onsubmit = async (e) => {
    e.preventDefault();
    const c = await api("/api/cases", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(new FormData(e.target))) });
    location.hash = `#/case/${c.id}`;
  };
}

async function viewCase(id) {
  const c = await api(`/api/cases/${id}`);
  const running = c.evidence.some(e => e.job && !String(e.job.status).startsWith("done"));
  app.innerHTML = `
  <div class="crumbs"><a href="#/">Cases</a> › ${esc(c.fir_no)}</div>
  <div class="spread"><div><h1>${esc(c.fir_no)}</h1><div class="sub">${esc(c.police_station || "")} · IO ${esc(c.io_name || "—")} · ${esc(c.title || "")}</div></div></div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card">${c.evidence.length ? `<table class="list"><tr><th>Exhibit</th><th>Type</th><th>SHA-256</th><th>Result</th></tr>
      ${c.evidence.map(e => `<tr class="click" data-id="${e.id}"><td><b>${esc(e.exhibit_no)}</b><div class="small muted">${esc(e.original_filename)}</div></td>
        <td>${e.modality}</td><td class="mono" title="${e.sha256}">${e.sha256.slice(0, 16)}…</td>
        <td>${e.job && !String(e.job.status).startsWith("done") ? `<span class="small muted">${esc(e.job.stage || "queued")}</span><div class="progress"><i style="width:${Math.round((e.job.progress || 0) * 100)}%"></i></div>` : vchip(e.verdict)}</td></tr>`).join("")}</table>`
      : `<div class="empty">No exhibits yet.</div>`}</div>
    <div class="card grid" style="align-content:start">
      <h2>Receive evidence</h2>
      <div class="drop" id="drop">Drop images, CCTV/video or audio files here<br><small>or click to choose · a whole DVR export folder works too</small>
        <input type="file" id="file" multiple hidden></div>
      <input id="acq" placeholder="Acquisition: e.g. DVR export via USB by SI Sharma">
      <input id="dev" placeholder="Device: e.g. Hikvision DS-7608, CAM-03">
      <div class="small muted">Files are hashed (SHA-256/SHA-1/MD5) on arrival, stored write-once, and analysed on a copy.</div>
    </div>
  </div>`;
  app.querySelectorAll("tr.click").forEach(tr => tr.onclick = () => location.hash = `#/ev/${tr.dataset.id}`);
  const drop = $("#drop"), file = $("#file");
  drop.onclick = () => file.click();
  ["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => upload(id, e.dataTransfer.files));
  file.onchange = () => upload(id, file.files);
  if (running) pollTimer = setInterval(() => viewCase(id), 2500);
}

async function upload(caseId, files) {
  if (!files.length) return;
  const fd = new FormData();
  [...files].forEach(f => fd.append("files", f));
  fd.append("acquisition_source", $("#acq").value);
  fd.append("device_details", $("#dev").value);
  toast(`Hashing and queuing ${files.length} file(s)…`);
  await api(`/api/cases/${caseId}/evidence`, { method: "POST", body: fd });
  viewCase(caseId);
}

/* ------------------------------------------------------------------ evidence */
let state = {};
async function viewEvidence(id) {
  const d = await api(`/api/evidence/${id}`);
  state = { d, lang: state.lang || "en", layer: "original", tab: "findings" };
  const e = d.evidence;
  if (!d.verdict) {
    const j = d.job;
    app.innerHTML = `<div class="crumbs"><a href="#/">Cases</a> › <a href="#/case/${e.case_id}">${esc(d.case?.fir_no)}</a> › ${esc(e.exhibit_no)}</div>
      <h1>${esc(e.original_filename)}</h1><div class="card" style="margin-top:12px">
      <b>Analysis ${j ? esc(j.status) : "not started"}</b><div class="small muted">${esc(j?.stage || "")}</div>
      <div class="progress"><i style="width:${Math.round((j?.progress || 0) * 100)}%"></i></div>
      ${j ? "" : `<button class="primary" id="go" style="margin-top:10px">Analyse</button>`}</div>`;
    if (!j) $("#go").onclick = async () => { await api(`/api/evidence/${id}/analyze`, { method: "POST" }); viewEvidence(id); };
    pollTimer = setInterval(async () => {
      const r = await api(`/api/evidence/${id}`);
      if (r.verdict) { clearInterval(pollTimer); viewEvidence(id); }
      else { const p = app.querySelector(".progress i"); if (p) p.style.width = Math.round((r.job?.progress || 0) * 100) + "%";
             const s = app.querySelector(".card .small"); if (s) s.textContent = r.job?.stage || ""; }
    }, 1500);
    return;
  }
  renderEvidence();
}

function renderEvidence() {
  const { d } = state, e = d.evidence, v = d.verdict.rationale, job = d.job;
  const tl = v.timeline || {};
  app.innerHTML = `
  <div class="crumbs"><a href="#/">Cases</a> › <a href="#/case/${e.case_id}">${esc(d.case?.fir_no)}</a> › ${esc(e.exhibit_no)}</div>
  <div class="spread">
    <div><h1>${esc(e.original_filename)}</h1><div class="sub">${esc(e.exhibit_no)} · ${e.modality} · ${e.size.toLocaleString()} bytes · received ${fmtTime(e.ingested_at)} by ${esc(e.ingested_by)}</div></div>
    <div class="row"><button id="reanalyse">Re-analyse</button><button class="primary" id="report">Generate signed report</button></div>
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card verdict ${verdictClass(v.category)}">
      <div class="spread"><div class="small muted">Authenticity assessment</div>
        <div class="lang"><button data-l="en" class="${state.lang === "en" ? "on" : ""}">English</button><button data-l="hi" class="${state.lang === "hi" ? "on" : ""}">हिन्दी</button></div></div>
      <div class="cat">${esc(v.category)}</div>
      <div class="small muted">${v.confidence !== "—" ? `Confidence <b>${v.confidence}</b> · ` : ""}combined evidence weight ${v.total_llr >= 0 ? "+" : ""}${v.total_llr} · coverage ${Math.round(v.coverage * 100)}%</div>
      <p class="narr" id="narr"></p>
      <div class="bars">${v.contributions.map(c => bar(c)).join("")}</div>
      <div class="small muted" style="margin-top:6px">Bars: evidence for (red) / against (green) manipulation, per independent family of tests.</div>
    </div>
    <div class="card">
      <div class="spread"><h2>Integrity</h2><span id="hashbadge" class="pill ok">SHA-256 matches intake</span></div>
      <dl class="kv"><dt>SHA-256</dt><dd class="mono">${e.sha256}</dd><dt>SHA-1</dt><dd class="mono">${e.sha1}</dd><dt>MD5</dt><dd class="mono">${e.md5}</dd>
      <dt>Acquired</dt><dd>${esc(e.acquisition_source || "—")}</dd><dt>Device</dt><dd>${esc(e.device_details || "—")}</dd></dl>
      <div class="row" style="margin-top:10px"><button id="rehash">Re-verify hash now</button>
      <a class="btn" href="/api/evidence/${e.id}/original?download=1">Export copy</a></div>
      <div class="small muted" style="margin-top:8px">A matching hash proves the file is unchanged since it entered the lab. It does not prove the scene is genuine — that is the authenticity assessment.</div>
      <div id="reports" style="margin-top:10px">${reportsHtml(d.reports)}</div>
    </div>
  </div>
  <div class="card" style="margin-top:14px">${mediaHtml(e, d.findings, job, tl)}</div>
  <div class="card" style="margin-top:14px">
    <div class="tabs"><button data-t="findings" class="${state.tab === "findings" ? "on" : ""}">Findings (${d.findings.filter(f => f.status !== "info").length})</button>
    <button data-t="custody" class="${state.tab === "custody" ? "on" : ""}">Chain of custody</button></div>
    <div id="tabbody"></div>
  </div>`;
  $("#narr").textContent = narration();
  app.querySelectorAll(".lang button").forEach(b => b.onclick = () => { state.lang = b.dataset.l; renderEvidence(); });
  app.querySelectorAll(".tabs button").forEach(b => b.onclick = () => { state.tab = b.dataset.t; renderEvidence(); });
  $("#rehash").onclick = async () => {
    const r = await api(`/api/evidence/${e.id}/verify-hash`, { method: "POST" });
    const b = $("#hashbadge"); b.className = "pill " + (r.ok ? "ok" : "bad");
    b.textContent = r.ok ? "Re-verified: matches intake" : "MISMATCH with intake hash"; ledgerBadge();
  };
  $("#reanalyse").onclick = async () => { await api(`/api/evidence/${e.id}/analyze`, { method: "POST" }); viewEvidence(e.id); };
  $("#report").onclick = async (ev) => {
    ev.target.disabled = true; ev.target.textContent = "Signing…";
    try { const r = await api(`/api/evidence/${e.id}/report`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ examiner: actor() }) });
      window.open(`/api/reports/${r.report_id}/pdf`, "_blank"); viewEvidence(e.id);
    } catch (err) { toast(err.message); ev.target.disabled = false; }
  };
  wireMedia(e, d.findings, job, tl);
  state.tab === "findings" ? renderFindings() : renderCustody();
}

function narration() {
  const n = state.d.narration || {};
  return state.lang === "hi" ? n.hi : (n.en_llm || n.en);
}
function bar(c) {
  const w = Math.min(Math.abs(c.llr) / 4, 1) * 75, pos = c.llr > 0;
  const style = pos ? `left:25%;width:${w}%;background:var(--fail)` : `right:75%;width:${Math.min(w, 25)}%;background:var(--pass)`;
  return `<div class="bar"><span>${esc(c.family)}</span><span class="track"><i style="${style}"></i></span><span class="mono">${c.llr > 0 ? "+" : ""}${c.llr}</span></div>`;
}
function reportsHtml(reps) {
  if (!reps?.length) return `<div class="small muted">No signed report yet.</div>`;
  return `<div class="small"><b>Signed reports</b></div>` + reps.map(r => `<div class="small row" style="justify-content:space-between">
    <a href="/api/reports/${r.id}/pdf" target="_blank">${r.id} · v${r.version}</a><a href="/verify/${r.id}" target="_blank">verify ↗</a></div>`).join("");
}

/* ------------------------------------------------------------------ media + timelines */
function times(f) {
  const L = f.location || {}, out = [];
  if (typeof L.time === "number") out.push([L.time, L.time]);
  (L.times || []).forEach(t => typeof t === "number" && out.push([t, t]));
  if (typeof L.start === "number") out.push([L.start, L.end ?? L.start]);
  (L.segments || []).forEach(s => out.push([s.start ?? s.repeat_start ?? s.source_start, s.end ?? s.repeat_end ?? s.source_end]));
  (L.gaps || []).forEach(g => out.push([g.at, g.at]));
  return out.filter(([a]) => typeof a === "number");
}
function mediaHtml(e, findings, job, tl) {
  const src = `/api/evidence/${e.id}/original`;
  if (e.modality === "image") {
    const arts = [["original", "Original"]];
    findings.forEach(f => (f.artifacts || []).forEach(a => arts.push([a, f.title.length > 34 ? f.title.slice(0, 32) + "…" : f.title])));
    return `<div class="spread"><h2>Image &amp; localisation maps</h2><span class="small muted">Red boxes = regions flagged by detectors</span></div>
      <div class="layers">${arts.map(([a, t]) => `<button data-a="${esc(a)}" class="${state.layer === a ? "on" : ""}">${esc(t)}</button>`).join("")}</div>
      <div class="viewer" id="viewer"><img id="img" src="${state.layer === "original" ? src : `/artifacts/${job.id}/${state.layer}`}"></div>`;
  }
  const flagged = findings.filter(f => ["fail", "warn"].includes(f.status) && times(f).length);
  const player = e.modality === "video" ? `<div class="viewer"><video id="media" src="${src}" controls preload="metadata"></video></div>` : `<audio id="media" src="${src}" controls preload="metadata"></audio>`;
  return `<div class="spread"><h2>${e.modality === "video" ? "Footage &amp; timeline reconciliation" : "Recording &amp; signal timeline"}</h2>
    <span class="small muted">Click the timeline to jump the player</span></div>${player}
    <div id="tl" style="margin-top:10px">${timelineSvg(tl, flagged, e.modality)}</div>`;
}
function wireMedia(e, findings, job, tl) {
  app.querySelectorAll(".layers button").forEach(b => b.onclick = () => { state.layer = b.dataset.a; renderEvidence(); });
  const img = $("#img");
  if (img) img.onload = () => {
    const w = img.naturalWidth, h = img.naturalHeight, box = img.getBoundingClientRect(), vb = $("#viewer").getBoundingClientRect();
    const s = Math.min(box.width / w, box.height / h), ox = (box.width - w * s) / 2, oy = (box.height - h * s) / 2;
    if (state.layer !== "original") return;
    findings.filter(f => ["fail", "warn"].includes(f.status) && f.location?.bbox).forEach((f, k) => {
      const b = f.location.bbox, d = document.createElement("div");
      d.className = "box"; d.style.cssText = `left:${ox + b.x * s}px;top:${oy + b.y * s}px;width:${b.w * s}px;height:${b.h * s}px`;
      d.innerHTML = `<span style="top:${-20 - k * 18}px">${esc(f.code.replace("IMG_", ""))}</span>`; $("#viewer").appendChild(d);
    });
  };
  const svg = $("#tl svg"), media = $("#media");
  if (svg && media) {
    svg.addEventListener("click", ev => {
      const r = svg.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width * 1000;
      const t = (x - 60) / 920 * (+svg.dataset.tmax);
      if (t >= 0) { media.currentTime = t; media.play?.(); }
    });
    media.addEventListener("timeupdate", () => {
      const c = $("#cursor"); if (!c) return;
      const x = 60 + media.currentTime / (+svg.dataset.tmax) * 920; c.setAttribute("x1", x); c.setAttribute("x2", x);
    });
  }
}
function timelineSvg(tl, flagged, modality) {
  const tracks = [];
  if (tl.osd?.pts?.length) tracks.push({ name: "OSD − file clock (s)", x: tl.osd.pts, y: tl.osd.offset, color: "var(--accent)", step: true });
  if (tl.pixels?.t?.length) tracks.push({ name: "Frame change", x: tl.pixels.t, y: tl.pixels.diff, color: "var(--info)" });
  if (tl.enf?.t?.length) tracks.push({ name: `Mains hum ${tl.enf.grid}Hz phase step`, x: tl.enf.t, y: tl.enf.phase_jump, color: "#8e44ad" });
  if (tl.audio?.t?.length) tracks.push({ name: "Noise floor (dBFS)", x: tl.audio.t, y: tl.audio.noise_floor_db, color: "var(--pass)" });
  if (tl.spoof?.t?.length) tracks.push({ name: "Synthetic-voice score", x: tl.spoof.t, y: tl.spoof.spoof_logit, color: "var(--warn)" });
  if (!tracks.length) return `<div class="small muted">No timeline signals for this file.</div>`;
  const tmax = Math.max(...tracks.map(t => t.x[t.x.length - 1] || 0), 1);
  const H = 62, W = 1000, L = 60, PW = 920, total = tracks.length * (H + 18) + 20;
  const X = t => L + t / tmax * PW;
  let s = `<svg class="tl" viewBox="0 0 ${W} ${total}" data-tmax="${tmax}" preserveAspectRatio="none" role="img" aria-label="Signal timeline">`;
  // flagged spans
  flagged.forEach(f => times(f).forEach(([a, b]) => {
    const x1 = X(a), x2 = Math.max(X(b), x1 + 3);
    s += `<rect x="${x1 - 1.5}" y="0" width="${x2 - x1 + 3}" height="${total - 16}" fill="var(--fail)" opacity=".16"><title>${esc(f.title)}</title></rect>`;
  }));
  tracks.forEach((tr, k) => {
    const y0 = 8 + k * (H + 18), ys = tr.y.filter(Number.isFinite);
    let lo = Math.min(...ys), hi = Math.max(...ys); if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
    const Y = v => y0 + H - (v - lo) / (hi - lo) * H;
    const pts = tr.x.map((x, i) => `${X(x).toFixed(1)},${Y(tr.y[i]).toFixed(1)}`).join(" ");
    s += `<text x="${L}" y="${y0 - 1}">${esc(tr.name)}</text>`;
    s += `<line x1="${L}" x2="${L + PW}" y1="${y0 + H}" y2="${y0 + H}" stroke="var(--line)"/>`;
    s += `<text x="${L - 6}" y="${y0 + 8}" text-anchor="end">${hi.toFixed(1)}</text><text x="${L - 6}" y="${y0 + H}" text-anchor="end">${lo.toFixed(1)}</text>`;
    s += `<polyline points="${pts}" fill="none" stroke="${tr.color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`;
  });
  for (let i = 0; i <= 10; i++) { const t = tmax * i / 10; s += `<text x="${X(t)}" y="${total - 2}" text-anchor="middle">${t.toFixed(t < 10 ? 1 : 0)}s</text>`; }
  s += `<line id="cursor" x1="${L}" x2="${L}" y1="0" y2="${total - 14}" stroke="var(--ink)" stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg>`;
  return s;
}

/* ------------------------------------------------------------------ findings / custody */
function renderFindings() {
  const { d } = state, job = d.job;
  const shown = d.findings.filter(f => f.status !== "info" || (f.artifacts || []).length);
  const info = d.findings.filter(f => f.status === "info" && !(f.artifacts || []).length);
  $("#tabbody").innerHTML = shown.map(f => findingHtml(f, job, f.status === "fail")).join("") +
    (info.length ? `<details style="margin-top:12px"><summary class="small muted">Informational (${info.length})</summary>${info.map(f => findingHtml(f, job)).join("")}</details>` : "");
  app.querySelectorAll(".seek").forEach(b => b.onclick = (ev) => { ev.preventDefault(); const m = $("#media"); if (m) { m.currentTime = +b.dataset.t; m.play?.(); m.scrollIntoView({ block: "center" }); } });
  app.querySelectorAll(".arts img").forEach(i => i.onclick = () => { const m = document.createElement("div"); m.className = "modal"; m.innerHTML = `<img src="${i.src}">`; m.onclick = () => m.remove(); document.body.appendChild(m); });
}
function findingHtml(f, job, open = false) {
  const ts = times(f).slice(0, 8);
  return `<details class="finding" ${open ? "open" : ""}><summary>${chip(f.status)}<span class="t">${esc(f.title)}</span>
    <span class="meta">${esc(f.module)} · ${esc(f.family)}${f.status !== "unavailable" && f.status !== "info" ? ` · LLR ${f.llr > 0 ? "+" : ""}${f.llr.toFixed(2)} · rel ${f.reliability.toFixed(2)}` : ""}</span></summary>
    <div class="body">${esc(f.explanation)}</div>
    ${ts.length ? `<div class="row" style="margin-top:6px">${ts.map(([a]) => `<button class="seek" data-t="${a}">▶ ${a.toFixed(1)}s</button>`).join("")}</div>` : ""}
    ${(f.innocent || []).length ? `<div class="inn"><b>Innocent explanations to rule out:</b> ${f.innocent.map(esc).join(" · ")}</div>` : ""}
    ${(f.artifacts || []).length ? `<div class="arts">${f.artifacts.map(a => `<img src="/artifacts/${job.id}/${esc(a)}" alt="${esc(a)}" loading="lazy">`).join("")}</div>` : ""}
  </details>`;
}
async function renderCustody() {
  const c = await api(`/api/evidence/${state.d.evidence.id}/custody`);
  $("#tabbody").innerHTML = `<div class="spread" style="margin-bottom:8px"><span class="pill ${c.chain.ok ? "ok" : "bad"}">${c.chain.ok ? `Global ledger intact (${c.chain.events} events)` : `Ledger broken at #${c.chain.broken_at}: ${esc(c.chain.reason)}`}</span>
    <span class="small muted">Each entry's hash covers the previous entry — editing any row breaks every later hash.</span></div>
    <table class="list custody"><tr><th>#</th><th>Time</th><th>Actor</th><th>Action</th><th>Details</th><th>Entry hash</th></tr>
    ${c.events.map(e => `<tr><td>${e.seq}</td><td class="small">${fmtTime(e.ts)}</td><td>${esc(e.actor)}</td><td><b>${esc(e.action)}</b></td>
      <td class="small mono" style="max-width:340px">${esc(JSON.stringify(e.details)).slice(0, 220)}</td><td class="mono" title="${e.entry_hash}">${e.entry_hash.slice(0, 14)}…</td></tr>`).join("")}</table>`;
}

route();
