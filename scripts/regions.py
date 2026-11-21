"""Engine output -> region text (the ground-truth format in gt/CONVENTIONS.md).

A page is a dict {head, body, margin, foot, pageno, other} of strings, one
paragraph per line, end-of-line hyphenation removed. Markup as in the
conventions: {italic}, <letterspaced>, ^1 footnote reference.
"""
import json
import re
import statistics

from lxml import etree

from common import GT, OCR

REGIONS = ["head", "body", "margin", "foot", "pageno", "other"]
INLINE_HYPHEN = re.compile(r"(\w)- (?=[a-zåäöæøéü])(?!(?:och|eller|samt|som|men|el\.|resp\.|&)\b)")
SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
# printer's sheet signature at the foot of the first page of a gathering, e.g. "18—3264. II."
SIGNATURE = re.compile(r"[\dIl]{1,2}\s*[—–-]\s*\d{3,5}\.?(\s+[IVX]+\.?)?")
NS = {"a": "http://www.loc.gov/standards/alto/ns-v4#"}


def dehyphenate(lines):
    """Join print lines into one string; a line-end '-' before a lowercase letter is a break."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("\u00ad"):  # soft hyphen = always a line-end break
            out = out[:-1] + ln
        elif out.endswith("-") and ln[0].islower() and not out.endswith(" -"):
            out = out[:-1] + ln
        else:
            out = (out + " " + ln) if out else ln
    return out


# ---- ground truth ----------------------------------------------------------

def read_gt(path):
    page, cur = {r: [] for r in REGIONS}, None
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = re.fullmatch(r"#(\w+)\s*", ln)
        if m and m.group(1) in REGIONS:
            cur = m.group(1)
        elif cur:
            page[cur].append(ln)
    return {r: "\n".join(l for l in v if l.strip()) for r, v in page.items()}


def write_gt(page):
    return "".join(f"#{r}\n{page[r].strip()}\n" for r in REGIONS if page.get(r, "").strip())


# ---- PDF text layer (ALTO from textlayer.py) --------------------------------

def _textlayer_paragraphs(vol, index, engine="textlayer"):
    """{region: [paragraph]}, paragraph = [line], line = (VPOS, HPOS, HEIGHT, WIDTH, text, words),
    words = [(content, (x0, y0, x1, y1))] in image pixels."""
    t = etree.parse(str(OCR / engine / vol / f"p{index:04d}.xml"))
    W = int(t.find(".//a:Page", NS).get("WIDTH"))

    def lines(kind):
        out = []
        for l in t.iterfind(f".//a:TextBlock[@TAGREFS='{kind}']/a:TextLine", NS):
            words = [(s.get("CONTENT"), (int(s.get("HPOS")), int(s.get("VPOS")),
                                         int(s.get("HPOS")) + int(s.get("WIDTH")),
                                         int(s.get("VPOS")) + int(s.get("HEIGHT"))))
                     for s in l.iterfind("a:String", NS)]
            out.append((int(l.get("VPOS")), int(l.get("HPOS")), int(l.get("HEIGHT")), int(l.get("WIDTH")),
                        " ".join(w[0] for w in words), words))
        return sorted(out, key=lambda l: l[:4])

    body = lines("body")
    page = {r: [] for r in REGIONS}
    if body and re.fullmatch(r"[\dilIoOsS]{1,4}", body[-1][4].strip()):
        page["pageno"] = [[body.pop()]]
    if len(body) > 3 and body[0][3] < 0.75 * W and not body[0][4].rstrip().endswith((".", ",", "-")):
        page["head"] = [[body.pop(0)]]
    if body and SIGNATURE.fullmatch(body[-1][4].strip()):
        page["other"] = [[body.pop()]]
    hs = [l[2] for l in body]
    med = statistics.median(hs) if hs else 0
    foot = []
    while body and body[-1][2] < 0.8 * med:
        foot.insert(0, body.pop())
    page["body"] = paragraphs(body)
    page["foot"] = paragraphs(foot)
    groups, prev = [], None
    for l in lines("margin"):  # a vertical gap > 2 line heights starts a new sidenote
        if prev is None or l[0] - prev > 2.2 * max(l[2], 1):
            groups.append([])
        groups[-1].append(l)
        prev = l[0]
    page["margin"] = groups
    return page


def paragraphs(lines):
    """Split print lines into paragraphs at first-line indents (HPOS > column left + 0.6 line height)."""
    if not lines:
        return []
    left = statistics.median(l[1] for l in lines)
    paras = [[]]
    for l in lines:
        if paras[-1] and l[1] > left + 0.6 * l[2]:
            paras.append([])
        paras[-1].append(l)
    return [p for p in paras if p]


def from_textlayer(vol, index, engine="textlayer"):
    """Region text from an ALTO with the text layer's line structure (the text layer itself, or a
    line recogniser such as kraken that wrote its text into the same lines)."""
    return {r: "\n".join(dehyphenate([l[4] for l in p]) for p in paras)
            for r, paras in _textlayer_paragraphs(vol, index, engine).items()}


def textlayer_tokens(vol, index, engine="textlayer"):
    """Whitespace tokens of from_textlayer() in REGIONS order, each with its word box(es):
    [{"t": token, "region": r, "boxes": [[x0, y0, x1, y1], ...]}]. Dehyphenated tokens have two boxes.
    engine: an ALTO set on the text layer's lines (e.g. kraken-ft3) instead of the text layer itself."""
    out = []
    for r, paras in _textlayer_paragraphs(vol, index, engine).items():
        for p in paras:
            toks = []
            for l in p:
                for i, (w, box) in enumerate(l[5]):
                    if not w:
                        continue
                    prev = toks[-1] if toks else None
                    first_in_line = i == 0
                    if prev and first_in_line and (prev["t"].endswith("\u00ad") or (
                            prev["t"].endswith("-") and w[0].islower() and prev["t"] != "-")):
                        prev["t"] = prev["t"][:-1] + w
                        prev["boxes"].append(list(box))
                    else:
                        toks.append({"t": w, "region": r, "boxes": [list(box)]})
            out.extend(toks)
    return out


