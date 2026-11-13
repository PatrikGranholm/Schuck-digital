"""OCR bake-off: CER/WER per region against ground truth (or another engine).

  python scripts/evaluate.py --engines textlayer,dots-layout [--ref gt] [--split test]
      -> report/ocr-bakeoff.md (or report/agreement-<ref>.md when --ref is an engine)

Levels (gt/CONVENTIONS.md):
  dipl  markup removed, whitespace collapsed; spelling, case, quotes, digits as printed
  norm  + casefolded, quote/dash styles folded, space before punctuation removed
Regions: body, margin, foot, head, and 'text' = body+margin+foot (region assignment
ignored: concatenated in that order). Paragraphs within a region are joined by spaces.
Also reported: bag-of-words error on 'text' (order-free; robust to reading order),
digit-token accuracy, italic F1 (character level, where both sides mark italics).
"""
import argparse
import csv
import re
import unicodedata
from collections import Counter

import edlib
from rapidfuzz.distance import Levenshtein

from common import GT, REPORT
from regions import load

SCORED = ["body", "margin", "foot", "head"]


def strip_markup(s):
    s = re.sub(r"\^(\d+)", r"\1", s)
    return re.sub(r"[{}<>]", "", s)


def dipl(s):
    s = unicodedata.normalize("NFC", strip_markup(s)).replace("­", "")
    return re.sub(r"\s+", " ", s).strip()


def norm(s):
    s = dipl(s).casefold()
    s = re.sub(r"[»«„“”\"]", '"', s)
    s = re.sub(r"[’‘`´]", "'", s)
    s = re.sub(r"[–—‐-]", "-", s)
    return re.sub(r" ([.,;:!?)])", r"\1", s)


def dist(a, b):
    return edlib.align(a, b)["editDistance"] if (a or b) else 0


def italic_mask(s):
    """Per character of dipl(s): True if inside {...}."""
    out, ital = [], False
    for c in re.sub(r"\^(\d+)", r"\1", s):
        if c == "{":
            ital = True
        elif c == "}":
            ital = False
        elif c not in "<>":
            out.append(ital)
    return out


def score_page(ref, hyp):
    r = {}
    for reg in SCORED + ["text"]:
        a = " ".join(ref[k] for k in ("body", "margin", "foot")) if reg == "text" else ref[reg]
        b = " ".join(hyp[k] for k in ("body", "margin", "foot")) if reg == "text" else hyp[reg]
        a, b = a.replace("\n", " "), b.replace("\n", " ")
        if not dipl(a):
            continue
        for lvl, f in (("dipl", dipl), ("norm", norm)):
            r[(reg, lvl)] = (dist(f(a), f(b)), len(f(a)))
        wa, wb = norm(a).split(), norm(b).split()
        r[(reg, "wer")] = (Levenshtein.distance(wa, wb), len(wa))
        if reg == "text":
            ca, cb = Counter(dipl(a).split()), Counter(dipl(b).split())
            r[("text", "bow")] = (sum((ca - cb).values()) + sum((cb - ca).values()), sum(ca.values()))
            da = Counter(w for w in dipl(a).split() if re.search(r"\d", w))
            db = Counter(w for w in dipl(b).split() if re.search(r"\d", w))
            r[("text", "digits")] = (sum((da - db).values()), sum(da.values()))
            ma, mb = italic_mask(a), italic_mask(b)
            if any(mb) and len(ma) == len(mb):  # exact-length case only (rough)
                tp = sum(x and y for x, y in zip(ma, mb))
                r[("text", "italic_f1")] = (tp, (sum(ma) + sum(mb)) / 2 or 1)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", required=True)
    ap.add_argument("--ref", default="gt")
    ap.add_argument("--split", choices=["all", "train", "test"], default="all")
    ap.add_argument("--sample", default=str(GT / "sample.tsv"))
    ap.add_argument("--vol", help="score every verified page of this volume (gt/pages/<vol>/) instead of the sample")
    a = ap.parse_args()
    if a.vol:
        rows = [{"vol": a.vol, "index": str(int(f.stem[1:])), "stratum": "", "split": ""}
                for f in sorted((GT / "pages" / a.vol).glob("p*.txt"))]
    else:
        rows = [r for r in csv.DictReader(open(a.sample), delimiter="\t")
                if a.split == "all" or r["split"] == a.split]
    if a.ref == "gt":
        rows = [r for r in rows if (GT / "pages" / r["vol"] / f"p{int(r['index']):04d}.txt").exists()]
    engines = a.engines.split(",")
    tot = {e: Counter() for e in engines}
    per_page = []
    for r in rows:
        ref = load(a.ref, r["vol"], int(r["index"]))
        line = [f"{r['vol']} p{int(r['index']):04d} ({r['stratum']})"]
        for e in engines:
            try:
                s = score_page(ref, load(e, r["vol"], int(r["index"])))
            except FileNotFoundError:
                line.append("–")
                continue
            for k, (err, n) in s.items():
                tot[e][(k, "err")] += err
                tot[e][(k, "n")] += n
            e_, n_ = s.get(("text", "dipl"), (0, 1))
            line.append(f"{100 * e_ / max(n_, 1):.2f}")
        per_page.append(line)

    def pct(e, k):
        n = tot[e][(k, "n")]
        return f"{100 * tot[e][(k, 'err')] / n:.2f}" if n else "–"

    title = "OCR bake-off vs ground truth" if a.ref == "gt" else f"Engine agreement with `{a.ref}` (not accuracy)"
    md = [f"# {title}", "", f"Pages: {len(rows)} (split: {a.split}). Error rates in %; lower is better.", "",
          "| engine | text dipl CER | text norm CER | text WER | bag-of-words | digit tokens | body dipl | margin dipl | foot dipl | head dipl |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for e in engines:
        md.append(f"| {e} | {pct(e, ('text', 'dipl'))} | {pct(e, ('text', 'norm'))} | {pct(e, ('text', 'wer'))} | "
                  f"{pct(e, ('text', 'bow'))} | {pct(e, ('text', 'digits'))} | {pct(e, ('body', 'dipl'))} | "
                  f"{pct(e, ('margin', 'dipl'))} | {pct(e, ('foot', 'dipl'))} | {pct(e, ('head', 'dipl'))} |")
    md += ["", "## Per page (text dipl CER)", "", "| page | " + " | ".join(engines) + " |",
           "|---|" + "---|" * len(engines)]
    md += ["| " + " | ".join(l) + " |" for l in per_page]
    REPORT.mkdir(exist_ok=True)
    suffix = f"-{a.vol}" if a.vol else ""
    out = REPORT / (f"ocr-bakeoff{suffix}.md" if a.ref == "gt" else f"agreement-{a.ref}{suffix}.md")
    out.write_text("\n".join(md) + "\n")
    print("\n".join(md[:5 + len(engines) + 1]))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
