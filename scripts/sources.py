"""Inventory of the archival sources Schück cites: a first step towards linking them to IIIF facsimiles.

  python scripts/sources.py [--vol all]   -> data/sources/citations.tsv, report/sources.md

Reads every parenthesis in the running text and every footnote of tei/schuck-<vol>.xml, splits it
at semicolons and classifies each part by the holding institution it names. The TEI's own
<bibl type="source"> (to_tei.py's grammar) only catches citations that open with a repository
abbreviation ("(K. B. sign. …", "(R. A. …"); this inventory also catches the many that name only
the series ("Kanslikoll. Inkomna skrivelser", "Riksregistraturet", "Prot. den 13 februari") and
marks each row with whether the TEI already tags it, so the grammar can be widened where it matters.

Holders (cues at the start of a citation, after an optional "jfr", "se" or "i"):
  RA    Riksarkivet, named or implied by a series it holds (Kanslikollegiet, Riksregistraturet, ...)
  KB    Kungliga biblioteket
  UUB   Uppsala universitetsbibliotek, including the Palmskiöld and Nordin collections
  VHAA  the academy's own archive (minutes, letters, inventories), cited as "V. H. A. A." in vols II-V. A bare "Prot." is taken to be
        the academy's minutes, which is what it means from vol. VI on; earlier volumes cite the
        Kanslikollegium's minutes as "Kanslikoll. prot."
  other Slottsarkivet, Skokloster, Kammararkivet, Nationalmuseum, Lund (L. B.), Linköping
  unknown  a bare shelfmark ("sign. F b. 5", "Papp. fol."), whose holder is named earlier in the text
"""
import argparse
import bisect
import csv
import re
from collections import Counter, defaultdict

from lxml import etree

from common import REPORT, ROOT, VOLUMES, parse_vols

T = "{http://www.tei-c.org/ns/1.0}"
HOLDERS = [
    ("RA", r"R\. ?A\.|Riksarkiv|Riksregistratur|Kanslikoll|Kanslersämbet|Civilexpedition|Sekreta utskott"
           r"|Mindre sekreta|Utskottshandl|Riksens ständers|Kammarkollegi|Biogra(?:ph|f)ica|Skrivelser till"
           r"|[Rr]egistratur|kanslikoll"),
    ("KB", r"K\. ?B\.|KB\b(?!-)|Kungl\. ?[Bb]ibl|Kungliga biblioteket"),
    ("UUB", r"U\. ?U\. ?B\.|U\. ?B\.|Ups(?:ala)?\.? ?univ\.?[- ]?bibl|Palmsk|Nordinska|Gahm"),
    ("VHAA", r"V\. ?H\. ?A\. ?A\.|Prot\.|Protokoll(?:et)? (?:den|\d)|[Aa]kad(?:emiens|s\.|:s|\.) (?:arkiv|protokoll)"
             r"|Vitt\.? ?[Aa]kad|Vitterhets Historie|Antikv\.?-?top|Antikvitetsarkiv|Kungl\. brev"),
    ("other", r"Slottsarkiv|Skokloster|Kammararkiv|Nationalmuse|L\. ?B\.|Linköpings stifts"),
    ("unknown", r"sign\. ?[A-ZÅÄÖ]|Papp\. fol|Perg\. fol"),   # the holder is named earlier in the text
]
CUE = re.compile(r"(?:(?:jfr|Jfr|se|Se|i|I)\.? )?(?:" + "|".join(f"(?P<{h}>{rx})" for h, rx in HOLDERS) + ")")
SHELF = re.compile(r"sign\.? ?[A-ZÅÄÖ][^,;)]*|vol\. ?\d+[^,;)]*")   # as in to_tei.py
# loose cue for the "unclassified" list: archive words in a parenthesis no holder rule caught
ARCHIVAL = re.compile(r"\b(?:sign\.|vol\.|fol\.|arkiv|protokoll|registratur|skrivelser|brev|handl)", re.I)


def blocks(vol):
    """(kind, text, page offsets, tagged ranges) for every paragraph and footnote; margin notes skipped."""
    tree = etree.parse(str(ROOT / "tei" / f"schuck-{vol}.xml"))
    page = [None]
    out = []

    def walk(el, buf, offs, tagged, notes):
        if el.tag == f"{T}pb" and el.get("n"):
            page[0] = el.get("n")
            offs.append((len("".join(buf)), page[0]))
        if el.tag == f"{T}note" and el is not root[0]:
            if el.get("place") != "margin":
                notes.append((el, page[0]))
            if el.tail:
                buf.append(el.tail)
            return
        start = len("".join(buf))
        if el.text:
            buf.append(el.text)
        for c in el:
            walk(c, buf, offs, tagged, notes)
        if el.tag == f"{T}bibl" and el.get("type") == "source":
            tagged.append((start, len("".join(buf))))
        if el is not root[0] and el.tail:
            buf.append(el.tail)

    root = [None]
    for el in tree.find(f"{T}text").iter(f"{T}pb", f"{T}p"):     # not the header's encoding notes
        if el.tag == f"{T}pb":
            if el.get("n") and not any(a.tag == f"{T}p" for a in el.iterancestors()):
                page[0] = el.get("n")
            continue
        if any(a.tag in (f"{T}p", f"{T}note") for a in el.iterancestors()):
            continue
        todo, body_end = [("body", el, page[0])], None
        while todo:
            kind, node, pg = todo.pop(0)
            root[0], page[0] = node, pg
            buf, offs, tagged, notes = [], [(0, pg)], [], []
            walk(node, buf, offs, tagged, notes)
            out.append((kind, "".join(buf), offs, tagged))
            body_end = body_end or page[0]          # the paragraph's last page, before its notes reset it
            todo += [("footnote", n, p) for n, p in notes]
        page[0] = body_end
    return out


