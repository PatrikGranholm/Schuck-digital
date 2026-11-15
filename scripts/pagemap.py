"""PDF page index -> printed page number, from the page numbers in the text layer.

  python scripts/pagemap.py --vol all     -> data/pagemap/<vol>.json

The printed number is the short numeric token on the last body line (the foot of the page;
the first line only ever gave false numbers, e.g. a stray mark on a frontispiece, vol. VI image 14).
Oldstyle figures are often misread (i5 = 15, l = 1, o/O = 0, s/S = 5), so tokens
are folded first. Anchors = the longest chain of read numbers that increases with
the image index and never advances faster than it (plates add unnumbered images).
Between two anchors the missing pages are filled in order when the image count
matches; otherwise images without running text (is_text: few lines or mostly
non-word tokens, i.e. plates, covers, blanks) are taken as plates
first; if the counts still differ the gap stays null and is flagged.
Records: {index, page, how: read|filled|plate|front|unresolved}.
"""
import argparse
import json
import re
from collections import Counter

from lxml import etree
from rapidfuzz import fuzz

from common import OCR, ROOT, VOLUMES, parse_vols

NS = {"a": "http://www.loc.gov/standards/alto/ns-v4#"}
FOLD = str.maketrans({"i": "1", "I": "1", "l": "1", "o": "0", "O": "0", "s": "5", "S": "5", "z": "2", "B": "8"})


def line_texts(path):
    t = etree.parse(str(path))
    out = []
    for tl in t.iterfind(".//a:TextBlock[@TAGREFS='body']/a:TextLine", NS):
        out.append(" ".join(s.get("CONTENT") for s in tl.iterfind("a:String", NS)))
    return out


def read_number(lines):
    for ln in lines[-1:]:
        for tok in ln.split():
            if 1 <= len(tok) <= 4:
                f = tok.translate(FOLD)
                if f.isdigit() and any(c.isdigit() for c in tok):
                    return int(f)
    return None


PLATE_LINES = 3
MIN_WORDLIKE = 0.65  # share of word-like tokens; covers, engraved facsimiles and blanks score lower


def is_text(lines):
    toks = " ".join(lines).split()
    if len(lines) <= PLATE_LINES or not toks:
        return False
    return sum(bool(re.fullmatch(r"[^\W\d_]{2,}[.,;:!?»«)-]*", w)) for w in toks) / len(toks) >= MIN_WORDLIKE


def same_head(lines, heads, page):
    """True if a text page's first line matches the running head of a numbered page of the same parity
    (p±2, p±4), ignoring spaces and case: the text layer often splits heads ('Antikvit et skolle giet II')."""
    if not is_text(lines):
        return False
    norm = lambda s: re.sub(r"\W", "", s).lower()
    first = norm(lines[0])
    refs = [norm(heads[q]) for q in (page - 2, page + 2, page - 4, page + 4) if q in heads]
    return bool(first) and any(len(r) >= 5 and fuzz.ratio(first, r) >= 85 for r in refs)


def chain(read):
    """Longest chain of (index, page) with page increasing and page step <= index step."""
    pts = [(i, p) for i, p in enumerate(read) if p is not None]
    best, prev = [1] * len(pts), [-1] * len(pts)
    for b in range(len(pts)):
        for a in range(b):
            (ia, pa), (ib, pb) = pts[a], pts[b]
            if pa < pb and pb - pa <= ib - ia and best[a] + 1 > best[b]:
                best[b], prev[b] = best[a] + 1, a
    if not pts:
        return []
    k = max(range(len(pts)), key=best.__getitem__)
    out = []
    while k >= 0:
        out.append(pts[k])
        k = prev[k]
    return out[::-1]


def build(vol):
    files = sorted((OCR / "textlayer" / vol).glob("p*.xml"))
    texts = [line_texts(f) for f in files]
    # a number only counts on a page with running text (blank leaves and plates carry stray digits)
    read = [read_number(t) if is_text(t) else None for t in texts]
    n = len(read)
    rec = [{"index": i + 1, "page": None, "how": "front"} for i in range(n)]
    anchors = chain(read)
    for i, p in anchors:
        rec[i].update(page=p, how="read")
    heads = {p: texts[i][0] for i, p in anchors if texts[i]}   # printed page -> first line (running head)
    for (ia, pa), (ib, pb) in zip(anchors, anchors[1:]):
        gap = list(range(ia + 1, ib))
        need = pb - pa - 1
        if len(gap) > need:
            plates = [i for i in gap if not is_text(texts[i])]
            for i in plates:
                rec[i]["how"] = "plate"
            gap = [i for i in gap if i not in plates]
        if len(gap) > need:   # unnumbered inserts with running text: extend from both anchors by running head
            left = []
            while len(left) < need and gap and same_head(texts[gap[0]], heads, pa + 1 + len(left)):
                left.append(gap.pop(0))
            right = []
            while len(left) + len(right) < need and gap and same_head(texts[gap[-1]], heads, pb - 1 - len(right)):
                right.insert(0, gap.pop())
            for k, i in enumerate(left):
                rec[i].update(page=pa + 1 + k, how="filled")
            for k, i in enumerate(right):
                rec[i].update(page=pb - len(right) + k, how="filled")
            need -= len(left) + len(right)
            if need == 0:
                for i in gap:
                    rec[i]["how"] = "plate"
                gap = []
        if len(gap) == need:
            for k, i in enumerate(gap):
                rec[i].update(page=pa + 1 + k, how="filled")
        else:
            for i in gap:
                rec[i]["how"] = "unresolved"
    if anchors:  # before the first anchor: count back over consecutive text pages down to page 1
        k, p = anchors[0][0] - 1, anchors[0][1] - 1
        while k >= 0 and p >= 1 and is_text(texts[k]):
            rec[k].update(page=p, how="filled")
            k, p = k - 1, p - 1
    if anchors:  # after the last anchor: fill consecutive text pages; from the first non-text image on
        il, pl = anchors[-1]  # (blank leaf, TOC with dot leaders) the count is unknown, so no numbers
        numbering = True
        for i in range(il + 1, n):
            numbering = numbering and is_text(texts[i])
            if numbering:
                pl += 1
                rec[i].update(page=pl, how="filled")
            else:
                rec[i]["how"] = "plate"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", default="all")
    a = ap.parse_args()
    d = ROOT / "data" / "pagemap"
    d.mkdir(parents=True, exist_ok=True)
    for vol in parse_vols(a.vol):
        m = build(vol)
        (d / f"{vol}.json").write_text(json.dumps(m, indent=0))
        pages = [r["page"] for r in m if r["page"]]
        how = Counter(r["how"] for r in m)
        unres = [r["index"] for r in m if r["how"] == "unresolved"]
        print(f"{vol} ({VOLUMES[vol]['part']}): {len(m)} images, pp. {min(pages)}-{max(pages)}, {dict(how)}"
              f"{', unresolved images ' + str(unres[:12]) if unres else ''}")


if __name__ == "__main__":
    main()
