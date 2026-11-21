"""EPUB 3 export of one volume from its TEI.

  python scripts/to_epub.py --vol 1932 [--check]     -> site/epub/schuck-<vol>.epub

Mapping TEI → XHTML:
  - one XHTML file per chapter, plus the title page, dedication, printed table of
    contents, errata and an "Om denna utgåva" page (text provenance, date)
  - footnotes → EPUB 3 popup notes (noteref + aside epub:type="footnote") at the end of
    the chapter
  - sidenotes → an italic subheading placed before the paragraph in which they begin
  - plates → <figure> with the page image (1000 px JPEG) and the printed caption;
    engraved text on plates is left out (uncorrected OCR; the image shows it)
  - printed page numbers → invisible page-break markers + a page-list in the nav
    document, so reading systems can go to and cite print pages
  - quotations keep their guillemets; whole-paragraph quotations are indented
  - errata: the corrected reading; running heads, printed readings (sic) and figure
    text from inline illustrations are not reproduced
  - cover: the scanned title page
--check runs epubcheck: $EPUBCHECK_JAR (default cache/tools/epubcheck-5.3.0/epubcheck.jar), Java from $EPUBCHECK_JAVA.
"""
import argparse
import datetime
import json
import re
import subprocess
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

from lxml import etree
from PIL import Image

from common import LIBRIS, ROOT, VOLUMES, img_path

T = "{http://www.tei-c.org/ns/1.0}"
XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

CSS = """
body { font-family: serif; line-height: 1.45; margin: 0 4%; }
p.part { font-size: 1.6em; text-align: center; margin: 3em 0 0; letter-spacing: .1em; text-transform: uppercase; }
h1 { font-size: 1.4em; text-align: center; margin: 2em 0 1.2em; font-weight: normal; letter-spacing: .05em; }
h2.sub { font-size: 1em; font-style: italic; font-weight: normal; text-align: center; margin: 1em 0 .5em; }
p { margin: 0; text-indent: 1.3em; text-align: justify; }
p.first, p.noindent { text-indent: 0; }
p.sidenote { font-style: italic; font-size: .85em; text-indent: 0; margin: .9em 0 .2em; color: #555; }
p.quoted { margin: .4em 1.6em; text-indent: 0; font-size: .95em; }
.spaced { letter-spacing: .12em; }
a.noteref { font-size: .7em; vertical-align: super; line-height: 0; text-decoration: none; }
aside.footnote { font-size: .85em; margin: .4em 0; }
aside.footnote p { text-indent: 0; }
section.footnotes { margin-top: 2em; border-top: 1px solid #999; padding-top: .5em; }
figure { margin: 1.2em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; max-height: 90vh; }
figcaption { font-size: .85em; font-style: italic; margin-top: .3em; }
.titlepage p, .dedication p { text-align: center; text-indent: 0; margin: .6em 0; }
.titlepage { margin-top: 3em; }
table { border-collapse: collapse; margin: 1em 0; font-size: .9em; }
td { padding: 0 .5em; vertical-align: top; }
.contents p, .corrigenda p { text-indent: 0; margin: .3em 0; }
.about p { text-indent: 0; margin: .6em 0; }
"""


def h(tag, attrs=None, text=None):
    e = etree.Element(f"{{{XHTML}}}{tag}")
    for k, v in (attrs or {}).items():
        e.set(k, v)
    if text:
        e.text = text
    return e


def append_text(parent, s):
    if not s:
        return
    if len(parent):
        parent[-1].tail = (parent[-1].tail or "") + s
    else:
        parent.text = (parent.text or "") + s


