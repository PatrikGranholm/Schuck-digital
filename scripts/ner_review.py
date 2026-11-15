"""NER review: the register and the ambiguous-mention queue, served by gt_server.py at /ner/.

  python scripts/gt_server.py            then open http://localhost:8765/ner/

Reads data/ner/<vol>.json (text units), data/ner/<vol>-links.json (link.py's rule output) and
data/register/persons.json, places.json. Writes only the two places manual work is kept in:

  data/register/persons.json, places.json   entry decisions, as fields of the entry:
      status "checked"  + wikidata (or null for "no Wikidata match"): reviewed
      status "rejected"                                              not a person / place
      status "merged"   + merged_into                                 same as another entry
      manual: true                                                    created here (a split)
    register.py keeps checked / rejected / merged entries as they are when it reruns.
  data/ner/<vol>-decisions.json             mention decisions, keyed "<sha>:<start>:<end>"
      {"ref": id}                link this mention to an entry (any type)
      {"reject": true}           not a name
      {"add": true, ...}         a name the model missed (unit, text, type, ref)
    link.py applies them over its rules on the next run.

Nothing here changes the TEI. After a review session:
  cd scripts && $P link.py --vol <vol> && cd .. && $P scripts/to_tei.py --vol <vol> && $P scripts/build_site.py
"""
import json
import os
import re
import time
from collections import Counter, defaultdict
from io import BytesIO

from common import ROOT, img_path

D = ROOT / "data"
WEB = ROOT / "web" / "ner"
_cache = {}


# ---------------------------------------------------------------- files

def _load(path, default):
    """JSON file, cached on mtime (the register can be rewritten by register.py meanwhile)."""
    try:
        mt = path.stat().st_mtime_ns
    except FileNotFoundError:
        return default
    hit = _cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    data = json.loads(path.read_text())
    _cache[path] = (mt, data)
    return data


def _save(path, data, indent=1):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent))
    os.replace(tmp, path)
    _cache.pop(path, None)


def vols():
    return sorted(p.stem for p in (D / "ner").glob("*.json") if re.fullmatch(r"\d{4}[ab]?", p.stem))


def units(vol):
    key = ("units", vol)
    f = D / "ner" / f"{vol}.json"
    mt = f.stat().st_mtime_ns
    if _cache.get(key, (None,))[0] != mt:
        us = json.loads(f.read_text())["units"]
        _cache[key] = (mt, {"id": {u["id"]: u for u in us}, "sha": {u["sha"]: u for u in us}})
    return _cache[key][1]


def links(vol):
    return _load(D / "ner" / f"{vol}-links.json", [])


def decisions(vol):
    return _load(D / "ner" / f"{vol}-decisions.json", {})


def register(kind):
    return {e["id"]: e for e in _load(D / "register" / f"{kind}.json", [])}


def save_register(kind, reg):
    _save(D / "register" / f"{kind}.json", sorted(reg.values(), key=lambda e: e["id"]))


def kind_of(eid):
    return "persons" if eid.startswith("pers-") else "places"


def entry(eid):
    return register(kind_of(eid)).get(eid)


def final(eid):
    """Follow merges."""
    seen = set()
    while eid and eid not in seen:
        seen.add(eid)
        e = entry(eid)
        if not e or e.get("status") != "merged" or not e.get("merged_into"):
            return eid
        eid = e["merged_into"]
    return eid


# ---------------------------------------------------------------- mentions

def key_of(l):
    return f"{l['sha']}:{l['start']}:{l['end']}"


def effective(vol):
    """link.py's links with this volume's decisions applied, plus added mentions."""
    dec = decisions(vol)
    out = []
    for l in links(vol):
        k = key_of(l)
        m = dict(l, key=k, vol=vol, decided=k in dec)
        d = dec.get(k)
        if d:
            if d.get("reject"):
                m.update(ref=None, cert=None, rejected=True)
            else:
                m.update(ref=d.get("ref"), cert="manual", type=d.get("type", l["type"]))
        out.append(m)
    have = {m["key"] for m in out}
    us = units(vol)["sha"]
    for k, d in dec.items():
        if d.get("add") and k not in have:
            sha, a, b = k.split(":")
            u = us.get(sha)
            if u:
                out.append({"key": k, "vol": vol, "sha": sha, "start": int(a), "end": int(b), "unit": u["id"],
                            "text": d.get("text", ""), "page": u["page"], "chapter": u["chapter"],
                            "type": d.get("type", "persName"), "ref": d.get("ref"), "cert": "manual",
                            "decided": True, "added": True})
    for m in out:
        if m.get("ref"):
            m["ref"] = final(m["ref"])
    return out


