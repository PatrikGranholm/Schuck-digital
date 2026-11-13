"""Build the TEI edition of one volume.

  python scripts/to_tei.py --vol 1932 [--validate]     -> tei/schuck-1932.xml

Inputs per page image, best first:
  gt/pages/<vol>/pNNNN.txt   verified ground truth (region text, gt/CONVENTIONS.md)
  ocr/dots-layout/...        dots.mocr layout blocks (scripts/regions.dots_blocks)
  ocr/textlayer/...          PDF text layer (fallback where dots.mocr failed)
plus data/pagemap/<vol>.json (printed pages, plates) and data/structure/<vol>.json
(chapters from the table of contents, back matter).

Encoding:
  - every included image gets <pb n facs resp> and <fw> for running head, page number and
    printer's signature; <facsimile> lists all images. @resp says where the text came from.
  - paragraphs run across pages: a page's first paragraph continues the previous one unless
    its first line is indented in the text layer or a chapter starts. A word broken by the
    page break is joined around <pb break="no"/>.
  - chapters (div type="chapter") from the structure file; the chapter title printed on its
    first page becomes <head>. Headings inside chapters: <ab type="heading">.
  - footnotes: <note place="foot" n> at their anchor (^n); unanchored ones at the end of the
    page's last paragraph with cert="low". Sidenotes: <note place="margin" type="summary"> at the
    start of the line beside which they begin (located via the text layer's line positions).
  - »…» quotations (« also closes, an OCR variant): <quote>; a quotation running over several paragraphs is split into parts
    linked with @next/@prev. Each part has an xml:id (q-<vol>-<n>).
  - source citations in parentheses (K. B., R. A., U. U. B., ...): <bibl type="source"> with
    <orgName> and <idno type="shelfmark"> (the hook for linking to manuscripta.se later).
  - {italic} -> <hi rend="italic">, <spaced> -> <hi rend="spaced">; tables -> <table>.
"""
import argparse
import json
import re
import statistics
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree
from rapidfuzz import fuzz

from common import GT, LIBRIS, OCR, ROOT, VOLUMES, img_path
from regions import _textlayer_paragraphs, dehyphenate, dots_blocks, read_gt

TEI_NS = "http://www.tei-c.org/ns/1.0"
SCHEMA = Path("/home/patrik/Oxygen XML Editor 28/frameworks/tei/xml/tei/custom/schema/relaxng/tei_all.rng")
JING = Path("/home/patrik/Oxygen XML Editor 28/lib/html5/lib/jing-20180722VNU.jar")

REPO = (r"K\. ?B\.|R\. ?A\.|U\. ?U\. ?B\.|Ups\. ?univ\.?[- ]?bibl\.|Riksarkivet|Kungl\. ?bibl(?:ioteket|\.)"
        r"|Vitt\.? ?akad\.|Antikv\.?-?top\.? ?ark(?:ivet|\.)|Skokloster")
BIBL = re.compile(rf"(?<=[(;] )(?:{REPO})[^();]{{0,160}}|(?<=\()(?:{REPO})[^();]{{0,160}}")
SHELF = re.compile(r"sign\.? ?[A-ZÅÄÖ][^,;)]*|vol\. ?\d+[^,;)]*")

# point placeholders (zero-width elements inside paragraph strings): plane-16 private use
PH0 = 0x100000


class Points:
    def __init__(self):
        self.xml = {}

    def add(self, xml):
        ch = chr(PH0 + len(self.xml))
        self.xml[ch] = xml
        return ch


def is_ph(c):
    return ord(c) >= PH0


# ---------------------------------------------------------------- inline markup

def parse_markup(s):
    """GT markup string -> (plain text, spans [(start, end, tag)], points [(pos, xml)])."""
    plain, spans, points, stack = [], [], [], {}
    i = 0
    while i < len(s):
        c = s[i]
        if c in "{<" and c not in stack:
            stack[c] = len(plain)
        elif c == "}" and "{" in stack:
            spans.append((stack.pop("{"), len(plain), "italic"))
        elif c == ">" and "<" in stack:
            spans.append((stack.pop("<"), len(plain), "spaced"))
        elif is_ph(c):
            points.append((len(plain), c))
        else:
            plain.append(c)
        i += 1
    for c, start in stack.items():  # unbalanced: close at end
        spans.append((start, len(plain), "italic" if c == "{" else "spaced"))
    return "".join(plain), spans, points


