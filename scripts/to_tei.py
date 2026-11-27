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
import csv
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree
from rapidfuzz import fuzz

from common import GT, LIBRIS, OCR, ROOT, VOLUMES, img_path
from manuscripta import isl_key, shelf_key
from riksarkivet import KANSLI, dlg_key, ra_key
from regions import _textlayer_paragraphs, dehyphenate, dots_blocks, from_textlayer, read_gt

TEI_NS = "http://www.tei-c.org/ns/1.0"
SCHEMA = Path("/home/patrik/Oxygen XML Editor 28/frameworks/tei/xml/tei/custom/schema/relaxng/tei_all.rng")
JING = Path("/home/patrik/Oxygen XML Editor 28/lib/html5/lib/jing-20180722VNU.jar")

REPO = (r"K\. ?B\.|R\. ?A\.|U\. ?U\. ?B\.|U\. ?B\.|Ups\. ?univ\.?[- ]?bibl\.|Riksarkivet|Kungl\. ?bibl(?:ioteket|\.)"
        r"|V\. ?H\. ?A\. ?A\.|Vitt\.? ?akad\.|Antikv\.?-?top\.? ?ark(?:ivet|\.)|Skokloster|Kammararkivet|Slottsarkivet"
        r"|L\. ?B\.(?= [A-ZÅÄÖ])|Linköpings stifts- och landsbibl")
# series cited without their holder (scripts/sources.py: about 300 such citations in vols II-V):
# a <bibl> without <orgName>. "Prot." is a minute book (Kanslikollegium's or the academy's).
SERIES = (r"[Kk]anslikoll(?:\.|egiets)|Kansli-koll\.|(?:Riks)?[Rr]egistraturet|Kanslersämbetets|Civilexpeditionens"
          r"|Sekreta utskottets|Mindre sekreta|Utskottshandl\.|Prot\.(?= (?:den |d\. |i |\d))"
          r"|[Aa]kad(?:emiens|s\.|:s) (?:arkiv|protokoll)|Kungl\. brev\b|Palmsk(?:iöld|\.)(?= (?:vol|sign))|Gahm(?=,? sign)")
# after "(" or "; " (also "(i K. B. sign. A 53)", vol. I), or at the start of a footnote (footnote=True)
BIBL = re.compile(rf"(?:(?<=[(;] )|(?<=\()|(?<=\(i ))(?:{REPO}|{SERIES})[^();]{{0,160}}")
BIBL_NOTE = re.compile(rf"^(?:{REPO}|{SERIES})[^();]{{0,160}}")
SHELF = re.compile(r"sign\.? ?[A-ZÅÄÖ][^,;)]*|vol\. ?\d+[^,;)]*")
# a shelfmark right after K. B. / U. B. without "sign.": "K. B. F. a. 10, 2", "U. B. N 73", "K. B. Fm 2 a"
# (series letters + number, a part ", 2" only at the end of the citation)
BARE_SHELF = re.compile(r" ([A-ZÅÄÖ](?:[a-z]|\. ?[a-z])?\.? ?\d+(?:, ?\d+(?=\W*$))?(?: [a-z]\b)?)(?=[ ,.]|$)")

# point placeholders (zero-width elements inside paragraph strings): plane-16 private use
PH0 = 0x100000


class Points:
    def __init__(self):
        self.xml = {}
        self.errata = {}    # opening marker of a printed erratum -> (closing marker, the misprint)

    def add(self, xml):
        ch = chr(PH0 + len(self.xml))
        self.xml[ch] = xml
        return ch


def is_ph(c):
    return ord(c) >= PH0


def first_char(s):
    """First character that is not a zero-width placeholder."""
    return next((c for c in s if not is_ph(c)), "")


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
        elif c in EDIT_OPEN:              # hand-encoded passages (data/edit): additions, item labels
            stack[c] = len(plain)
        elif c in EDIT_CLOSE and EDIT_CLOSE[c][0] in stack:
            o, tag = EDIT_CLOSE[c]
            spans.append((stack.pop(o), len(plain), tag))
        elif is_ph(c):
            points.append((len(plain), c))
        else:
            plain.append(c)
        i += 1
    for c, start in stack.items():  # unbalanced: close at end
        spans.append((start, len(plain), "italic" if c == "{" else "spaced" if c == "<" else EDIT_OPEN[c]))
    return "".join(plain), spans, points


# data/edit markup: ⟦small type in the text column⟧, ⦃small type in the margin⦄ (later additions),
# ⸢item number⸣ and ⸤year beside it⸥ (written by load_edit from an entry's fields)
EDIT_OPEN = {"⟦": "add", "⦃": "addm", "⸢": "label", "⸤": "labelyear"}
EDIT_TAGS = {"add", "addm", "label", "labelyear"}
EDIT_CLOSE = {"⟧": ("⟦", "add"), "⦄": ("⦃", "addm"), "⸣": ("⸢", "label"), "⸥": ("⸤", "labelyear")}


def quote_spans(plain, open_at_start):
    """»…» spans. Swedish quotes use » for both ends: an opening one is followed by a
    non-space and preceded by start/space/bracket. Returns spans and whether a quote is
    still open at the end (continues in the next paragraph)."""
    if open_at_start:
        # a quotation carried over from the previous paragraph ends there if this paragraph opens a new
        # quotation in mid-text before closing anything (a paragraph that *starts* with » is the Swedish
        # way of continuing a long quotation, so that one still continues)
        first = re.search("[»«]", plain)
        if first and first.start() > 0 and first.group(0) == "»" and plain[first.start() - 1] in " (\n[—–-/" \
                and first.start() + 1 < len(plain) and not plain[first.start() + 1].isspace():
            open_at_start = False
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


def bibl_spans(plain, footnote=False):
    out = []
    ms = list(BIBL.finditer(plain))
    if footnote and (m0 := BIBL_NOTE.match(plain)) and not any(m.start() == 0 for m in ms):
        ms.insert(0, m0)
    for m in ms:
        a, b = m.start(), m.end()
        txt = m.group(0).rstrip(" .")
        b = a + len(txt)
        # a De la Gardie collection volume named by the citation (no shelfmark): the link goes on the <bibl>
        dlg = RIKSARKIVET.get(("delagardie", dlg_key(txt))) if "De la Gardie" in txt else None
        if dlg and dlg["id"]:
            attrs = {"corresp": dlg["url"]}
        elif acad := academy_volume(txt):
            # the academy's minute book or royal letters for the cited year, digitised on manuscripta.se
            attrs = {"corresp": acad["url"], "facs": acad["manifest"]}
        else:
            attrs = {}
        out.append((a, b, "bibl", attrs))
        rm = re.match(REPO, txt)
        if rm:
            out.append((a, a + rm.end(), "orgName"))
        holder = holder_of(rm.group(0)) if rm else None
        # Kanslikollegium's incoming letters ("R. A. Kanslikoll. Inkomna skrivelser sign. E IV. 9", also
        # without "R. A."): a volume of the fonds SE/RA/1411 (scripts/riksarkivet.py)
        kansli = KANSLI.search(txt) and holder is None and (not rm or rm.group(0).startswith(("R", "Riks")))
        # (start, shelfmark text, key in data/sources/manuscripta.tsv)
        shelves = [(sm.start(), s := sm.group(0).rstrip(" ."), shelf_key(s)) for sm in SHELF.finditer(txt)]
        if holder == "KB":
            shelves += isl_shelves(txt)
        if not shelves and holder and (bm := BARE_SHELF.match(txt, rm.end())):
            shelves = [(bm.start(1), s := bm.group(1).rstrip(" ."), shelf_key(s))]
        # the holder named last before the shelfmark ("K. B. papp. fol. 28 … samt U. B. sign. R 705")
        orgs = [(m.start(), holder_of(m.group(0))) for m in re.finditer(REPO, txt)]
        for start, shelf, key in sorted(shelves):
            h = next((o for s, o in reversed(orgs) if s < start), holder)
            attrs = {}
            if corr := CORRECTIONS.get((CURRENT["vol"], shelf)):   # a misprint: link the corrected shelfmark
                key = isl_key(corr) or shelf_key(corr)
                attrs["_corr"] = corr
            if h and (ms := MANUSCRIPTA.get((h, key))) and ms["manifest"]:
                # a digitised manuscript on manuscripta.se: record (@corresp) and IIIF manifest (@facs)
                attrs |= {"corresp": ms["url"], "facs": ms["manifest"]}
            elif kansli and (ra := RIKSARKIVET.get(("kanslikollegium", ra_key(shelf)))) and ra["id"]:
                # the volume's catalogue page at Riksarkivet; its IIIF manifest where it is digitised
                attrs |= {"corresp": ra["url"]} | ({"facs": ra["manifest"]} if ra["manifest"] else {})
            out.append((a + start, a + start + len(shelf), "idno", attrs))
    return out