# ---- VLM outputs ------------------------------------------------------------

def md_to_markup(s):
    s = re.sub(r"^#+\s*", "", s, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<![\w*])[*_](\S(?:.*?\S)?)[*_](?![\w*])", r"{\1}", s)
    s = re.sub(r"\$\^\{?(\d+)\}?\$|<sup>(\d+)</sup>", lambda m: "^" + (m.group(1) or m.group(2)), s)
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", lambda m: "^" + m.group(0).translate(SUPER), s)
    s = s.replace("\\", "")
    return s.strip()


def dots_blocks(vol, index, mode="dots-layout"):
    """Classified dots.mocr blocks in reading order, or None if the reply did not parse.
    Each block: {kind, text (GT markup), bbox, cat, rows?} with kind one of
    head (running head), pageno, signature, foot, margin, body, heading (Section-header/Title
    inside the text column), table (rows = [[cell, ...], ...]), figure, caption, other."""
    rec = json.loads((OCR / mode / vol / f"p{index:04d}.json").read_text())
    blocks = rec.get("blocks")
    if not blocks:
        return None
    texts = [b for b in blocks if b.get("category") in ("Text", "List-item") and b.get("bbox")]
    # the text column's edges, from blocks at least half as wide as the widest: on a page with few
    # paragraphs the sidenotes would otherwise pull the median into the margin (vol. VII p. 27: two
    # paragraphs, two sidenotes -> right edge 1220, and the sidenote at x 1173 counted as body)
    widest = max((b["bbox"][2] - b["bbox"][0] for b in texts), default=0)
    wide = [b for b in texts if b["bbox"][2] - b["bbox"][0] >= widest / 2]
    left = statistics.median(b["bbox"][0] for b in wide) if wide else 0
    right = statistics.median(b["bbox"][2] for b in wide) if wide else 10**6
    top_text = min((b["bbox"][1] for b in texts), default=10**6)
    out = []
    for i, b in enumerate(blocks):
        cat, txt, bb = b.get("category"), md_to_markup(b.get("text") or ""), b.get("bbox") or [0, 0, 0, 0]
        blk = {"cat": cat, "text": txt, "bbox": bb}
        if cat == "Picture":
            out.append(blk | {"kind": "figure"})
            continue
        if not txt:
            continue
        if SIGNATURE.fullmatch(txt):
            kind = "signature"
        elif cat == "Table":
            rows = re.findall(r"<tr>(.*?)</tr>", b.get("text") or "", flags=re.S)
            blk["rows"] = [[md_to_markup(re.sub(r"<[^>]+>", "", c)) for c in
                            re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)] for r in rows]
            kind = "table"
        elif cat == "Page-footer" and re.fullmatch(r"\d{1,4}|[\dIil]{1,4}", txt):  # oldstyle 1 read as I
            kind = "pageno"
        elif cat == "Page-header" or (i == 0 and cat in ("Section-header", "Title") and len(txt) < 80
                                        and not txt.endswith(".") and bb[3] <= top_text + 5):
            kind = "head"
        elif cat == "Footnote":
            kind = "foot"
        elif cat in ("Text", "List-item", "Section-header", "Title") and (bb[0] >= right - 15 or bb[2] <= left + 15):
            kind = "margin"
            blk["text"] = re.sub(r"(\w)- (?=[a-zåäöæ])", r"\1", txt.replace("\n", " "))
        elif cat in ("Section-header", "Title"):
            kind = "heading"
        elif cat in ("Text", "List-item", "Formula"):
            kind = "body"
            # a line-end hyphen dots.mocr left inside a line ("haf- wer"); not a suspended compound
            # before a conjunction ("skrif- och tryckpapper")
            blk["text"] = INLINE_HYPHEN.sub(r"\1", txt)
        elif cat == "Caption":
            kind = "caption"
        else:
            kind = "other"
        out.append(blk | {"kind": kind})
    return out