def quote_spans(plain, open_at_start):
    """»…» spans. Swedish quotes use » for both ends: an opening one is followed by a
    non-space and preceded by start/space/bracket. Returns spans and whether a quote is
    still open at the end (continues in the next paragraph)."""
    spans, stack = [], [0] if open_at_start else []
    cont_start = bool(open_at_start)
    for m in re.finditer("[»«]", plain):
        i = m.start()
        before = plain[i - 1] if i else " "
        after = plain[i + 1] if i + 1 < len(plain) else " "
        opening = m.group(0) == "»" and before in " (\n[—–-/" and not after.isspace()  # « only closes
        if opening:
            stack.append(i)
        elif stack:
            st = stack.pop()
            if not stack:
                spans.append((st, i + 1, "quote", cont_start and st == 0))
                cont_start = False
    still_open = bool(stack)
    if still_open:
        spans.append((stack[0], len(plain), "quote", cont_start and stack[0] == 0))
    return spans, still_open


def bibl_spans(plain):
    out = []
    for m in BIBL.finditer(plain):
        a, b = m.start(), m.end()
        txt = m.group(0).rstrip(" .")
        b = a + len(txt)
        out.append((a, b, "bibl"))
        rm = re.match(REPO, txt)
        if rm:
            out.append((a, a + rm.end(), "orgName"))
        for sm in SHELF.finditer(txt):
            out.append((a + sm.start(), a + sm.start() + len(sm.group(0).rstrip(" .")), "idno"))
    return out


OPEN = {"italic": '<hi rend="italic">', "spaced": '<hi rend="spaced">', "bibl": '<bibl type="source">',
        "orgName": "<orgName>", "idno": '<idno type="shelfmark">'}
CLOSE = {"italic": "</hi>", "spaced": "</hi>", "bibl": "</bibl>", "orgName": "</orgName>", "idno": "</idno>",
         "quote": "</quote>"}
RANK = {"quote": 0, "bibl": 1, "orgName": 2, "idno": 2, "italic": 3, "spaced": 3}


def serialize(plain, spans, points, ph):
    """Plain text + spans + zero-width points -> well-formed XML string. Spans that cross
    are split (the inner one is closed and reopened)."""
    spans = [s for s in spans if s[1] > s[0]]
    bounds = sorted({0, len(plain)} | {s[0] for s in spans} | {s[1] for s in spans} | {p for p, _ in points})
    starts = {}
    for s in spans:
        starts.setdefault(s[0], []).append(s)
    pts = {}
    for p, c in points:
        pts.setdefault(p, []).append(c)
    out, stack, prev = [], [], 0
    for pos in bounds:
        out.append(escape(plain[prev:pos]))
        # close spans ending here
        while any(s[1] == pos for s in stack):
            reopen = []
            while stack[-1][1] != pos:
                s = stack.pop()
                out.append(CLOSE[s[2]])
                reopen.append(s)
            s = stack.pop()
            out.append(CLOSE[s[2]])
            for s in reversed(reopen):
                out.append(open_tag(s, continued=True))
                stack.append(s)
        for c in pts.get(pos, []):
            out.append(ph[c])
        for s in sorted(starts.get(pos, []), key=lambda s: (-s[1], RANK[s[2]])):
            out.append(open_tag(s))
            stack.append(s)
        prev = pos
    out.append(escape(plain[prev:]))
    while stack:
        out.append(CLOSE[stack.pop()[2]])
    return "".join(out)


def open_tag(s, continued=False):
    if s[2] == "quote":
        attrs = s[3] if len(s) > 3 and isinstance(s[3], dict) else {}
        if continued:
            attrs = {k: v for k, v in attrs.items() if k != "xml:id"}
        return "<quote" + "".join(f' {k}="{v}"' for k, v in attrs.items()) + ">"
    return OPEN[s[2]]


