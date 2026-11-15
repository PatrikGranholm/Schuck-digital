"use strict";
// Schück NER review. Talks to scripts/ner_review.py (served by scripts/gt_server.py).
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const WD = (q) => `https://www.wikidata.org/wiki/${q}`;

const S = {
  tab: "register",
  r: { list: [], pos: -1, detail: null, sel: new Set(), open: new Set(), paras: new Set() },
  q: { groups: [], pos: -1, focus: 0, open: new Set(), paras: new Set() },
  summary: null,
};

async function api(path, opts = {}) {
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function flash(msg, warn = false) {
  const m = $("msg");
  m.textContent = msg;
  m.style.color = warn ? "var(--bad)" : "";
  clearTimeout(flash.t);
  flash.t = setTimeout(() => (m.textContent = ""), 5000);
}
async function guarded(fn) {
  try { return await fn(); } catch (e) { flash(e.message, true); }
}
const dates = (c) => (c && (c.birth || c.death) ? `${c.birth ?? "?"}–${c.death ?? ""}` : "");
const typing = (ev) => /^(INPUT|SELECT|TEXTAREA)$/.test(ev.target.tagName) || ev.target.isContentEditable;

// ================================================================ summary / tabs
async function loadSummary() {
  S.summary = await api("/api/ner/summary");
  const sm = S.summary, p = sm.register.persons || {}, l = sm.register.places || {};
  const vols = Object.entries(sm.vols).map(([v, x]) => `${v}: ${x.guess.open + x.unresolved.open} open`).join(" · ");
  $("progress").textContent =
    `persons ${p.review || 0} to review, ${p.auto || 0} auto · places ${l.review || 0} to review · mentions ${vols}`;
  const sel = $("q-vol");
  if (!sel.options.length) {
    for (const v of sm.volumes) sel.add(new Option(v, v));
    const saved = localGet("q-vol");
    if (saved && sm.volumes.includes(saved)) sel.value = saved;
  }
}
function localGet(k) { try { return localStorage.getItem("ner-" + k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem("ner-" + k, v); } catch { /* private mode */ } }

function showTab(tab) {
  S.tab = tab;
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", b.dataset.tab === tab);
  $("view-register").hidden = tab !== "register";
  $("view-queue").hidden = tab !== "queue";
  localSet("tab", tab);
  if (tab === "queue" && !S.q.groups.length) loadQueue();
  if (tab === "register" && !S.r.list.length) loadEntries();
}

// ================================================================ picker (entry search / new entry)
function pickEntry(title, initial = "") {
  const dlg = $("picker"), q = $("picker-q"), list = $("picker-list");
  $("picker-title").textContent = title;
  q.value = initial;
  $("picker-newkind").value = $("r-kind").value || "persons";
  let results = [], t = null;
  return new Promise((resolve) => {
    const done = (v) => { dlg.close(); cleanup(); resolve(v); };
    const render = () => {
      $("picker-newname").textContent = q.value.trim();
      $("picker-new").disabled = !q.value.trim();
      list.innerHTML = results.map((r, i) =>
        `<li data-i="${i}"><span class="badge ${esc(r.status)}">${r.id.startsWith("pers-") ? "person" : "place"}</span>
         <span>${esc(r.name)} <span class="muted small">${esc(dates(r))} ${esc(r.desc || "")}</span></span><span class="n">${r.n}</span></li>`).join("")
        || `<li class="muted">no match</li>`;
    };
    const search = () => {
      clearTimeout(t);
      t = setTimeout(async () => { results = await api("/api/ner/search?q=" + encodeURIComponent(q.value)); render(); }, 150);
    };
    const onClick = (ev) => { const li = ev.target.closest("li[data-i]"); if (li) done(results[+li.dataset.i].id); };
    const onKey = (ev) => { if (ev.key === "Enter" && results.length) { ev.preventDefault(); done(results[0].id); } };
    const onNew = async () => {
      const r = await guarded(() => api("/api/ner/entry", { method: "POST", body: { kind: $("picker-newkind").value, name: q.value } }));
      if (r) { flash(`new entry ${r.id}`); done(r.id); }
    };
    const onCancel = () => done(null);
    const cleanup = () => {
      q.removeEventListener("input", search); list.removeEventListener("click", onClick);
      q.removeEventListener("keydown", onKey); $("picker-new").removeEventListener("click", onNew);
      $("picker-cancel").removeEventListener("click", onCancel); dlg.removeEventListener("cancel", onCancel);
    };
    q.addEventListener("input", search); list.addEventListener("click", onClick); q.addEventListener("keydown", onKey);
    $("picker-new").addEventListener("click", onNew); $("picker-cancel").addEventListener("click", onCancel);
    dlg.addEventListener("cancel", onCancel);
    render(); search();
    dlg.showModal(); q.focus(); q.select();
  });
}

// ================================================================ mention rendering (shared)
function mentionHTML(m, { checkbox = false, focus = false, openScan = false, openPara = false, here = null } = {}) {
  let state;
  if (m.rejected) state = `<span class="ref rej">not a name</span>`;
  else if (m.decided) state = `<span class="ref">→ ${esc(m.ref_name || m.ref)}</span><span class="badge checked">decided</span>`;
  else if (m.ref && m.cert === "low") state = `<span class="ref guess">guess: ${esc(m.ref_name || m.ref)}</span><span class="muted">${esc(m.how || "")}</span>`;
  else if (m.ref) state = `<span class="ref">${esc(m.ref_name || m.ref)}</span><span class="badge">${esc(m.cert || "")} ${esc(m.how || "")}</span>`;
  else state = `<span class="ref guess">unlinked</span>`;
  if (here && m.ref && m.ref !== here && !m.rejected) state = `<span class="badge review">other entry</span>` + state;
  if (m.added) state += `<span class="badge auto">added</span>`;
  const scan = openScan && m.facs
    ? `<div class="scan"><img alt="scan of ${esc(m.text)}" src="/api/ner/crop?vol=${m.vol}&facs=${m.facs}&text=${encodeURIComponent(m.hit)}&before=${encodeURIComponent(m.pre.slice(-40))}"></div>` : "";
  return `<li class="mention${focus ? " focus" : ""}${m.decided ? " decided" : ""}" data-key="${esc(m.key)}" data-vol="${m.vol}">
    <span>${checkbox ? `<input type="checkbox" class="sel" aria-label="select">` : ""}</span>
    <span class="ctx">${esc(m.pre)}<mark>${esc(m.hit)}</mark>${esc(m.post)}</span>
    <span class="where">${m.vol} · p. ${esc(m.page ?? "–")}${m.kind && m.kind !== "p" ? ` · ${esc(m.kind)}` : ""}<br>
      <button class="small btn-scan" title="scan (Space)">scan</button> <button class="small btn-para" title="paragraph (P)">¶</button></span>
    <span class="state">${state}</span>${scan}${openPara ? `<div class="para" data-sha="${m.sha}">…</div>` : ""}</li>`;
}

async function fillPara(el, vol, sha, curKey) {
  const u = await api(`/api/ner/unit?vol=${vol}&sha=${sha}`);
  let html = "", pos = 0;
  for (const m of u.mentions) {
    if (m.start < pos || m.rejected) continue;
    html += esc(u.text.slice(pos, m.start));
    const cls = (m.type === "placeName" ? "m-plc" : "m-per") + (m.key === curKey ? " m-cur" : "");
    html += `<span class="${cls}" title="${esc(m.ref_name || "unlinked")}">${esc(u.text.slice(m.start, m.end))}</span>`;
    pos = m.end;
  }
  html += esc(u.text.slice(pos));
  el.innerHTML = html + `<div class="row small muted" style="margin-top:6px;font-family:system-ui">
    Select a missed name in the paragraph, then <button class="btn-add" disabled>add as mention…</button></div>`;
  const btn = el.querySelector(".btn-add");
  let sel = null;
  el.addEventListener("mouseup", () => {
    const s = window.getSelection();
    if (!s.rangeCount || s.isCollapsed) { sel = null; btn.disabled = true; return; }
    const r = s.getRangeAt(0);
    if (!el.contains(r.startContainer) || el.querySelector(".row").contains(r.startContainer)) return;
    const pre = document.createRange();
    pre.setStart(el, 0); pre.setEnd(r.startContainer, r.startOffset);
    let start = pre.toString().length, text = r.toString();
    const lead = text.length - text.trimStart().length;
    text = text.trim(); start += lead;
    if (!text || u.text.slice(start, start + text.length) !== text) { sel = null; btn.disabled = true; return; }
    sel = { start, end: start + text.length, text };
    btn.disabled = false;
    btn.textContent = `add “${text}” as mention…`;
  });
  btn.addEventListener("click", async () => {
    if (!sel) return;
    const ref = await pickEntry(`“${sel.text}” is a mention of…`, sel.text);
    if (!ref) return;
    await guarded(async () => {
      await api("/api/ner/add", { method: "POST", body: { vol, sha, start: sel.start, end: sel.end, ref } });
      flash(`added “${sel.text}”`);
      fillPara(el, vol, sha, curKey);
    });
  });
}

// ================================================================ register
async function loadEntries(keepId = null) {
  const kind = $("r-kind").value, status = $("r-status").value, q = $("r-q").value;
  S.r.list = await api(`/api/ner/entries?kind=${kind}&status=${encodeURIComponent(status)}&q=${encodeURIComponent(q)}`);
  $("r-count").textContent = `${S.r.list.length} entries`;
  $("r-list").innerHTML = S.r.list.map((e, i) =>
    `<li data-i="${i}"><span class="badge ${esc(e.status)}">${esc(e.status)}</span><span>${esc(e.name)}</span><span class="n">${e.n}</span></li>`).join("");
  const i = keepId ? S.r.list.findIndex((e) => e.id === keepId) : -1;
  if (i >= 0) markEntry(i);
  else if (S.r.list.length) openEntry(0);
  else { S.r.pos = -1; $("r-detail").innerHTML = `<p class="muted pad">No entries.</p>`; }
}
function markEntry(i) {
  S.r.pos = i;
  for (const li of $("r-list").children) li.classList.toggle("active", +li.dataset.i === i);
  $("r-list").children[i]?.scrollIntoView({ block: "nearest" });
}
async function openEntry(i) {
  if (i < 0 || i >= S.r.list.length) return;
  markEntry(i);
  S.r.sel.clear();
  S.r.open = new Set(); S.r.paras = new Set();
  renderEntry(await api("/api/ner/entry?id=" + encodeURIComponent(S.r.list[i].id)));
}
function renderEntry(d) {
  S.r.detail = d;
  const e = d.entry, chosen = e.wikidata;
  const counts = Object.entries(d.counts).map(([v, n]) => `${v}: ${n}`).join(" · ") || "no mentions";
  const cands = (e.candidates || []).map((c, i) => `
    <div class="cand${c.qid === chosen ? " chosen" : ""}" data-qid="${c.qid}">
      <span class="k">${i < 9 ? i + 1 : ""}</span>
      <span><b>${esc(c.label)}</b> ${esc(dates(c))}<div class="desc">${esc(c.desc || "")}</div></span>
      <span class="meta">${c.score != null ? `score ${c.score}` : "manual"}<br>
        <a href="${WD(c.qid)}" target="_blank" rel="noopener">${c.qid}</a>
        ${c.sbl ? ` · <a href="https://sok.riksarkivet.se/sbl/Presentation.aspx?id=${esc(c.sbl)}" target="_blank" rel="noopener">SBL</a>` : ""}</span>
    </div>`).join("") || `<p class="muted">No Wikidata candidates.</p>`;
  const merged = e.status === "merged"
    ? `<p>Merged into <a href="#" data-goto="${esc(e.merged_into)}">${esc(e.merged_into)}</a>.</p>` : "";
  const mergedHere = d.merged_here.length ? `<p class="muted small">Merged into this entry: ${d.merged_here.map(esc).join(", ")}</p>` : "";
  $("r-detail").innerHTML = `
    <h2>${esc(e.name)} <span class="badge ${esc(e.status)}">${esc(e.status)}</span>${e.manual ? `<span class="badge">manual</span>` : ""}
      <button id="rename" class="small">rename</button></h2>
    <div class="muted">${esc(e.id)} · ${counts}${e.context_year ? ` · context year ${e.context_year}` : ""}</div>
    ${merged}${mergedHere}
    <h3>Forms</h3><div class="forms">${Object.entries(e.forms || {}).map(([f, n]) => `<span>${esc(f)} <span class="muted">${n}</span></span>`).join("") || `<span class="muted">none</span>`}</div>
    <h3>Wikidata</h3><div class="cands">${cands}</div>
    <div class="actions">
      ${e.status === "auto" ? `<button id="confirm" class="primary" title="Enter">Confirm match ↵</button>` : ""}
      <button id="none" title="0">No match (0)</button>
      <button id="reject" class="danger" title="X">Not a ${e.id.startsWith("pers-") ? "person" : "place"} (X)</button>
      <button id="merge">Merge into…</button>
      ${["checked", "rejected", "merged"].includes(e.status) ? `<button id="reopen">Reopen</button>` : ""}
    </div>
    <div class="actions">
      <input id="research" type="search" placeholder="search Wikidata (S)" value="${esc(e.name)}">
      <button id="research-go">Search</button>
      <input id="qid" type="text" placeholder="Q12345" size="9">
      <button id="qid-go">Use QID</button>
    </div>
    <h3>Mentions <span class="muted">(${d.n_mentions}${d.n_mentions > d.mentions.length ? `, first ${d.mentions.length} shown` : ""})</span></h3>
    <div class="actions small">
      <button id="sel-all">select all</button>
      <button id="sel-move" disabled>move selected to…</button>
      <button id="sel-reject" disabled>selected: not a name</button>
      <button id="sel-undo" disabled>selected: undo decision</button>
    </div>
    <ol class="mentions" id="r-mentions">${d.mentions.map((m) => mentionHTML(m, {
      checkbox: true, here: e.id, openScan: S.r.open.has(m.key), openPara: S.r.paras.has(m.key) })).join("")}</ol>`;
  for (const el of $("r-detail").querySelectorAll(".para")) {
    const li = el.closest(".mention");
    fillPara(el, li.dataset.vol, el.dataset.sha, li.dataset.key);
  }
  for (const li of $("r-detail").querySelectorAll(".mention"))
    li.querySelector(".sel").checked = S.r.sel.has(li.dataset.key);
  updateSel();
}
function updateSel() {
  const n = S.r.sel.size;
  for (const id of ["sel-move", "sel-reject", "sel-undo"]) if ($(id)) $(id).disabled = !n;
  if ($("sel-move")) $("sel-move").textContent = n ? `move ${n} selected to…` : "move selected to…";
}
async function entryAction(body, advance = true) {
  const id = S.r.detail.entry.id;
  const d = await guarded(() => api("/api/ner/entry", { method: "PUT", body: { id, ...body } }));
  if (!d) return;
  flash(`${d.entry.name}: ${d.entry.status}${d.entry.wikidata ? " " + d.entry.wikidata : ""}`);
  const li = $("r-list").children[S.r.pos];
  if (li) li.querySelector(".badge").outerHTML = `<span class="badge ${esc(d.entry.status)}">${esc(d.entry.status)}</span>`;
  S.r.list[S.r.pos].status = d.entry.status;
  loadSummary();
  if (advance && d.entry.status !== "review") openEntry(S.r.pos + 1);
  else renderEntry(d);
}
function selectedByVol() {
  const by = {};
  for (const li of $("r-detail").querySelectorAll(".mention")) {
    if (S.r.sel.has(li.dataset.key)) (by[li.dataset.vol] ||= []).push(li.dataset.key);
  }
  return by;
}
async function decideSelected(body) {
  const by = selectedByVol();
  for (const [vol, keys] of Object.entries(by))
    await api("/api/ner/decide", { method: "PUT", body: { vol, keys, ...body } });
  const n = Object.values(by).flat().length;
  flash(`${n} mention(s) updated; rerun link.py to apply`);
  S.r.sel.clear();
  renderEntry(await api("/api/ner/entry?id=" + encodeURIComponent(S.r.detail.entry.id)));
  loadSummary();
}

$("r-list").addEventListener("click", (ev) => { const li = ev.target.closest("li[data-i]"); if (li) openEntry(+li.dataset.i); });
for (const id of ["r-kind", "r-status"]) $(id).addEventListener("change", () => { localSet(id, $(id).value); loadEntries(); });
$("r-q").addEventListener("input", () => { clearTimeout(loadEntries.t); loadEntries.t = setTimeout(loadEntries, 250); });
$("r-detail").addEventListener("click", async (ev) => {
  const t = ev.target, d = S.r.detail;
  if (!d) return;
  const cand = t.closest(".cand");
  if (cand && !t.closest("a")) return entryAction({ action: "choose", qid: cand.dataset.qid });
  const go = t.closest("[data-goto]");
  if (go) {
    ev.preventDefault();
    renderEntry(await api("/api/ner/entry?id=" + encodeURIComponent(go.dataset.goto)));
    return;
  }
  const li = t.closest(".mention");
  if (li && t.classList.contains("sel")) {
    t.checked ? S.r.sel.add(li.dataset.key) : S.r.sel.delete(li.dataset.key);
    return updateSel();
  }
  if (li && t.classList.contains("btn-scan")) {
    S.r.open.has(li.dataset.key) ? S.r.open.delete(li.dataset.key) : S.r.open.add(li.dataset.key);
    return renderEntry(d);
  }
  if (li && t.classList.contains("btn-para")) {
    S.r.paras.has(li.dataset.key) ? S.r.paras.delete(li.dataset.key) : S.r.paras.add(li.dataset.key);
    return renderEntry(d);
  }
  switch (t.id) {
    case "confirm": return entryAction({ action: "confirm" });
    case "none": return entryAction({ action: "none" });
    case "reject": return entryAction({ action: "reject" });
    case "reopen": return entryAction({ action: "reopen" }, false);
    case "rename": {
      const name = prompt("Name of the entry", d.entry.name);
      if (name) return entryAction({ action: "rename", name }, false);
      return;
    }
    case "merge": {
      const into = await pickEntry(`Merge “${d.entry.name}” into…`, d.entry.surname || d.entry.name);
      if (into) return entryAction({ action: "merge", into });
      return;
    }
    case "research-go": flash("searching Wikidata…"); return entryAction({ action: "research", query: $("research").value }, false);
    case "qid-go": return entryAction({ action: "choose", qid: $("qid").value.trim() });
    case "sel-all":
      for (const m of d.mentions) S.r.sel.add(m.key);
      return renderEntry(d);
    case "sel-move": {
      const ref = await pickEntry(`Move ${S.r.sel.size} mention(s) to…`, "");
      if (ref) return guarded(() => decideSelected({ ref }));
      return;
    }
    case "sel-reject": return guarded(() => decideSelected({ reject: true }));
    case "sel-undo": return guarded(() => decideSelected({ clear: true }));
  }
});
$("r-detail").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && ev.target.id === "research") { ev.preventDefault(); $("research-go").click(); }
  if (ev.key === "Enter" && ev.target.id === "qid") { ev.preventDefault(); $("qid-go").click(); }
});