def from_dots(vol, index, mode="dots-layout"):
    blocks = dots_blocks(vol, index, mode)
    page = {r: [] for r in REGIONS}
    if blocks is None:
        rec = json.loads((OCR / mode / vol / f"p{index:04d}.json").read_text())
        page["body"] = [md_to_markup(rec["raw"])]
        return {r: "\n".join(v) for r, v in page.items()}
    region = {"head": "head", "pageno": "pageno", "signature": "other", "foot": "foot", "margin": "margin",
              "body": "body", "heading": "body", "caption": "other", "other": "other"}
    for b in blocks:
        if b["kind"] == "figure":
            continue
        if b["kind"] == "table":
            page["body"].extend(" ".join(r) for r in b["rows"])
        elif b["kind"] in ("body", "heading"):
            # one block = one paragraph; dots.mocr may keep the printed line breaks: join them as to_tei.py does
            page["body"].append(dehyphenate(b["text"].split("\n")))
        else:
            page[region[b["kind"]]].append(b["text"])
    return {r: "\n".join(v) for r, v in page.items()}


def from_plain(vol, index, mode):
    rec = json.loads((OCR / mode / vol / f"p{index:04d}.json").read_text())
    return {r: "" for r in REGIONS} | {"body": md_to_markup(rec["raw"])}


def load(engine, vol, index):
    if engine == "gt":
        return read_gt(GT / "pages" / vol / f"p{index:04d}.txt")
    if engine == "textlayer" or engine.startswith("kraken"):
        return from_textlayer(vol, index, engine)
    if engine.startswith("dots-layout"):
        return from_dots(vol, index, engine)
    return from_plain(vol, index, engine)