def inline(text, ph, extra_spans=()):
    plain, spans, points = parse_markup(text)
    return serialize(plain, spans + list(extra_spans) + bibl_spans(plain), points, ph)


# ---------------------------------------------------------------- pages

def page_source(vol, idx):
    """-> (resp, blocks). Blocks as in regions.dots_blocks (kind, text, bbox)."""
    g = GT / "pages" / vol / f"p{idx:04d}.txt"
    dots = None
    try:
        dots = dots_blocks(vol, idx)
    except FileNotFoundError:
        pass
    if g.exists():
        return "#gt", gt_blocks(read_gt(g), dots or [])
    if dots is not None:
        return "#dots", dots
    return "#textlayer", textlayer_blocks(vol, idx)


def gt_blocks(page, dots):
    """Verified region text -> blocks; headings and sidenote positions borrowed from dots."""
    headings = [b["text"] for b in dots if b["kind"] == "heading"]
    margins = [b for b in dots if b["kind"] == "margin"]
    out = []
    for kind in ("head", "pageno"):
        if page[kind]:
            out.append({"kind": kind, "text": page[kind], "bbox": None})
    for line in page["body"].split("\n"):
        if line.strip():
            k = "heading" if any(fuzz.ratio(line, h) > 85 for h in headings) else "body"
            out.append({"kind": k, "text": line, "bbox": None})
    for i, line in enumerate(l for l in page["margin"].split("\n") if l.strip()):
        out.append({"kind": "margin", "text": line, "bbox": margins[i]["bbox"] if i < len(margins) else None})
    if page["foot"]:
        out.append({"kind": "foot", "text": page["foot"], "bbox": None})
    if page["other"]:
        out.append({"kind": "signature", "text": page["other"], "bbox": None})
    return out


def textlayer_blocks(vol, idx):
    def box(lines):
        ws = [w[1] for l in lines for w in l[5]]
        return [min(b[0] for b in ws), min(b[1] for b in ws), max(b[2] for b in ws), max(b[3] for b in ws)]
    regs = _textlayer_paragraphs(vol, idx)
    kinds = {"head": "head", "pageno": "pageno", "other": "signature", "body": "body", "margin": "margin",
             "foot": "foot"}
    out = []
    for r in ("head", "body", "margin", "foot", "pageno", "other"):
        for p in regs[r]:
            out.append({"kind": kinds[r], "text": dehyphenate([l[4] for l in p]), "bbox": box(p)})
    return out


def first_line_indented(vol, idx):
    """True/False from the text layer's first body line, None if unknown."""
    try:
        body = _textlayer_paragraphs(vol, idx)["body"]
    except OSError:
        return None
    lines = [l for p in body for l in p]
    if len(lines) < 3:
        return None
    left = statistics.median(l[1] for l in lines)
    first = lines[0]
    return first[1] > left + 0.6 * first[2]


def textlayer_lines(vol, idx):
    try:
        return [l for p in _textlayer_paragraphs(vol, idx)["body"] for l in p]
    except OSError:
        return []


def split_footnotes(text):
    """'^1 text ^2 text' or '1 text' -> {n: text}."""
    parts = re.split(r"(?:^|\s)\^(\d{1,2})\s*", text)
    notes = {}
    if len(parts) > 1:
        for n, t in zip(parts[1::2], parts[2::2]):
            notes[n] = t.strip()
        if parts[0].strip():  # text before the first number: continuation of a note from the last page
            notes["cont"] = parts[0].strip()
        return notes
    m = re.match(r"(\d{1,2})\s+(.*)", text, flags=re.S)
    return {m.group(1): m.group(2).strip()} if m else {"cont": text.strip()}


