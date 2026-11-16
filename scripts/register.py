"""NER phase 2: register draft (persons, places) with Wikidata and manuscripta candidates.

  python scripts/register.py --vol 1932 [--offline]
      -> data/register/persons.json, data/register/places.json, report/register-<vol>.md

Input: data/ner/<vol>.json (scripts/ner.py). Standard library only.

Persons
  1. Normal form per mention: strip prefixes (Wij, Hertig, Drottning, Doctor, H:r ...),
     the possessive ’s, and fold genitive -s / Latin case endings (Rugmans → Rugman,
     Messenio → Messenius, Olaum → Olaus, Johannis → Johannes). A fold is made only when
     the folded word is more frequent in the volume than the unfolded one, so it never
     invents forms ("Magni" and "Petri" stay as they are).
  2. Rejects: God (Gud, Gudh, Gudz), works (Saga, Codex, Annales ...), chancery words
     (Cantzelie, Cammar ...), the printer, all-caps strings with "&".
  3. Entries: full names cluster on (first word, last word) after folding, e.g.
     "Laurentius Jacobi Bureus" + "Laurentius Bureus". A bare surname ("Bureus") is
     assigned to the one entry with that surname. When there are several, it is
     *ambiguous* and goes to the review queue (phase 3). When there are none, it is an
     entry of its own.
Places
  Genitive folding as above (Sveriges → Sverige). Institutions (bibliotek, arkiv,
  universitet ...) are moved out of the place list.
Wikidata (cache/wikidata/, about 1 request per second, generic User-Agent)
  wbsearchentities (sv) + wbgetentities. Person candidates must be humans born
  1300-1900. Candidates are scored:
    - name match against label and aliases
    - forename match
    - Nordic citizenship or an SBL id
    - descriptions of the antiquarian/clerical/royal kind
    - fit between the lifespan and the years printed near the mentions
  Places: label/alias match, Nordic country, coordinates, countries.
  status: "auto" when the best candidate is clearly ahead (score threshold and margin),
  otherwise "review". Entries with very few mentions are not looked up ("skipped").
manuscripta: VIAF id of the chosen candidate (Wikidata P214), matched against
  ../manuscripta/id/person/*.xml.
Entries that were already reviewed in data/register/*.json (status "checked" or
"rejected") are kept as they are when the script is rerun.
"""
import argparse
import json
import re
import statistics
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "wikidata"
UA = "schuck-digital-register/0.1 (research script; Python urllib)"
API = "https://www.wikidata.org/w/api.php"
MANUSCRIPTA = ROOT.parent / "manuscripta" / "id" / "person"

PREFIX = re.compile(r"(?i)^(?:Wij|Wi|Vi|Hertigh?(?:en)?|Drottning(?:en)?|Konung(?:en)?|Kung|Doctor|Doct\.|Dr\.?|D:r|"
                    r"Mag\.|Magister|M\.|Herr|Her|H:r|Hr\.?|Prof\.?|Professor|Biskop(?:en)?|Ärkebiskop(?:en)?|"
                    r"Mäster|Monsieur|Mons\.|Mr\.?|Fru|[Ss]ahl\.|Sal\.|Kgl\.|Kongl\.|Edel|Wälbördigh?|Welbördigh?|Grefve|Greve|Riks\w+|"
                    # titles of rank and office (vol. VII: "Canzli Rådet Hallenberg", "Amanuensen Sundel",
                    # "Friherre Adlerbeth" made entries of their own beside the name, so a bare surname had
                    # two candidates). A two-word title ("Canzli Rådet", "Expeditions Secreteraren") is
                    # removed word by word. Not "C. R." (Cantzli Råd), which is also a pair of initials.
                    r"Baron(?:en)?|Friherr(?:e|en)|Frih\.|Gref(?:we|ven|wen)?|Grefvinnan|Kong|Kejsar(?:en)?|"
                    r"Cantzlie?|Cancellie|Canzli|Kansli|Justiti[æa]e?|Expeditions|Hof|"
                    r"\w*Råd(?:et|h|)|\w*Cantzler(?:en)?|\w*Secreterar(?:e|en)|\w*Sekreterar(?:e|en)|"
                    r"Assessor(?:n)?|Lagman(?:nen)?|Häradshöfding(?:en)?|Landshöfding(?:en)?|Presidenten|"
                    r"Prob?sten|Prost|Kyrkoherde(?:n)?|Lect?or(?:n)?|Lektor(?:n)?|Rect?or(?:n)?|Rektor(?:n)?|"
                    r"Amanuens(?:en|is)?|Docens|Docent(?:en)?|Adjunct(?:en)?|Doktor(?:n)?|Apotekar(?:e|en)?|"
                    r"Löjtnant(?:en)?|Lieutenant(?:en)?|Kapten|Capitain(?:en)?|Major(?:en)?|Öfverste(?:n)?|"
                    r"H[oå]f(?:[Mm]arskalk|[Ii]ntendent|[Jj]unkare|[Pp]redikant|[Kk]apellmästare|[Ss]ekreterare|[Ss]ecreterare)\w*|Kammar[Hh]err(?:e|en)|Translator(?:e|en)?|Borgmästar(?:e|en)?|Biskoppen)\s+")
