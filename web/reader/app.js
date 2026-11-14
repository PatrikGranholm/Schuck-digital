"use strict";
// Schück digital: static reader for the TEI volumes in data/ (built by scripts/build_site.py).
const TEI = "http://www.tei-c.org/ns/1.0";
const $ = (id) => document.getElementById(id);
const S = { vols: [], cache: {}, cur: null, osd: null, page: -1, view: "read", reg: { persons: [], places: [] }, entry: {} };

// ---------------------------------------------------------------- helpers
function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
const kids = (n) => Array.from(n.childNodes).filter((c) => c.nodeType === 1);
const tei = (doc, name) => Array.from(doc.getElementsByTagNameNS(TEI, name));
const norm = (s) => s.replace(/\s+/g, " ").trim();
const titleCase = (s) => (s === s.toUpperCase() ? s[0] + s.slice(1).toLowerCase() : s).replace(/\.$/, "");
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
function partLabel(m) { return `Del ${m.part} (${m.year})`; }

// TEI text without notes, running heads and printed readings (for search and lists)
function plain(node, noQuotes = false) {
  let s = "";
  for (const c of node.childNodes) {
    if (c.nodeType === 3) s += c.nodeValue;
    else if (c.nodeType === 1 && noQuotes && c.localName === "quote") s += " ";
    else if (c.nodeType === 1 && !["note", "fw", "sic", "pb", "rdg"].includes(c.localName)) s += plain(c, noQuotes);
  }
  return s;
}

// ---------------------------------------------------------------- loading + rendering
async function loadVol(vol) {
  if (S.cache[vol]) return S.cache[vol];
  const meta = S.vols.find((v) => v.vol === vol);
  const xml = await (await fetch(meta.file)).text();
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  const V = { meta, doc, surfaces: {}, pages: [], quotes: [], sources: [], paras: [], notes: [], names: {}, nameN: 0 };
  for (const s of tei(doc, "surface")) {
    const g = s.getElementsByTagNameNS(TEI, "graphic")[0];
    V.surfaces[s.getAttribute("xml:id")] = { url: g.getAttribute("url"), n: s.getAttribute("n") };
  }
  V.root = h("div", "volume");
  const ctx = { V, page: null, chapter: "", p: 0 };
  const text = tei(doc, "text")[0];
  for (const part of kids(text)) {
    const sec = h("section", part.localName === "back" ? "backmatter" : part.localName);
    renderKids(part, sec, ctx);
    V.root.appendChild(sec);
  }
  // quotations: one entry per chain (first part), with the whole chain's text
  const byId = {};
  for (const q of tei(doc, "quote")) byId[q.getAttribute("xml:id")] = q;
  for (const q of V.quotes) {
    let n = byId[q.id], t = "";
    while (n) { t += " " + plain(n); const nx = n.getAttribute("next"); n = nx ? byId[nx.slice(1)] : null; }
    q.text = norm(t);
  }
  S.cache[vol] = V;
  return V;
}

function renderKids(node, parent, ctx) { for (const c of node.childNodes) render(c, parent, ctx); }