class Book:
    def __init__(self, vol):
        self.vol = vol
        self.struct = json.loads((ROOT / "data" / "structure" / f"{vol}.json").read_text())
        self.tei = etree.parse(str(ROOT / "tei" / f"schuck-{vol}.xml"))
        self.docs = []          # (filename, title, body element, nav level)
        self.pages = []         # (label, filename, id)
        self.page_ids = set()
        self.images = {}        # epub path -> source image path
        self.fn = 0

    # ---------------------------------------------------------------- inline rendering
    def inline(self, node, out, ctx):
        """Render node's children into the XHTML element `out`. Block-level things met
        inside a paragraph (sidenotes, plates) are collected in ctx for hoisting."""
        append_text(out, node.text)
        for c in node:
            self.element(c, out, ctx)
            append_text(out, c.tail)

    def element(self, c, out, ctx):
        tag = c.tag.replace(T, "") if isinstance(c.tag, str) else ""
        if tag in ("fw", "sic", "rdg", ""):   # rdg: dots.mocr's reading beside the old-spelling rule's lem
            return
        if tag == "pb":
            n = c.get("n")
            if n:
                pid = f"page-{n}"
                k = 2
                while pid in self.page_ids:
                    pid, k = f"page-{n}-{k}", k + 1
                self.page_ids.add(pid)
                out.append(h("span", {f"{{{EPUB}}}type": "pagebreak", "role": "doc-pagebreak", "id": pid,
                                      "aria-label": n, "title": n}))
                self.pages.append((n, ctx["file"], pid))
            return
        if tag == "note" and c.get("place") == "margin":
            ctx["sidenotes"].append("".join(c.itertext()).strip())
            return
        if tag == "note":
            self.fn += 1
            k = self.fn
            a = h("a", {f"{{{EPUB}}}type": "noteref", "role": "doc-noteref", "href": f"#fn{k}", "id": f"fnref{k}",
                        "class": "noteref"}, c.get("n") or "*")
            out.append(a)
            aside = h("aside", {f"{{{EPUB}}}type": "footnote", "role": "doc-footnote", "id": f"fn{k}", "class": "footnote"})
            p = h("p")
            back = h("a", {"href": f"#fnref{k}"}, c.get("n") or "*")
            back.tail = " "
            p.append(back)
            self.inline(c, p, ctx)
            aside.append(p)
            ctx["footnotes"].append(aside)
            return
        if tag == "figure":
            ctx["figures"].append(c)
            return
        if tag == "hi":
            e = h("i") if c.get("rend") == "italic" else h("span", {"class": "spaced"})
        elif tag == "quote":
            e = h("span", {"class": "quote"})
        elif tag == "bibl":
            e = h("span", {"class": "source"})
            if c.get("corresp"):                       # a cited volume without shelfmark: a link after it
                self.inline(c, e, ctx)
                out.append(e)
                append_text(out, " ")
                out.append(h("a", {"href": c.get("corresp")}, "↗"))
                return
        elif tag == "lb":
            out.append(h("br"))
            return
        elif tag == "idno" and c.get("corresp"):      # digitised on manuscripta.se
            e = h("a", {"href": c.get("corresp")})
        elif tag in ("choice", "corr", "orgName", "idno", "persName", "placeName", "titlePart"):
            self.inline(c, out, ctx)
            return
        else:
            self.inline(c, out, ctx)
            return
        self.inline(c, e, ctx)
        out.append(e)

    # ---------------------------------------------------------------- blocks
    def figure(self, fig, body):
        facs = (fig.get("facs") or "")[1:]
        if fig.get("type") != "plate" or not facs:
            return                                   # inline illustrations: not reproduced
        idx = int(facs.rsplit("-", 1)[1])
        if blank(img_path(self.vol, idx)):           # the empty back of a plate leaf
            return
        name = f"images/plate-{idx:04d}.jpg"
        self.images[name] = img_path(self.vol, idx)
        head = fig.find(f"{T}head")
        cap = re.sub(r"\s+", " ", "".join(head.itertext())).strip(" /") if head is not None else ""
        if len(cap) > 80:      # engraved title of an old print read by OCR, not a printed caption
            cap = ""
        f = h("figure")
        f.append(h("img", {"src": name, "alt": cap or "Plansch"}))
        if cap:
            f.append(h("figcaption", None, cap))
        body.append(f)

    def blocks(self, node, body, ctx):
        """Block-level TEI children of node → XHTML in body."""
        first = True
        for c in node:
            tag = c.tag.replace(T, "") if isinstance(c.tag, str) else ""
            if tag in ("head", "fw"):
                continue
            if tag == "pb":
                self.element(c, body, ctx)          # a page break between paragraphs
                continue
            if tag == "figure":
                self.figure(c, body)
                continue
            if tag == "p" or tag == "ab":
                if c.get("type") in ("figText", "plateText"):
                    continue
                if c.get("type") == "heading":
                    e = h("h2", {"class": "sub"})
                    self.inline(c, e, ctx)
                    body.append(e)
                    first = True
                    continue
                ctx["sidenotes"], ctx["figures"] = [], []
                kids = [k for k in c if isinstance(k.tag, str) and k.tag.replace(T, "") not in ("pb", "note", "fw")]
                whole_quote = (len(kids) == 1 and kids[0].tag == f"{T}quote" and not (c.text or "").strip()
                               and kids[0].get("rend") == "block")
                p = h("p", {"class": "quoted" if whole_quote else ("first" if first else "")})
                if not p.get("class"):
                    del p.attrib["class"]
                self.inline(c, p, ctx)
                for s in ctx["sidenotes"]:            # sidenotes become a subheading before the paragraph
                    body.append(h("p", {"class": "sidenote"}, s))
                body.append(p)
                for fig in ctx["figures"]:            # plates met inside the paragraph follow it
                    self.figure(fig, body)
                first = False
                continue
            if tag == "table":
                t = h("table")
                for row in c.iter(f"{T}row"):
                    tr = h("tr")
                    for cell in row.iter(f"{T}cell"):
                        td = h("td")
                        self.inline(cell, td, ctx)
                        tr.append(td)
                    t.append(tr)
                body.append(t)
                continue
            if tag == "div":
                self.blocks(c, body, ctx)
                continue

    def doc(self, filename, title, cls, fill, level=1):
        body = h("body")
        etype = {"contents": "toc", "corrigenda": "errata", "about": "colophon"}.get(cls, cls)  # EPUB vocabulary
        sec = h("section", {"class": cls, f"{{{EPUB}}}type": etype})
        body.append(sec)
        ctx = {"file": filename, "footnotes": [], "sidenotes": [], "figures": []}
        fill(sec, ctx)
        if ctx["footnotes"]:
            fs = h("section", {"class": "footnotes", f"{{{EPUB}}}type": "footnotes"})
            for a in ctx["footnotes"]:
                fs.append(a)
            sec.append(fs)
        self.docs.append((filename, title, body, level))

    # ---------------------------------------------------------------- build
    def build(self):
        text = self.tei.find(f".//{T}text")
        front = text.find(f"{T}front")
        meta = VOLUMES[self.vol]
        # title page (front matter: title pages and dedication; blank leaves skipped)
        if front is not None:
            def fill_title(sec, ctx):
                for c in front:
                    t = c.tag.replace(T, "")
                    if t == "titlePage" and c.get("type") == "titlepage":
                        d = h("div", {"class": "titlepage"})
                        for pb in c.iter(f"{T}pb"):
                            self.element(pb, d, ctx)
                        for tp in c.iter(f"{T}titlePart"):
                            p = h("p")
                            self.inline(tp, p, ctx)
                            d.append(p)
                        sec.append(d)
            self.doc("title.xhtml", "Titelblad", "titlepage", fill_title)
            ded = [c for c in front if c.get("type") == "dedication"]
            if ded:
                def fill_ded(sec, ctx):
                    d = h("div", {"class": "dedication"})
                    for c in ded[0]:
                        if c.tag == f"{T}pb":
                            self.element(c, d, ctx)
                        elif c.tag == f"{T}p":
                            p = h("p")
                            self.inline(c, p, ctx)
                            d.append(p)
                    sec.append(d)
                self.doc("dedication.xhtml", "Tillägnan", "dedication", fill_ded)
        titles = {str(k + 1): ch["title"] for k, ch in enumerate(self.struct["chapters"])}
        body = text.find(f"{T}body")
        parent = {c: p for p in body.iter(f"{T}div") for c in p}
        for d in body.iter(f"{T}div"):
            if d.get("type") != "chapter":
                continue
            n = d.get("n")
            title = titles.get(n, f"Kapitel {n}")
            par = parent.get(d)   # vol. V: the part title before the part's first chapter
            part = ("".join(par.find(f"{T}head").itertext())
                    if par is not None and par.get("type") == "part"
                    and next(c for c in par if c.get("type") == "chapter") is d else None)

            def fill_ch(sec, ctx, d=d, title=title, part=part):
                # the page break before the chapter heading
                for c in d:
                    if c.tag == f"{T}pb":
                        self.element(c, sec, ctx)
                    elif c.tag == f"{T}head":
                        break
                if part:
                    sec.append(h("p", {"class": "part"}, part))
                sec.append(h("h1", None, title))
                self.blocks(d, sec, ctx)
            self.doc(f"chapter{int(n):02d}.xhtml", title, "chapter", fill_ch)
        back = text.find(f"{T}back")
        if back is not None:
            for d in back:
                t = d.get("type")
                label = {"contents": "Innehåll (tryckt)", "corrigenda": "Rättelser"}.get(t)
                if not label:
                    continue

                def fill_back(sec, ctx, d=d, label=label):
                    sec.append(h("h1", None, label))
                    for c in d.iter():
                        tag = c.tag.replace(T, "") if isinstance(c.tag, str) else ""
                        if tag == "pb":
                            self.element(c, sec, ctx)
                        elif tag == "ab":
                            p = h("p")
                            self.inline(c, p, ctx)
                            sec.append(p)
                self.doc(f"{t}.xhtml", label, t, fill_back)
        resp = [pb.get("resp") for pb in self.tei.iter(f"{T}pb")]
        today = datetime.date.today().isoformat()

        def fill_about(sec, ctx):
            sec.append(h("h1", None, "Om denna utgåva"))
            for s in (
                f"Henrik Schück, Kgl. Vitterhets historie och antikvitets akademien. Dess förhistoria och historia, "
                f"del {meta['part']} (Stockholm {meta['year']}). Verket är fritt från upphovsrätt (Schück dog 1947).",
                "Texten är framställd med OCR (dots.mocr) ur den digitaliserade utgåvan och har ännu inte "
                f"korrekturlästs i sin helhet: {resp.count('#gt')} av {len(resp)} sidor är granskade mot faksimilet. "
                "Stavningen i citerade källor återges som i trycket. Rättelser ur trycket (Rättelser) är införda.",
                "Marginalrubrikerna återges som kursiva mellanrubriker, fotnoterna som noter. Trycksidornas "
                "nummer finns som sidmarkeringar och kan användas för hänvisningar.",
                f"Schück digital, version {today}. Källa: {LIBRIS}",
            ):
                sec.append(h("p", None, s))
        self.doc("about.xhtml", "Om denna utgåva", "about", fill_about)

    # ---------------------------------------------------------------- package
    def xhtml(self, title, body):
        html = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML, "epub": EPUB})
        html.set("{http://www.w3.org/XML/1998/namespace}lang", "sv")
        html.set("lang", "sv")
        head = etree.SubElement(html, f"{{{XHTML}}}head")
        etree.SubElement(head, f"{{{XHTML}}}title").text = title
        etree.SubElement(head, f"{{{XHTML}}}link", rel="stylesheet", type="text/css", href="styles.css")
        html.append(body)
        return b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n' + etree.tostring(html, encoding="UTF-8")

    def nav(self):
        toc = "".join(f'<li><a href="{f}">{etree.Element("x").text or ""}{_esc(t)}</a></li>' for f, t, _, _ in self.docs)
        pages = "".join(f'<li><a href="{f}#{i}">{_esc(n)}</a></li>' for n, f, i in self.pages)
        first_ch = next((f for f, _, _, _ in self.docs if f.startswith("chapter")), self.docs[0][0])
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="sv" lang="sv">
<head><title>Innehåll</title><link rel="stylesheet" type="text/css" href="styles.css"/></head>
<body>
<nav epub:type="toc" id="toc" role="doc-toc"><h1>Innehåll</h1><ol>{toc}</ol></nav>
<nav epub:type="page-list" id="page-list" hidden=""><h1>Sidor</h1><ol>{pages}</ol></nav>
<nav epub:type="landmarks" id="landmarks" hidden=""><h1>Hitta</h1><ol>
<li><a epub:type="toc" href="nav.xhtml">Innehåll</a></li>
<li><a epub:type="bodymatter" href="{first_ch}">Texten</a></li></ol></nav>
</body></html>""".encode()

    def write(self, out):
        meta = VOLUMES[self.vol]
        uid = uuid.uuid5(uuid.NAMESPACE_URL, f"{LIBRIS}#{self.vol}")
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        title = f"{self.struct['title']}. {meta['part']}"
        cover_src = self.tei_cover()
        files = {}
        for f, t, body, _ in self.docs:
            files[f] = self.xhtml(t, body)
        files["nav.xhtml"] = self.nav()
        files["styles.css"] = CSS.encode()
        imgs = {}
        for name, src in self.images.items():
            imgs[name] = jpeg(src, 1000)
        if cover_src:
            imgs["images/cover.jpg"] = jpeg(cover_src, 1200)
            files["cover.xhtml"] = (
                '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="sv" lang="sv"><head><title>Omslag</title>'
                '<style>body{margin:0;text-align:center}img{max-width:100%;max-height:100vh}</style></head><body>'
                '<section epub:type="cover"><img src="images/cover.jpg" alt="Titelbladet"/></section></body></html>').encode()
        spine_files = (["cover.xhtml"] if cover_src else []) + [f for f, _, _, _ in self.docs]
        man = []
        for f in files:
            props = ' properties="nav"' if f == "nav.xhtml" else ""
            mt = "text/css" if f.endswith(".css") else "application/xhtml+xml"
            man.append(f'<item id="{_id(f)}" href="{f}" media-type="{mt}"{props}/>')
        for f in imgs:
            props = ' properties="cover-image"' if f.endswith("cover.jpg") else ""
            man.append(f'<item id="{_id(f)}" href="{f}" media-type="image/jpeg"{props}/>')
        spine = "".join(f'<itemref idref="{_id(f)}"/>' for f in spine_files) + \
            f'<itemref idref="{_id("nav.xhtml")}" linear="no"/>'   # the nav is linked from landmarks
        opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="sv">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">urn:uuid:{uid}</dc:identifier>
<dc:title>{_esc(title)}</dc:title>
<dc:creator>Henrik Schück</dc:creator>
<dc:language>sv</dc:language>
<dc:date>{meta['year']}</dc:date>
<dc:publisher>Schück digital</dc:publisher>
<dc:rights>Public domain</dc:rights>
<dc:source>{LIBRIS}</dc:source>
<meta property="dcterms:modified">{now}</meta>
<meta property="belongs-to-collection" id="coll">Kgl. Vitterhets historie och antikvitets akademien</meta>
<meta refines="#coll" property="collection-type">series</meta>
<meta refines="#coll" property="group-position">{list(VOLUMES).index(self.vol) + 1}</meta>
</metadata>
<manifest>
{chr(10).join(man)}
</manifest>
<spine>{spine}</spine>
</package>
""".encode()
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" '
                       'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                       'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                       compress_type=zipfile.ZIP_DEFLATED)
            z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
            for f, data in files.items():
                z.writestr(f"OEBPS/{f}", data, compress_type=zipfile.ZIP_DEFLATED)
            for f, data in imgs.items():
                z.writestr(f"OEBPS/{f}", data, compress_type=zipfile.ZIP_STORED)
        return out

    def tei_cover(self):
        tp = self.struct.get("front", {}).get("titlepage")
        return img_path(self.vol, tp[0]) if tp else None