REJECT = re.compile(r"^(?:Gud[hzs]?|Guds|Gudhs|Herren|Herr|Hans|EDERS|Eders|Edert|Edre|Edra)$|\b(?:May|Maij?|Maij?:t\w*|Majest\w*|Excellen\w*|Saga|Sogur|Codex|Annales|Chronicon|"
                    r"Cantz\w*|Cammar\w*|Cronones|Colleg\w*|Kansli\w*|ALMQVIST|WIKSELL\w*)\b|&")
FORENAMES = [  # Latin and Swedish forms of one forename; used only to cluster entries of the same surname
    "Johannes Johan Joan Johannis Johanne Jahan Jöns Hans", "Laurentius Lars Lorentz Laurentio", "Olaus Olof Olavus Olaf Olao Olaum Olai",
    "Martinus Mårten Martin", "Petrus Per Peder Peter Petro", "Nicolaus Nils Niels Nicolai", "Georgius Georg Jöran Jören Göran Jörgen",
    "Ericus Erik Erich Erici", "Andreas Anders Andreæ", "Benedictus Bengt Bencht", "Magnus Måns", "Jacobus Jakob Jacob Jacobi",
    "Haquinus Håkan Haquin", "Sigismundus Sigismund", "Christianus Christian Christiern Kristian", "Gustavus Gustav Gustaf",
    "Carolus Karl Carl", "Henricus Henrik Henrich", "Stephanus Staffan Stefan", "Canutus Knut", "Gabriel Gabrieli"]
FORENAME = {w: c.split()[0] for c in FORENAMES for w in c.split()}
PARTICLES = {"de", "De", "la", "La", "von", "Von", "af", "Af", "van", "du", "di"}
INSTITUTION = re.compile(r"bibliotek|arkiv|universitet|akademi|kollegi|kansli|kyrkan$|samling", re.I)
NORDIC = {"Q34", "Q35", "Q20", "Q33", "Q189", "Q215443", "Q1054923", "Q62633", "Q170072", "Q1649871", "Q756617"}
COUNTRY = {"Q6256", "Q3624078", "Q3024240", "Q417175"}
GOOD_DESC = re.compile(r"antikvar|antiquar|histori|scholar|lärd|präst|priest|bishop|biskop|professor|poet|skald|king|"
                       r"kung|queen|drottning|chancellor|kansler|statesman|riksråd|runolog|philolog|filolog|"
                       r"librarian|bibliotekarie|archivist|arkivarie|humanist|writer|författare|theolog|teolog|"
                       r"Swedish|svensk|Danish|dansk|Icelandic|isländsk|Norwegian|norsk|Finnish", re.I)


# ---------------------------------------------------------------- normalisation

def build_vocab(forms):
    c = Counter()
    for f, n in forms.items():
        for w in f.split():
            c[w] += n
    return c