function render(node, parent, ctx) {
  if (node.nodeType === 3) { parent.appendChild(document.createTextNode(node.nodeValue)); return; }
  if (node.nodeType !== 1) return;
  const a = (k) => node.getAttribute(k);
  const V = ctx.V;
  let e;
  switch (node.localName) {
    case "div": {
      const t = a("type");
      e = h("section", t === "chapter" ? "chapter" : t || "div");
      if (t === "chapter") e.id = `ch${a("n")}`;
      if (t === "corrigenda") e.id = "corrigenda";
      if (t === "contents") e.id = "contents";
      renderKids(node, e, ctx);
      break;
    }
    case "head": {
      if (parent.tagName === "FIGURE") { e = h("figcaption"); renderKids(node, e, ctx); break; }
      e = h("h2");
      renderKids(node, e, ctx);
      // headings printed in capitals: use the table-of-contents title (proper case)
      const div = node.parentNode, ch = div.getAttribute("type") === "chapter" ? V.meta.chapters.find((c) => c.n === div.getAttribute("n")) : null;
      ctx.chapter = ch ? ch.title : titleCase(norm(e.textContent));
      if (e.textContent === e.textContent.toUpperCase()) e.textContent = ctx.chapter;
      break;
    }
    case "titlePage": e = h("div", "titlepage"); renderKids(node, e, ctx); break;
    case "titlePart": e = h("div", "titlepart"); renderKids(node, e, ctx); break;
    case "p": case "ab": {
      const t = a("type");
      if (t === "heading") e = h("h3");
      else if (t === "figText" || t === "plateText") e = h("div", "figtext");
      else e = h("p", node.localName === "ab" ? "ab" : "");
      if (node.localName === "p") {
        e.id = `p${ctx.p++}`;
        V.paras.push({ id: e.id, page: ctx.page, chapter: ctx.chapter, text: norm(plain(node)),
          textNQ: norm(plain(node, true)) });   // without quotations, for "utanför citat"
      }
      renderKids(node, e, ctx);
      break;
    }
    case "pb": {
      const facs = (a("facs") || "").slice(1);
      const n = a("n");
      const resp = (a("resp") || "").slice(1);
      ctx.page = n;
      e = h("span", `pb ${resp}${a("break") === "no" ? " nobreak" : ""}`, n ? `s. ${n}` : "bild");
      e.dataset.i = V.pages.length;
      e.title = { gt: "Granskad text", dots: "OCR (dots.mocr), ej korrekturläst", textlayer: "PDF-textlager, ej korrekturläst" }[resp] || "";
      V.pages.push({ facs, n, resp, el: e });
      break;
    }
    case "fw": case "sic": case "rdg": return;
    case "app": {   // old-spelling rule: lem = text layer + Kraken, rdg = dots.mocr
      const rdg = node.getElementsByTagNameNS(TEI, "rdg")[0];
      e = h("span", "app");
      e.title = `Gammal stavning enligt PDF-textlagret och Kraken; OCR (dots.mocr) läste: ${rdg ? norm(rdg.textContent) : ""}`;
      renderKids(node, e, ctx);
      break;
    }
    case "note": {
      if (a("place") === "margin") { e = h("span", "sidenote"); renderKids(node, e, ctx); break; }
      const body = h("div"), k = V.notes.length;
      V.notes.push({ n: a("n"), body, page: ctx.page, low: a("cert") === "low" });
      ctx.note = k;
      renderKids(node, body, ctx);
      ctx.note = undefined;
      e = h("button", `fnref${a("cert") === "low" ? " low" : ""}`, a("n") || "*");
      e.dataset.note = k;
      e.id = `fnref${k}`;
      e.title = "Fotnot";
      break;
    }
    case "quote": {
      e = h("span", "quote");
      const id = a("xml:id");
      if (id) e.id = id;
      if (id && !a("prev")) V.quotes.push({ id, page: ctx.page, chapter: ctx.chapter });
      renderKids(node, e, ctx);
      break;
    }
    case "hi": e = h(a("rend") === "italic" ? "em" : "span", a("rend") === "spaced" ? "spaced" : ""); renderKids(node, e, ctx); break;
    case "bibl": {
      e = h("span", "source");
      e.id = `src${V.sources.length}`;
      renderKids(node, e, ctx);
      const org = node.getElementsByTagNameNS(TEI, "orgName")[0];
      V.sources.push({ id: e.id, org: org ? norm(org.textContent) : "", text: norm(plain(node)), page: ctx.page, chapter: ctx.chapter });
      e.title = "Källhänvisning";
      break;
    }
    case "idno": e = h("span", "shelfmark"); renderKids(node, e, ctx); break;
    case "persName": case "placeName": {
      const ref = (a("ref") || "").split("#").pop();
      e = h("span", `name ${node.localName === "persName" ? "per" : "plc"}${a("cert") === "low" ? " low" : ""}`);
      e.id = `nm${V.nameN++}`;
      if (ref) {
        e.dataset.ref = ref;
        (V.names[ref] = V.names[ref] || []).push({ id: e.id, page: ctx.page, chapter: ctx.chapter, low: a("cert") === "low",
          note: ctx.note });
      }
      renderKids(node, e, ctx);
      break;
    }
    case "choice": {
      const sic = node.getElementsByTagNameNS(TEI, "sic")[0];
      e = h("span", "choice");
      e.title = `Rättat enligt Rättelser. Trycket har: ${sic ? norm(sic.textContent) : ""}`;
      renderKids(node, e, ctx);
      break;
    }
    case "figure": {
      e = h("figure", a("type") === "plate" ? "plate" : "fig");
      renderKids(node, e, ctx);
      if (!e.textContent.trim()) e.appendChild(h("div", "muted", a("type") === "plate" ? "Plansch" : "Bild"));
      const facs = (a("facs") || "").slice(1);
      if (facs) {
        const b = h("button", "ghost", "Visa i faksimil");
        b.dataset.facs = facs;
        e.appendChild(b);
      }
      break;
    }
    case "table": e = h("table"); renderKids(node, e, ctx); break;
    case "row": e = h("tr"); renderKids(node, e, ctx); break;
    case "cell": e = h("td"); renderKids(node, e, ctx); break;
    case "lb": e = h("br"); break;
    default: renderKids(node, parent, ctx); return;
  }
  parent.appendChild(e);
}