# KB's Icelandic collection, cited "K. B. Papp. fol. nr 24", "Papp. 4:0 40, a", "papp. fol. 28 och papp. kvart
# 53", "Papp. fol. 58, 46 och 47": manuscripta's "Isl. papp. fol. 24" (scripts/manuscripta.py, isl_key)
ISL_SHELF = re.compile(r"\b([Pp]app|[Pp]erg)\. ?(fol\.|4:[o0]|4:to|kvart|8:[o0]|8:vo) ?(?:n:r |nr\.? )?(\d+(?:, ?[a-z]\b)?)")
ISL_MORE = re.compile(r"(?:,| och| samt) (\d+)\b(?![:.]?\d)")


def isl_shelves(txt):
    """(start, text, key) for each Icelandic shelfmark in a K. B. citation; a following bare number
    ("58, 46 och 47") is another volume of the same series and size."""
    out = []
    for m in ISL_SHELF.finditer(txt):
        out.append((m.start(), m.group(0), isl_key(m.group(0))))
        end = m.end()
        while mm := ISL_MORE.match(txt, end):
            out.append((mm.start(1), mm.group(1), isl_key(f"{m.group(1)}. {m.group(2)} {mm.group(1)}")))
            end = mm.end()
    return out


# The academy's own minutes ("Prot. 15 dec. 1835", "Akademiens protokoll den 2 januari 1789", "akad. prot.")
# and royal letters to it ("Kungl. brev den 18 mars 1817"), cited without a holder. Only in vols VI-VIII,
# where "Prot." is the academy's minute book; in vols III-V it is Kanslikollegium's or the Council's.
# Today in Riksantikvarieämbetet's archive, Ämbetsarkiv 2 (scripts/manuscripta.py, holder RAA).
ACADEMY_VOLS = {"1943a", "1943b", "1944"}
ACADEMY_CITE = re.compile(r"(?:Prot\.|[Aa]kad(?:emiens|\.|:s|s\.) prot(?:okoll|\.)|(Kungl\. ?[Bb]r(?:ev|\.)))"
                          r"[^;]*?\b(1[78]\d\d)\b")
CURRENT = {"vol": None}


def academy_volume(txt):
    """The manuscripta.se record of the minute book (A 1 a) or royal letters (E 1) volume covering the first
    year a citation gives, when the citation opens with one of them; None otherwise."""
    m = ACADEMY_CITE.match(txt) if CURRENT["vol"] in ACADEMY_VOLS else None
    if not m:
        return None
    series, year = ("E 1/" if m.group(1) else "A 1 a/"), int(m.group(2))
    for r in ACADEMY:
        lo, _, hi = r["date"].partition("-")
        if r["key"].startswith(series) and r["manifest"] and lo and int(lo) <= year <= int(hi or lo):
            return r
    return None


def holder_of(org):
    """'K. B.' -> KB, 'U. B.' / 'U. U. B.' / 'Ups. univ.-bibl.' -> UUB (the holders manuscripta.se covers)."""
    o = org.replace(" ", "")
    return "KB" if o.startswith(("K.B.", "Kungl.bibl")) else "UUB" if o.startswith(("U.B.", "U.U.B.", "Ups.")) else None


# scripts/manuscripta.py: (holder, shelf key) -> record and manifest
MANUSCRIPTA = {}
_mf = ROOT / "data" / "sources" / "manuscripta.tsv"
if _mf.exists():
    with _mf.open() as fh:
        MANUSCRIPTA = {(r["holder"], r["key"]): r for r in csv.DictReader(fh, delimiter="\t")}
ACADEMY = [r for (h, _), r in MANUSCRIPTA.items() if h == "RAA"]
# misprinted shelfmarks: (vol, shelfmark as printed) -> corrected shelfmark, linked in its place and encoded
# as choice/sic/corr (resp="#editor"); the note column says how the correction was established
CORRECTIONS = {}
_cf = ROOT / "data" / "sources" / "corrections.tsv"
if _cf.exists():
    with _cf.open() as fh:
        CORRECTIONS = {(r["vol"], r["printed"]): r["corrected"] for r in csv.DictReader(fh, delimiter="\t")}
# scripts/riksarkivet.py: (archive, key) -> record and manifest; ("kanslikollegium", "E IV 9"),
# ("delagardie", "antikvitetskollegium" or a letter writer)
RIKSARKIVET = {}
_rf = ROOT / "data" / "sources" / "riksarkivet.tsv"
if _rf.exists():
    with _rf.open() as fh:
        RIKSARKIVET = {(r["archive"], r["key"]): r for r in csv.DictReader(fh, delimiter="\t")}


OPEN = {"italic": '<hi rend="italic">', "spaced": '<hi rend="spaced">', "bibl": '<bibl type="source">',
        "orgName": "<orgName>", "idno": '<idno type="shelfmark">',
        "add": '<add place="inline">', "addm": '<add place="margin">', "label": "<label>",
        "labelyear": '<label type="year" place="margin">'}
CLOSE = {"italic": "</hi>", "spaced": "</hi>", "bibl": "</bibl>", "orgName": "</orgName>", "idno": "</idno>",
         "quote": "</quote>", "persName": "</persName>", "placeName": "</placeName>",
         "add": "</add>", "addm": "</add>", "label": "</label>", "labelyear": "</label>", "errata": "</corr></choice>"}
RANK = {"quote": 0, "errata": 0.2, "label": 0.5, "labelyear": 0.5, "add": 0.5, "addm": 0.5, "bibl": 1, "orgName": 2, "idno": 2,
        "persName": 2.5, "placeName": 2.5, "italic": 3, "spaced": 3}

# named entities from scripts/link.py: plain-text hash of a text unit -> [(start, end, type, attrs)]
NER = {}


def load_ner(vol):
    NER.clear()
    f = ROOT / "data" / "ner" / f"{vol}-links.json"
    if not f.exists():
        return 0
    for l in json.loads(f.read_text()):
        attrs = {}
        if l.get("ref"):
            attrs["ref"] = f"register.xml#{l['ref']}"
        if l.get("cert"):
            attrs["cert"] = l["cert"]
        hits = NER.setdefault(l["sha"], [])
        # units with the same text (a signature, a repeated heading) share a hash: one element per span
        if not any(h[0] == l["start"] and h[1] == l["end"] and h[2] == l["type"] for h in hits):
            hits.append((l["start"], l["end"], l["type"], attrs, l["text"]))
    return sum(map(len, NER.values()))


def ner_spans(plain):
    """Entity spans for a text unit, matched by the hash of its plain text (see scripts/ner.py)."""
    import hashlib
    hits = NER.get(hashlib.sha1(plain.encode()).hexdigest()[:12], [])
    return [(a, b, t, attrs) for a, b, t, attrs, text in hits if plain[a:b] == text]


def serialize(plain, spans, points, ph, repl=()):
    """Plain text + spans + zero-width points -> well-formed XML string. Spans that cross
    are split (the inner one is closed and reopened). repl: [(start, end, xml)] replaces
    plain[start:end] with xml (no other bound may fall strictly inside)."""
    spans = [s for s in spans if s[1] > s[0]]
    # <idno> allows no elements: a note/footnote anchor inside a shelfmark moves to its end
    idnos = [(s[0], s[1]) for s in spans if s[2] == "idno"]
    points = [(next((b for a, b in idnos if a < p < b), p), c) for p, c in points]
    reps = {a: (b, x) for a, b, x in repl}
    bounds = sorted({0, len(plain)} | {s[0] for s in spans} | {s[1] for s in spans} | {p for p, _ in points}
                    | {a for a, _, _ in repl} | {b for _, b, _ in repl})
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
        if pos in reps:
            prev, x = reps[pos]
            out.append(x)
    out.append(escape(plain[prev:]))
    while stack:
        out.append(CLOSE[stack.pop()[2]])
    return "".join(out)


def open_tag(s, continued=False):
    if s[2] in ("persName", "placeName"):
        attrs = s[3] if len(s) > 3 and isinstance(s[3], dict) else {}
        return f"<{s[2]}" + "".join(f' {k}="{escape(v)}"' for k, v in attrs.items()) + ">"
    if s[2] == "bibl" and len(s) > 3 and s[3]:          # a citation linked to a Riksarkivet volume (De la Gardie)
        return '<bibl type="source"' + "".join(f' {k}="{escape(v)}"' for k, v in s[3].items()) + ">"
    if s[2] == "idno" and len(s) > 3 and s[3]:          # a shelfmark linked to manuscripta.se
        return '<idno type="shelfmark"' + "".join(f' {k}="{escape(v)}"' for k, v in s[3].items()) + ">"
    if s[2] == "errata":                                # a printed erratum (Rättelser): misprint, then the correction
        return f'<choice><sic>{escape(s[3]["sic"] if not continued else "")}</sic><corr resp="#errata" source="#corrigenda">'
    if s[2] == "quote":
        attrs = s[3] if len(s) > 3 and isinstance(s[3], dict) else {}
        if continued:
            attrs = {k: v for k, v in attrs.items() if k != "xml:id"}
        return "<quote" + "".join(f' {k}="{v}"' for k, v in attrs.items()) + ">"
    return OPEN[s[2]]


