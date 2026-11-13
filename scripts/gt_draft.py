"""Ground-truth drafts for the sample pages, plus a checklist of engine disagreements.

  python scripts/gt_draft.py [--base dots-layout] [--other textlayer]

For each page in gt/sample.tsv writes
  gt/drafts/<vol>/pNNNN.txt        the base engine's text in GT format
  gt/drafts/<vol>/pNNNN.check.txt  word-level differences base vs other, per region:
                                   these are the first places to check against the image
A draft becomes ground truth only when copied to gt/pages/ after a full check
(italics {..} and letterspacing <..> must be added by hand; neither engine gives them).
"""
import argparse
import csv
import difflib

from common import GT
from regions import REGIONS, load, write_gt
from evaluate import dipl


def diffs(a, b):
    wa, wb = dipl(a).split(), dipl(b).split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if op != "equal":
            ctx = " ".join(wa[max(0, i1 - 3):i1])
            out.append(f"  …{ctx} [{' '.join(wa[i1:i2])}] ≠ [{' '.join(wb[j1:j2])}]")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="dots-layout")
    ap.add_argument("--other", default="textlayer")
    a = ap.parse_args()
    n_diff = 0
    for r in csv.DictReader(open(GT / "sample.tsv"), delimiter="\t"):
        vol, idx = r["vol"], int(r["index"])
        base, other = load(a.base, vol, idx), load(a.other, vol, idx)
        d = GT / "drafts" / vol
        d.mkdir(parents=True, exist_ok=True)
        (d / f"p{idx:04d}.txt").write_text(write_gt(base), encoding="utf-8")
        lines = [f"# {vol} p{idx:04d} (printed p. {r['page']}): {a.base} [..] vs {a.other} [..]"]
        for reg in REGIONS:
            if base[reg] or other[reg]:
                dd = diffs(base[reg], other[reg])
                n_diff += len(dd)
                lines += [f"#{reg}: {len(dd)} differences"] + dd
        (d / f"p{idx:04d}.check.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"drafts + checklists -> {GT / 'drafts'}; {n_diff} differences in total")


if __name__ == "__main__":
    main()