def snippet(vol, m, width=110):
    u = units(vol)["id"].get(m["unit"]) or units(vol)["sha"].get(m["sha"])
    t = u["text"] if u else ""
    a, b = m["start"], m["end"]
    lo, hi = max(0, a - width), min(len(t), b + width)
    return {"pre": ("…" if lo else "") + t[lo:a], "hit": t[a:b], "post": t[b:hi] + ("…" if hi < len(t) else ""),
            "facs": u["facs"] if u else None, "kind": u["kind"] if u else None}


def mention_out(m):
    e = entry(m["ref"]) if m.get("ref") else None
    return {k: m.get(k) for k in ("key", "vol", "text", "page", "chapter", "type", "ref", "cert", "how",
                                  "candidates", "decided", "rejected", "added", "unit", "sha", "start", "end")} \
        | snippet(m["vol"], m) | {"ref_name": e["name"] if e else None}


def entry_brief(eid):
    e = entry(eid)
    if not e:
        return {"id": eid, "name": eid, "missing": True}
    c = next((c for c in e.get("candidates", []) if c["qid"] == e.get("wikidata")), {})
    return {"id": eid, "name": e["name"], "status": e.get("status"), "wikidata": e.get("wikidata"),
            "desc": c.get("desc"), "birth": c.get("birth"), "death": c.get("death")}


QUEUE_KINDS = {
    "guess": lambda m: m.get("how", "").startswith("guess"),
    "unresolved": lambda m: m.get("how") in ("unresolved", "ambiguous") or (m["type"] == "persName" and not m.get("ref")
                                                                         and m.get("candidates")),
    "medium": lambda m: m.get("cert") == "medium",
}


def queue(vol, kind):
    test = QUEUE_KINDS[kind]
    groups = defaultdict(list)
    for m in effective(vol):
        if m.get("added") or not test(m):
            continue
        cands = tuple(sorted(m.get("candidates") or ([m["ref"]] if m.get("ref") else [])))
        groups[(cands, m.get("chapter") or "")].append(m)
    out = []
    for (cands, ch), mm in groups.items():
        cands = list(dict.fromkeys(final(c) for c in cands))
        label = Counter(m["text"] for m in mm).most_common(1)[0][0]
        open_ = [m for m in mm if not m["decided"]]
        out.append({"id": f"{'|'.join(cands)}@{ch}", "label": label, "chapter": ch, "n": len(mm),
                    "open": len(open_), "candidates": [entry_brief(c) for c in cands],
                    "mentions": [mention_out(m) for m in sorted(mm, key=lambda m: (m["unit"], m["start"]))]})
    out.sort(key=lambda g: (g["open"] == 0, -g["open"], g["label"]))
    return out


# ---------------------------------------------------------------- register views

def entry_counts(e):
    vs = e.get("vols", {})
    return {v: s.get("n", s.get("n_full", 0) + s.get("n_bare", 0) + s.get("n_ambiguous", 0)) for v, s in vs.items()}


def entries(kind, status, q):
    reg = register(kind)
    st = set(status.split(",")) if status and status != "all" else None
    q = (q or "").lower()
    out = []
    for e in reg.values():
        if st and e.get("status") not in st:
            continue
        if q and q not in e["name"].lower() and not any(q in f.lower() for f in e.get("forms", {})):
            continue
        n = entry_counts(e)
        top = max((c.get("score") or 0 for c in e.get("candidates", [])), default=None)
        out.append({"id": e["id"], "name": e["name"], "status": e.get("status"), "wikidata": e.get("wikidata"),
                    "n": sum(n.values()), "vols": n, "top": top, "merged_into": e.get("merged_into")})
    out.sort(key=lambda e: (-e["n"], e["name"]))
    return out