def rescue_margins(vol, idx, blocks):
    """dots.mocr sometimes reads a sidenote into the adjacent paragraph. The text layer separates
    margin words by column, so a text-layer sidenote without a dots counterpart is searched for
    in the body blocks, cut out and turned into a margin block. Returns the number rescued."""
    try:
        groups = _textlayer_paragraphs(vol, idx)["margin"]
    except OSError:
        return 0
    have = [b["text"] for b in blocks if b["kind"] == "margin"]
    n = 0
    for g in groups:
        text = dehyphenate([l[4] for l in g])
        if len(text) < 4 or any(fuzz.ratio(text.lower(), h.lower()) >= 75 for h in have):
            continue
        best = None
        for b in blocks:
            if b["kind"] != "body":
                continue
            al = fuzz.partial_ratio_alignment(text, b["text"], score_cutoff=85)
            if al and (best is None or al.score > best[1].score):
                best = (b, al)
        if not best:
            continue
        b, al = best
        cut = b["text"][al.dest_start:al.dest_end]
        b["text"] = re.sub(r"\s{2,}", " ", b["text"][:al.dest_start] + " " + b["text"][al.dest_end:]).strip()
        ws = [w[1] for l in g for w in l[5]]
        blocks.append({"kind": "margin", "text": cut.strip(), "cat": "rescued",
                       "bbox": [min(x[0] for x in ws), min(x[1] for x in ws), max(x[2] for x in ws), max(x[3] for x in ws)]})
        n += 1
    return n


