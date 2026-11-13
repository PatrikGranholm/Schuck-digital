"""NER phase 1: detect person and place mentions in a volume's TEI (KB-BERT).

  ../HS-nominalkatalog/.venv/bin/python scripts/ner.py --vol 1932
      -> data/ner/<vol>.json, report/ner-<vol>.md

Runs in the HS-nominalkatalog venv (transformers + torch with CUDA; the model
KB/bert-base-swedish-cased-ner is cached in ~/.cache/huggingface). Standard library
otherwise: the TEI is read with xml.etree.

Text units are the TEI's <p>, <ab>, <head>, <titlePart>, table cells and notes
(footnotes and sidenotes are units of their own, cut out of their paragraph). Each
unit keeps its page (from the last <pb>), chapter, kind, and the character ranges of
quotations and source citations inside it. The model reads at most 512 tokens, so
long units are cut into sentence-bounded chunks (~1000 characters) and offsets
mapped back. Raw model output is cached by unit-text hash in data/ner/<vol>-raw.json,
so a rerun after OCR corrections only processes changed units.

Clean-up rules (after HS-nominalkatalog pipeline/names.py), applied to PER/LOC:
  - score below 0.8: dropped
  - inside a source citation (<bibl type="source">): dropped. There "Per Brahe" or
    "De la Gardie" name an archival series, not a mention in the running text.
  - a name followed by vol./saml./sign./fol./N:o, or ending in -samlingen /
    -samling: a collection, dropped
  - a name made only of lowercase letters, digits or punctuation: dropped
Mentions keep their printed form; normalisation and clustering happen in phase 2.
"""
import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T = "{http://www.tei-c.org/ns/1.0}"
MODEL = "KB/bert-base-swedish-cased-ner"
MIN_SCORE = 0.8
UNIT_TAGS = {"p", "ab", "head", "titlePart", "cell"}
SKIP_TEXT = {"fw", "sic"}
COLLECTION_AFTER = re.compile(r"\s*[,.]?\s*(?:vol\.|saml\.|samlingen\b|sign\.|fol\.|N:o|n:o)", re.I)
COLLECTION_NAME = re.compile(r"samling(?:en)?$", re.I)


# ---------------------------------------------------------------- units from TEI

def extract_units(tei_path):
    root = ET.parse(tei_path).getroot()
    units, state = [], {"page": None, "facs": None, "chapter": None}

    def new_unit(kind, el):
        return {"kind": kind, "page": state["page"], "facs": state["facs"], "chapter": state["chapter"],
                "buf": [], "quotes": [], "bibl": [], "pages": [], "el": el}

    def walk(el, u):
        """Append el's text to unit u; nested notes become their own units."""
        tag = el.tag.replace(T, "")
        if tag == "pb":
            state["page"], state["facs"] = el.get("n"), (el.get("facs") or "")[1:]
            if u is not None:
                u["pages"].append([sum(map(len, u["buf"])), state["page"]])
            return
        if tag in SKIP_TEXT:
            return
        if tag == "note":
            n = new_unit("footnote" if el.get("place") == "foot" else "sidenote", el)
            fill(el, n)
            return
        start = sum(map(len, u["buf"])) if u is not None else 0
        if el.text and u is not None:
            u["buf"].append(el.text)
        for c in el:
            walk(c, u)
            if c.tail and u is not None:
                u["buf"].append(c.tail)
        end = sum(map(len, u["buf"])) if u is not None else 0
        if u is not None and tag == "quote":
            u["quotes"].append([start, end])
        if u is not None and tag == "bibl" and el.get("type") == "source":
            u["bibl"].append([start, end])

    def fill(el, u):
        if el.text:
            u["buf"].append(el.text)
        for c in el:
            walk(c, u)
            if c.tail:
                u["buf"].append(c.tail)
        u["text"] = "".join(u["buf"])
        units.append(u)

    def visit(el):
        tag = el.tag.replace(T, "")
        if tag == "div" and el.get("type") == "chapter":
            state["chapter"] = el.get("n")
        if tag == "pb":
            state["page"], state["facs"] = el.get("n"), (el.get("facs") or "")[1:]
            return
        if tag in ("teiHeader", "facsimile", "fw"):
            return
        if tag in UNIT_TAGS:
            fill(el, new_unit(tag, el))
            return
        for c in el:
            visit(c)

    visit(root)
    out = []
    for k, u in enumerate(units):
        # normalise whitespace but keep offsets valid: replace runs by single spaces via a map
        text = u["text"]
        out.append({"id": f"u{k:05d}", "kind": u["kind"], "page": u["page"], "facs": u["facs"],
                    "chapter": u["chapter"], "text": text, "quotes": u["quotes"], "bibl": u["bibl"],
                    "pages": u["pages"], "sha": hashlib.sha1(text.encode()).hexdigest()[:12]})
    return [u for u in out if u["text"].strip()]