def entry_detail(eid, limit=300):
    e = entry(eid)
    if not e:
        return None
    ms = []
    for v in vols():
        for m in effective(v):
            if m.get("ref") == eid or (eid in (m.get("candidates") or []) and not m.get("decided")):
                ms.append(m)
    ms.sort(key=lambda m: (m["vol"], m["unit"], m["start"]))
    merged_here = [x["id"] for x in register(kind_of(eid)).values() if x.get("merged_into") == eid]
    return {"entry": e, "counts": entry_counts(e), "n_mentions": len(ms), "merged_here": merged_here,
            "mentions": [mention_out(m) for m in ms[:limit]]}


def search_entries(q, limit=20):
    q = q.lower().strip()
    if not q:
        return []
    out = []
    for kind in ("persons", "places"):
        for e in register(kind).values():
            if e.get("status") in ("rejected", "merged"):
                continue
            name = e["name"].lower()
            if q in name or any(q in f.lower() for f in e.get("forms", {})):
                n = sum(entry_counts(e).values())
                out.append((not name.startswith(q), -n, entry_brief(e["id"]) | {"n": n}))
    return [x[2] for x in sorted(out, key=lambda x: x[:2])[:limit]]


# ---------------------------------------------------------------- writes

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def update_entry(eid, body):
    kind = kind_of(eid)
    reg = register(kind)
    e = reg.get(eid)
    if not e:
        raise KeyError(eid)
    act = body["action"]
    if act == "choose":
        qid = body["qid"]
        if not any(c["qid"] == qid for c in e.get("candidates", [])):
            e.setdefault("candidates", []).append(wikidata_candidate(qid))
        e.update(wikidata=qid, status="checked")
        c = next(c for c in e["candidates"] if c["qid"] == qid)
        if kind == "persons":
            e["manuscripta"] = manuscripta().get(c.get("viaf")) if c.get("viaf") else None
    elif act == "none":
        e.update(wikidata=None, status="checked")
    elif act == "confirm":                     # keep the automatic match
        e["status"] = "checked"
    elif act == "reject":
        e["status"] = "rejected"
    elif act == "reopen":
        e["status"] = "review"
        e.pop("merged_into", None)
    elif act == "rename":
        e["name"] = body["name"].strip() or e["name"]
    elif act == "merge":
        into = final(body["into"])
        if into == eid or kind_of(into) != kind:
            raise ValueError("merge target must be another entry of the same kind")
        e.update(status="merged", merged_into=into)
    elif act == "research":
        import register as R
        R.OFFLINE = False
        probe = {"name": body["query"].strip(), "context_year": e.get("context_year")}
        new = R.person_candidates(probe) if kind == "persons" else R.place_candidates(probe)
        have = {c["qid"] for c in e.get("candidates", [])}
        e["candidates"] = e.get("candidates", []) + [c for c in new if c["qid"] not in have]
    else:
        raise ValueError(f"unknown action {act}")
    e["reviewed"] = now()
    reg[eid] = e
    save_register(kind, reg)
    return entry_detail(eid)


def new_entry(kind, name):
    reg = register(kind)
    base = ("pers-" if kind == "persons" else "plac-") + re.sub(r"\W+", "-", name.lower()).strip("-")
    eid, k = base, 2
    while eid in reg:
        eid, k = f"{base}-{k}", k + 1
    e = {"id": eid, "name": name.strip(), "forms": {}, "candidates": [], "wikidata": None, "status": "checked",
         "manual": True, "vols": {}, "reviewed": now()}
    if kind == "persons":
        e.update(surname=name.split()[-1], n_full=0, n_bare=0)
    else:
        e["n"] = 0
    reg[eid] = e
    save_register(kind, reg)
    return entry_brief(eid)