# ---------------------------------------------------------------- historical-spelling rule
# dots.mocr sometimes modernises the 17th c. spelling of quotations (vårt for wårt, Fiolmus for
# Fjolmus, skrifter for skriffter). Where the PDF text layer has the same word differing only by
# w/v, j/i or a doubled letter (same case, letters only), its reading is taken -- inside <quote>
# only, decided in build().flush_para. The change is encoded as <app> keeping dots.mocr's reading.

OLDMAP = str.maketrans("wWjJ", "vViI")


def spell_key(tok):
    """Alignment key: alphanumerics, lowercased, w=v, j=i, doubled letters collapsed."""
    s = "".join(c for c in tok if c.isalnum()).lower().translate(OLDMAP)
    return re.sub(r"(.)\1+", r"\1", s)


def letter_core(tok):
    """(start, end) of the run from the first to the last letter, if it is letters only."""
    idx = [i for i, c in enumerate(tok) if c.isalpha()]
    if not idx:
        return None
    a, b = idx[0], idx[-1] + 1
    return (a, b) if tok[a:b].isalpha() else None


def spelling_variant(d, t):
    """True if two letter strings differ only by w/v, j/i or doubled letters (case kept)."""
    if d == t:
        return False
    f = lambda s: re.sub(r"(.)\1+", r"\1", s.translate(OLDMAP))
    return f(d) == f(t)


SPELL_ENGINES = ("textlayer", "kraken-ft3")   # both must give the old form (kraken-ft3: Kraken round 3)


def spelling_candidates(vol, idx, texts):
    """texts: the page's body paragraph strings (dots.mocr, with markup and placeholders).
    Aligns their words with the body tokens of the text layer and of Kraken r3 (both on the
    text layer's lines) and returns, per text, [(offset, dots_word, old_word)] for words where
    both engines read the same form and it differs from dots.mocr's only in old spelling.
    Kraken guards against text-layer garbage: on some pages of vol. II the text layer doubles
    letters at random (tilll, SSist, miin)."""
    import difflib
    from regions import textlayer_tokens
    words = [(k, m.start(), m.group(0)) for k, s in enumerate(texts) for m in re.finditer(r"\S+", s)]
    dk = [spell_key(w) for _, _, w in words]
    found = []
    for eng in SPELL_ENGINES:
        try:
            other = [t["t"] for t in textlayer_tokens(vol, idx, eng) if t["region"] == "body"]
        except OSError:
            return [[] for _ in texts]
        hits = set()
        sm = difflib.SequenceMatcher(None, dk, [spell_key(t) for t in other], autojunk=False)
        for i, j, n in sm.get_matching_blocks():
            for x in range(n):
                w, t = words[i + x][2], other[j + x]
                cw, ct = letter_core(w), letter_core(t)
                if cw and ct and spelling_variant(w[cw[0]:cw[1]], t[ct[0]:ct[1]]):
                    hits.add((i + x, t[ct[0]:ct[1]]))
        found.append(hits)
    out = [[] for _ in texts]
    for i, ow in sorted(set.intersection(*found)):
        k, off, w = words[i]
        c0, c1 = letter_core(w)
        out[k].append((off + c0, w[c0:c1], ow))
    return out


def corrected_shelves(spans):
    """Split off the shelfmarks corrected from data/sources/corrections.tsv: (other spans, repl for serialize).
    <idno> may not contain <choice>, so the choice wraps two idnos: the printed one and the corrected, linked one."""
    keep, repl = [], []
    for s in spans:
        if s[2] == "idno" and len(s) > 3 and "_corr" in s[3]:
            attrs = {k: v for k, v in s[3].items() if k != "_corr"}
            repl.append((s[0], s[1], s[3]["_corr"], attrs))
        else:
            keep.append(s)
    return keep, repl


def choice_xml(plain, a, b, corr, attrs):
    linked = "".join(f' {k}="{escape(v)}"' for k, v in attrs.items())
    return (f'<choice><sic><idno type="shelfmark">{escape(plain[a:b])}</idno></sic><corr resp="#editor">'
            f'<idno type="shelfmark"{linked}>{escape(corr)}</idno></corr></choice>')


def inline(text, ph, extra_spans=(), footnote=False):
    plain, spans, points = parse_markup(text)
    bs, corr = corrected_shelves(bibl_spans(plain, footnote))
    repl = [(a, b, choice_xml(plain, a, b, c, at)) for a, b, c, at in corr]
    return serialize(plain, spans + list(extra_spans) + bs + ner_spans(plain), points, ph, repl)


# ---------------------------------------------------------------- pages

def load_edit(vol):
    """Hand-encoded passages, data/edit/<vol>-*.txt (format in data/edit/README.md) -> {image index: page},
    page = {"page": printed number, "head": running head, "items": [(kind, payload)], "foot": {n: text}};
    kind: "+" (continues the paragraph from the previous page), "p", "h", "fig" (payload: caption), "e" (an inventory entry, its
    number and year wrapped in ⸢…⸣ ⸤…⸥), "table" (payload: rows) or "q" (payload: True where a quoted
    document starts, False where it stops)."""
    pages = {}
    for f in sorted((ROOT / "data" / "edit").glob(f"{vol}-*.txt")):
        cur, table_open = None, False
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln.strip() or ln.startswith("#"):
                table_open = False
                continue
            if ln.startswith("=== "):
                _, i, n = ln.split(maxsplit=2)
                printed = not (n.startswith("[") or n == "-")   # [48]: page not numbered in print; -: a plate
                n = n.strip("[]")
                cur = pages[int(i)] = {"page": int(n) if n.isdigit() else (None if n == "-" else n),
                                       "printed": printed, "head": "", "sig": "", "items": [], "foot": {}}
                table_open = False
                continue
            key, _, rest = ln.partition(" ")
            rest = rest.strip()
            if key == "head":
                cur["head"] = rest
            elif key == "sig":
                cur["sig"] = rest
            elif key == "fig":                # a picture; its printed caption (may be empty)
                cur["items"].append(("fig", rest))
            elif key == "foot":
                n, _, t = rest.partition(" ")
                cur["foot"][n] = t
            elif key == "t":
                row = [c.strip() for c in rest.split("|")]
                if table_open:
                    cur["items"][-1][1].append(row)
                else:
                    cur["items"].append(("table", [row]))
                table_open = True
                continue
            elif key == "e":
                num, year, text = (x.strip() for x in rest.split("|", 2))
                cur["items"].append(("e", (f"⸢{num}⸣ " if num else "") + (f"⸤{year}⸥ " if year else "") + text))
            elif key in ("p", "h", "+"):
                cur["items"].append((key, rest))
            elif key in ("q+", "q-"):         # a quoted document starts / stops
                cur["items"].append(("q", key == "q+"))
            else:
                raise ValueError(f"{f.name}: unknown line {ln[:60]!r}")
            table_open = False
    return pages


def page_source(vol, idx):
    """-> (resp, blocks). Blocks as in regions.dots_blocks (kind, text, bbox)."""
    g = GT / "pages" / vol / f"p{idx:04d}.txt"
    dots = None
    try:
        dots = dots_blocks(vol, idx)
    except FileNotFoundError:
        pass
    if g.exists():
        try:
            tlm = [(dehyphenate([l[4] for l in grp]),
                    [min(w[1][0] for l in grp for w in l[5]), min(w[1][1] for l in grp for w in l[5]),
                     max(w[1][2] for l in grp for w in l[5]), max(w[1][3] for l in grp for w in l[5])])
                   for grp in tl_margin_groups(vol, idx)]
        except OSError:
            tlm = []
        return "#gt", gt_blocks(read_gt(g), dots or [], tlm)
    if dots is not None:
        return "#dots", dots
    return "#textlayer", textlayer_blocks(vol, idx)


def gt_blocks(page, dots, tl_margins=()):
    """Verified region text -> blocks; headings and sidenote positions borrowed from dots, or for a sidenote
    dots.mocr did not read as one, from the text layer's margin words (tl_margins: [(text, bbox)])."""
    headings = [b["text"] for b in dots if b["kind"] == "heading"]
    margins = [b for b in dots if b["kind"] == "margin"]

    def margin_bbox(line, i):
        cands = [(fuzz.ratio(line.lower(), _joined(b["text"])), b["bbox"]) for b in margins] + \
                [(fuzz.ratio(line.lower(), _joined(t)), bb) for t, bb in tl_margins]
        best = max(cands, default=(0, None))
        if best[0] >= 70:
            return best[1]
        return margins[i]["bbox"] if i < len(margins) else None
    out = []
    for kind in ("head", "pageno"):
        if page[kind]:
            out.append({"kind": kind, "text": page[kind], "bbox": None})
    for line in page["body"].split("\n"):
        if line.strip():
            k = "heading" if any(fuzz.ratio(line, h) > 85 for h in headings) else "body"
            out.append({"kind": k, "text": line, "bbox": None})
    for i, line in enumerate(l for l in page["margin"].split("\n") if l.strip()):
        out.append({"kind": "margin", "text": line, "bbox": margin_bbox(line, i)})
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