def fold_word(w, vocab):
    """Fold a genitive -s or a Latin case ending when the folded word is more frequent."""
    cands = []
    if w.endswith("s"):
        cands.append(w[:-1])
    for end, rep in (("um", "us"), ("o", "us"), ("is", "es"), ("em", "es"), ("i", ""), ("e", "us"), ("ii", "ius")):
        if w.endswith(end) and len(w) > len(end) + 2:
            cands.append(w[: -len(end)] + rep)
    best = max(cands, key=lambda c: vocab.get(c, 0), default=None)
    return best if best and vocab.get(best, 0) > vocab.get(w, 0) else w


def normal_form(text, vocab):
    s = re.sub(r"[’']s?$", "", text.strip())
    prev = None
    while prev != s:
        prev, s = s, PREFIX.sub("", s)
    words = [fold_word(w, vocab) for w in s.split()]
    return " ".join(words)


# ---------------------------------------------------------------- Wikidata

def wd(params):
    CACHE.mkdir(parents=True, exist_ok=True)
    key = urllib.parse.urlencode(sorted(params.items()))
    f = CACHE / (re.sub(r"[^\w.-]", "_", key)[:180] + ".json")
    if f.exists():
        return json.loads(f.read_text())
    if OFFLINE:
        return {}
    url = API + "?" + urllib.parse.urlencode(params | {"format": "json"})
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except Exception:
            time.sleep(5 * (attempt + 1))
    else:
        return {}
    f.write_text(json.dumps(data))
    time.sleep(1.0)
    return data


def search(name):
    hits = wd({"action": "wbsearchentities", "search": name, "language": "sv", "uselang": "sv",
               "type": "item", "limit": "12"}).get("search", [])
    return [h["id"] for h in hits]


def entities(qids):
    out = {}
    for k in range(0, len(qids), 50):
        part = qids[k:k + 50]
        data = wd({"action": "wbgetentities", "ids": "|".join(part), "props": "labels|aliases|descriptions|claims",
                   "languages": "sv|en|la|da|is"}).get("entities", {})
        out.update(data)
    return out


def claim_ids(e, p):
    return [c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for c in e.get("claims", {}).get(p, [])
            if c["mainsnak"].get("datavalue")]


def claim_year(e, p):
    for c in e.get("claims", {}).get(p, []):
        t = c["mainsnak"].get("datavalue", {}).get("value", {}).get("time")
        if t:
            m = re.match(r"([+-]\d+)", t)
            return int(m.group(1)) if m else None
    return None


def claim_str(e, p):
    for c in e.get("claims", {}).get(p, []):
        v = c["mainsnak"].get("datavalue", {}).get("value")
        if isinstance(v, str):
            return v
    return None


def names_of(e):
    out = set()
    for lang, v in e.get("labels", {}).items():
        out.add(v["value"])
    for lang, vs in e.get("aliases", {}).items():
        out.update(v["value"] for v in vs)
    return out


def desc_of(e):
    d = e.get("descriptions", {})
    return (d.get("sv") or d.get("en") or next(iter(d.values()), {"value": ""}))["value"]


def label_of(e):
    lab = e.get("labels", {})
    return (lab.get("sv") or lab.get("en") or next(iter(lab.values()), {"value": ""}))["value"]


