"""Kraken on the text layer's line geometry: silver training lines and line OCR.

Run with the schlyter-digital venv (kraken 13, Python 3.12).

  silver: line images + .gt.txt for training, from lines where dots.mocr and the PDF text layer
          agree word for word (after dehyphenation and apostrophe folding). The target is the
          text layer's line as printed (line-end hyphen kept, ’ kept). Pages with ground truth
          and the sample test pages are excluded, so the GT test pages stay unseen.
    python scripts/kraken_lines.py silver --vol 1932 [--max-lines 6000]
        -> cache/kraken/<vol>-lines/*.png|.gt.txt, train.lst, val.lst

  ocr:    recognise every text-layer line (body and margin blocks) of the given pages with a
          kraken model, and write the text into a copy of the page's text-layer ALTO, so
          regions.from_textlayer/evaluate.py can read it as engine "kraken-<tag>".
    python scripts/kraken_lines.py ocr --vol 1932 --model M --tag kraken-r8 [--pages gt/vol1932.tsv | --all]
        -> ocr/kraken-<tag>/<vol>/pNNNN.xml

Line images are cropped from data/img with a small padding. Kraken's italic codepoints
(U+F0000 + c, from the Schlyter print models) are folded back to plain letters.
"""
import argparse
import csv
import difflib
import json
import random
import re
import sys
from pathlib import Path

from lxml import etree
from rapidfuzz import fuzz
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import GT, OCR, ROOT, img_path  # noqa: E402
from evaluate import dipl  # noqa: E402
from regions import load  # noqa: E402

NS = {"a": "http://www.loc.gov/standards/alto/ns-v4#"}
PAD = 6
SPECIAL = re.compile(r"[þÞðÐáÁéÉíÍóÓúÚýÝøØæÆœŒ]")
ASCII = str.maketrans("þÞðÐáÁéÉíÍóÓúÚýÝøØæÆœŒ", "pPdDaAeEiIoOuUyYoOaAoO")


def fold(w):
    return re.sub(r"[’‘`´]", "'", w)


def alto_lines(vol, idx):
    """Text-layer lines in reading order of the ALTO: [(element, [words], bbox)]."""
    t = etree.parse(str(OCR / "textlayer" / vol / f"p{idx:04d}.xml"))
    out = []
    for tl in t.iterfind(".//a:TextLine", NS):
        words = [s.get("CONTENT") for s in tl.iterfind("a:String", NS)]
        x, y, w, h = (int(tl.get(k)) for k in ("HPOS", "VPOS", "WIDTH", "HEIGHT"))
        out.append((tl, words, (x, y, x + w, y + h)))
    return t, out


def joined_tokens(lines):
    """Dehyphenated token sequence over the lines; each token lists the line indices it spans."""
    toks = []
    for li, (_, words, _) in enumerate(lines):
        for k, w in enumerate(words):
            if not w:
                continue
            if k == 0 and toks and (toks[-1][0].endswith("­") or (toks[-1][0].endswith("-") and w[:1].islower())):
                toks[-1] = (toks[-1][0][:-1] + w, toks[-1][1] + [li])
            else:
                toks.append((w, [li]))
    return toks


def printed(words):
    """A text-layer line as printed: the soft hyphen at a line end is a visible hyphen."""
    return " ".join(words).replace("­", "-")