function registerKeys(ev) {
  const d = S.r.detail;
  const k = ev.key.toLowerCase();
  if (k === "j") return openEntry(S.r.pos + 1), true;
  if (k === "k") return openEntry(S.r.pos - 1), true;
  if (k === "/") return $("r-q").focus(), true;
  if (!d) return false;
  if (k === "s") return $("research").focus(), $("research").select(), true;
  if (/^[1-9]$/.test(k)) {
    const c = (d.entry.candidates || [])[+k - 1];
    if (c) entryAction({ action: "choose", qid: c.qid });
    return true;
  }
  if (k === "0") return entryAction({ action: "none" }), true;
  if (k === "x") return entryAction({ action: "reject" }), true;
  if (k === "enter" && d.entry.status === "auto") return entryAction({ action: "confirm" }), true;
  return false;
}

// ================================================================ mention queue
async function loadQueue(keepGroup = null) {
  const vol = $("q-vol").value, kind = $("q-kind").value;
  if (!vol) return;
  S.q.groups = await api(`/api/ner/queue?vol=${vol}&kind=${kind}`);
  renderQueueList();
  const show = visibleGroups();
  const i = keepGroup ? show.findIndex((g) => g.id === keepGroup) : -1;
  if (i >= 0) openGroup(i, true);
  else if (show.length) openGroup(0);
  else { S.q.pos = -1; $("q-detail").innerHTML = `<p class="muted pad">Nothing in this queue.</p>`; }
}
const visibleGroups = () => S.q.groups.filter((g) => g.open || $("q-done").checked);
function renderQueueList() {
  const show = visibleGroups();
  const open = S.q.groups.reduce((n, g) => n + g.open, 0), all = S.q.groups.reduce((n, g) => n + g.n, 0);
  $("q-count").textContent = `${open} open of ${all} mentions in ${S.q.groups.length} groups`;
  $("q-list").innerHTML = show.map((g, i) =>
    `<li data-i="${i}" class="${g.open ? "" : "done"}"><span>${esc(g.label)}</span><span class="muted small">ch. ${esc(g.chapter || "–")}</span>
     <span class="n">${g.open}/${g.n}</span></li>`).join("");
}
function openGroup(i, keepFocus = false) {
  const show = visibleGroups();
  if (i < 0 || i >= show.length) return;
  S.q.pos = i;
  for (const li of $("q-list").children) li.classList.toggle("active", +li.dataset.i === i);
  $("q-list").children[i]?.scrollIntoView({ block: "nearest" });
  const g = show[i];
  if (!keepFocus) {
    const f = g.mentions.findIndex((m) => !m.decided);
    S.q.focus = f >= 0 ? f : 0;
  }
  S.q.focus = Math.min(S.q.focus, g.mentions.length - 1);
  renderGroup();
}
function curGroup() { return visibleGroups()[S.q.pos]; }
function renderGroup() {
  const g = curGroup();
  if (!g) return;
  const cb = g.candidates.map((c, i) =>
    `<button data-ref="${esc(c.id)}" title="${i + 1} this mention · Shift+${i + 1} all open"><span class="k">${i + 1}</span>
      ${esc(c.name)} <span class="muted small">${esc(dates(c))} ${esc((c.desc || "").slice(0, 40))}</span></button>`).join("");
  const allBtns = g.candidates.map((c, i) => `<button data-all="${esc(c.id)}" class="small">all open → ${esc(c.name)}</button>`).join("");
  $("q-detail").innerHTML = `
    <div class="qhead">
      <h2>${esc(g.label)} <span class="muted small">chapter ${esc(g.chapter || "–")} · ${g.open} open of ${g.n}</span></h2>
      <div class="cbtns">${cb}<button id="q-other">other entry…</button><button id="q-reject" class="danger">not a name (X)</button></div>
      <div class="actions"><button id="q-accept-all" class="small" title="Shift+Enter">accept all open guesses</button>${allBtns}</div>
    </div>
    <ol class="mentions" id="q-mentions">${g.mentions.map((m, j) => mentionHTML(m, {
      focus: j === S.q.focus, openScan: S.q.open.has(m.key), openPara: S.q.paras.has(m.key) })).join("")}</ol>`;
  for (const el of $("q-detail").querySelectorAll(".para")) {
    const li = el.closest(".mention");
    fillPara(el, li.dataset.vol, el.dataset.sha, li.dataset.key);
  }
  $("q-mentions").children[S.q.focus]?.scrollIntoView({ block: "nearest" });
}
async function decideQueue(keys, body, msg) {
  const vol = $("q-vol").value;
  await guarded(async () => {
    await api("/api/ner/decide", { method: "PUT", body: { vol, keys, ...body } });
    flash(msg || `${keys.length} decided`);
    const g = curGroup();
    const next = g.mentions.findIndex((m, j) => j > S.q.focus && !m.decided && !keys.includes(m.key));
    if (next >= 0) S.q.focus = next;
    await loadQueue(g.id);
    loadSummary();
  });
}
const focused = () => curGroup()?.mentions[S.q.focus];
const openKeys = () => curGroup().mentions.filter((m) => !m.decided).map((m) => m.key);