def person_candidates(entry):
    q = entry["name"]
    qids = search(q)
    if " " in q and len(qids) < 3:                       # "Johannes Magni" may also be found under the surname
        qids += [x for x in search(q.split()[-1]) if x not in qids]
    ents = entities(qids[:20])
    first, last = q.split()[0].lower(), q.split()[-1].lower()
    year = entry.get("context_year")
    cands = []
    for qid in qids[:20]:
        e = ents.get(qid)
        if not e or "Q5" not in claim_ids(e, "P31"):
            continue
        b, d = claim_year(e, "P569"), claim_year(e, "P570")
        if b and not (1300 <= b <= 1900):
            continue
        names = {n.lower() for n in names_of(e)}
        score = 0
        if q.lower() in names:
            score += 3
        elif any(last in n.split() or n.endswith(last) for n in names):
            score += 1
        else:
            continue
        if " " in q and any(n.split()[0] == first for n in names if " " in n):
            score += 1
        nordic = bool(set(claim_ids(e, "P27")) & NORDIC) or bool(claim_str(e, "P3217"))
        score += 2 if nordic else 0
        desc = desc_of(e)
        score += 1 if GOOD_DESC.search(desc) else 0
        if year and b:
            lo, hi = b + 10, (d or b + 80) + 60      # mentioned in his active life or soon after
            score += 2 if lo <= year <= hi else (-1 if year < b else 0)
        cands.append({"qid": qid, "label": label_of(e), "desc": desc, "birth": b, "death": d, "score": score,
                      "viaf": claim_str(e, "P214"), "sbl": claim_str(e, "P3217"), "libris": claim_str(e, "P906")})
    cands.sort(key=lambda c: -c["score"])
    return cands[:6]


def place_candidates(entry):
    q = entry["name"]
    qids = search(q)
    ents = entities(qids[:12])
    cands = []
    for qid in qids[:12]:
        e = ents.get(qid)
        if not e:
            continue
        p31 = set(claim_ids(e, "P31"))
        if "Q5" in p31:
            continue
        coord = bool(e.get("claims", {}).get("P625"))
        country = bool(p31 & COUNTRY)
        if not (coord or country):
            continue
        names = {n.lower() for n in names_of(e)}
        score = 3 if label_of(e).lower() == q.lower() else 2 if q.lower() in names else 0
        if not score:
            continue
        nordic = bool(set(claim_ids(e, "P17")) & NORDIC) or qid in NORDIC
        score += (2 if nordic else 0) + (2 if country else 0) + (1 if coord else 0)
        cands.append({"qid": qid, "label": label_of(e), "desc": desc_of(e), "score": score})
    cands.sort(key=lambda c: -c["score"])
    return cands[:6]


def decide(cands, threshold, margin):
    if not cands:
        return None, "none"
    best = cands[0]
    second = cands[1]["score"] if len(cands) > 1 else -99
    if best["score"] >= threshold and best["score"] - second >= margin:
        return best["qid"], "auto"
    return None, "review"


# ---------------------------------------------------------------- manuscripta

def manuscripta_by_viaf():
    out = {}
    if not MANUSCRIPTA.exists():
        return out
    for f in MANUSCRIPTA.glob("*.xml"):
        s = f.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'subtype="VIAF"[^>]*>\s*https?://viaf\.org/viaf/(\d+)', s)
        if m:
            out[m.group(1)] = f"https://www.manuscripta.se/person/{f.stem}"
    return out


# ---------------------------------------------------------------- build

def context_years(ner, unit_ids):
    texts = {u["id"]: u["text"] for u in ner["units"]}
    ys = [int(y) for uid in unit_ids for y in re.findall(r"\b(1[3-8]\d\d)\b", texts.get(uid, ""))]
    return int(statistics.median(ys)) if ys else None


