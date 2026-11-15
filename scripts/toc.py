"""Parse a volume's table of contents and check the TEI's marginal summaries against it.

  python scripts/toc.py --vol 1932     -> data/structure/<vol>-toc.json, report/sidenotes-<vol>.md

The TOC lists each chapter followed by its marginal topics with page numbers
("Fornminnenas förstöring 1, Den börjande göticismen 5, ..."). These are the sidenotes,
so the TOC is an independent check of the TEI's <note place="margin" type="summary">:
  found      a sidenote in the TEI matches the topic and sits on the TOC page (±1)
  misplaced  matched, but on another page
  in body    not a sidenote, but the topic text occurs in body text on that page
             (a sidenote OCR merged into the paragraph)
  missing    nowhere on the page
Text comes from the PDF text layer of the TOC images listed in the structure file.
"""
import argparse
import json
import re

from lxml import etree
from rapidfuzz import fuzz

from common import REPORT, ROOT
from regions import dehyphenate, from_textlayer

NS = {"t": "http://www.tei-c.org/ns/1.0"}
FOLD = str.maketrans({"i": "1", "I": "1", "l": "1", "o": "0", "O": "0", "°": "0", ";": "5", ">": "1", "?": "9",
                      "’": "9", "Ö": "6", "s": "5", "S": "5"})


def parse_toc(vol, images, chapters):
    """Topics ("name page, name page, ...") from the TOC text layer, assigned to chapters by page range.
    Lines that are only (part of) a chapter title, dot leaders or a page number are dropped first:
    the text layer splits hanging chapter titles over lines and out of order ('medeltidsskrifter' /
    'Svenska') and files the chapter page numbers at the foot of the page."""
    titles = [re.sub(r"\W", "", c["title"]).lower() for c in chapters]
    lines = []
    for i in images:
        page = from_textlayer(vol, i)
        for l in page["body"].split("\n"):
            core = re.sub(r"[ .…]{4,}\s*[\diIlo°O ]*$", "", l.strip())
            key = re.sub(r"\W", "", core).lower()
            if len(key) <= 4 or key == "innehåll" or any(key in t for t in titles):
                continue
            lines.append(l)
    text = dehyphenate(lines)
    # a chapter line still on a line with topics = title + dot leaders + page: cut it out
    # (its first word may be missing: chapter lines hang left of the column and the text layer
    # files those words as margin, so a title may start lowercase after the previous full stop)
    text = re.sub(r"(?:^|(?<=\. ))([^.,\d»]{3,80}?)\s*[ .…,]{6,}\s*[\diIlo°]{0,4}", ", ", text)
    starts = [c["page"] for c in chapters]
    out = [{"chapter": c["title"], "topics": []} for c in chapters]
    words = [set(re.findall(r"\w+", c["title"].lower())) for c in chapters]
    found = []
    # a lone letter other than I/l is not a page number ("Miracuia S. Erici 245": S is not page 5)
    for t in re.finditer(r"([^,]+?)\s+((?![oOsS°;>?’Ö]\s*(?:[,.>?’»;]|$))[\diIloOsS°;>?’Ö]{1,3}(?: ?\d)?)"
                         r"\s*(?=[,.>?’»;]|$)", text):
        name, raw = t.group(1).strip(" .,»>’"), t.group(2).replace(" ", "")
        num = raw.translate(FOLD)
        if not (num.isdigit() and name):
            continue
        m = re.search(r"\s(\d{1,2})$", name)               # "Birkenius 1 51": a page number split by OCR
        if m and int(m.group(1) + num) > (found[-1][1] if found else 0):
            name, num = name[:m.start()], m.group(1) + num
        for part in re.split(r"(?<=\s\d{3})\s|(?<=\s\d{2})\s(?=[A-ZÅÄÖ])", name):  # missing comma
            m = re.match(r"(.+)\s(\d{2,3})$", part)
            found.append((m.group(1), int(m.group(2))) if m else (part, int(num)))
    for name, n in found:
        k = max((j for j, s0 in enumerate(starts) if s0 <= n), default=0)
        # a chapter-title fragment before the first topic ("Urkundsutgivning Recesserna och landslagen"):
        # strip title words only when a capitalised topic follows ("Bureus' ungdom" keeps its name)
        w = name.split()
        j = 0
        while j < len(w) - 1 and set(re.findall(r"\w+", w[j].lower())) <= words[k]:
            j += 1
        tp = {"topic": name.strip(" ,"), "page": n}
        if j and w[j][:1].isupper():          # keep the full form too: "Bureus och Kabbala" is a real topic
            tp = {"topic": " ".join(w[j:]).strip(" ,"), "page": n, "full": tp["topic"]}
        out[k]["topics"].append(tp)
    return out