// ---------------------------------------------------------------- reading view
async function openVol(vol) {
  if (S.cur && S.cur.meta.vol === vol) return S.cur;
  $("text").innerHTML = '<p class="loading">Laddar…</p>';
  const V = await loadVol(vol);
  S.cur = V;
  S.page = -1;
  $("volsel").value = vol;
  const text = $("text");
  text.innerHTML = "";
  text.appendChild(V.root);
  const toc = $("toc");
  toc.innerHTML = '<option value="">Innehåll…</option>';
  const add = (label, val) => { const o = h("option", "", label); o.value = val; toc.appendChild(o); };
  if (V.root.querySelector(".titlepage")) add("Titelblad", `${vol}/top`);
  for (const c of V.meta.chapters) add(`${c.n}. ${c.title}${c.page ? ` (s. ${c.page})` : ""}`, `${vol}/ch${c.n}`);
  if (V.root.querySelector("#contents")) add("Innehållsförteckning", `${vol}/contents`);
  if (V.root.querySelector("#corrigenda")) add("Rättelser", `${vol}/corrigenda`);
  S.qRendered = S.sRendered = false;
  const ep = $("epublink");
  ep.hidden = !V.meta.epub;
  if (V.meta.epub) { ep.href = V.meta.epub; ep.title = `Ladda ner ${partLabel(V.meta)} som e-bok (EPUB, ${V.meta.epub_mb} MB)`; }
  onScroll();
  return V;
}

function goTo(target, flash = true) {
  const V = S.cur;
  let el = null;
  if (target === "top") el = $("text").firstElementChild;
  else if (/^s\d+$/.test(target)) el = (V.pages.find((p) => p.n === target.slice(1)) || {}).el;
  else el = document.getElementById(target);
  if (!el) return;
  setView("read");
  // pages, chapters and front/back sections open at their start; a paragraph or quotation is centred
  const big = el.classList.contains("pb") || ["SECTION", "DIV", "H2"].includes(el.tagName);
  el.scrollIntoView({ block: big ? "start" : "center" });
  if (el.classList.contains("pb")) showPage(+el.dataset.i);
  const fl = el.tagName === "SECTION" ? el.querySelector("h2") || null : el.classList.contains("pb") ? null : el;
  if (flash && fl) { fl.classList.remove("flash"); void fl.offsetWidth; fl.classList.add("flash"); }
  if (el.classList.contains("fnref")) setTimeout(() => el.click(), 300);   // a mention in a footnote: open the note
}

// current page = last page marker above the top of the reading pane
let scrollTimer;
function onScroll() {
  clearTimeout(scrollTimer);
  scrollTimer = setTimeout(() => {
    const V = S.cur;
    if (!V || !V.pages.length || !$("follow").checked) return;
    const top = document.querySelector(".reader").getBoundingClientRect().top + 90;
    let lo = 0, hi = V.pages.length - 1, best = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (V.pages[mid].el.getBoundingClientRect().top <= top) { best = mid; lo = mid + 1; } else hi = mid - 1;
    }
    showPage(best);
  }, 80);
}