def build_persons(ner):
    ms = [m for m in ner["mentions"] if m["label"] == "PER"]
    forms = Counter(m["text"] for m in ms)
    vocab = build_vocab(Counter({re.sub(r"[’']s?$", "", f): n for f, n in forms.items()}))
    rejected = Counter()
    full, bare = defaultdict(list), defaultdict(list)
    bare_vocab = Counter()
    first_vocab = Counter()
    for m in ms:
        nf = normal_form(m["text"], vocab)
        if nf and " " not in nf:
            bare_vocab[nf] += 1
        elif nf:
            first_vocab[nf.split()[0]] += 1
    for m in ms:
        nf = normal_form(m["text"], vocab)
        if not nf or REJECT.search(nf) or len(nf) < 3:
            rejected[m["text"]] += 1
            continue
        w = nf.split()
        # "Bureus Messenius" = a list of two surnames, not one name
        if len(w) == 2 and all(bare_vocab[x] >= 3 for x in w) and first_vocab[w[0]] <= 1 and w[0] not in FORENAME:
            for x in w:
                bare[x].append(m | {"normal": x})
            continue
        m["normal"] = nf
        # a surname with a particle ("De la Gardie") or a single word is a bare surname
        (bare if len(w) == 1 or w[0] in PARTICLES else full)[nf].append(m)
    # cluster full names on (first, last)
    clusters = defaultdict(list)
    for nf, mm in full.items():
        w = nf.split()
        clusters[(FORENAME.get(w[0], w[0]), w[-1])].append((nf, mm))
    entries = {}
    by_surname = defaultdict(list)
    for (first, last), group in clusters.items():
        name = max(group, key=lambda g: (len(g[1]), len(g[0])))[0]   # most frequent full form
        mm = [m for _, g in group for m in g]
        eid = "pers-" + re.sub(r"\W+", "-", f"{last}-{first}".lower()).strip("-")
        entries[eid] = {"id": eid, "name": name, "surname": last, "forms": dict(Counter(m["text"] for m in mm)),
                        "n_full": len(mm), "n_bare": 0, "units": sorted({m["unit"] for m in mm})}
        by_surname[last].append(eid)
    ambiguous = {}
    for nf, mm in bare.items():
        key = nf.split()[-1]
        owners = by_surname.get(key, [])
        # one-off full forms (OCR, lists) are not real competitors for a bare surname
        real = [o for o in owners if entries[o]["n_full"] >= 2] or owners
        if len(real) == 1:
            e = entries[real[0]]
            e["n_bare"] += len(mm)
            if len(owners) > 1:
                e["bare_probable"] = e.get("bare_probable", 0) + len(mm)   # assigned by dominance: review later
            e["forms"] = dict(Counter(e["forms"]) + Counter(m["text"] for m in mm))
            e["units"] = sorted(set(e["units"]) | {m["unit"] for m in mm})
        elif len(real) > 1:
            ambiguous[nf] = {"mentions": len(mm), "candidates": real}
        else:
            eid = "pers-" + re.sub(r"\W+", "-", nf.lower()).strip("-")
            if eid in entries:
                e = entries[eid]
                e["n_bare"] += len(mm)
                e["forms"] = dict(Counter(e["forms"]) + Counter(m["text"] for m in mm))
                e["units"] = sorted(set(e["units"]) | {m["unit"] for m in mm})
            else:
                entries[eid] = {"id": eid, "name": nf, "surname": key, "forms": dict(Counter(m["text"] for m in mm)),
                                "n_full": 0, "n_bare": len(mm), "units": sorted({m["unit"] for m in mm})}
    for nf, a in ambiguous.items():           # ambiguous mentions count as possible for each candidate
        for eid in a["candidates"]:
            entries[eid]["n_ambiguous"] = entries[eid].get("n_ambiguous", 0) + a["mentions"]
    return entries, ambiguous, rejected


def build_places(ner):
    ms = [m for m in ner["mentions"] if m["label"] == "LOC"]
    forms = Counter(m["text"] for m in ms)
    vocab = build_vocab(forms)
    entries, moved = {}, Counter()
    for m in ms:
        if INSTITUTION.search(m["text"]):
            moved[m["text"]] += 1
            continue
        w = m["text"].split()
        w[-1] = fold_word(w[-1], vocab) if w[-1].endswith("s") else w[-1]
        nf = " ".join(w)
        eid = "plac-" + re.sub(r"\W+", "-", nf.lower()).strip("-")
        e = entries.setdefault(eid, {"id": eid, "name": nf, "forms": Counter(), "n": 0, "units": set()})
        e["forms"][m["text"]] += 1
        e["n"] += 1
        e["units"].add(m["unit"])
    for e in entries.values():
        e["forms"], e["units"] = dict(e["forms"]), sorted(e["units"])
    return entries, moved


PER_VOL = ("forms", "n_full", "n_bare", "n_ambiguous", "bare_probable", "n", "units")
LEGACY_VOL = "1932"     # entries written before the register had per-volume slices came from vol. I