def silver(a):
    out = Path(a.out) if a.out else ROOT / "cache" / "kraken" / f"{a.vol}-lines"
    out.mkdir(parents=True, exist_ok=True)
    pm = json.loads((ROOT / "data" / "pagemap" / f"{a.vol}.json").read_text())
    excluded = {int(f.stem[1:]) for f in (GT / "pages" / a.vol).glob("p*.txt")}
    excluded |= {int(r["index"]) for r in csv.DictReader(open(GT / "sample.tsv"), delimiter="\t")
                 if r["vol"] == a.vol and r["split"] == "test"}
    items, seen_lines, kept_lines = [], 0, 0
    for r in pm:
        idx = r["index"]
        if r["how"] not in ("read", "filled") or idx in excluded:
            continue
        try:
            d = load("dots-layout", a.vol, idx)
        except FileNotFoundError:
            continue
        dw = [fold(w) for w in dipl(" ".join(d[k] for k in ("head", "body", "margin", "foot", "pageno"))).split()]
        _, lines = alto_lines(a.vol, idx)
        toks = joined_tokens(lines)
        tw = [fold(dipl(t)) for t, _ in toks]
        ok_tok, fix = set(), {}
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, tw, dw, autojunk=False).get_opcodes():
            if op == "equal":
                ok_tok.update(range(i1, i2))
            elif a.special and op == "replace" and i2 - i1 == j2 - j1:
                # the text layer cannot read Nordic/accented letters: take dots.mocr's word when the two
                # differ only there (same length, same letters once those characters are folded)
                for k in range(i2 - i1):
                    t_w, d_w = tw[i1 + k], dw[j1 + k]
                    if SPECIAL.search(d_w) and len(toks[i1 + k][1]) == 1 and abs(len(t_w) - len(d_w)) <= 2 and \
                            fuzz.ratio(d_w.translate(ASCII), t_w) >= 60:
                        fix[i1 + k] = d_w
        bad_lines = {li for k, (_, lis) in enumerate(toks) if k not in ok_tok and k not in fix for li in lis}
        fixed_lines = {}
        for k, w in fix.items():
            fixed_lines.setdefault(toks[k][1][0], []).append((toks[k][0], w))
        img = None
        for li, (tl, words, box) in enumerate(lines):
            seen_lines += 1
            if li in bad_lines or len(words) < 2:
                continue
            text = printed(words)
            special = False
            if li in fixed_lines:                       # put dots.mocr's special characters into the line
                for tl_word, d_word in fixed_lines[li]:
                    text = text.replace(tl_word.replace("\u00ad", "-"), d_word, 1)
                special = True
            if img is None:
                img = Image.open(img_path(a.vol, idx)).convert("L")
            x0, y0, x1, y1 = box
            crop = img.crop((max(0, x0 - PAD), max(0, y0 - PAD), min(img.width, x1 + PAD), min(img.height, y1 + PAD)))
            stem = out / f"p{idx:04d}_l{li:03d}"
            crop.save(stem.with_suffix(".png"))
            stem.with_suffix(".gt.txt").write_text(text, encoding="utf-8")
            items.append((special or bool(SPECIAL.search(text)), str(stem.with_suffix(".png"))))
            kept_lines += 1
    random.seed(1)
    random.shuffle(items)
    spec = [p for sp, p in items if sp]                 # lines with rare characters: all of them
    rest = [p for sp, p in items if not sp]
    items = spec + rest[: max(0, a.max_lines - len(spec))]
    random.shuffle(items)
    print(f"lines with special characters: {len(spec)}")
    nval = max(50, len(items) // 20)
    (out / "val.lst").write_text("\n".join(items[:nval]) + "\n")
    train = items[nval:]
    train += [p for p in train if p in set(spec)] * 2          # rare characters: three times the weight
    (out / "train.lst").write_text("\n".join(train) + "\n")
    print(f"{a.vol}: {seen_lines} text-layer lines on non-GT pages, {kept_lines} agree "
          f"({100 * kept_lines / max(1, seen_lines):.0f}%); using {len(items)} "
          f"({len(items) - nval} train, {nval} val) -> {out}")


def ocr(a):
    from kraken.configs import RecognitionInferenceConfig
    from kraken.containers import BBoxLine, Segmentation
    from kraken.tasks import RecognitionTaskModel      # kraken 13 API (loads .safetensors models)
    net = RecognitionTaskModel.load_model(Path(a.model))
    config = RecognitionInferenceConfig(accelerator="gpu" if a.device.startswith("cuda") else "cpu",
                                        batch_size=16, padding=16, num_line_workers=4)
    if a.all:
        pm = json.loads((ROOT / "data" / "pagemap" / f"{a.vol}.json").read_text())
        pages = [r["index"] for r in pm if r["how"] in ("read", "filled")]
    else:
        pages = [int(r["index"]) for r in csv.DictReader(open(a.pages), delimiter="\t") if r["vol"] == a.vol]
    outdir = OCR / a.tag / a.vol
    outdir.mkdir(parents=True, exist_ok=True)
    for idx in pages:
        t, lines = alto_lines(a.vol, idx)
        if not lines:
            continue
        im = Image.open(img_path(a.vol, idx)).convert("L")
        seg = Segmentation(type="bbox", imagename=str(img_path(a.vol, idx)), text_direction="horizontal-lr",
                           script_detection=False,
                           lines=[BBoxLine(id=f"l{k}", bbox=(max(0, b[0] - PAD), max(0, b[1] - PAD),
                                                             min(im.width, b[2] + PAD), min(im.height, b[3] + PAD)))
                                  for k, (_, _, b) in enumerate(lines)])
        texts = [rec.prediction for rec in net.predict(im=im, segmentation=seg, config=config)]
        for (tl, _, box), text in zip(lines, texts):
            text = "".join(chr(ord(c) - 0xF0000) if ord(c) >= 0xF0000 else c for c in text)
            for s in list(tl):
                tl.remove(s)
            for k, w in enumerate(text.split()):
                if k:
                    etree.SubElement(tl, "{http://www.loc.gov/standards/alto/ns-v4#}SP")
                etree.SubElement(tl, "{http://www.loc.gov/standards/alto/ns-v4#}String", HPOS=str(box[0]),
                                 VPOS=str(box[1]), WIDTH=str(box[2] - box[0]), HEIGHT=str(box[3] - box[1]), CONTENT=w)
        t.write(str(outdir / f"p{idx:04d}.xml"), encoding="UTF-8", xml_declaration=True)
    print(f"{a.tag}: {len(pages)} pages -> {outdir}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("silver")
    s.add_argument("--vol", required=True)
    s.add_argument("--max-lines", type=int, default=6000)
    s.add_argument("--special", action="store_true",
                   help="also lines where the text layer only misreads þ/ð/á/ó/ø/æ...: dots.mocr's word is used")
    s.add_argument("--out", help="output folder (default cache/kraken/<vol>-lines)")
    o = sub.add_parser("ocr")
    o.add_argument("--vol", required=True)
    o.add_argument("--model", required=True)
    o.add_argument("--tag", required=True)
    o.add_argument("--pages", default=str(GT / "vol1932.tsv"))
    o.add_argument("--all", action="store_true")
    o.add_argument("--device", default="cuda:0")
    a = ap.parse_args()
    silver(a) if a.cmd == "silver" else ocr(a)


if __name__ == "__main__":
    main()