function showPage(i) {
  const V = S.cur;
  if (!V || i < 0 || i >= V.pages.length) return;
  if (S.page >= 0 && V.pages[S.page]) V.pages[S.page].el.classList.remove("current");
  S.page = i;
  const p = V.pages[i];
  p.el.classList.add("current");
  const surf = V.surfaces[p.facs];
  const img = p.facs.split("-").pop().replace(/^0+/, "");
  $("pglabel").textContent = `${p.n ? "s. " + p.n : "opaginerad"} · bild ${img}`;
  $("pgsource").textContent = { gt: "Texten på denna sida är granskad mot faksimilet.",
    dots: "Texten på denna sida är okorrigerad OCR.",
    textlayer: "Texten på denna sida kommer från PDF-filens textlager (okorrigerad)." }[p.resp] || "";
  if (!surf || document.body.classList.contains("nofacs")) return;
  if (!S.osd && window.OpenSeadragon) {
    S.osd = OpenSeadragon({
      element: $("osd"), prefixUrl: "https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/images/",
      showNavigator: false, gestureSettingsMouse: { clickToZoom: false }, visibilityRatio: 0.9, minZoomImageRatio: 0.8,
      showFullPageControl: false,
    });
  }
  if (S.osd && S.osdUrl !== surf.url) {
    S.osdUrl = surf.url;
    S.osd.open({ type: "image", url: surf.url });
  }
}