def merge_existing(path, new, vol):
    """Merge this volume's entries into the shared register.

    Each entry keeps per-volume slices in e["vols"][vol] (forms, counts, units: unit ids are
    per volume). This run replaces only its own volume's slice; other volumes' entries stay.
    Top-level forms and counts are the sums over volumes. The Wikidata decision of an existing
    entry is kept, except that one never looked up ("skipped") takes this run's lookup, and a
    reviewed one (checked / rejected / merged, set in scripts/ner_review.py) is never touched."""
    old = {e["id"]: e for e in json.loads(path.read_text())} if path.exists() else {}
    for e in old.values():
        if "vols" not in e:
            e["vols"] = {LEGACY_VOL: {k: e[k] for k in PER_VOL if k in e}}
    out = {}
    for eid in sorted(set(old) | set(new)):
        o, n = old.get(eid), new.get(eid)
        if o is None:
            e = dict(n, vols={})
        else:
            e = dict(o)
            e["vols"] = dict(o["vols"])
            e["vols"].pop(vol, None)
            if n is not None and o.get("status") == "skipped":
                e.update({k: n[k] for k in ("candidates", "wikidata", "status", "manuscripta", "context_year")
                          if k in n})
        if n is not None:
            e["vols"][vol] = {k: n[k] for k in PER_VOL if k in n}
        if not e.get("vols") and e.get("status") not in ("checked", "rejected", "merged"):
            continue
        sl = e["vols"].values()
        e["forms"] = dict(sum((Counter(v.get("forms", {})) for v in sl), Counter()))
        for k in ("n_full", "n_bare", "n_ambiguous", "n"):
            if any(k in v for v in sl):
                e[k] = sum(v.get(k, 0) for v in sl)
        e.pop("units", None)
        e.pop("bare_probable", None)
        out[eid] = e
    return out


def main():
    global OFFLINE
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", required=True)
    ap.add_argument("--offline", action="store_true", help="use only cached Wikidata replies")
    ap.add_argument("--min-mentions", type=int, default=2, help="look up entries with at least this many mentions")
    a = ap.parse_args()
    OFFLINE = a.offline
    ner = json.loads((ROOT / "data" / "ner" / f"{a.vol}.json").read_text())
    persons, ambiguous, rejected = build_persons(ner)
    places, moved = build_places(ner)
    viaf = manuscripta_by_viaf()
    todo = sorted(persons.values(), key=lambda e: -(e["n_full"] + e["n_bare"] + e.get("n_ambiguous", 0)))
    looked = 0
    for e in todo:
        total = e["n_full"] + e["n_bare"] + e.get("n_ambiguous", 0)
        e["context_year"] = context_years(ner, e["units"])
        if total < a.min_mentions:
            e["status"], e["candidates"], e["wikidata"] = "skipped", [], None
            continue
        e["candidates"] = person_candidates(e)
        e["wikidata"], e["status"] = decide(e["candidates"], threshold=6, margin=2)
        chosen = next((c for c in e["candidates"] if c["qid"] == e["wikidata"]), None)
        e["manuscripta"] = viaf.get(chosen["viaf"]) if chosen and chosen.get("viaf") else None
        looked += 1
        if looked % 25 == 0:
            print(f"  persons looked up: {looked}", flush=True)
    for e in sorted(places.values(), key=lambda e: -e["n"]):
        if e["n"] < a.min_mentions:
            e["status"], e["candidates"], e["wikidata"] = "skipped", [], None
            continue
        e["candidates"] = place_candidates(e)
        e["wikidata"], e["status"] = decide(e["candidates"], threshold=6, margin=2)
    d = ROOT / "data" / "register"
    d.mkdir(parents=True, exist_ok=True)
    persons = merge_existing(d / "persons.json", persons, a.vol)
    places = merge_existing(d / "places.json", places, a.vol)
    (d / "persons.json").write_text(json.dumps(sorted(persons.values(), key=lambda e: e["id"]), ensure_ascii=False, indent=1))
    (d / "places.json").write_text(json.dumps(sorted(places.values(), key=lambda e: e["id"]), ensure_ascii=False, indent=1))
    (d / f"ambiguous-{a.vol}.json").write_text(json.dumps(ambiguous, ensure_ascii=False, indent=1))
    view = lambda reg: {k: e | e["vols"][a.vol] for k, e in reg.items() if a.vol in e.get("vols", {})}
    vp, vl = view(persons), view(places)       # this volume's entries with its own counts
    (ROOT / "report" / f"register-{a.vol}.md").write_text(report(a.vol, vp, vl, ambiguous, rejected, moved))
    st = Counter(e["status"] for e in vp.values())
    sp = Counter(e["status"] for e in vl.values())
    print(f"register (all volumes): {len(persons)} persons, {len(places)} places")
    persons, places = vp, vl
    print(f"persons: {len(persons)} entries {dict(st)}; ambiguous surnames: {len(ambiguous)} "
          f"({sum(x['mentions'] for x in ambiguous.values())} mentions); places: {len(places)} {dict(sp)}")