def decide(vol, body):
    path = D / "ner" / f"{vol}-decisions.json"
    dec = dict(decisions(vol))
    by_key = {m["key"]: m for m in effective(vol)}
    for k in body["keys"]:
        if body.get("clear"):
            if not dec.get(k, {}).get("add"):
                dec.pop(k, None)
            continue
        m = by_key.get(k, {})
        d = {"text": m.get("text"), "at": now()}
        if dec.get(k, {}).get("add"):
            d = dec[k] | {"at": now()}
        if body.get("reject"):
            if d.get("add"):
                dec.pop(k, None)
                continue
            d.update(reject=True)
            d.pop("ref", None)
        else:
            ref = body["ref"]
            if not entry(ref):
                raise KeyError(ref)
            d.update(ref=ref, type="persName" if ref.startswith("pers-") else "placeName")
            d.pop("reject", None)
        dec[k] = d
    _save(path, dec)
    return {"ok": True, "n": len(body["keys"])}


def add_mention(vol, body):
    u = units(vol)["sha"].get(body["sha"])
    if not u:
        raise KeyError(body["sha"])
    a, b = int(body["start"]), int(body["end"])
    text = u["text"][a:b]
    if not text.strip() or text != text.strip():
        raise ValueError("select the name without surrounding spaces")
    ref = body["ref"]
    if not entry(ref):
        raise KeyError(ref)
    k = f"{body['sha']}:{a}:{b}"
    if any(m["key"] == k for m in effective(vol)):
        return decide(vol, {"keys": [k], "ref": ref})
    path = D / "ner" / f"{vol}-decisions.json"
    dec = dict(decisions(vol))
    dec[k] = {"add": True, "unit": u["id"], "text": text, "ref": ref,
              "type": "persName" if ref.startswith("pers-") else "placeName", "at": now()}
    _save(path, dec)
    return {"ok": True, "key": k}


def unit_view(vol, sha):
    u = units(vol)["sha"].get(sha)
    if not u:
        return None
    ms = [mention_out(m) for m in effective(vol) if m["sha"] == sha]
    return {"vol": vol, "sha": sha, "text": u["text"], "page": u["page"], "facs": u["facs"], "kind": u["kind"],
            "mentions": sorted(ms, key=lambda m: m["start"])}


def summary():
    out = {"vols": {}, "register": {}}
    for kind in ("persons", "places"):
        out["register"][kind] = dict(Counter(e.get("status") for e in register(kind).values()))
    for v in vols():
        eff = effective(v)
        out["vols"][v] = {k: {"open": sum(1 for m in eff if t(m) and not m["decided"] and not m.get("added")),
                              "decided": sum(1 for m in eff if t(m) and m["decided"] and not m.get("added"))}
                          for k, t in QUEUE_KINDS.items()} | {
            "mentions": len(eff), "decisions": len(decisions(v)),
            "added": sum(1 for m in eff if m.get("added"))}
    return out


# ---------------------------------------------------------------- Wikidata / manuscripta

def wikidata_candidate(qid):
    import register as R
    R.OFFLINE = False
    if not re.fullmatch(r"Q\d+", qid):
        raise ValueError("a Wikidata id looks like Q12345")
    e = R.entities([qid]).get(qid)
    if not e:
        raise KeyError(qid)
    return {"qid": qid, "label": R.label_of(e), "desc": R.desc_of(e), "birth": R.claim_year(e, "P569"),
            "death": R.claim_year(e, "P570"), "score": None, "viaf": R.claim_str(e, "P214"),
            "sbl": R.claim_str(e, "P3217"), "libris": R.claim_str(e, "P906"), "manual": True}


def manuscripta():
    if "ms" not in _cache:
        import register as R
        _cache["ms"] = R.manuscripta_by_viaf()
    return _cache["ms"]


# ---------------------------------------------------------------- scan crop