// ---------------------------------------------------------------- popovers (footnotes)
function showPopover(anchor, content, foot) {
  const pop = $("popover");
  pop.innerHTML = "";
  pop.appendChild(content);
  if (foot) pop.appendChild(h("div", "muted", foot));
  pop.hidden = false;
  const r = anchor.getBoundingClientRect(), w = Math.min(420, innerWidth * 0.9);
  pop.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.left - 40))}px`;
  pop.style.top = `${r.bottom + 8 + 200 > innerHeight ? Math.max(8, r.top - 8 - pop.offsetHeight) : r.bottom + 8}px`;
}
document.addEventListener("click", (e) => {
  const fn = e.target.closest(".fnref");
  if (fn) {
    const n = S.cur.notes[+fn.dataset.note];
    showPopover(fn, n.body.cloneNode(true), `Fotnot ${n.n || ""}${n.page ? " · s. " + n.page : ""}${n.low ? " · placeringen osäker" : ""}`);
    e.stopPropagation();
    return;
  }
  const nm = e.target.closest(".name[data-ref]");
  if (nm && !e.target.closest(".fnref")) {
    showPopover(nm, entryCard(nm.dataset.ref, nm.classList.contains("low")));
    e.stopPropagation();
    return;
  }
  const pb = e.target.closest(".pb");
  if (pb && S.cur) { document.body.classList.remove("nofacs"); showPage(+pb.dataset.i); return; }
  const fb = e.target.closest("figure button[data-facs]");
  if (fb) { const i = S.cur.pages.findIndex((p) => p.facs === fb.dataset.facs); if (i >= 0) { document.body.classList.remove("nofacs"); showPage(i); } return; }
  if (!e.target.closest("#popover")) $("popover").hidden = true;
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("popover").hidden = true; });

// ---------------------------------------------------------------- views
function setView(v) {
  S.view = v;
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("on", b.dataset.view === v);
  for (const s of document.querySelectorAll(".view")) s.classList.toggle("on", s.id === `view-${v}`);
  if (v === "quotes" && !S.qRendered) renderQuotes();
  if (v === "sources" && !S.sRendered) renderSources();
  if (v === "about") renderAbout();
  if (v === "persons") renderEntries("persons");
  if (v === "places") renderEntries("places");
}

function li(where, html, target) {
  const e = h("li");
  e.appendChild(h("span", "where", where));
  const t = h("span");
  t.innerHTML = html;
  e.appendChild(t);
  e.onclick = () => { location.hash = target; };
  return e;
}
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
const where = (x) => `${x.page ? "s. " + x.page : ""}${x.chapter ? " · " + x.chapter : ""}`;

function renderQuotes() {
  S.qRendered = true;
  const f = $("qfilter").value.toLowerCase(), V = S.cur, list = $("qlist");
  list.innerHTML = "";
  const hits = V.quotes.filter((q) => !f || q.text.toLowerCase().includes(f));
  $("qcount").textContent = `${hits.length} av ${V.quotes.length} citat i ${partLabel(V.meta)}`;
  for (const q of hits.slice(0, 1000)) {
    const t = q.text.length > 320 ? q.text.slice(0, 320) + " …" : q.text;
    list.appendChild(li(where(q), esc(t), `${V.meta.vol}/${q.id}`));
  }
}

const REPOS = [[/^(R\. ?A\.|Riksarkiv)/, "Riksarkivet (R. A.)"], [/^(K\. ?B\.|Kungl\. ?bibl)/, "Kungl. biblioteket (K. B.)"],
  [/^(U\. ?U\. ?B\.|Ups)/, "Uppsala universitetsbibliotek (U. U. B.)"]];
function renderSources() {
  S.sRendered = true;
  const f = $("sfilter").value.toLowerCase(), V = S.cur, box = $("slist");
  box.innerHTML = "";
  const hits = V.sources.filter((s) => !f || s.text.toLowerCase().includes(f));
  $("scount").textContent = `${hits.length} av ${V.sources.length} källhänvisningar i ${partLabel(V.meta)}`;
  const groups = {};
  for (const s of hits) {
    const g = (REPOS.find(([re]) => re.test(s.org)) || [null, s.org || "Övriga"])[1];
    (groups[g] = groups[g] || []).push(s);
  }
  for (const [g, items] of Object.entries(groups).sort((a, b) => b[1].length - a[1].length)) {
    const d = h("details");
    d.open = !!f || Object.keys(groups).length < 4;
    const sum = h("summary", "", g + " ");
    sum.appendChild(h("span", "muted", `(${items.length})`));
    d.appendChild(sum);
    const ol = h("ol", "items");
    for (const s of items) ol.appendChild(li(where(s), esc(s.text), `${V.meta.vol}/${s.id}`));
    d.appendChild(ol);
    box.appendChild(d);
  }
}

// historical-spelling fold: one character for one, so match positions stay valid
const OLD = { w: "v", j: "i", æ: "ä", œ: "ö", ø: "ö", é: "e", è: "e", ſ: "s", ÿ: "y", q: "k", z: "s" };
function fold(s, old) {
  s = s.toLowerCase();
  return old ? s.replace(/[wjæœøéèſÿqz]/g, (c) => OLD[c]) : s;
}
async function runSearch(ev) {
  if (ev) ev.preventDefault();
  const q = $("q").value.trim(), old = $("qold").checked, list = $("results");
  list.innerHTML = "";
  if (!q) { $("rcount").textContent = ""; return; }
  const pat = fold(q, old).replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, "[^\\s]*");
  const re = new RegExp(pat, "g");
  const vols = $("qall").checked ? S.vols.map((v) => v.vol) : [S.cur.meta.vol];
  const scope = $("qscope").value;       // all | quotes | noquotes
  let n = 0, shown = 0;
  const qn = q.replace(/\*/g, "").toLowerCase();
  for (const kind of scope === "all" ? ["persons", "places"] : []) {
    const tot = (e) => Object.values(e.counts).reduce((x, y) => x + y, 0);
    const hits = S.reg[kind].filter((e) => qn.length > 2 && e.name.toLowerCase().includes(qn)).sort((a, b) => tot(b) - tot(a));
    for (const e of hits.slice(0, 5)) {
      const total = tot(e);
      const it = li(`${kind === "persons" ? "Person" : "Ort"} i registret`, `<b>${esc(e.name)}</b> ${esc(lifespan(e))} · ${total} omnämnanden`,
        `${kind === "persons" ? "person" : "place"}/${e.id}`);
      it.classList.add("result-entry");
      list.appendChild(it);
    }
  }
  for (const vol of vols) {
    const V = await loadVol(vol);
    // quotations: one item per quotation chain; otherwise paragraphs (with or without their quotations)
    const items = scope === "quotes" ? V.quotes
      : V.paras.map((p) => (scope === "noquotes" ? { ...p, text: p.textNQ } : p));
    for (const p of items) {
      const hay = fold(p.text, old);
      const ms = [...hay.matchAll(re)];
      if (!ms.length) continue;
      n += ms.length;
      if (shown >= 500) continue;
      shown++;
      const m = ms[0], a = Math.max(0, m.index - 90), b = Math.min(p.text.length, m.index + m[0].length + 140);
      const snip = (a ? "… " : "") + esc(p.text.slice(a, m.index)) + "<mark>" + esc(p.text.slice(m.index, m.index + m[0].length)) +
        "</mark>" + esc(p.text.slice(m.index + m[0].length, b)) + (b < p.text.length ? " …" : "");
      list.appendChild(li(`${vols.length > 1 ? partLabel(V.meta) + " · " : ""}${where(p)}${ms.length > 1 ? ` · ${ms.length} träffar` : ""}`,
        snip, `${vol}/${p.id}`));
    }
  }
  $("rcount").textContent = `${n} träffar i ${shown}${shown >= 500 ? "+" : ""} ${scope === "quotes" ? "citat" : "stycken"}`;
}

function renderAbout() {
  const ul = $("epubs");
  ul.innerHTML = "";
  for (const v of S.vols.filter((v) => v.epub)) {
    const li = h("li"), a = h("a", "", `${partLabel(v)}`);
    a.href = v.epub;
    a.setAttribute("download", "");
    li.appendChild(a);
    li.appendChild(document.createTextNode(` (EPUB, ${v.epub_mb} MB)`));
    ul.appendChild(li);
  }
  $("volstats").textContent = S.vols.map((v) => `${partLabel(v)}: ${v.pages} sidor, ${v.verified_pages} granskade`).join(" · ");
}


// ---------------------------------------------------------------- register (persons, places)
function lifespan(e) {
  return e.birth || e.death ? `(${e.birth ? +e.birth : "?"}–${e.death ? +e.death : "?"})` : "";
}
const ID_LABEL = { wikidata: "Wikidata", SBL: "SBL", VIAF: "VIAF", Libris: "Libris", manuscripta: "manuscripta.se" };
function idLinks(e) {
  const box = h("div", "links");
  for (const [k, url] of Object.entries(e.ids || {})) {
    const a = h("a", "", ID_LABEL[k] || k);
    a.href = url; a.target = "_blank"; a.rel = "noopener";
    box.appendChild(a);
  }
  return box;
}
function entryCard(id, low) {
  const e = S.entry[id], box = h("div");
  if (!e) { box.appendChild(h("div", "muted", "Ej identifierad i registret.")); return box; }
  box.appendChild(h("h4", "", `${e.name} ${lifespan(e)}`));
  if (e.desc) box.appendChild(h("div", "", e.desc));
  const here = (S.cur.names[id] || []).length, total = Object.values(e.counts).reduce((x, y) => x + y, 0);
  box.appendChild(h("div", "muted", `${here} omnämnanden i ${partLabel(S.cur.meta)}${S.vols.length > 1 ? `, ${total} i alla delar` : ""}`));
  if (low) box.appendChild(h("div", "muted", "Osäker identifiering: automatisk gissning, ännu inte granskad."));
  if (!e.ids) box.appendChild(h("div", "muted", "Ännu inte kopplad till Wikidata."));
  box.appendChild(idLinks(e));
  const act = h("div", "actions"), b = h("button", "ghost", "Alla omnämnanden");
  b.onclick = () => { $("popover").hidden = true; location.hash = `${e.kind === "persons" ? "person" : "place"}/${id}`; };
  act.appendChild(b);
  box.appendChild(act);
  return box;
}
function renderEntries(kind) {
  const isP = kind === "persons";
  const f = $(isP ? "pfilter" : "lfilter").value.toLowerCase(), sort = $(isP ? "psort" : "lsort").value;
  const list = $(isP ? "plist" : "llist");
  const total = (e) => Object.values(e.counts).reduce((x, y) => x + y, 0);
  let items = S.reg[kind].filter((e) => !f || e.name.toLowerCase().includes(f) || (e.desc || "").toLowerCase().includes(f));
  items.sort(sort === "name" ? (a, b) => a.name.localeCompare(b.name, "sv") : (a, b) => total(b) - total(a));
  $(isP ? "pcount" : "lcount").textContent = `${items.length} av ${S.reg[kind].length} ${isP ? "personer" : "orter"}`;
  list.innerHTML = "";
  let target = null;
  for (const e of items.slice(0, 400)) {
    const it = h("li"), head = h("div", "ent-head");
    head.appendChild(h("span", "ent-name", e.name));
    head.appendChild(h("span", "ent-meta", [lifespan(e), e.desc].filter(Boolean).join(" · ")));
    head.appendChild(h("span", "ent-count", Object.entries(e.counts).map(([v, n]) => `${partLabel(S.vols.find((x) => x.vol === v) || { part: v, year: "" }).replace(/ \(.*\)/, "")}: ${n}`).join(" · ")));
    it.appendChild(head);
    it.appendChild(idLinks(e));
    it.onclick = (ev) => { if (!ev.target.closest("a, .mentions")) toggleMentions(it, e); };
    list.appendChild(it);
    if (S.open === e.id) { toggleMentions(it, e); target = it; S.open = null; }
  }
  // scroll once the whole list is in place: while it is still being built the entry is the last item
  // and can only reach the bottom of the viewport
  if (target) {
    const view = list.closest(".view"), bar = view.querySelector(".list-head");   // sticky; taller when it wraps on phones
    target.scrollIntoView({ block: "start" });
    if (bar) view.scrollTop += target.getBoundingClientRect().top - bar.getBoundingClientRect().bottom - 8;
  }
}
// excerpt around one name mention: the name's own position in its paragraph (or sidenote / footnote),
// without the paragraph's sidenotes and footnote numbers
function snippetFor(V, m) {
  const root = m.note !== undefined ? V.notes[m.note].body : V.root;
  const el = root.querySelector(`#${m.id}`);
  if (!el) return { html: "", where: "" };
  let box = el.closest(".sidenote"), where = box ? "marginalrubrik" : m.note !== undefined ? "fotnot" : "";
  if (!box) box = el.closest("p, h3, td, .figtext, figcaption") || (m.note !== undefined ? root : el.parentNode);
  const clone = box.cloneNode(true);
  const target = clone.querySelector(`#${m.id}`);
  for (const x of clone.querySelectorAll(".sidenote, .fnref")) if (!x.contains(target)) x.remove();
  let pos = 0;
  const walk = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (target.contains(n)) break;
    pos += n.nodeValue.length;
  }
  const t = clone.textContent, name = target.textContent;
  const a = Math.max(0, pos - 80), b = Math.min(t.length, pos + name.length + 100);
  const html = (a ? "… " : "") + esc(t.slice(a, pos)) + "<mark>" + esc(name) + "</mark>" +
    esc(t.slice(pos + name.length, b)) + (b < t.length ? " …" : "");
  return { html, where };
}
async function toggleMentions(it, e) {
  const old = it.querySelector(".mentions");
  if (old) { old.remove(); return; }
  const ol = h("ol", "mentions");
  it.appendChild(ol);
  for (const vol of Object.keys(e.counts)) {
    const V = await loadVol(vol);
    for (const m of V.names[e.id] || []) {
      const sn = snippetFor(V, m);
      const row = h("li");
      row.innerHTML = `<span class="where">${esc(partLabel(V.meta))}${m.page ? " · s. " + m.page : ""}${sn.where ? " · " + sn.where : ""}` +
        `${m.low ? " · osäker" : ""}</span>${sn.html}`;
      row.onclick = () => { location.hash = m.note !== undefined ? `${vol}/fnref${m.note}` : `${vol}/${m.id}`; };
      ol.appendChild(row);
    }
  }
}