def place_margin(blocks, margin, tl_lines):
    """Index of the body block and character offset where a sidenote begins."""
    body = [i for i, b in enumerate(blocks) if b["kind"] == "body" and b.get("bbox")]
    if not body:
        return None, 0
    y = margin["bbox"][1] if margin.get("bbox") else None
    if y is None:
        return body[0], 0
    target = next((i for i in body if blocks[i]["bbox"][1] - 10 <= y <= blocks[i]["bbox"][3]), None)
    if target is None:
        target = next((i for i in body if blocks[i]["bbox"][1] > y), body[-1])
    b = blocks[target]
    text = b["text"]
    # the text-layer line nearest to the sidenote's top; find its first words in the block
    line = min(tl_lines, key=lambda l: abs(l[0] - y), default=None)
    frac = max(0.0, min(1.0, (y - b["bbox"][1]) / max(1, b["bbox"][3] - b["bbox"][1])))
    guess = int(frac * len(text))
    if line is not None and abs(line[0] - y) < 40 and line[5]:
        words = [re.sub(r"[­-]$", "", w[0]) for w in line[5][:2]]
        best = None
        for m in re.finditer(re.escape(words[0]), text):
            if m.start() and text[m.start() - 1] not in " »(":
                continue
            d = abs(m.start() - guess)
            if best is None or d < best[0]:
                best = (d, m.start())
        if best and best[0] < max(200, len(text) // 3):
            return target, best[1]
    sp = text.rfind(" ", 0, guess)
    return target, sp + 1 if sp >= 0 else 0


# ---------------------------------------------------------------- build

def build(vol):
    meta = VOLUMES[vol]
    struct = json.loads((ROOT / "data" / "structure" / f"{vol}.json").read_text())
    pm = json.loads((ROOT / "data" / "pagemap" / f"{vol}.json").read_text())
    chapters = struct["chapters"]
    back = struct.get("back", {})
    back_idx = {i: k for k, v in back.items() for i in v}
    front_idx = {i: k for k, v in struct.get("front", {}).items() for i in v}
    first_ch = chapters[0]["page"]
    first_idx = next(r["index"] for r in pm if r["page"] == first_ch)
    last_idx = max((r["index"] for r in pm if r["how"] in ("read", "filled") and r["index"] not in back_idx),
                   default=len(pm))
    ph = Points()
    qn = [0]
    stats = {"pages": 0, "gt": 0, "dots": 0, "textlayer": 0, "foot": 0, "foot_unanchored": 0, "margin": 0,
             "quotes": 0, "bibl": 0, "paragraphs": 0, "margin_rescued": 0, "errata": 0}

    front, body_divs, back_divs = [], [], {"contents": [], "colophon_errata": []}
    cur_div = None          # list of xml strings for the current chapter
    para = None             # current paragraph: list of string pieces (markup + placeholders)
    quote_open = [False]    # a quotation continues into the next paragraph
    last_quote = [None]     # xml:id of the last quote part (for @next/@prev)
    quote_links = {}        # id -> next id

    def flush_para():
        nonlocal para
        if para is None:
            return
        text = "".join(para).strip()
        para = None
        if not text:
            return
        plain, spans, points = parse_markup(text)
        qs, still = quote_spans(plain, quote_open[0])
        qspans = []
        for a, b, _, cont in qs:
            qn[0] += 1
            qid = f"q-{vol}-{qn[0]:05d}"
            attrs = {"xml:id": qid}
            if cont and last_quote[0]:
                attrs["prev"] = f"#{last_quote[0]}"
                quote_links[last_quote[0]] = qid
            if a == 0 and b == len(plain) and (cont or still):
                attrs["rend"] = "block"
            qspans.append((a, b, "quote", attrs))
            last_quote[0] = qid
        if not still:
            last_quote[0] = last_quote[0] if False else None
        quote_open[0] = still
        stats["quotes"] += len(qspans)
        bs = bibl_spans(plain)
        stats["bibl"] += sum(1 for s in bs if s[2] == "bibl")
        xml = serialize(plain, spans + qspans + bs, points, ph.xml)
        cur_div.append(f"<p>{xml}</p>")
        stats["paragraphs"] += 1

    def page_points(idx, page, resp, blocks):
        surf = f"f{vol}-{idx:04d}"
        n = f' n="{page}"' if page else ""
        pts = [f'<pb{n} facs="#{surf}" resp="{resp}"/>']
        for b in blocks:
            if b["kind"] == "head":
                pts.append(f'<fw type="header" place="top">{inline(b["text"], ph.xml)}</fw>')
        for b in blocks:
            if b["kind"] == "pageno":
                pts.append(f'<fw type="pageNum" place="bottom">{escape(b["text"])}</fw>')
            elif b["kind"] == "signature":
                pts.append(f'<fw type="sig" place="bottom">{escape(b["text"])}</fw>')
        return "".join(pts)

    chapter_no = 0
    for r in pm:
        idx, page, how = r["index"], r["page"], r["how"]
        kind = back_idx.get(idx)
        if kind == "duplicate":
            continue
        if not img_path(vol, idx).exists():
            continue
        if idx < first_idx:   # front matter by type from the structure file; binding/scan pages: facsimile only
            ftype = front_idx.get(idx)
            if ftype == "binding":
                continue
            resp, blocks = page_source(vol, idx)
            texts = [b["text"] for b in blocks if b["kind"] not in ("figure", "pageno", "signature") and b["text"].strip()]
            pb_xml = page_points(idx, page, resp, [])
            if ftype in ("halftitle", "titlepage"):
                front.append(f'<titlePage type="{ftype}">{pb_xml}' + "".join(
                    f"<titlePart>{inline(t, ph.xml)}</titlePart>" for t in texts) + "</titlePage>")
            elif ftype:
                if front and front[-1].startswith(f'<div type="{ftype}">'):
                    front[-1] = front[-1][:-len("</div>")] + pb_xml + "".join(f"<p>{inline(t, ph.xml)}</p>" for t in texts) + "</div>"
                else:
                    front.append(f'<div type="{ftype}">{pb_xml}' + "".join(f"<p>{inline(t, ph.xml)}</p>" for t in texts) + "</div>")
            else:                 # blank or unclassified leaf
                front.append(f'<div type="leaf">{pb_xml}' + "".join(f"<ab>{inline(t, ph.xml)}</ab>" for t in texts) + "</div>")
            stats["pages"] += 1
            continue
        if kind == "binding":
            continue
        # back matter
        if (idx > last_idx and not kind) or kind in ("contents", "colophon_errata"):
            resp, blocks = page_source(vol, idx)
            target = back_divs[kind] if kind else None
            if target is None:   # covers / blank leaves after the back matter: image only
                continue
            target.append(page_points(idx, page, resp, []))
            for b in blocks:
                if b["kind"] in ("body", "heading", "margin", "foot", "caption", "other", "head") and b["text"]:
                    target.append(f"<ab>{inline(b['text'], ph.xml)}</ab>")
            stats["pages"] += 1
            continue
        if how == "plate" or (how not in ("read", "filled") and idx > first_idx):
            resp, blocks = page_source(vol, idx)
            texts = [b["text"] for b in blocks if b["kind"] not in ("figure", "table", "pageno") and b["text"].strip()]
            if sum(map(len, texts)) < 300:   # caption, e.g. "JOHANNES BUREUS / Efter porträtt på Gripsholm"
                inner = f"<head>{inline(' / '.join(texts), ph.xml)}</head>" if texts else ""
            else:                            # engraved text reproduced on the plate (uncorrected OCR)
                inner = "".join(f'<ab type="plateText">{inline(t, ph.xml)}</ab>' for t in texts)
            pts = ph.add(f'<pb facs="#f{vol}-{idx:04d}" resp="{resp}"/>'
                         f'<figure type="plate" facs="#f{vol}-{idx:04d}">{inner}</figure>')
            if para is not None:
                para.append(pts)
            else:
                cur_div.append(ph.xml[pts])
            stats["pages"] += 1
            continue

        resp, blocks = page_source(vol, idx)
        stats["pages"] += 1
        stats[resp[1:]] += 1
        if resp == "#dots":
            stats["margin_rescued"] += rescue_margins(vol, idx, blocks)
        # an illustration printed in the text (e.g. a runic stone) whose inscriptions OCR reads
        # as many marginal blocks: more than four "sidenotes" on one page -> figure text
        margins = [b for b in blocks if b["kind"] == "margin"]
        if len(margins) > 4:
            for b in margins:
                b["kind"] = "figtext"
            blocks.append({"kind": "figure", "text": "", "bbox": None,
                           "figtext": [b["text"] for b in margins]})
        for e in struct.get("errata", []):   # the printed errata list: <choice><sic/><corr/></choice>
            if e["page"] != page:
                continue
            for b in blocks:
                if b["kind"] == "body" and e["sic"] in b["text"]:
                    b["text"] = b["text"].replace(e["sic"], ph.add(
                        f'<choice><sic>{escape(e["sic"])}</sic><corr resp="#errata" source="#corrigenda">'
                        f'{escape(e["corr"])}</corr></choice>'), 1)
                    stats["errata"] += 1
                    break
        # chapter start?
        ch = next((c for c in chapters if c["page"] == page), None)
        head_xml = None
        if ch:
            cand = [b for b in blocks if b["kind"] in ("head", "heading") and fuzz.partial_ratio(
                b["text"].lower(), ch["title"].lower()) > 80]
            if cand:
                head_xml = inline(cand[0]["text"], ph.xml)
                blocks = [b for b in blocks if b is not cand[0]]
            else:
                head_xml = escape(ch["title"])
        # footnotes of this page
        notes = {}
        for b in blocks:
            if b["kind"] == "foot":
                notes.update(split_footnotes(b["text"]))
        stats["foot"] += len([k for k in notes if k != "cont"])
        # sidenotes -> placeholders inserted into body blocks
        tl = textlayer_lines(vol, idx)
        inserts = {}
        for b in blocks:
            if b["kind"] == "margin":
                bi, off = place_margin(blocks, b, tl)
                if bi is not None:
                    inserts.setdefault(bi, []).append((off, ph.add(
                        f'<note place="margin" type="summary">{inline(b["text"], ph.xml)}</note>')))
                    stats["margin"] += 1
        pb = page_points(idx, page, resp, blocks)

        if ch:
            flush_para()
            chapter_no += 1
            cur_div = [f'<div type="chapter" n="{chapter_no}">', pb, f"<head>{head_xml}</head>"]
            body_divs.append(cur_div)
            pb = None
            quote_open[0] = False

        body_blocks = [i for i, b in enumerate(blocks) if b["kind"] in ("body", "heading", "table", "figure")]
        used = set()
        for k, bi in enumerate(body_blocks):
            b = blocks[bi]
            text = b["text"]
            for off, c in sorted(inserts.get(bi, []), reverse=True):
                text = text[:off] + c + text[off:]

            def anchor(m):
                n = m.group(1)
                if n in notes:
                    used.add(n)
                    stats_note = notes[n]
                    return ph.add(f'<note place="foot" n="{n}">{inline(stats_note, ph.xml)}</note>')
                return m.group(0)
            text = re.sub(r"\^(\d{1,2})", anchor, text)

            if b["kind"] == "table":
                flush_para()
                if pb:
                    cur_div.append(pb)
                    pb = None
                rows = "".join("<row>" + "".join(f"<cell>{inline(c, ph.xml)}</cell>" for c in row) + "</row>"
                               for row in b["rows"])
                cur_div.append(f"<table>{rows}</table>")
                continue
            if b["kind"] == "figure":
                inner = "".join(f'<ab type="figText">{inline(t, ph.xml)}</ab>' for t in b.get("figtext", []))
                fig = f'<figure facs="#f{vol}-{idx:04d}">{inner}</figure>' if inner else f'<figure facs="#f{vol}-{idx:04d}"/>'
                if para is not None:
                    para.append(ph.add(fig))
                else:
                    cur_div.append(fig)
                continue
            if b["kind"] == "heading":
                flush_para()
                if pb:
                    cur_div.append(pb)
                    pb = None
                cur_div.append(f'<ab type="heading">{inline(text, ph.xml)}</ab>')
                continue
            # body paragraph(s): a block may hold several paragraphs (GT / text layer lines)
            # GT: one paragraph per line; OCR blocks: one paragraph, printed line breaks dehyphenated
            parts = text.split("\n") if resp == "#gt" else [dehyphenate(text.split("\n"))]
            for j, ptxt in enumerate(t for t in parts if t.strip()):
                continues = (k == 0 and j == 0 and pb is not None and para is not None and not ch)
                if continues:
                    ind = first_line_indented(vol, idx)
                    prev = "".join(para).rstrip()
                    if ind is None:
                        ind = bool(re.search(r"[.!?»:]$", prev)) and ptxt[:1].isupper()
                    continues = not ind
                if continues:
                    prev = "".join(para).rstrip()
                    para = [prev]
                    if prev.endswith("-") and ptxt[:1].islower():
                        para[0] = prev[:-1]
                        para.append(ph.add(pb.replace("<pb ", '<pb break="no" ', 1)))
                    else:
                        para.append(ph.add(pb))
                        para.append(" ")
                    para.append(ptxt)
                else:
                    flush_para()
                    if pb:
                        cur_div.append(pb)
                    para = [ptxt]
                pb = None
        if pb:  # page without body text
            flush_para()
            cur_div.append(pb)
        # footnotes without an anchor: attach to the end of the current paragraph
        for n, t in notes.items():
            if n in used:
                continue
            stats["foot_unanchored"] += 1
            el = ph.add(f'<note place="foot"{"" if n == "cont" else f" n={chr(34)}{n}{chr(34)}"} cert="low">'
                        f'{inline(t, ph.xml)}</note>')
            if para is not None:
                para.append(el)
            else:
                cur_div.append(ph.xml[el])
    flush_para()

    # quote chains: add @next to earlier parts
    def link(xml):
        return re.sub(r'<quote xml:id="([^"]+)"', lambda m: f'<quote xml:id="{m.group(1)}" next="#{quote_links[m.group(1)]}"'
                      if m.group(1) in quote_links else m.group(0), xml)

    body = "\n".join(link("\n".join(d)) + "\n</div>" for d in body_divs)
    front_xml = f'<front>{"".join(front)}</front>'.replace("\n", "<lb/>") if front else ""  # printed line breaks
    back_xml = "<back>" + "".join(
        f'<div type="{t}"{" xml:id=" + chr(34) + "corrigenda" + chr(34) if t == "corrigenda" else ""}>{"".join(v)}</div>' for t, v in (("contents", back_divs["contents"]),
                                                            ("corrigenda", back_divs["colophon_errata"])) if v) + "</back>"
    surfaces = []
    from PIL import Image
    for r in pm:
        p = img_path(vol, r["index"])
        if not p.exists():
            continue
        with Image.open(p) as im:
            w, h = im.size
        n = f' n="{r["page"]}"' if r["page"] else ""
        surfaces.append(f'<surface xml:id="f{vol}-{r["index"]:04d}"{n} ulx="0" uly="0" lrx="{w}" lry="{h}">'
                        f'<graphic url="../data/img/{vol}/{p.name}" width="{w}px" height="{h}px"/></surface>')
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<?xml-model href="http://www.tei-c.org/release/xml/tei/custom/schema/relaxng/tei_all.rng" type="application/xml" schematypens="http://relaxng.org/ns/structure/1.0"?>
<TEI xmlns="{TEI_NS}" xml:lang="sv">
<teiHeader>
<fileDesc>
<titleStmt>
<title>{escape(struct["title"])}. {meta["part"]}</title>
<author>Henrik Schück</author>
<respStmt xml:id="gt"><resp>Transcription checked against the page image</resp><name>Schück digital</name></respStmt>
<respStmt xml:id="dots"><resp>OCR, layout analysis</resp><name>dots.mocr (rednote-hilab), via vLLM</name></respStmt>
<respStmt xml:id="errata"><resp>Correction from the printed errata list (Rättelser)</resp><name>Henrik Schück</name></respStmt>
<respStmt xml:id="textlayer"><resp>OCR</resp><name>Text layer of the source PDF</name></respStmt>
</titleStmt>
<publicationStmt><authority>Schück digital (working draft generated by scripts/to_tei.py; not for citation)</authority>
<availability status="free"><p>The text is in the public domain (Henrik Schück died 1947).</p></availability></publicationStmt>
<sourceDesc><bibl><author>Henrik Schück</author>, <title>{escape(struct["title"])}</title> <biblScope unit="volume">{meta["part"]}</biblScope>. <pubPlace>Stockholm</pubPlace> <date when="{meta["year"]}">{meta["year"]}</date>. <ref target="{LIBRIS}">Libris</ref></bibl></sourceDesc>
</fileDesc>
<encodingDesc>
<editorialDecl><p>Text from verified ground truth where available (pb/@resp="#gt"), otherwise from dots.mocr OCR (#dots) or the PDF text layer (#textlayer). Uncorrected OCR. Spelling, including that of quoted sources, is kept as printed. End-of-line hyphenation is removed; words broken across pages carry pb/@break="no". Footnotes and marginal summaries are placed at their anchors; placements marked cert="low" are guesses.</p></editorialDecl>
</encodingDesc>
</teiHeader>
<facsimile>
{chr(10).join(surfaces)}
</facsimile>
<text>
{front_xml}
<body>
{body}
</body>
{back_xml}
</text>
</TEI>
"""
    # resolve nested placeholders (notes inside notes are not expected, but pb inside a note text could be)
    for _ in range(3):
        if not re.search(r"[\U00100000-\U0010FFFD]", xml):
            break
        xml = re.sub(r"[\U00100000-\U0010FFFD]", lambda m: ph.xml.get(m.group(0), ""), xml)
    return xml, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", required=True)
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()
    xml, stats = build(a.vol)
    out = ROOT / "tei" / f"schuck-{a.vol}.xml"
    out.parent.mkdir(exist_ok=True)
    try:
        etree.fromstring(xml.encode())
    except etree.XMLSyntaxError as e:
        bad = out.with_suffix(".broken.xml")
        bad.write_text(xml, encoding="utf-8")
        raise SystemExit(f"not well-formed: {e}; written to {bad}")
    out.write_text(xml, encoding="utf-8")
    print(f"-> {out}  {stats}")
    if a.validate:
        r = subprocess.run(["java", "-jar", str(JING), str(SCHEMA), str(out)], capture_output=True, text=True)
        errs = r.stdout.strip().splitlines()
        print("valid against tei_all" if r.returncode == 0 else f"{len(errs)} validation errors:\n" + "\n".join(errs[:25]))


if __name__ == "__main__":
    main()
