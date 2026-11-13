"""Pick the OCR bake-off / ground-truth sample: 3 pages per volume, stratified.

  python scripts/sample.py [--seed 1] -> gt/sample.tsv  (vol, index, page, stratum, split)

Strata (from the text layer, numbered text pages only):
  quote    most guillemets (» «) among pages that also carry a marginal sidenote
  footnote a page with small-type lines at the foot (footnotes)
  body     a random remaining page
Split: per volume, the 'body' or 'footnote' page alternates as held-out test,
giving 8 test pages and 16 train pages.
"""
import argparse
import json
import random
import statistics

from lxml import etree

from common import GT, OCR, ROOT, parse_vols

NS = {"a": "http://www.loc.gov/standards/alto/ns-v4#"}


def features(path):
    t = etree.parse(str(path))
    body = t.findall(".//a:TextBlock[@TAGREFS='body']/a:TextLine", NS)
    text = " ".join(s.get("CONTENT") for s in t.iterfind(".//a:String", NS))
    hs = [int(l.get("HEIGHT")) for l in body]
    med = statistics.median(hs) if hs else 0
    # footnote: >= 2 of the last 6 lines (excluding the page number) clearly smaller than the body
    tail = [h for h in hs[:-1][-6:]]
    small = sum(h < 0.8 * med for h in tail)
    return {"lines": len(body), "guil": text.count("»") + text.count("«"),
            "margin": t.find(".//a:TextBlock[@TAGREFS='margin']", NS) is not None,
            "footnote": small >= 2}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", default="all")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    rows = []
    for n, vol in enumerate(parse_vols(a.vol)):
        pm = json.loads((ROOT / "data" / "pagemap" / f"{vol}.json").read_text())
        cand = {r["index"]: r["page"] for r in pm if r["how"] in ("read", "filled") and r["page"] > 10}
        f = {i: features(OCR / "textlayer" / vol / f"p{i:04d}.xml") for i in cand}
        f = {i: v for i, v in f.items() if v["lines"] >= 25}
        quote = max((i for i in f if f[i]["margin"]), key=lambda i: (f[i]["guil"], -i))
        fn = rng.choice(sorted(i for i in f if f[i]["footnote"] and i != quote))
        body = rng.choice(sorted(i for i in f if i not in (quote, fn) and not f[i]["footnote"]))
        test = body if n % 2 == 0 else fn
        for i, stratum in ((quote, "quote"), (fn, "footnote"), (body, "body")):
            rows.append((vol, i, cand[i], stratum, "test" if i == test else "train"))
    GT.mkdir(exist_ok=True)
    out = GT / "sample.tsv"
    out.write_text("vol\tindex\tpage\tstratum\tsplit\n" + "".join("\t".join(map(str, r)) + "\n" for r in rows))
    for r in rows:
        print(*r, sep="\t")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