def candidates(kind, text):
    """(start, end) spans that may hold citations: parentheses, and for footnotes the whole note."""
    spans = [(m.start(1), m.end(1)) for m in re.finditer(r"\(([^()]{3,300})\)", text)]
    if kind == "footnote":
        spans.append((0, len(text)))
    for a, b in spans:
        pos = a
        for part in re.split(r";\s*", text[a:b]):
            yield pos, pos + len(part), part
            pos += len(part) + 1
            while pos < b and text[pos] == " ":
                pos += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", default="all")
    a = ap.parse_args()
    vols = [v for v in parse_vols(a.vol) if (ROOT / "tei" / f"schuck-{v}.xml").exists()]
    rows, unclassified = [], defaultdict(Counter)
    for vol in vols:
        seen = set()
        for kind, text, offs, tagged in blocks(vol):
            starts = [o for o, _ in offs]
            for s, e, part in candidates(kind, text):
                part = part.strip()
                m = CUE.match(part)
                if not m:
                    if kind == "body" and ARCHIVAL.search(part) and len(part) < 120:
                        unclassified[vol][re.sub(r"\d.*", "", part)[:40].strip(" .,")] += 1
                    continue
                key = (kind, s + text[s:e].find(part))
                if key in seen:
                    continue
                seen.add(key)
                holder = m.lastgroup
                series = re.split(r"\s(?:sign\.|vol\.|fol\.|den|\d)", part[m.start(holder):], maxsplit=1)[0].strip(" .,:")
                shelf = "; ".join(x.group(0).rstrip(" .") for x in SHELF.finditer(part))
                page = offs[bisect.bisect_right(starts, key[1]) - 1][1]
                # overlap, not containment: "i K. B. sign. A 53" is tagged from "K. B." on
                is_tagged = any(ta < key[1] + len(part) and key[1] < tb for ta, tb in tagged)
                rows.append({"vol": vol, "part": VOLUMES[vol]["part"], "page": page, "where": kind, "holder": holder,
                             "series": series, "shelfmark": shelf, "tagged": "yes" if is_tagged else "no",
                             "citation": re.sub(r"\s+", " ", part)[:300]})
    out = ROOT / "data" / "sources" / "citations.tsv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    # report
    hs = [h for h, _ in HOLDERS]
    md = ["# Source citations: inventory", "",
          f"`scripts/sources.py` over the TEI of vols {', '.join(VOLUMES[v]['part'] for v in vols)}. "
          f"{len(rows)} citations; one row each in `data/sources/citations.tsv`. "
          "\"tagged\" = already a `<bibl type=\"source\">` in the TEI.", "",
          "## By holder and volume", "",
          "| vol | " + " | ".join(hs) + " | all | tagged |", "|---|" + "---|" * (len(hs) + 2)]
    for vol in vols:
        r = [x for x in rows if x["vol"] == vol]
        c = Counter(x["holder"] for x in r)
        md.append(f"| {VOLUMES[vol]['part']} | " + " | ".join(str(c[h]) for h in hs)
                  + f" | {len(r)} | {sum(x['tagged'] == 'yes' for x in r)} |")
    c = Counter(x["holder"] for x in rows)
    md += ["| **all** | " + " | ".join(f"**{c[h]}**" for h in hs)
           + f" | **{len(rows)}** | **{sum(x['tagged'] == 'yes' for x in rows)}** |", ""]
    for h in hs:
        r = [x for x in rows if x["holder"] == h]
        if not r:
            continue
        md += [f"## {h}: most cited series ({len(r)} citations, {sum(x['shelfmark'] != '' for x in r)} with a shelfmark)", "",
               "| citations | series | vols |", "|---|---|---|"]
        by = defaultdict(list)
        for x in r:
            by[x["series"]].append(x)
        for s, xs in sorted(by.items(), key=lambda kv: -len(kv[1]))[:25]:
            v = ", ".join(sorted({x["part"] for x in xs}, key=lambda p: list(VOLUMES).index(
                next(k for k, m in VOLUMES.items() if m["part"] == p))))
            md.append(f"| {len(xs)} | {s.replace('|', '/')} | {v} |")
        sh = Counter(x["shelfmark"] for x in r if x["shelfmark"])
        if sh:
            md += ["", "Most cited shelfmarks: " + ", ".join(f"`{s}` × {n}" for s, n in sh.most_common(15))]
        md.append("")
    md += ["## Unclassified parentheses with archival words", "",
           "Openings no holder rule caught (body text only, numbers cut), to widen the rules:", ""]
    for vol in vols:
        top = unclassified[vol].most_common(12)
        if top:
            md.append(f"- **{VOLUMES[vol]['part']}:** " + "; ".join(f"{s} × {n}" for s, n in top))
    rep = REPORT / "sources.md"
    rep.write_text("\n".join(md) + "\n")
    print("\n".join(md[4:6 + len(vols) + 2]))
    print(f"-> {out}\n-> {rep}")


if __name__ == "__main__":
    main()