# ---------------------------------------------------------------- model

def chunks(text, size=1000):
    """Sentence-bounded chunks (start, end) of at most ~size characters."""
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind(". ", start, end), text.rfind("; ", start, end))
            if cut <= start + size // 3:
                cut = text.rfind(" ", start, end)
            end = cut + 1 if cut > start else end
        out.append((start, end))
        start = end
    return out


def run_model(units, cache_path, batch_size=32):
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [u for u in units if u["sha"] not in cache]
    if todo:
        import torch
        from transformers import pipeline
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        ner = pipeline("ner", model=MODEL, tokenizer=MODEL, aggregation_strategy="max", device=dev)
        pieces = [(u["sha"], a, u["text"][a:b]) for u in todo for a, b in chunks(u["text"]) if u["text"][a:b].strip()]
        res = ner([p[2] for p in pieces], batch_size=batch_size)
        for u in todo:
            cache[u["sha"]] = []
        for (sha, off, _), ents in zip(pieces, res):
            for e in ents:
                cache[sha].append([e["entity_group"], off + int(e["start"]), off + int(e["end"]), round(float(e["score"]), 3)])
        cache_path.write_text(json.dumps(cache))
        print(f"model: {len(todo)} units ({len(pieces)} chunks) on {dev}", file=sys.stderr)
    return cache


# ---------------------------------------------------------------- clean-up

def inside(a, b, ranges):
    return any(s <= a and b <= e for s, e in ranges)


def mentions(units, cache):
    out, dropped = [], Counter()
    for u in units:
        t = u["text"]
        for label, a, b, score in cache.get(u["sha"], []):
            if label not in ("PER", "LOC", "ORG"):
                continue
            # extend to whole words and trim punctuation the model sometimes includes
            while a > 0 and t[a - 1].isalpha():
                a -= 1
            while b < len(t) and t[b].isalpha():
                b += 1
            s = t[a:b]
            lead = len(s) - len(s.lstrip(" »«(\"'"))
            s2 = s.strip(" »«()\"',.;:")
            a, b = a + lead, a + lead + len(s2)
            s = s2
            why = None
            if score < MIN_SCORE:
                why = "low score"
            elif inside(a, b, u["bibl"]):
                why = "in source citation"
            elif label == "PER" and (COLLECTION_AFTER.match(t, b) or COLLECTION_NAME.search(s)):
                why = "collection"
            elif not re.search(r"[A-ZÅÄÖÆØÞ]", s):
                why = "no capital"
            if why:
                dropped[(label, why)] += 1
                continue
            page = u["page"]
            for off, p in u["pages"]:
                if off <= a:
                    page = p
            out.append({"unit": u["id"], "kind": u["kind"], "label": label, "text": s, "start": a, "end": b,
                        "score": score, "page": page, "chapter": u["chapter"],
                        "in_quote": inside(a, b, u["quotes"])})
    return out, dropped


