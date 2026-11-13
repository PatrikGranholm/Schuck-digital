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
    lines = []
    for i in images:
        page = from_textlayer(vol, i)
        lines += [l for l in page["body"].split("\n") if l.strip()]
    text = dehyphenate(lines)
    # a chapter line = title + dot leaders + page; the topics follow until the next chapter line
    # (its first word may be missing: chapter lines hang left of the column and the text layer
    # files those words as margin, so a title may start lowercase after the previous full stop)
    head = re.compile(r"(?:^|(?<=\. ))([^.,\d»]{3,80}?)\s*[ .…,]{6,}\s*[\diIlo°]{0,4}")
    heads = list(head.finditer(text))
    out = []
    for k, m in enumerate(heads):
        title = m.group(1).strip()
        best = max(chapters, key=lambda c: fuzz.partial_ratio(title.lower(), c["title"].lower()))
        body = text[m.end(): heads[k + 1].start() if k + 1 < len(heads) else len(text)]
        topics = []
        for t in re.finditer(r"([^,]+?)\s+([\diIloOsS°;>?’Ö]{1,3}(?: ?\d)?)\s*(?=[,.>?’»;]|$)", body):
            name, num = t.group(1).strip(" .,»>’"), t.group(2).replace(" ", "").translate(FOLD)
            if num.isdigit() and name:
                topics.append({"topic": name, "page": int(num)})
        out.append({"chapter": best["title"], "topics": topics})
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
    for ch in toc:
        for tp in ch["topics"]:
            best = max(((fuzz.ratio(tp["topic"].lower(), n["text"].lower()), k) for k, n in enumerate(notes)
                        if k not in used), default=(0, None))
            if best[0] >= 80:
                n = notes[best[1]]
                used.add(best[1])
                ok = n["page"] is not None and abs(n["page"] - tp["page"]) <= 1
                res["found" if ok else "misplaced"].append((tp, n))
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