def snap_word(t, ds, de):
    """Move a cut [ds, de) in t that starts or ends inside a word to a word boundary. A garbled text-layer
    sidenote ("Fornminnenas forstonnS", vol. I p. 1) aligns with only part of its last word, and the rest
    ("öring") would stay behind as body text: complete the word when the missing part is no longer than
    the part inside the cut. Otherwise the alignment ran into a neighbouring body word ("Axehiälm b|egagnade"):
    leave that word out. -> (ds, de), or (None, None) when nothing is left."""
    if ds > 0 and t[ds - 1].isalnum() and t[ds].isalnum():
        w0 = ds
        while w0 > 0 and t[w0 - 1].isalnum():
            w0 -= 1
        w1 = ds
        while w1 < de and t[w1].isalnum():
            w1 += 1
        ds = w0 if ds - w0 <= w1 - ds else w1
    if de < len(t) and de > ds and t[de - 1].isalnum() and t[de].isalnum():
        w1 = de
        while w1 < len(t) and t[w1].isalnum():
            w1 += 1
        w0 = de
        while w0 > ds and t[w0 - 1].isalnum():
            w0 -= 1
        de = w1 if w1 - de <= de - w0 else w0
    return (ds, de) if t[ds:de].strip() else (None, None)


def tl_margin_groups(vol, idx):
    """The text layer's margin line groups that are set smaller than the body, as sidenotes are (lines 0.69-0.80
    of the body's height: "Regner" 22/28, "Rhezelius" 22/30). The text layer also files the end of a line that
    runs past the column as margin: full-size words of a quoted inventory ("oinbundne».", "utskurit.",
    vol. II p. 321), which are not sidenotes. Oldstyle figures make a year's box taller (0.89-0.94: "i792",
    "Consignât™- nen 1673"), so a group with a year may be up to 0.97 of the body's height."""
    regs = _textlayer_paragraphs(vol, idx)
    body = [l[2] for g in regs["body"] for l in g]
    if not body:
        return regs["margin"]
    h = statistics.median(body)

    def small(g):
        ratio = statistics.median(l[2] for l in g) / h
        year = re.search(r"(?<!\d)1[5-9]\d\d(?!\d)", oldstyle_years(" ".join(l[4] for l in g)))
        return ratio < (0.97 if year else 0.88)
    return [g for g in regs["margin"] if small(g)]


OLDSTYLE = str.maketrans("iIlLjJoO°", "111177000")


def oldstyle_years(text):
    """The text layer's reading of oldstyle figures in a sidenote: "Fynd ijjo" -> "Fynd 1770", "IJJ7" -> 1777,
    "i 797 — i 799" -> "1797–1799". Only whole four-character tokens that come out as a year 1500-1949."""
    text = re.sub(r"(?<!\w)[iIlJ] ?(\d{3})(?!\d)", r"1\1", text)     # "J793" (vol. VI p. 115)

    def year(m):
        tok = m.group(0)
        y = tok.translate(OLDSTYLE)
        # a token of letters only must look like 1 + 7/digit + 7/digit + digit ("ijjo", "IJJ7"), not a word ("Ilio")
        shaped = not tok.isalpha() or re.fullmatch(r"[iIl][jJ\d][jJ\d][\doO°jJ]", tok)
        return y if y.isdigit() and 1500 <= int(y) < 1950 and shaped else tok
    text = re.sub(r"(?<!\w)[\diIljJoO°]{4}(?!\w)", year, text)
    return re.sub(r"\s*[—–-]\s*(?=\d)", "–", text) if re.search(r"\d{4}\s*[—–-]", text) else text


def rescue_margins(vol, idx, blocks):
    """dots.mocr sometimes reads a sidenote into the adjacent paragraph. The text layer separates
    margin words by column, so a text-layer sidenote without a dots counterpart is searched for
    in the body blocks, cut out and turned into a margin block. Returns the number rescued."""
    try:
        groups = tl_margin_groups(vol, idx)
    except OSError:
        return 0
    have = [b["text"] for b in blocks if b["kind"] == "margin"]
    n = 0
    tl_body = None
    for g in groups:
        text = dehyphenate([l[4] for l in g])
        # the text layer reads an oldstyle 1 as "i" and spaces dashes: "i 797 — i 799" for "1797–1799" (vol. VII p. 46)
        text = oldstyle_years(text)
        if len(text) < 4 or any(fuzz.ratio(text.lower(), h.lower()) >= 75 for h in have):
            continue
        # prices set flush right in a quoted account ("Dr 1.8.", "42.14.»", vol. IV pp. 586-587) are not
        # sidenotes: a sidenote has a word in it (years are handled below)
        if not re.search(r"[^\W\d_]{3}", text) and not YEARS.fullmatch(text):
            continue
        best = None
        for b in blocks:
            if b["kind"] != "body":
                continue
            al = fuzz.partial_ratio_alignment(text, b["text"], score_cutoff=85)
            if not al:
                continue
            # a block that is (nearly) all sidenote beats an exact match inside running text
            # ("Johan Pe-ringer" as its own block vs. "nämligen Johan Peringer, vilken", vol. II p. 88)
            score = al.score + (10 if al.dest_end - al.dest_start >= 0.8 * len(b["text"].strip()) else 0)
            if best is None or score > best[2]:
                best = (b, al, score)
        ws = [w[1] for l in g for w in l[5]]
        box = [min(x[0] for x in ws), min(x[1] for x in ws), max(x[2] for x in ws), max(x[3] for x in ws)]
        # a bare year ("1792") may be a sidenote merged into the text ("nytt prisämne 1789—1792 för följande
        # års", vol. VI p. 101) or the text's own year beside a sidenote dots.mocr dropped ("utsattes därför på
        # nytt till 1792", p. 112; "för 1792. 1792 I laga tid", p. 124, has both). The text layer, which keeps
        # margin words out of the body, decides as in rescue_toc: an occurrence is cut only when the text
        # around it matches the text layer's body at least 5 points better without it; with none, the
        # sidenote is inserted from the text layer.
        year = YEARS.fullmatch(text) and re.search(r"\d{4}", text) \
            and all(1500 <= int(y) < 1950 for y in re.findall(r"\d+", text))
        if year:
            if tl_body is None:
                tl_body = _plain(from_textlayer(vol, idx)["body"])
            pat = r"(?<!\d)" + r"\s*[—–-]\s*".join(re.findall(r"\d{4}", text)) + r"(?!\d)"
            best = None
            for b in blocks:
                if b["kind"] != "body":
                    continue
                t = b["text"]
                for m in re.finditer(pat, t):
                    w = _plain(t[max(0, m.start() - 45):m.end() + 45])
                    wo = _plain(t[max(0, m.start() - 45):m.start()] + " " + t[m.end():m.end() + 45])
                    gain = fuzz.partial_ratio(wo, tl_body) - fuzz.partial_ratio(w, tl_body)
                    # at the start of a block any gain will do: "1792 I laga tid" (+2, the text layer "för 1792.
                    # I laga tid"); real text there scores 0 or less ("1804 var ett för kansliets kassa")
                    need = 0.5 if not t[:m.start()].strip(" {") else 5
                    if gain >= need and (best is None or gain > best[2]):
                        best = (b, m, gain)
            if best:
                b, m, _ = best
                t = b["text"]
                b["text"] = re.sub(r"\s{2,}", " ", t[:m.start()] + " " + t[m.end():]).strip()
                blocks.append({"kind": "margin", "text": m.group(0), "cat": "rescued", "bbox": box})
            else:
                blocks.append({"kind": "margin", "text": text.strip(), "cat": "rescued", "bbox": box, "resp": "#textlayer"})
            n += 1
            continue
        if not best:
            if year:
                blocks.append({"kind": "margin", "text": text.strip(), "cat": "rescued", "bbox": box, "resp": "#textlayer"})
                n += 1
            continue
        b, al, _ = best
        t = b["text"]
        ds, de = snap_word(t, al.dest_start, al.dest_end)
        if ds is None:
            continue
        cut = t[ds:de]
        b["text"] = re.sub(r"\s{2,}", " ", t[:ds] + " " + t[de:]).strip()
        blocks.append({"kind": "margin", "text": cut.strip(), "cat": "rescued", "bbox": box})
        n += 1
    return n


def _plain(s):
    return re.sub(r"\s+", " ", re.sub(r"[{}^]", "", s)).strip()


def _joined(s):
    """_plain with line-end hyphens joined, for comparing a block with a topic ("Äldre lag- editioner")."""
    return re.sub(r"(\w)-\s+(?=[a-zåäö])", r"\1", _plain(s)).lower()


def _same_topic(tp, text):
    """A sidenote text that carries the topic: fuzzy words, but the same years ("Resor 1677 och 1678"
    is not "Resor 1679 och 1680")."""
    return fuzz.partial_ratio(tp.lower(), text) >= 85 and set(re.findall(r"\d+", tp)) <= set(re.findall(r"\d+", text))