// ---------------------------------------------------------------- routing + UI
async function route() {
  const em = location.hash.slice(1).match(/^(person|place)\/(.+)$/);
  if (em) {
    if (!S.cur) await openVol(S.vols[0].vol);
    const kind = em[1] === "person" ? "persons" : "places";
    S.open = decodeURIComponent(em[2]);
    $(kind === "persons" ? "pfilter" : "lfilter").value = "";
    setView(kind);
    return;
  }
  const m = location.hash.slice(1).match(/^(\w+)(?:\/(.+))?$/);
  if (!m || !S.vols.some((v) => v.vol === m[1])) {
    if (!S.cur) await openVol(S.vols[0].vol);
    if (location.hash === "" || location.hash === "#") setView(S.cur && S.view !== "about" ? S.view : "read");
    return;
  }
  await openVol(m[1]);
  if (m[2]) goTo(decodeURIComponent(m[2]));
  else setView("read");
}

function initUI() {
  const vs = $("volsel");
  for (const v of S.vols) { const o = h("option", "", partLabel(v)); o.value = v.vol; vs.appendChild(o); }
  vs.onchange = () => { location.hash = vs.value; };
  $("toc").onchange = (e) => { if (e.target.value) location.hash = e.target.value; e.target.value = ""; };
  for (const b of document.querySelectorAll(".tabs button")) b.onclick = () => setView(b.dataset.view);
  $("qfilter").oninput = () => renderQuotes();
  $("sfilter").oninput = () => renderSources();
  $("searchform").onsubmit = runSearch;
  $("qscope").onchange = () => { if ($("q").value.trim()) runSearch(); };
  $("pfilter").oninput = () => renderEntries("persons");
  $("lfilter").oninput = () => renderEntries("places");
  $("psort").onchange = () => renderEntries("persons");
  $("lsort").onchange = () => renderEntries("places");
  $("namesbtn").onclick = () => {
    const on = !document.body.classList.contains("marknames");
    document.body.classList.toggle("marknames", on);
    $("namesbtn").setAttribute("aria-pressed", on);
    store.set("marknames", on ? "1" : "");
  };
  if (store.get("marknames") === "1") { document.body.classList.add("marknames"); $("namesbtn").setAttribute("aria-pressed", "true"); }
  document.querySelector(".brand").onclick = (e) => { e.preventDefault(); setView("about"); };
  document.querySelector(".reader").addEventListener("scroll", onScroll, { passive: true });
  $("pgprev").onclick = () => { $("follow").checked = false; showPage(S.page - 1); };
  $("pgnext").onclick = () => { $("follow").checked = false; showPage(S.page + 1); };
  $("follow").onchange = () => { if ($("follow").checked) onScroll(); };
  $("facsbtn").onclick = () => {
    document.body.classList.toggle("nofacs");
    store.set("nofacs", document.body.classList.contains("nofacs") ? "1" : "");
    if (!document.body.classList.contains("nofacs")) { S.osdUrl = null; showPage(Math.max(0, S.page)); }
  };
  if (store.get("nofacs") === "1" || innerWidth < 760) document.body.classList.add("nofacs");
  const theme = store.get("theme");
  if (theme) document.documentElement.dataset.theme = theme;
  $("themebtn").onclick = () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    store.set("theme", document.documentElement.dataset.theme);
  };
  // resizable facsimile pane
  const div = $("divider"), view = $("view-read");
  const w = store.get("facsw");
  if (w) view.style.setProperty("--facs-w", w);
  div.onpointerdown = (e) => {
    div.setPointerCapture(e.pointerId);
    div.onpointermove = (ev) => {
      const r = view.getBoundingClientRect();
      const pct = Math.min(70, Math.max(20, ((r.right - ev.clientX) / r.width) * 100));
      view.style.setProperty("--facs-w", pct + "%");
    };
    div.onpointerup = () => { div.onpointermove = null; store.set("facsw", view.style.getPropertyValue("--facs-w")); };
  };
}

(async () => {
  try {
    S.vols = await (await fetch("data/volumes.json")).json();
  } catch {
    $("text").innerHTML = '<p class="loading">Kunde inte läsa data/volumes.json.</p>';
    return;
  }
  try {
    S.reg = await (await fetch("data/register.json")).json();
    for (const k of ["persons", "places"]) for (const e of S.reg[k]) S.entry[e.id] = { ...e, kind: k };
  } catch { /* no register yet: the name views stay empty */ }
  initUI();
  addEventListener("hashchange", route);
  await route();
})();