def tei_notes(vol):
    t = etree.parse(str(ROOT / "tei" / f"schuck-{vol}.xml"))
    notes, body = [], {}
    page = None
    for el in t.iter():
        if el.tag == f"{{{NS['t']}}}pb" and el.get("n"):
            page = int(el.get("n"))
        elif el.tag == f"{{{NS['t']}}}note" and el.get("place") == "margin":
            notes.append({"text": "".join(el.itertext()).strip(), "page": page})
        elif el.tag == f"{{{NS['t']}}}p" and page:
            # body text per page (approximate: whole paragraph credited to its pages)
            body.setdefault(page, []).append("".join(x for x in el.itertext()))
    return notes, {p: " ".join(v) for p, v in body.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", required=True)
    a = ap.parse_args()
    st = json.loads((ROOT / "data" / "structure" / f"{a.vol}.json").read_text())
    toc = parse_toc(a.vol, st["back"]["contents"], st["chapters"])
    (ROOT / "data" / "structure" / f"{a.vol}-toc.json").write_text(json.dumps(toc, ensure_ascii=False, indent=1))
    notes, body = tei_notes(a.vol)
    res = {"found": [], "misplaced": [], "in body": [], "missing": []}
    used = set()
    near = lambda tp, k: notes[k]["page"] is not None and abs(notes[k]["page"] - tp["page"]) <= 1
    topics = [tp for ch in toc for tp in ch["topics"]]
    match = {}
    # pass 1: a sidenote on the TOC page (±1); pass 2: best ratio anywhere, nearer page breaking ties
    # (topics recur: Verelius 131 and 262; similar ones must not cascade: Resor 1669, Resor 1670, ...)
    for strict in (True, False):
        for ti, tp in enumerate(topics):
            if ti in match:
                continue
            cands = [(max(fuzz.ratio(f.lower(), n["text"].lower()) for f in (tp["topic"], tp.get("full", tp["topic"]))), k)
                     for k, n in enumerate(notes)
                     if k not in used and (near(tp, k) or not strict)]
            cands = [c for c in cands if c[0] >= 80]
            if cands:
                r, k = max(cands, key=lambda c: (c[0], -abs((notes[c[1]]["page"] or 10**6) - tp["page"])))
                match[ti] = k
                used.add(k)
    for ti, tp in enumerate(topics):
        if ti in match:
            n = notes[match[ti]]
            res["found" if near(tp, match[ti]) else "misplaced"].append((tp, n))
        elif any(fuzz.partial_ratio(tp["topic"].lower(), body.get(p, "").lower()) >= 90
                 for p in (tp["page"], tp["page"] + 1)):
            res["in body"].append((tp, None))
        else:
            res["missing"].append((tp, None))
    extra = [n for k, n in enumerate(notes) if k not in used]
    n_topics = sum(len(c["topics"]) for c in toc)
    md = [f"# Sidenotes vs table of contents, vol. {a.vol}", "",
          f"TOC topics: {n_topics} in {len(toc)} chapters; TEI sidenotes: {len(notes)}.", "",
          "| result | count |", "|---|---|"]
    md += [f"| {k} | {len(v)} |" for k, v in res.items()] + [f"| TEI sidenote not in TOC | {len(extra)} |", ""]
    for k in ("misplaced", "in body", "missing"):
        md += [f"## {k}", ""] + [f"- p. {tp['page']}: {tp['topic']}" + (f" → TEI p. {n['page']}: {n['text']}" if n else "")
                                 for tp, n in res[k]] + [""]
    md += ["## TEI sidenotes not in the TOC", ""] + [f"- p. {n['page']}: {n['text']}" for n in extra]
    out = REPORT / f"sidenotes-{a.vol}.md"
    out.write_text("\n".join(md) + "\n")
    print("\n".join(md[:12]))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