MARGIN_JOIN = re.compile(r"(\w)- (?=[a-zåäö])")    # "Tjänstemän- nen" -> "Tjänstemännen"
YEARS = re.compile(r"[\d\s–—-]+(?:och [\d\s–—-]+)?")


def rescue_toc(vol, idx, topics, blocks):
    """A second rescue, with the table of contents as the oracle: the TOC lists the sidenotes of each
    page, so a topic of this page that is not among its margin blocks may have been read into the body.
    (1) A short body block that holds the topic is a sidenote dots.mocr placed just inside the column
    ("Historiografernas arbeten", "Fynd 1769"). (2) Inside a longer block, an occurrence of the topic
    is cut only when the text layer, which keeps margin words out of the body lines, confirms it: the
    body text around it must match the text layer at least 5 points better without the words
    ("att resa kring Resor 1672 i Uppland", "Isthmenius Verelius betraktade") than with them
    ("enligt Adlerbeths tolkning" stays). A bare year ("1788") is cut only at the start of a block,
    together with a short lead such as "Fynd". -> (number rescued, topics now present as sidenotes)"""
    have = [_joined(b["text"]) for b in blocks if b["kind"] == "margin"]
    done = {tp for tp in topics if any(_same_topic(tp, h) for h in have)}
    tl = None
    n = 0
    for tp in topics:
        if tp in done or len(tp) < 3:
            continue
        low = tp.lower()
        body = [b for b in blocks if b["kind"] == "body" and b["text"].strip()]
        whole = next((b for b in body if len(_joined(b["text"])) <= len(tp) + 15
                      and not re.search(r"[.;:!?»]\s", _plain(b["text"]))
                      and fuzz.partial_ratio(low, _joined(b["text"])) >= 90
                      and set(re.findall(r"\d+", tp)) <= set(re.findall(r"\d+", b["text"]))), None)
        if whole:
            whole["kind"], whole["cat"] = "margin", "toc"
            whole["text"] = MARGIN_JOIN.sub(r"\1", whole["text"].replace("\n", " "))   # as dots_blocks does
            done.add(tp)
            n += 1
            continue
        if tl is None:
            try:
                tl = _plain(from_textlayer(vol, idx)["body"])
            except OSError:
                return n, done
        best = None
        for b in body:
            t = b["text"]
            spans = {(m.start(), m.end()) for m in re.finditer(re.escape(low), t.lower())}
            al = fuzz.partial_ratio_alignment(low, t.lower(), score_cutoff=88)
            if al:
                spans.add((al.dest_start, al.dest_end))
            for s0, e0 in spans:
                ds, de = snap_word(t, s0, e0)
                if ds is None:
                    continue
                lead = t[:ds].strip()
                # a bare year: at the start of a block, after at most a short lead, or at its very end
                # ("förslag till nya prisämnen för 1793 års tävlan: 1793", vol. VI p. 105)
                if YEARS.fullmatch(tp) and lead and not re.fullmatch(r"\{?\w+(?: \d{4})?\}?", lead) \
                        and t[de:].strip():
                    continue
                w = _plain(t[max(0, ds - 45):de + 45])
                wo = _plain(t[max(0, ds - 45):ds] + " " + t[de:de + 45])
                gain = fuzz.partial_ratio(wo, tl) - fuzz.partial_ratio(w, tl)
                if gain >= 5 and (best is None or gain > best[0]):
                    best = (gain, b, ds, de)
        if not best:
            # dots.mocr took the sidenote's first line into the paragraph beside it and dropped the rest:
            # "Riksarkivets »1590» — har Buræus antecknat" for "Riksarkivets / första tid" (vol. I p. 116).
            # The words are cut when the text layer, which has no sidenote there, agrees; the sidenote gets
            # the TOC's wording.
            tw = tp.split()
            for k in range(len(tw) - 1, 0, -1):
                lead = " ".join(tw[:k])
                if len(lead) < 6:
                    break
                for b in body:
                    t = b["text"]
                    m = re.match(r"\{?" + re.escape(lead) + r"\}?\s+", t)
                    if not m:
                        continue
                    gain = fuzz.partial_ratio(_plain(t[m.end():m.end() + 60]), tl) - fuzz.partial_ratio(_plain(t[:m.end() + 60]), tl)
                    if gain >= 5:
                        b["text"] = t[m.end():]
                        blocks.append({"kind": "margin", "text": tp, "cat": "toc", "bbox": b.get("bbox"),
                                       "resp": "#toc", "anchor": (b, 0)})
                        done.add(tp)
                        n += 1
                        break
                if tp in done:
                    break
            continue
        _, b, ds, de = best
        t = b["text"]
        if re.fullmatch(r"\{?\w+(?: \d{4})?\}?\s*", t[:ds]) and len(t[:ds].strip()) <= 15:
            ds = 0                                   # "Fynd 1779 1779 var ...": the lead belongs to the note
        if ds > 0 and t[ds - 1] == "{" and t[de:de + 1] == "}":
            ds, de = ds - 1, de + 1                  # an italic span that is all sidenote
        left, right = t[:ds].rstrip(), t[de:].lstrip()
        b["text"] = f"{left} {right}" if left and right else left or right
        blocks.append({"kind": "margin", "text": MARGIN_JOIN.sub(r"\1", t[ds:de].strip().replace("\n", " ")),
                       "cat": "toc", "bbox": b.get("bbox"),
                       "anchor": (b, len(left) + 1 if left and right else len(left))})
        done.add(tp)
        n += 1
    return n, done


def rescue_inserted(vol, idx, topics, done, blocks, vocab):
    """A third rescue, for sidenotes dots.mocr dropped altogether (neither a margin block nor in the
    body): where the text layer has margin words on this page that match a TOC topic not yet present,
    a sidenote is inserted with the TOC's wording (the text layer's spelling is poor: "Arkivet i
    Stockkoim", "Svenska tortratt") at the position of those words, marked resp="#toc". A group matches
    as a whole, as a lead + the topic where the TOC elides a series' common word ("Fynd 1755" for
    "1755"; the lead words must occur in the TOC), or with the topic at its start (two sidenotes the
    text layer ran together). Years must agree. -> number inserted (topics added to done)"""
    try:
        groups = tl_margin_groups(vol, idx)
    except OSError:
        return 0
    have = [_joined(b["text"]) for b in blocks if b["kind"] == "margin"]
    n = 0
    for g in groups:
        text = oldstyle_years(dehyphenate([l[4] for l in g]))          # "Fynd ijjo" -> "Fynd 1770" (vol. V p. 527)
        gl = re.sub(r"(\w)-\s+(?=[a-zåäö])", r"\1", re.sub(r"\s+", " ", text)).lower().strip(" .,")
        # already there; a sidenote of a series differs only in its year ("Fynd 1776", "Fynd 1777": vol. V p. 539)
        if len(gl) < 3 or any(fuzz.ratio(gl, h) >= 75 and set(re.findall(r"\d+", gl)) == set(re.findall(r"\d+", h))
                              for h in have):
            continue
        for tp in topics:
            if tp in done or len(tp) < 3:
                continue
            t = tp.lower()
            digits = set(re.findall(r"\d+", tp))
            if not digits <= set(re.findall(r"\d+", gl)):
                continue
            note, lines = None, g
            if fuzz.ratio(t, gl) >= 80:
                note = tp
            elif gl.endswith(t) and 0 < len(gl) - len(t):
                lead = text.strip(" .,")[:len(text.strip(" .,")) - len(t)].strip()
                if 1 <= len(lead.split()) <= 2 and all(w.lower() in vocab for w in lead.split()):
                    note = f"{lead} {tp}"
            elif fuzz.ratio(t, gl[:len(t)]) >= 88 and gl[len(t):len(t) + 1] in ("", " ", "."):
                note = tp
                lines = g[:max(1, round(len(g) * len(t) / len(gl)))]
            elif len(gl) >= 6 and fuzz.ratio(gl, t[:len(gl)]) >= 80 and t[len(gl):len(gl) + 1] in (" ", "s"):
                # the text layer kept only the start: "Utomståendes" of "Utomståendes avhandlingar" (its second
                # line ran into the body), or the TOC adds a genitive -s to a name in a series ("Inträdestalen.
                # Dahls 173. Regnérs 174" for the sidenote "Regnér"): print the name as the sidenote has it
                note = tp[:-1] if t[len(gl):] == "s" else tp
            elif len(gl) >= 12 and gl.split()[0] == t.split()[0] and \
                    fuzz.partial_ratio(re.sub(r"\W+$", "", gl), t) >= 85:
                # the text layer garbles the rest: "Det Gustavigravk°-" for "Det Gustavianska gravkoret" (vol. VIII p. 313)
                note = tp
            if note:
                bbox = [min(l[1] for l in lines), min(l[0] for l in lines),
                        max(l[1] + l[3] for l in lines), max(l[0] + l[2] for l in lines)]
                blocks.append({"kind": "margin", "text": note, "cat": "toc", "bbox": bbox, "resp": "#toc"})
                done.add(tp)
                n += 1
                break
    return n