$("q-list").addEventListener("click", (ev) => { const li = ev.target.closest("li[data-i]"); if (li) openGroup(+li.dataset.i); });
$("q-vol").addEventListener("change", () => { localSet("q-vol", $("q-vol").value); loadQueue(); });
$("q-kind").addEventListener("change", () => loadQueue());
$("q-done").addEventListener("change", () => { renderQueueList(); openGroup(0); });
$("q-detail").addEventListener("click", async (ev) => {
  const t = ev.target, g = curGroup();
  if (!g) return;
  const li = t.closest(".mention");
  if (li) {
    S.q.focus = g.mentions.findIndex((m) => m.key === li.dataset.key);
    if (t.classList.contains("btn-scan")) S.q.open.has(li.dataset.key) ? S.q.open.delete(li.dataset.key) : S.q.open.add(li.dataset.key);
    if (t.classList.contains("btn-para")) S.q.paras.has(li.dataset.key) ? S.q.paras.delete(li.dataset.key) : S.q.paras.add(li.dataset.key);
    if (t.closest(".para")) return;            // selecting text for "add mention"
    return renderGroup();
  }
  const ref = t.closest("[data-ref]")?.dataset.ref;
  const m = focused();
  if (ref && m) return decideQueue([m.key], { ref });
  const all = t.closest("[data-all]")?.dataset.all;
  if (all) return decideQueue(openKeys(), { ref: all }, `${openKeys().length} → ${all}`);
  if (t.id === "q-reject" && m) return decideQueue([m.key], { reject: true });
  if (t.id === "q-accept-all") return acceptAllGuesses();
  if (t.id === "q-other" && m) {
    const r = await pickEntry(`“${m.text}” is…`, m.text);
    if (r) decideQueue([m.key], { ref: r });
  }
});
async function acceptAllGuesses() {
  const g = curGroup(), vol = $("q-vol").value;
  const by = {};
  for (const m of g.mentions) if (!m.decided && m.ref) (by[m.ref] ||= []).push(m.key);
  await guarded(async () => {
    for (const [ref, keys] of Object.entries(by)) await api("/api/ner/decide", { method: "PUT", body: { vol, keys, ref } });
    flash(`${Object.values(by).flat().length} guesses accepted`);
    await loadQueue(g.id);
    loadSummary();
  });
}
function queueKeys(ev) {
  const g = curGroup();
  if (!g) return false;
  const m = focused();
  const k = ev.key;
  if (k === "ArrowDown" || k === "j") { S.q.focus = Math.min(g.mentions.length - 1, S.q.focus + 1); renderGroup(); return true; }
  if (k === "ArrowUp" || k === "k") { S.q.focus = Math.max(0, S.q.focus - 1); renderGroup(); return true; }
  if (k === "PageDown" || k === "n") { openGroup(S.q.pos + 1); return true; }
  if (k === "PageUp") { openGroup(S.q.pos - 1); return true; }
  if (k === "Enter" && ev.shiftKey) { acceptAllGuesses(); return true; }
  if (k === "Enter" && m && !m.decided && m.ref) { decideQueue([m.key], { ref: m.ref }); return true; }
  const digit = ev.code?.match(/^Digit([1-9])$/);
  if (digit) {
    const c = g.candidates[+digit[1] - 1];
    if (!c) return true;
    if (ev.shiftKey) decideQueue(openKeys(), { ref: c.id }, `${openKeys().length} → ${c.name}`);
    else if (m) decideQueue([m.key], { ref: c.id });
    return true;
  }
  if ((k === "x" || k === "X") && m) { decideQueue([m.key], { reject: true }); return true; }
  if ((k === "u" || k === "U") && m && m.decided) { decideQueue([m.key], { clear: true }, "decision undone"); return true; }
  if (k === " " && m) { S.q.open.has(m.key) ? S.q.open.delete(m.key) : S.q.open.add(m.key); renderGroup(); return true; }
  if ((k === "p" || k === "P") && m) { S.q.paras.has(m.key) ? S.q.paras.delete(m.key) : S.q.paras.add(m.key); renderGroup(); return true; }
  return false;
}

// ================================================================ global
document.addEventListener("keydown", (ev) => {
  if (document.querySelector("dialog[open]")) return;
  if (typing(ev)) { if (ev.key === "Escape") ev.target.blur(); return; }
  if (ev.ctrlKey || ev.metaKey) return;
  if (ev.key === "?") { $("help").showModal(); ev.preventDefault(); return; }
  const used = S.tab === "register" ? registerKeys(ev) : queueKeys(ev);
  if (used) ev.preventDefault();
});
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
$("helpbtn").addEventListener("click", () => $("help").showModal());

(async () => {
  for (const id of ["r-kind", "r-status"]) { const v = localGet(id); if (v) $(id).value = v; }
  await guarded(loadSummary);
  showTab(localGet("tab") === "queue" ? "queue" : "register");
})();