# ---------------------------------------------------------------- report

def surname(s):
    s = re.sub(r"[’']s?$", "", s)
    return s.split()[-1] if s.split() else s


def report(vol, units, ms, dropped):
    by = defaultdict(Counter)
    for m in ms:
        by[m["label"]][re.sub(r"[’']s?$|’$", "", m["text"])] += 1
    per = [m for m in ms if m["label"] == "PER"]
    loc = [m for m in ms if m["label"] == "LOC"]
    groups = defaultdict(Counter)
    for m in per:
        groups[surname(m["text"])][m["text"]] += 1
    multi = sorted(((k, v) for k, v in groups.items() if len(v) > 1), key=lambda kv: -sum(kv[1].values()))
    words = sum(len(u["text"].split()) for u in units)
    L = [f"# NER phase 1: vol. {vol}", "",
         f"Model `{MODEL}`, score ≥ {MIN_SCORE}. {len(units)} text units, {words} words.", "",
         "| type | mentions | distinct forms | in quotations | in footnotes | in sidenotes |", "|---|---|---|---|---|---|"]
    for lab in ("PER", "LOC", "ORG"):
        mm = [m for m in ms if m["label"] == lab]
        L.append(f"| {lab} | {len(mm)} | {len(by[lab])} | {sum(m['in_quote'] for m in mm)} | "
                 f"{sum(m['kind'] == 'footnote' for m in mm)} | {sum(m['kind'] == 'sidenote' for m in mm)} |")
    L += ["", "Dropped by the clean-up rules: " + ", ".join(f"{l} {w}: {n}" for (l, w), n in sorted(dropped.items())), "",
          f"## Persons: {len(groups)} distinct surnames", "",
          "Most frequent printed forms (genitive -s/’ folded):", ""]
    L += [f"{n} × {f}" + "  " for f, n in by["PER"].most_common(80)]
    L += ["", "## Surnames written in several forms (candidates for one person, or several)", ""]
    for k, v in multi[:50]:
        L.append(f"- **{k}** ({sum(v.values())}): " + ", ".join(f"{f} {n}" for f, n in v.most_common(8)))
    L += ["", f"## Places: {len(by['LOC'])} distinct forms", ""]
    L += [f"{n} × {f}" + "  " for f, n in by["LOC"].most_common(60)]
    L += ["", "## Organisations (kept for information only)", ""]
    L += [f"{n} × {f}" + "  " for f, n in by["ORG"].most_common(30)]
    L += ["", "## Single-mention persons (sample, alphabetical)", ""]
    singles = sorted(f for f, n in by["PER"].items() if n == 1)
    L.append(", ".join(singles[:150]) + (" …" if len(singles) > 150 else ""))
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", required=True)
    a = ap.parse_args()
    units = extract_units(ROOT / "tei" / f"schuck-{a.vol}.xml")
    d = ROOT / "data" / "ner"
    d.mkdir(parents=True, exist_ok=True)
    cache = run_model(units, d / f"{a.vol}-raw.json")
    ms, dropped = mentions(units, cache)
    (d / f"{a.vol}.json").write_text(json.dumps(
        {"vol": a.vol, "model": MODEL, "min_score": MIN_SCORE,
         "units": [{k: u[k] for k in ("id", "kind", "page", "facs", "chapter", "sha", "text", "quotes")} for u in units],
         "mentions": ms}, ensure_ascii=False, indent=0))
    (ROOT / "report").mkdir(exist_ok=True)
    (ROOT / "report" / f"ner-{a.vol}.md").write_text(report(a.vol, units, ms, dropped))
    c = Counter(m["label"] for m in ms)
    print(f"{a.vol}: {len(units)} units, mentions {dict(c)}; dropped {sum(dropped.values())} "
          f"-> data/ner/{a.vol}.json, report/ner-{a.vol}.md")


if __name__ == "__main__":
    main()