def report(vol, persons, places, ambiguous, rejected, moved):
    P = sorted(persons.values(), key=lambda e: -(e["n_full"] + e["n_bare"] + e.get("n_ambiguous", 0)))
    st = Counter(e["status"] for e in P)
    L = [f"# Register draft, vol. {vol}", "",
         f"**Persons:** {len(P)} entries: " + ", ".join(f"{k} {v}" for k, v in st.items()) +
         f". With a manuscripta link: {sum(1 for e in P if e.get('manuscripta'))}.", "",
         f"**Ambiguous bare surnames:** {len(ambiguous)} surnames, {sum(x['mentions'] for x in ambiguous.values())} mentions "
         "(the review queue for phase 3):", ""]
    for nf, x in sorted(ambiguous.items(), key=lambda kv: -kv[1]["mentions"]):
        L.append(f"- **{nf}** × {x['mentions']}: " + ", ".join(persons[c]["name"] for c in x["candidates"]))
    L += ["", f"**Rejected as non-persons:** " + ", ".join(f"{f} ({n})" for f, n in rejected.most_common(40)), "",
          "## Persons by frequency (top 120)", "",
          "| # | entry | mentions (full + bare + ambiguous) | year in context | status | Wikidata (best candidate) | manuscripta |",
          "|---|---|---|---|---|---|---|"]
    for k, e in enumerate(P[:120], 1):
        c = e["candidates"][0] if e.get("candidates") else None
        best = (f"{'✓ ' if e['wikidata'] else ''}[{c['label']}](https://www.wikidata.org/wiki/{c['qid']}) "
                f"{c.get('birth') or '?'}–{c.get('death') or '?'}, {c['desc'][:50]} (score {c['score']})") if c else "–"
        L.append(f"| {k} | {e['name']} | {e['n_full']} + {e['n_bare']} + {e.get('n_ambiguous', 0)} | "
                 f"{e.get('context_year') or ''} | {e['status']} | {best} | {'✓' if e.get('manuscripta') else ''} |")
    PL = sorted(places.values(), key=lambda e: -e["n"])
    sp = Counter(e["status"] for e in PL)
    L += ["", f"## Places: {len(PL)} entries: " + ", ".join(f"{k} {v}" for k, v in sp.items()), "",
          "Moved out as institutions: " + ", ".join(f"{f} ({n})" for f, n in moved.most_common(20)), "",
          "| place | mentions | status | Wikidata (best candidate) |", "|---|---|---|---|"]
    for e in PL[:60]:
        c = e["candidates"][0] if e.get("candidates") else None
        best = (f"{'✓ ' if e['wikidata'] else ''}[{c['label']}](https://www.wikidata.org/wiki/{c['qid']}), "
                f"{c['desc'][:50]} (score {c['score']})") if c else "–"
        L.append(f"| {e['name']} | {e['n']} | {e['status']} | {best} |")
    return "\n".join(L) + "\n"


OFFLINE = False
if __name__ == "__main__":
    main()
