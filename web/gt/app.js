"use strict";
// Schück GT editor. Talks to scripts/gt_server.py.
const $ = (id) => document.getElementById(id);
const ta = $("text"), backdrop = $("backdrop"), viewer = $("viewer"), stage = $("stage"),
  img = $("pageimg"), overlay = $("overlay");
const SVGNS = "http://www.w3.org/2000/svg";
const PALETTE = ["æ", "Æ", "œ", "Œ", "ä", "ö", "å", "é", "ü", "ſ", "»", "’", "—", "–", "§", "½", "¼", "¾", "†", "×"];

const S = {
  pages: [], pos: -1, page: null, dirty: false,
  tokens: [], tl: [], others: {}, mapTL: [], mapOther: {}, ops: [], diffs: [], active: -1,
  view: { s: 1, x: 0, y: 0 }, meta: { dismissed: [] },
};

// ---------- helpers ----------
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function flash(msg, warn = false) {
  const m = $("msg");
  m.textContent = msg;
  m.style.color = warn ? "var(--warn)" : "";
  clearTimeout(flash.t);
  flash.t = setTimeout(() => (m.textContent = ""), 4000);
}
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);

function keyOf(t, strict) {
  let k = t.normalize("NFC").replace(/[{}<>­]/g, "").replace(/\^(\d+)/g, "$1")
    .replace(/[⁰¹²³⁴⁵⁶⁷⁸⁹]/g, (c) => "⁰¹²³⁴⁵⁶⁷⁸⁹".indexOf(c));
  if (!strict) k = k.replace(/[’‘`´]/g, "'").replace(/[»«„“”"]/g, '"').replace(/[–—‐]/g, "-");
  return k;
}

// tokens of the editor text with character offsets; region marker lines are skipped
function tokenize(text) {
  const out = [];
  let region = "", off = 0;
  for (const line of text.split("\n")) {
    const m = line.match(/^#(\w+)\s*$/);
    if (m) region = m[1];
    else for (const w of line.matchAll(/\S+/g)) out.push({ t: w[0], s: off + w.index, e: off + w.index + w[0].length, region });
    off += line.length + 1;
  }
  return out;
}

// LCS alignment of two key arrays -> map a->b (index or -1) and opcodes of unequal spans
function align(a, b) {
  const n = a.length, m = b.length, W = m + 1;
  const L = new Uint16Array((n + 1) * W);
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      L[i * W + j] = a[i] === b[j] ? L[(i + 1) * W + j + 1] + 1 : Math.max(L[(i + 1) * W + j], L[i * W + j + 1]);
  const map = new Int32Array(n).fill(-1), pairs = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { map[i] = j; pairs.push([i, j]); i++; j++; }
    else if (L[(i + 1) * W + j] >= L[i * W + j + 1]) i++;
    else j++;
  }
  const ops = [];
  let pi = 0, pj = 0;
  for (const [x, y] of [...pairs, [n, m]]) {
    if (x > pi || y > pj) ops.push({ i1: pi, i2: x, j1: pj, j2: y });
    pi = x + 1; pj = y + 1;
  }
  return { map, ops };
}

// tokens of b that correspond to a's span [i1, i2), using the matched neighbours
function spanIn(map, i1, i2, bLen) {
  let b0 = 0, b1 = bLen;
  for (let i = i1 - 1; i >= 0; i--) if (map[i] >= 0) { b0 = map[i] + 1; break; }
  for (let i = i2; i < map.length; i++) if (map[i] >= 0) { b1 = map[i]; break; }
  return [b0, Math.max(b0, b1)];
}

// ---------- pages ----------
async function loadPages() {
  S.pages = await api("/api/pages");
  const sel = $("pagesel");
  sel.innerHTML = "";
  S.pages.forEach((p, i) => {
    const o = document.createElement("option");
    o.value = i;
    sel.appendChild(o);
  });
  labelPages();
}
function labelPages() {
  const sel = $("pagesel");
  const mark = { verified: "✓ ", wip: "✎ ", draft: "  " };
  S.pages.forEach((p, i) => {
    const bits = [p.page ? `p. ${p.page}` : "", p.stratum, p.split === "test" ? "TEST" : ""].filter(Boolean);
    sel.options[i].textContent = `${mark[p.status] || ""}${p.vol} #${p.index}${bits.length ? " · " + bits.join(" · ") : ""}`;
  });
  const v = S.pages.filter((p) => p.status === "verified").length;
  $("progress").textContent = `${v}/${S.pages.length} verified`;
}