def blank(src):
    """True for an (almost) empty page: few dark pixels in the central area."""
    with Image.open(src) as im:
        g = im.convert("L")
        w, hgt = g.size
        g = g.crop((int(w * .2), int(hgt * .15), int(w * .8), int(hgt * .85))).resize((300, 400))
        dark = sum(g.histogram()[:110])
        return dark / (300 * 400) < 0.004


def jpeg(src, width):
    with Image.open(src) as im:
        im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        b = BytesIO()
        im.save(b, "JPEG", quality=80, optimize=True)
        return b.getvalue()


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _id(f):
    return "i-" + re.sub(r"[^\w]", "-", f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", required=True)
    ap.add_argument("--out", default=str(ROOT / "site" / "epub"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    b = Book(a.vol)
    b.build()
    out = b.write(Path(a.out) / f"schuck-{a.vol}.epub")
    print(f"-> {out} ({out.stat().st_size / 1e6:.1f} MB): {len(b.docs)} documents, {b.fn} footnotes, "
          f"{len(b.pages)} page markers, {len(b.images)} plates")
    if a.check:
        # epubcheck jar from $EPUBCHECK_JAR (default: the official 5.3.0 release unzipped into
        # cache/tools/); Java from $EPUBCHECK_JAVA (epubcheck 5 needs Java 17+)
        import os
        jar = Path(os.environ.get("EPUBCHECK_JAR", ROOT / "cache" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar"))
        java = os.environ.get("EPUBCHECK_JAVA", "java")
        if not jar.exists():
            raise SystemExit(f"epubcheck not found: {jar} (set EPUBCHECK_JAR)")
        r = subprocess.run([java, "-jar", str(jar), str(out)], capture_output=True, text=True)
        lines = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
        print("\n".join(lines[-25:]))

if __name__ == "__main__":
    main()