def crop(vol, facs, text, before=""):
    """JPEG crop of the page around the word(s) of a mention, found in the text layer's word boxes.
    The occurrence whose preceding words best match `before` wins; the next page is tried too."""
    from PIL import Image
    from rapidfuzz import fuzz
    from regions import textlayer_tokens
    idx = int(facs.rsplit("-", 1)[1])
    words = text.split()
    prev = before.split()[-3:]
    best = None
    for i in (idx, idx + 1):
        if not img_path(vol, i).exists():
            continue
        toks = textlayer_tokens(vol, i)
        for k, t in enumerate(toks):
            s = fuzz.ratio(re.sub(r"\W", "", t["t"]), re.sub(r"\W", "", words[0])) if words else 0
            if s < 70:
                continue
            ctx = " ".join(x["t"] for x in toks[max(0, k - len(prev)):k])
            score = s + (0.6 * fuzz.ratio(ctx, " ".join(prev)) if prev else 0) - (5 if i != idx else 0)
            if not best or score > best[0]:
                boxes = [b for x in toks[k:k + max(1, len(words))] for b in x["boxes"]]
                best = (score, i, boxes)
    if not best:
        return None
    _, i, boxes = best
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    if y1 - y0 > 120:                                   # hyphenated over two lines: take the first box
        x0, y0, x1, y1 = boxes[0]
    h = y1 - y0
    with Image.open(img_path(vol, i)) as im:
        c = im.convert("L").crop((max(0, x0 - 6 * h), max(0, y0 - int(1.3 * h)),
                                  min(im.width, x1 + 6 * h), min(im.height, y1 + int(1.3 * h))))
        from PIL import ImageDraw
        dr = ImageDraw.Draw(c)
        ox, oy = max(0, x0 - 6 * h), max(0, y0 - int(1.3 * h))
        dr.rectangle((x0 - ox - 3, y0 - oy - 3, x1 - ox + 3, y1 - oy + 3), outline=0, width=2)
        c.thumbnail((900, 200))
        buf = BytesIO()
        c.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------- HTTP

def handle(h, method):
    """Route /ner/ and /api/ner/* for gt_server's handler. Returns False when the path is not ours."""
    from urllib.parse import parse_qs, urlsplit
    u = urlsplit(h.path)
    path, qs = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
    if method == "GET" and path in ("/ner", "/ner/"):
        h.send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        return True
    if method == "GET" and path in ("/ner/app.js", "/ner/style.css"):
        ctype = "text/javascript" if path.endswith(".js") else "text/css"
        h.send(200, (WEB / path.rsplit("/", 1)[1]).read_bytes(), ctype + "; charset=utf-8")
        return True
    if not path.startswith("/api/ner/"):
        return False
    route = path[len("/api/ner/"):]
    try:
        body = json.loads(h.rfile.read(int(h.headers.get("Content-Length") or 0)) or b"{}") \
            if method in ("PUT", "POST") else {}
        vol = qs.get("vol") or body.get("vol")
        if vol and vol not in vols():
            return h.send(404, {"error": f"no NER data for {vol}"}) or True
        if method == "GET":
            if route == "summary":
                res = summary() | {"volumes": vols()}
            elif route == "entries":
                res = entries(qs.get("kind", "persons"), qs.get("status", "review"), qs.get("q"))
            elif route == "entry":
                res = entry_detail(qs["id"])
            elif route == "search":
                res = search_entries(qs.get("q", ""))
            elif route == "queue":
                res = queue(vol, qs.get("kind", "guess"))
            elif route == "unit":
                res = unit_view(vol, qs["sha"])
            elif route == "crop":
                jpg = crop(vol, qs["facs"], qs.get("text", ""), qs.get("before", ""))
                if jpg is None:
                    return h.send(404, {"error": "word not found on the page"}) or True
                return h.send(200, jpg, "image/jpeg") or True
            else:
                res = None
        elif method == "PUT" and route == "entry":
            res = update_entry(body["id"], body)
        elif method == "POST" and route == "entry":
            res = new_entry(body["kind"], body["name"])
        elif method == "PUT" and route == "decide":
            res = decide(vol, body)
        elif method == "POST" and route == "add":
            res = add_mention(vol, body)
        else:
            res = None
        if res is None:
            h.send(404, {"error": "not found"})
        else:
            h.send(200, res)
    except (KeyError, ValueError) as ex:
        h.send(400, {"error": f"{type(ex).__name__}: {ex}"})
    return True