async function openPage(i) {
  if (i < 0 || i >= S.pages.length) return;
  if (S.dirty) await save("save", true);
  const p = S.pages[i];
  let d;
  try { d = await api(`/api/page/${p.vol}/${p.index}`); }
  catch (e) { flash(e.message, true); return; }
  S.pos = i; S.page = d; S.dirty = false; S.active = -1;
  S.meta = Object.assign({ dismissed: [] }, d.meta);
  $("pagesel").value = i;
  ta.value = d.text;
  ta.scrollTop = 0;
  S.tl = d.textlayer;
  S.others = d.others;
  img.onload = () => { overlay.setAttribute("width", d.size[0]); overlay.setAttribute("height", d.size[1]); fit(); };
  img.src = `/img/${p.vol}/${p.index}`;
  history.replaceState(null, "", `#${p.vol}/${p.index}`);
  setBadge(d.status, d.draft_source);
  recompute();
}

function setBadge(status, src) {
  const b = $("badge");
  b.className = "badge " + status;
  b.textContent = status === "draft" ? `draft (${src})` : status === "wip" ? "work in progress" : "verified";
  $("unverify").disabled = status !== "verified";
  S.pages[S.pos].status = status;
  labelPages();
}

async function save(action = "save", quiet = false) {
  if (!S.page) return;
  const p = S.pages[S.pos];
  try {
    const r = await api(`/api/page/${p.vol}/${p.index}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, text: ta.value, meta: S.meta }),
    });
    S.dirty = false;
    setBadge(r.status, S.page.draft_source);
    if (!quiet) flash(r.warning ? `Saved, but: ${r.warning}` : { save: "Saved", verify: "Verified ✓", unverify: "Back to work in progress", revert: "Reverted" }[action], !!r.warning);
    return r;
  } catch (e) { flash(e.message, true); }
}

// ---------- alignment, disagreements ----------
let recomputeTimer;
function scheduleRecompute() { clearTimeout(recomputeTimer); recomputeTimer = setTimeout(recompute, 250); }

function recompute() {
  const strict = $("strict").checked;
  S.tokens = tokenize(ta.value);
  const keys = S.tokens.map((t) => keyOf(t.t, strict));
  const tl = align(keys, S.tl.map((t) => keyOf(t.t, strict)));
  S.mapTL = tl.map;
  S.mapOther = {};
  for (const [e, toks] of Object.entries(S.others)) S.mapOther[e] = { map: align(keys, toks.map((t) => keyOf(t, strict))).map, toks };
  S.diffs = tl.ops.map((op) => {
    const gt = S.tokens.slice(op.i1, op.i2).map((t) => t.t).join(" ");
    const tlr = S.tl.slice(op.j1, op.j2).map((t) => t.t).join(" ");
    const ctx = S.tokens.slice(Math.max(0, op.i1 - 4), op.i1).map((t) => t.t).join(" ");
    const readings = { "text layer": tlr };
    for (const [e, o] of Object.entries(S.mapOther)) {
      const [b0, b1] = spanIn(o.map, op.i1, op.i2, o.toks.length);
      readings[e] = o.toks.slice(b0, b1).join(" ");
    }
    let boxes = S.tl.slice(op.j1, op.j2).flatMap((t) => t.boxes), near = false;
    if (!boxes.length) { near = true; const t = S.tl[op.j1 - 1] || S.tl[op.j1]; boxes = t ? t.boxes : []; }
    const key = `${keyOf(gt, false)}|${keyOf(tlr, false)}|${keyOf(ctx.split(" ").slice(-2).join(" "), false)}`;
    return { ...op, gt, readings, ctx, boxes, near, key, region: (S.tokens[op.i1] || S.tokens[op.i1 - 1] || {}).region };
  });
  renderDiffs();
  renderBackdrop();
  renderOverlay();
  followCursor();
}

function isDismissed(d) { return S.meta.dismissed.includes(d.key); }
function visibleDiffs() { return S.diffs.filter((d) => $("showdismissed").checked || !isDismissed(d)); }

function renderDiffs() {
  const list = $("diffs"), vis = visibleDiffs();
  const open = S.diffs.filter((d) => !isDismissed(d)).length;
  $("diffcount").textContent = `${open} open · ${S.diffs.length - open} dismissed`;
  list.innerHTML = "";
  if (!vis.length) { list.innerHTML = `<li class="empty">No open disagreements. Still read the whole page against the scan before marking it verified.</li>`; return; }
  for (const d of vis) {
    const li = document.createElement("li");
    const idx = S.diffs.indexOf(d);
    li.dataset.i = idx;
    if (idx === S.active) li.classList.add("active");
    if (isDismissed(d)) li.classList.add("dismissed");
    const others = Object.entries(d.readings);
    const against = others.filter(([, r]) => keyOf(r, false) !== keyOf(d.gt, false)).length;
    const rd = others.map(([e, r]) => {
      const same = keyOf(r, false) === keyOf(d.gt, false);
      return `<span class="${same ? "same" : ""}"><b>${esc(e)}</b>${r ? esc(r) : "<i>(nothing)</i>"}</span>`;
    }).join("");
    li.innerHTML = `<div class="ctx">#${esc(d.region || "?")} …${esc(d.ctx)} <span class="gt">[${d.gt ? esc(d.gt) : "<i>nothing</i>"}]</span>
        <span class="vote ${against === others.length && others.length > 1 ? "strong" : ""}" title="engines reading something else than your text">${against}/${others.length} differ</span>${d.near ? ' <span class="muted">(box: nearby word)</span>' : ""}</div>
      <div class="rd">${rd}</div>
      <div class="acts">${others.filter(([, r]) => keyOf(r, false) !== keyOf(d.gt, false))
        .map(([e]) => `<button data-use="${esc(e)}" title="Replace your text with this reading">Use ${esc(e === "text layer" ? "TL" : e.replace("-layout", ""))}</button>`).join("")}
        <button data-dismiss>${isDismissed(d) ? "Undismiss" : "Dismiss"}</button></div>`;
    list.appendChild(li);
  }
}

$("diffs").addEventListener("click", (ev) => {
  const li = ev.target.closest("li[data-i]");
  if (!li) return;
  const d = S.diffs[+li.dataset.i];
  if (ev.target.dataset.use !== undefined) return useReading(d, ev.target.dataset.use);
  if (ev.target.hasAttribute("data-dismiss")) {
    const k = S.meta.dismissed.indexOf(d.key);
    if (k >= 0) S.meta.dismissed.splice(k, 1); else S.meta.dismissed.push(d.key);
    S.dirty = true;
    renderDiffs(); renderBackdrop(); renderOverlay();
    return;
  }
  goDiff(+li.dataset.i);
});

function diffRange(d) {
  if (d.i2 > d.i1) return [S.tokens[d.i1].s, S.tokens[d.i2 - 1].e];
  const prev = S.tokens[d.i1 - 1];
  const at = prev ? prev.e : 0;
  return [at, at];
}

function goDiff(i) {
  const d = S.diffs[i];
  if (!d) return;
  S.active = i;
  const [a, b] = diffRange(d);
  ta.focus();
  ta.setSelectionRange(a, b);
  scrollCaretIntoView(a);
  if (d.boxes.length) zoomTo(d.boxes);
  renderDiffs(); renderBackdrop(); renderOverlay();
  const li = $("diffs").querySelector(`li[data-i="${i}"]`);
  if (li) li.scrollIntoView({ block: "nearest" });
}

function stepDiff(dir) {
  const vis = visibleDiffs().map((d) => S.diffs.indexOf(d));
  if (!vis.length) return;
  const caret = ta.selectionStart;
  let target;
  if (S.active >= 0 && vis.includes(S.active)) target = vis[(vis.indexOf(S.active) + dir + vis.length) % vis.length];
  else target = dir > 0 ? (vis.find((i) => diffRange(S.diffs[i])[0] > caret) ?? vis[0]) : ([...vis].reverse().find((i) => diffRange(S.diffs[i])[0] < caret) ?? vis[vis.length - 1]);
  goDiff(target);
}

function useReading(d, engine) {
  const r = d.readings[engine];
  let [a, b] = diffRange(d);
  let ins = r;
  if (a === b) ins = (r ? " " + r : "");                // GT has nothing here: insert after the previous word
  else if (!r) { while (a > 0 && ta.value[a - 1] === " ") a--; } // GT has extra words: delete them with the space before
  ta.focus();
  ta.setSelectionRange(a, b);
  document.execCommand("insertText", false, ins); // keeps the browser's undo stack
  onEdit();
}

function scrollCaretIntoView(pos) {
  const lineH = parseFloat(getComputedStyle(ta).lineHeight);
  // approximate: measure via backdrop span position after render
  requestAnimationFrame(() => {
    const m = backdrop.querySelector("mark.d.active");
    const y = m ? m.offsetTop : (ta.value.slice(0, pos).split("\n").length - 1) * lineH;
    if (y < ta.scrollTop + lineH || y > ta.scrollTop + ta.clientHeight - 2 * lineH) ta.scrollTop = Math.max(0, y - ta.clientHeight / 3);
    backdrop.scrollTop = ta.scrollTop;
  });
}

// ---------- editor backdrop (highlights) ----------
function renderBackdrop() {
  const text = ta.value, n = text.length;
  const cls = new Array(n).fill("");
  const add = (a, b, c) => { for (let k = a; k < b; k++) cls[k] = cls[k] ? cls[k] + " " + c : c; };
  for (const m of text.matchAll(/\{[^{}\n]*\}/g)) add(m.index, m.index + m[0].length, "it");
  for (const m of text.matchAll(/<[^<>\n]*>/g)) add(m.index, m.index + m[0].length, "sp");
  for (const m of text.matchAll(/\^\d+/g)) add(m.index, m.index + m[0].length, "fn");
  for (const m of text.matchAll(/^#\w+[ \t]*$/gm)) add(m.index, m.index + m[0].length, "rg");
  S.diffs.forEach((d, i) => {
    if (isDismissed(d)) return;
    let [a, b] = diffRange(d);
    if (a === b) { b = Math.min(n, a + 1); a = Math.max(0, a - 1); } // show insertions as a thin mark
    add(a, b, i === S.active ? "d active" : "d");
  });
  let html = "", run = "", cur = null;
  const flush = () => { if (run) html += cur ? `<mark class="${cur}">${esc(run)}</mark>` : esc(run); run = ""; };
  for (let k = 0; k < n; k++) {
    if (cls[k] !== cur) { flush(); cur = cls[k]; }
    run += text[k];
  }
  flush();
  backdrop.innerHTML = html + "\n\n";
  backdrop.scrollTop = ta.scrollTop;
}
ta.addEventListener("scroll", () => (backdrop.scrollTop = ta.scrollTop));

// ---------- image viewer ----------
function applyView() { stage.style.transform = `translate(${S.view.x}px, ${S.view.y}px) scale(${S.view.s})`; }
function fit() {
  if (!S.page) return;
  const [W, H] = S.page.size, vw = viewer.clientWidth, vh = viewer.clientHeight;
  const s = Math.min(vw / W, vh / H) * 0.98;
  S.view = { s, x: (vw - W * s) / 2, y: (vh - H * s) / 2 };
  applyView();
}
function zoomAt(f, cx, cy) {
  const v = S.view, s = Math.min(6, Math.max(0.1, v.s * f));
  v.x = cx - (cx - v.x) * (s / v.s); v.y = cy - (cy - v.y) * (s / v.s); v.s = s;
  applyView();
}
function boxUnion(boxes) {
  return boxes.reduce((u, b) => [Math.min(u[0], b[0]), Math.min(u[1], b[1]), Math.max(u[2], b[2]), Math.max(u[3], b[3])], [1e9, 1e9, -1e9, -1e9]);
}
function zoomTo(boxes, onlyIfHidden = false) {
  const [x0, y0, x1, y1] = boxUnion(boxes), vw = viewer.clientWidth, vh = viewer.clientHeight, v = S.view;
  if (onlyIfHidden) {
    const sx0 = v.x + x0 * v.s, sy0 = v.y + y0 * v.s, sx1 = v.x + x1 * v.s, sy1 = v.y + y1 * v.s;
    if (sx0 > 20 && sy0 > 20 && sx1 < vw - 20 && sy1 < vh - 20) return;
  } else {
    v.s = Math.min(3, Math.max(v.s, 34 / Math.max(10, y1 - y0)));   // readable line height
  }
  v.x = vw / 2 - ((x0 + x1) / 2) * v.s;
  v.y = vh / 2 - ((y0 + y1) / 2) * v.s;
  applyView();
}
viewer.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = viewer.getBoundingClientRect();
  zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
}, { passive: false });
let drag = null;
viewer.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, vx: S.view.x, vy: S.view.y }; viewer.setPointerCapture(e.pointerId); viewer.classList.add("dragging"); });
viewer.addEventListener("pointermove", (e) => { if (!drag) return; S.view.x = drag.vx + e.clientX - drag.x; S.view.y = drag.vy + e.clientY - drag.y; applyView(); });
viewer.addEventListener("pointerup", () => { drag = null; viewer.classList.remove("dragging"); });
$("fit").onclick = fit;
$("zoomin").onclick = () => zoomAt(1.25, viewer.clientWidth / 2, viewer.clientHeight / 2);
$("zoomout").onclick = () => zoomAt(0.8, viewer.clientWidth / 2, viewer.clientHeight / 2);
window.addEventListener("resize", () => S.page && fit());

let curBoxes = [];
function renderOverlay() {
  overlay.innerHTML = "";
  const rect = (b, c) => {
    const r = document.createElementNS(SVGNS, "rect");
    r.setAttribute("x", b[0] - 3); r.setAttribute("y", b[1] - 3);
    r.setAttribute("width", b[2] - b[0] + 6); r.setAttribute("height", b[3] - b[1] + 6);
    r.setAttribute("rx", 3); r.setAttribute("class", c);
    overlay.appendChild(r);
  };
  const all = $("showall").checked;
  S.diffs.forEach((d, i) => {
    if (isDismissed(d) || !(all || i === S.active)) return;
    d.boxes.forEach((b) => rect(b, i === S.active ? "diff active" : "diff"));
  });
  curBoxes.forEach((b) => rect(b, "cur"));
}

function followCursor() {
  if (!S.tokens.length || !S.tl.length) return;
  const pos = ta.selectionStart;
  let i = S.tokens.findIndex((t) => t.e >= pos);
  if (i < 0) i = S.tokens.length - 1;
  let j = S.mapTL[i];
  if (j < 0) { const [b0] = spanIn(S.mapTL, i, i + 1, S.tl.length); j = Math.min(b0, S.tl.length - 1); }
  curBoxes = S.tl[j] ? S.tl[j].boxes : [];
  renderOverlay();
  if ($("follow").checked && curBoxes.length && document.activeElement === ta) zoomTo(curBoxes, true);
}
document.addEventListener("selectionchange", () => { if (document.activeElement === ta) followCursor(); });

// ---------- editing ----------
function onEdit() {
  if (!S.dirty) { S.dirty = true; $("badge").textContent += " • unsaved"; }
  renderBackdrop();
  scheduleRecompute();
}
ta.addEventListener("input", onEdit);

function wrap(open, close) {
  const a = ta.selectionStart, b = ta.selectionEnd;
  const sel = ta.value.slice(a, b);
  document.execCommand("insertText", false, open + sel + close);
  if (a === b) ta.setSelectionRange(a + open.length, a + open.length);
  onEdit();
}
function insert(s) { ta.focus(); document.execCommand("insertText", false, s); onEdit(); }
for (const ch of PALETTE) {
  const b = document.createElement("button");
  b.textContent = ch; b.title = `insert ${ch} (U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")})`;
  b.onmousedown = (e) => e.preventDefault();
  b.onclick = () => insert(ch);
  $("palette").appendChild(b);
}
for (const [label, o, c, title] of [["{ }", "{", "}", "italic (Ctrl+I)"], ["< >", "<", ">", "letterspaced (Ctrl+U)"], ["^n", "^", "", "footnote reference"]]) {
  const b = document.createElement("button");
  b.textContent = label; b.title = title;
  b.onmousedown = (e) => e.preventDefault();
  b.onclick = () => { ta.focus(); wrap(o, c); };
  $("palette").appendChild(b);
}
for (const r of ["#head", "#body", "#margin", "#foot", "#pageno", "#other"]) {
  const b = document.createElement("button");
  b.textContent = r; b.title = "insert region marker line";
  b.onmousedown = (e) => e.preventDefault();
  b.onclick = () => { ta.focus(); const a = ta.selectionStart; const atStart = a === 0 || ta.value[a - 1] === "\n"; insert((atStart ? "" : "\n") + r + "\n"); };
  $("palette").appendChild(b);
}

document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save("save"); }
  else if (mod && e.key.toLowerCase() === "i" && document.activeElement === ta) { e.preventDefault(); wrap("{", "}"); }
  else if (mod && e.key.toLowerCase() === "u" && document.activeElement === ta) { e.preventDefault(); wrap("<", ">"); }
  else if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); stepDiff(1); }
  else if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); stepDiff(-1); }
  else if (e.altKey && e.key === "PageDown") { e.preventDefault(); openPage(S.pos + 1); }
  else if (e.altKey && e.key === "PageUp") { e.preventDefault(); openPage(S.pos - 1); }
  else if (e.key === "f" && document.activeElement !== ta && !mod) fit();
});