def place_margin(blocks, margin, tl_lines):
    """Index of the body block and character offset where a sidenote begins."""
    if margin.get("anchor"):                         # cut out of a body block by rescue_toc: its own position
        bo, off = margin["anchor"]
        i = next((i for i, b in enumerate(blocks) if b is bo), None)
        if i is not None:
            return i, min(off, len(bo["text"]))
    body = [i for i, b in enumerate(blocks) if b["kind"] == "body" and b.get("bbox")]
    if not body:
        # ground truth: paragraphs without boxes. The text-layer line beside the sidenote; its first words
        # are looked for in the paragraphs (vol. I p. 217 "Verelius' biografi", p. 159 "Rhezelius")
        paras = [i for i, b in enumerate(blocks) if b["kind"] == "body"]
        if not paras:
            return None, 0
        if margin.get("bbox") and tl_lines:
            y = margin["bbox"][1]
            line = min(tl_lines, key=lambda l: abs(l[0] - y))
            words = [re.sub(r"[­-]$", "", w[0]) for w in line[5][:3]] if abs(line[0] - y) < 40 else []
            for k in (3, 2, 1):
                if len(words) < k or (k == 1 and len(words[0]) < 5):
                    continue
                pat = r"(?<![\w])" + r"\W*\s*".join(re.escape(re.sub(r"\W+$", "", w)) for w in words[:k])
                hits = [(i, m.start()) for i in paras for m in re.finditer(pat, blocks[i]["text"])]
                if len(hits) == 1:
                    return hits[0]
        return paras[0], 0
    y = margin["bbox"][1] if margin.get("bbox") else None
    if y is None:
        return body[0], 0
    # the text-layer line nearest to the sidenote's top; its first words are looked for in the block
    line = min(tl_lines, key=lambda l: abs(l[0] - y), default=None)
    hits = [i for i in body if blocks[i]["bbox"][1] - 10 <= y <= blocks[i]["bbox"][3]]
    if len(hits) > 1 and line is not None and line[5]:
        # y on the boundary of two blocks (vol. VII p. 174: "Regnér" at the top of the last line, which is
        # also the bottom of the block before): the block that holds the line's first word
        w0 = re.sub(r"[­-]$", "", line[5][0][0])
        hits = [i for i in hits if w0 in blocks[i]["text"]] or hits
    target = hits[0] if hits else next((i for i in body if blocks[i]["bbox"][1] > y), body[-1])
    b = blocks[target]
    text = b["text"]
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
    CURRENT["vol"] = vol
    meta = VOLUMES[vol]
    struct = json.loads((ROOT / "data" / "structure" / f"{vol}.json").read_text())
    # sidenote topics per page from the table of contents (toc.py), the oracle for rescue_toc
    toc_topics, toc_done = {}, set()
    toc_file = ROOT / "data" / "structure" / f"{vol}-toc.json"
    if toc_file.exists():
        for ch in json.loads(toc_file.read_text()):
            for tp in ch["topics"]:
                toc_topics.setdefault(tp["page"], []).append(tp["topic"])
    # words of the TOC topics: the lead a series elides ("Fynd" in "Fynd 1754 270, 1755 275")
    toc_vocab = {w.lower() for v in toc_topics.values() for tp in v for w in tp.split()}
    pm = json.loads((ROOT / "data" / "pagemap" / f"{vol}.json").read_text())
    chapters = struct["chapters"]
    back = struct.get("back", {})
    back_idx = {i: k for k, v in back.items() for i in v}
    front_idx = {i: k for k, v in struct.get("front", {}).items() for i in v}
    # parts (vol. V): <div type="part"> around their chapters; the part's title leaves (title, blank
    # leaves, a frontispiece) open the div, and the printed part title becomes its <head>
    vparts = struct.get("parts", [])
    part_leaf = {i: k for k, p in enumerate(vparts) for i in p.get("leaves", [])}
    part_at = {p["chapter"]: k for k, p in enumerate(vparts)}
    part_lead = [[] for _ in vparts]
    first_ch = chapters[0]["page"]
    first_idx = next(r["index"] for r in pm if r["page"] == first_ch)
    last_idx = max((r["index"] for r in pm if r["how"] in ("read", "filled") and r["index"] not in back_idx),
                   default=len(pm))
    ph = Points()
    edit = load_edit(vol)
    n_links = load_ner(vol)
    qn = [0]
    stats = {"pages": 0, "gt": 0, "dots": 0, "textlayer": 0, "foot": 0, "foot_unanchored": 0, "margin": 0,
             "quotes": 0, "bibl": 0, "paragraphs": 0, "margin_rescued": 0, "margin_toc": 0, "margin_inserted": 0, "manuscripta": 0, "riksarkivet": 0, "academy": 0, "errata": 0,
             "names": 0, "spelling": 0, "edit": 0, "captions": 0, "corrected": 0}
    spell = {}              # placeholder -> (dots word, text-layer word, page): old-spelling candidates
    spell_log = []          # applied changes, for report/spelling-<vol>.md

    front, body_divs, back_divs = [], [], {"contents": [], "colophon_errata": []}
    cur_div = None          # list of xml strings for the current chapter
    para = None             # current paragraph: list of string pieces (markup + placeholders)
    quote_open = [False]    # a quotation continues into the next paragraph
    last_quote = [None]     # xml:id of the last quote part (for @next/@prev)
    quote_links = {}        # id -> next id

    para_el = ["p"]         # element of the current paragraph: "p", or 'ab type="heading"' (data/edit headings)

    def flush_para():
        nonlocal para
        if para is None:
            return
        text = "".join(para).strip()
        para = None
        el, para_el[0] = para_el[0], "p"
        if not text:
            return
        plain, spans, points = parse_markup(text)
        for o, (c, sic) in ph.errata.items():
            po = next((p for p, ch in points if ch == o), None)
            pc = next((p for p, ch in points if ch == c), None)
            if po is not None and pc is not None:
                spans.append((po, pc, "errata", {"sic": sic}))
                points = [(p, ch) for p, ch in points if ch not in (o, c)]
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
        stats["manuscripta"] += sum(1 for s in bs if s[2] == "idno" and len(s) > 3 and "manuscripta" in s[3].get("corresp", ""))
        stats["academy"] += sum(1 for s in bs if s[2] == "bibl" and len(s) > 3 and "manuscripta" in s[3].get("corresp", ""))
        stats["riksarkivet"] += sum(1 for s in bs if s[2] in ("idno", "bibl") and len(s) > 3 and "riksarkivet" in s[3].get("corresp", ""))
        names = ner_spans(plain)
        # a name never runs across the edge of a hand-encoded addition or label ("Kannenberg K." from an
        # entry into the margin note beside it, vol. V p. 385)
        edges = [(s[0], s[1]) for s in spans if s[2] in EDIT_TAGS or s[2] == "errata"]
        names = [n for n in names if not any(n[0] < x < n[1] for e in edges for x in e)]
        stats["names"] += len(names)
        # old-spelling markers: apply inside quotations only, where no other bound falls inside the word
        marks = [(p, c) for p, c in points if c in spell]
        points = [(p, c) for p, c in points if c not in spell]
        inner = {s[0] for s in spans + qspans + bs + names} | {s[1] for s in spans + qspans + bs + names} \
            | {p for p, _ in points}
        repl = []
        for p, c in marks:
            dw, tw, page_n = spell.pop(c)
            e = p + len(dw)
            if plain[p:e] != dw or any(p < x < e for x in inner):
                continue
            if not any(a <= p and e <= b for a, b, *_ in qspans):
                continue
            repl.append((p, e, f'<app type="ocr"><lem wit="#textlayer" resp="#spelling">{escape(tw)}</lem>'
                               f'<rdg wit="#dots">{escape(dw)}</rdg></app>'))
            spell_log.append((page_n, dw, tw, plain[max(0, p - 50):p] + "[" + dw + "]" + plain[e:e + 50]))
        stats["spelling"] += len(repl)
        bs, corr = corrected_shelves(bs)
        for a, b, c, at in corr:
            if not any(a < x < b for x in inner - {a, b}):
                repl.append((a, b, choice_xml(plain, a, b, c, at)))
                stats["corrected"] += 1
            else:                       # another element inside the shelfmark: keep it as printed, unlinked
                bs.append((a, b, "idno", {}))
        names = [n for n in names if not any(a < n[0] < b or a < n[1] < b for a, b, _ in repl)]
        xml = serialize(plain, spans + qspans + bs + names, points, ph.xml, repl)
        cur_div.append(f"<{el}>{xml}</{el.split()[0]}>")
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
        if idx in part_leaf:
            k = part_leaf[idx]
            resp, blocks = page_source(vol, idx)
            texts = [b["text"] for b in blocks if b["kind"] not in ("figure", "pageno", "signature") and b["text"].strip()
                     and fuzz.ratio(b["text"].strip(" .").lower(), vparts[k]["title"].lower()) < 80]
            lead = page_points(idx, page, resp, [])
            if texts:   # frontispiece caption
                lead += (f'<figure type="plate" facs="#f{vol}-{idx:04d}">'
                         f'<head>{inline(" / ".join(texts), ph.xml)}</head></figure>')
            part_lead[k].append(lead)
            stats["pages"] += 1
            continue
        if idx < first_idx:   # front matter by type from the structure file; binding/scan pages: facsimile only
            ftype = front_idx.get(idx)
            if ftype == "binding":
                continue
            resp, blocks = page_source(vol, idx)
            texts = [b["text"] for b in blocks if b["kind"] not in ("figure", "pageno", "signature") and b["text"].strip()]
            pb_xml = page_points(idx, page, resp, [])
            if ftype == "frontispiece":   # plate facing the title page: the printed caption becomes the figure's head
                fig = (f'<figure type="plate" facs="#f{vol}-{idx:04d}">'
                       + (f'<head>{inline(" / ".join(texts), ph.xml)}</head>' if texts else "") + "</figure>")
                front.append(f'<div type="frontispiece">{pb_xml}{fig}</div>')
            elif ftype in ("halftitle", "titlepage"):
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
            if idx in edit:   # hand-encoded (a TOC page dots.mocr could not read): one ab per line
                resp, blocks = "#edit", [{"kind": "body", "text": x} for k, x in edit[idx]["items"] if k in ("p", "h")]
                stats["edit"] += 1
            target.append(page_points(idx, page, resp, []))
            for b in blocks:
                if b["kind"] in ("body", "heading", "margin", "foot", "caption", "other", "head") and b["text"]:
                    target.append(f"<ab>{inline(b['text'], ph.xml)}</ab>")
            stats["pages"] += 1
            continue
        if how == "plate" or (how not in ("read", "filled") and idx > first_idx):
            resp, blocks = page_source(vol, idx)
            texts = [b["text"] for b in blocks if b["kind"] not in ("figure", "table", "pageno") and b["text"].strip()]
            if idx in edit:   # hand-encoded: the caption only, the plate's own lettering not transcribed
                resp, texts = "#edit", [x for k, x in edit[idx]["items"] if k == "fig" and x]
                stats["edit"] += 1
            if sum(map(len, texts)) < 300:   # caption, e.g. "JOHANNES BUREUS / Efter porträtt på Gripsholm"
                inner = f"<head>{inline(' / '.join(texts), ph.xml)}</head>" if texts else ""
            else:                            # lettering on the plate itself (an old print's title, a facsimile): not transcribed
                inner = ""
            pts = ph.add(f'<pb facs="#f{vol}-{idx:04d}" resp="{resp}"/>'
                         f'<figure type="plate" facs="#f{vol}-{idx:04d}">{inner}</figure>')
            if para is not None:
                para.append(pts)
            else:
                cur_div.append(ph.xml[pts])
            stats["pages"] += 1
            continue

        if idx in edit:     # a hand-encoded passage (data/edit): its items replace the OCR blocks
            ep = edit[idx]
            stats["pages"] += 1
            stats["edit"] += 1
            used = set()

            def fill(t, ep=ep, used=used):
                t = t.replace(" // ", " " + ph.add("<lb/>")).replace("//", ph.add("<lb/>"))
                t = re.sub(r"⦗(.*?)⦘", lambda m: ph.add(
                    f'<note place="margin" type="summary">{inline(m.group(1), ph.xml)}</note>'), t)

                def foot(m):
                    n = m.group(1)
                    if n not in ep["foot"]:
                        return m.group(0)
                    used.add(n)
                    return ph.add(f'<note place="foot" n="{n}">{inline(ep["foot"][n], ph.xml, footnote=True)}</note>')
                return re.sub(r"\^(\d{1,2})", foot, t)
            pb = page_points(idx, page, "#edit", ([{"kind": "head", "text": ep["head"]}] if ep["head"] else [])
                             + ([{"kind": "pageno", "text": str(page)}] if ep["printed"] else [])
                             + ([{"kind": "signature", "text": ep["sig"]}] if ep["sig"] else []))
            def quoted(xml):
                """A table cell inside a quoted document: one more part of its quotation chain."""
                if not quote_open[0]:
                    return xml
                qn[0] += 1
                qid = f"q-{vol}-{qn[0]:05d}"
                prev = f' prev="#{last_quote[0]}"' if last_quote[0] else ""
                if last_quote[0]:
                    quote_links[last_quote[0]] = qid
                last_quote[0] = qid
                stats["quotes"] += 1
                return f'<quote xml:id="{qid}"{prev}>{xml}</quote>'
            for kind, x in ep["items"]:
                if kind == "q":
                    flush_para()
                    quote_open[0], last_quote[0] = x, None
                    continue
                if kind == "fig":         # a picture in the text: stays inside a paragraph that runs past it
                    head = f"<head>{inline(x, ph.xml)}</head>" if x else ""
                    fig = (pb or "") + f'<figure facs="#f{vol}-{idx:04d}">{head}</figure>'
                    pb = None
                    if para is not None:
                        para.append(ph.add(fig))
                    else:
                        cur_div.append(fig)
                    continue
                if kind == "+" and para is not None and pb:
                    prev = "".join(para).rstrip()
                    para = [prev]
                    if prev.endswith("-") and first_char(x).islower():
                        para[0] = prev[:-1]
                        para.append(ph.add(pb.replace("<pb ", '<pb break="no" ', 1)))
                    else:
                        para.append(ph.add(pb))
                        para.append(" ")
                    para.append(fill(x))
                    pb = None
                    continue
                flush_para()
                if pb:
                    cur_div.append(pb)
                    pb = None
                if kind == "table":
                    cur_div.append("<table>" + "".join("<row>" + "".join(
                        f"<cell>{quoted(inline(c, ph.xml))}</cell>" for c in row) + "</row>" for row in x) + "</table>")
                elif kind == "h":         # through flush_para, so that a heading inside a quoted document is quoted
                    para, para_el[0] = [fill(x)], 'ab type="heading"'
                    flush_para()
                else:
                    para = [fill(x)]
            if pb:
                flush_para()
                cur_div.append(pb)
            for n in set(ep["foot"]) - used:
                print(f"  {vol} p{idx:04d}: footnote {n} has no anchor", file=sys.stderr)
            continue

        resp, blocks = page_source(vol, idx)
        stats["pages"] += 1
        stats[resp[1:]] += 1
        if resp == "#dots":
            stats["margin_rescued"] += rescue_margins(vol, idx, blocks)
            # topics of this page, and of the previous one when not found there (the TOC is off by one)
            topics = toc_topics.get(page, []) + [t for t in toc_topics.get(page - 1, []) if t not in toc_done] \
                if isinstance(page, int) else []
            k, done = rescue_toc(vol, idx, topics, blocks)
            stats["margin_toc"] += k
            stats["margin_inserted"] += rescue_inserted(vol, idx, topics, done, blocks, toc_vocab)
            toc_done = done if isinstance(page, int) else set()
        # an illustration printed in the text (e.g. a runic stone) whose inscriptions OCR reads
        # as many marginal blocks: more than four "sidenotes" on one page -> a figure, its text dropped
        margins = [b for b in blocks if b["kind"] == "margin"]
        if len([b for b in margins if b.get("cat") != "toc"]) > 4:
            for b in margins:
                b["kind"] = "figtext"
            blocks.append({"kind": "figure", "text": "", "bbox": None,
                           "figtext": [b["text"] for b in margins]})
        # a figure's caption: the Caption block, or text dots.mocr called a page footer, just below the picture;
        # one caption under several pictures (the two halves of a plate) makes them one figure
        figs = [b for b in blocks if b["kind"] == "figure" and b.get("bbox")]
        for c in [b for b in blocks if b["kind"] == "caption" or (b["kind"] == "other" and b.get("cat") == "Page-footer")]:
            cb = c["bbox"]
            under = [f for f in figs if -10 <= cb[1] - f["bbox"][3] <= 120 and f["bbox"][0] < cb[2] and cb[0] < f["bbox"][2]]
            if not under and c["kind"] == "caption" and figs:
                under = [min(figs, key=lambda f: abs(cb[1] - f["bbox"][3]))]
            if under:
                under[0]["caption"] = (under[0].get("caption", "") + " " + c["text"]).strip()
                for f in under[1:]:
                    f["kind"] = "figpart"
                c["kind"] = "figcaption"
                stats["captions"] += 1
        for e in struct.get("errata", []):   # the printed errata list: <choice><sic/><corr/></choice>
            if e["page"] != page:
                continue
            for b in blocks:
                if b["kind"] == "body" and e["sic"] in b["text"]:
                    # the corrected reading stays in the text between two markers (an "errata" span after
                    # parse_markup), so the paragraph's plain text is what ner.py reads (it skips <sic>)
                    o, c = ph.add(""), ph.add("")
                    ph.errata[o] = (c, e["sic"])
                    b["text"] = b["text"].replace(e["sic"], o + e["corr"] + c, 1)
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
                    mresp = f' resp="{b["resp"]}"' if b.get("resp") else ""     # rescue_inserted: "#toc"
                    inserts.setdefault(bi, []).append((off, ph.add(
                        f'<note place="margin" type="summary"{mresp}>{inline(b["text"], ph.xml)}</note>')))
                    stats["margin"] += 1
        pb = page_points(idx, page, resp, blocks)

        if ch:
            flush_para()
            if page in part_at:
                body_divs.append(part_at[page])
            chapter_no += 1
            cur_div = [f'<div type="chapter" n="{chapter_no}">', pb, f"<head>{head_xml}</head>"]
            body_divs.append(cur_div)
            pb = None
            quote_open[0] = False

        body_blocks = [i for i, b in enumerate(blocks) if b["kind"] in ("body", "heading", "table", "figure")]
        used = set()

        def anchor(m):
            n = m.group(1)
            if n in notes:
                used.add(n)
                return ph.add(f'<note place="foot" n="{n}">{inline(notes[n], ph.xml, footnote=True)}</note>')
            return m.group(0)
        prepared = {}       # block index -> text with sidenotes and footnotes placed; body: paragraph parts
        for bi in body_blocks:
            text = blocks[bi]["text"]
            for off, c in sorted(inserts.get(bi, []), reverse=True):
                text = text[:off] + c + text[off:]
            text = re.sub(r"\^(\d{1,2})", anchor, text)
            if blocks[bi]["kind"] == "body":
                # GT: one paragraph per line; OCR blocks: one paragraph, printed line breaks dehyphenated
                parts = text.split("\n") if resp == "#gt" else [dehyphenate(text.split("\n"))]
                prepared[bi] = [t for t in parts if t.strip()]
            else:
                prepared[bi] = text
        if resp == "#dots":   # old-spelling candidates: zero-width markers before the word
            keys = [(bi, j) for bi in body_blocks if blocks[bi]["kind"] == "body" for j in range(len(prepared[bi]))]
            cands = spelling_candidates(vol, idx, [prepared[bi][j] for bi, j in keys])
            for (bi, j), cs in zip(keys, cands):
                s = prepared[bi][j]
                for off, dw, tw in sorted(cs, reverse=True):
                    c = ph.add("")
                    spell[c] = (dw, tw, page)
                    s = s[:off] + c + s[off:]
                prepared[bi][j] = s

        for k, bi in enumerate(body_blocks):
            b = blocks[bi]
            text = prepared[bi]

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
                inner = f"<head>{inline(b['caption'], ph.xml)}</head>" if b.get("caption") else ""   # lettering in the picture: not transcribed
                fig = f'<figure facs="#f{vol}-{idx:04d}">{inner}</figure>' if inner else f'<figure facs="#f{vol}-{idx:04d}"/>'
                if pb and all(blocks[j]["kind"] == "figure" for j in body_blocks[k:]):
                    fig, pb = pb + fig, None   # a page of pictures: its page break before them, in the paragraph
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
            for j, ptxt in enumerate(text):
                continues = (k == 0 and j == 0 and pb is not None and para is not None and not ch)
                if continues:
                    ind = first_line_indented(vol, idx)
                    prev = "".join(para).rstrip()
                    if ind is None:
                        ind = bool(re.search(r"[.!?»:]$", prev)) and first_char(ptxt).isupper()
                    continues = not ind
                if continues:
                    prev = "".join(para).rstrip()
                    para = [prev]
                    if prev.endswith("-") and first_char(ptxt).islower():
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
                        f'{inline(t, ph.xml, footnote=True)}</note>')
            if para is not None:
                para.append(el)
            else:
                cur_div.append(ph.xml[el])
    flush_para()

    # quote chains: add @next to earlier parts
    def link(xml):
        return re.sub(r'<quote xml:id="([^"]+)"', lambda m: f'<quote xml:id="{m.group(1)}" next="#{quote_links[m.group(1)]}"'
                      if m.group(1) in quote_links else m.group(0), xml)

    out, in_part = [], False
    for d in body_divs:
        if isinstance(d, int):   # a part starts before this chapter
            out.append(("</div>\n" if in_part else "") + f'<div type="part" n="{d + 1}">'
                       + "".join(part_lead[d]) + f"<head>{escape(vparts[d]['title'])}</head>")
            in_part = True
        else:
            out.append(link("\n".join(d)) + "\n</div>")
    body = "\n".join(out) + ("\n</div>" if in_part else "")
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
    quoted_doc = any(k == "e" for ep in edit.values() for k, _ in ep["items"])
    unread = any(not any(k == "e" for k, _ in ep["items"]) and not any(k == "q" for k, _ in ep["items"])
                 for ep in edit.values())    # hand-encoded because OCR failed (figures, a TOC page)
    edit_resp = ('<respStmt xml:id="edit"><resp>Hand-encoded page (data/edit): text corrected against the page '
                 'image and its structure encoded by hand' + (' (in a quoted document: entries, item numbers, later '
                 'additions in small type as add)' if quoted_doc else '') + '</resp><name>Schück digital</name>'
                 '</respStmt>\n') if edit else ""
    edit_decl = (" Pages OCR could not read are encoded by hand (#edit); pictures and plates are not transcribed, "
                 "only their printed captions." if unread else "") + (
                 " Some quoted documents are encoded by hand (#edit): entries carry their item number and year as "
                 "label, and the additions Schück prints in smaller type are add (place=\"margin\" where printed in "
                 "the margin)." if quoted_doc else "")
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
<respStmt xml:id="editor"><resp>Correction of a misprinted shelfmark, checked against the manuscript catalogue (data/sources/corrections.tsv)</resp><name>Schück digital</name></respStmt>
<respStmt xml:id="textlayer"><resp>OCR</resp><name>Text layer of the source PDF</name></respStmt>
{edit_resp}<respStmt xml:id="toc"><resp>Sidenote the OCR dropped: wording from the printed table of contents, position from the text layer's margin words</resp><name>scripts/to_tei.py</name></respStmt>
<respStmt xml:id="spelling"><resp>Old-spelling rule: inside quotations, the text layer's reading where dots.mocr differs from it only by w/v, j/i or a doubled letter (dots.mocr's reading kept as rdg)</resp><name>scripts/to_tei.py</name></respStmt>
</titleStmt>
<publicationStmt><authority>Schück digital (working draft generated by scripts/to_tei.py; not for citation)</authority>
<availability status="free"><p>The text is in the public domain (Henrik Schück died 1947).</p></availability></publicationStmt>
<sourceDesc><bibl><author>Henrik Schück</author>, <title>{escape(struct["title"])}</title> <biblScope unit="volume">{meta["part"]}</biblScope>. <pubPlace>Stockholm</pubPlace> <date when="{meta["year"]}">{meta["year"]}</date>. <ref target="{LIBRIS}">Libris</ref></bibl></sourceDesc>
</fileDesc>
<encodingDesc>
<editorialDecl><p>Text from verified ground truth where available (pb/@resp="#gt"), otherwise from dots.mocr OCR (#dots) or the PDF text layer (#textlayer). Uncorrected OCR.{edit_decl} Spelling, including that of quoted sources, is kept as printed. A misprinted shelfmark is given as choice/sic/corr (resp="#editor"), and only the corrected one is linked. Where dots.mocr modernised the spelling of a quotation (w/v, j/i, doubled letters) and the text layer has the old form, the old form is given in app/lem (resp="#spelling") with dots.mocr's reading in rdg. End-of-line hyphenation is removed; words broken across pages carry pb/@break="no". Footnotes and marginal summaries are placed at their anchors; placements marked cert="low" are guesses. Person and place names (persName, placeName) come from named-entity recognition (KB-BERT); @ref points to tei/register.xml, and @cert gives the certainty of the identification (low = an unreviewed guess).</p></editorialDecl>
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
    return xml, stats, spell_log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", required=True)
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()
    xml, stats, spell_log = build(a.vol)
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
    rep = ROOT / "report" / f"spelling-{a.vol}.md"
    rows = [f"| {p or ''} | {escape(d)} | {escape(t)} | {c.replace('|', '/')} |" for p, d, t, c in spell_log]
    rep.write_text(f"# Old-spelling rule, vol. {a.vol}\n\nInside quotations, dots.mocr's word was replaced by the text "
                   f"layer's where they differ only by w/v, j/i or doubled letters: {len(rows)} changes. "
                   "Spot-check against the scan.\n\n| page | dots.mocr | text layer | context |\n|---|---|---|---|\n"
                   + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"-> {rep}")
    if a.validate:
        r = subprocess.run(["java", "-jar", str(JING), str(SCHEMA), str(out)], capture_output=True, text=True)
        errs = r.stdout.strip().splitlines()
        print("valid against tei_all" if r.returncode == 0 else f"{len(errs)} validation errors:\n" + "\n".join(errs[:25]))


if __name__ == "__main__":
    main()