// ---------- buttons ----------
$("pagesel").onchange = (e) => openPage(+e.target.value);
$("prev").onclick = () => openPage(S.pos - 1);
$("next").onclick = () => openPage(S.pos + 1);
$("save").onclick = () => save("save");
$("verify").onclick = async () => {
  const open = S.diffs.filter((d) => !isDismissed(d)).length;
  if (open && !confirm(`${open} disagreement(s) are neither resolved nor dismissed. Mark the page verified anyway?`)) return;
  await save("verify");
};
$("unverify").onclick = () => save("unverify");
$("revert").onclick = async () => {
  if (!confirm("Discard all edits to this page and go back to the engine draft?")) return;
  S.meta = { dismissed: [] };
  await save("revert");
  S.dirty = false;
  openPage(S.pos);
};
$("strict").onchange = recompute;
$("showdismissed").onchange = () => { renderDiffs(); };
$("showall").onchange = renderOverlay;
$("prevdiff").onclick = () => stepDiff(-1);
$("nextdiff").onclick = () => stepDiff(1);
$("helpbtn").onclick = () => $("help").showModal();
window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
setInterval(() => { if (S.dirty) save("save", true); }, 60000); // autosave to gt/wip every minute

(async () => {
  await loadPages();
  const m = location.hash.match(/^#(\w+)\/(\d+)$/);
  const i = m ? S.pages.findIndex((p) => p.vol === m[1] && +p.index === +m[2]) : -1;
  openPage(i >= 0 ? i : Math.max(0, S.pages.findIndex((p) => p.status !== "verified")));
})();
