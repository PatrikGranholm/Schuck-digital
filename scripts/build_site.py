"""Build the static reader site for GitHub Pages.

  python scripts/build_site.py [--vol 1932,1933a] [--out site]
  python3 -m http.server 8000 -d site          # preview at http://localhost:8000/

For every volume with a tei/schuck-<vol>.xml:
  site/data/schuck-<vol>.xml   the TEI, with <graphic> pointing at the web images
  site/img/<vol>/pNNNN.webp    the page images referenced by a <pb> (binding/scan images are
                               left out), 1200 px wide, WebP q70 (~150 KB; the whole edition
                               must stay under GitHub Pages' 1 GB site limit)
plus site/data/volumes.json, site/epub/schuck-<vol>.epub (scripts/to_epub.py, rebuilt when
the TEI is newer) and the app files from web/reader/. Images are only rebuilt
when missing. In the site TEI the margin notes get xml:id sn<k> (document order); volumes.json
lists each chapter's printed TOC topics from data/structure/<vol>-toc.json (scripts/toc.py),
with "note": k where the topic was matched to sidenote k. The site is published from its own branch (gh-pages), so the images never
enter the main branch.
"""
import argparse
import json
import re
import shutil

from lxml import etree
from PIL import Image

from common import ROOT, VOLUMES, img_path

TEI = "{http://www.tei-c.org/ns/1.0}"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
WIDTH, QUALITY = 1200, 70


def web_image(vol, idx, out):
    dst = out / "img" / vol / f"p{idx:04d}.webp"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(img_path(vol, idx)) as im:
            h = round(im.height * WIDTH / im.width)
            im.convert("RGB").resize((WIDTH, h), Image.LANCZOS).save(dst, "WEBP", quality=QUALITY, method=6)
    with Image.open(dst) as im:
        return dst, im.size


def build_volume(vol, out):
    t = etree.parse(str(ROOT / "tei" / f"schuck-{vol}.xml"))
    used = {pb.get("facs")[1:] for pb in t.iter(f"{TEI}pb") if pb.get("facs")}
    for s in list(t.iter(f"{TEI}surface")):
        sid = s.get(XML_ID)
        if sid not in used:              # binding, scan target, duplicates: not published
            s.getparent().remove(s)
            continue
        idx = int(sid.rsplit("-", 1)[1])
        dst, (w, h) = web_image(vol, idx, out)
        s.set("lrx", str(w))
        s.set("lry", str(h))
        g = s.find(f"{TEI}graphic")
        g.set("url", f"img/{vol}/{dst.name}")
        g.set("width", f"{w}px")
        g.set("height", f"{h}px")
    # sidenotes get ids in document order, as toc.py numbers them, so the reader's TOC can link to them
    for k, n in enumerate(x for x in t.iter(f"{TEI}note") if x.get("place") == "margin"):
        n.set(XML_ID, f"sn{k}")
    (out / "data").mkdir(parents=True, exist_ok=True)
    t.write(str(out / "data" / f"schuck-{vol}.xml"), encoding="UTF-8", xml_declaration=True)
    struct = json.loads((ROOT / "data" / "structure" / f"{vol}.json").read_text())
    titles = [c["title"] for c in struct["chapters"]]   # proper case, from the table of contents
    chapters = []
    for d in t.iter(f"{TEI}div"):
        if d.get("type") == "chapter":
            head = d.find(f"{TEI}head")
            pb = d.find(f".//{TEI}pb")
            title = re.sub(r"\s+", " ", "".join(head.itertext())).strip(" .") if head is not None else ""
            k = int(d.get("n")) - 1
            chapters.append({"n": d.get("n"), "title": titles[k] if k < len(titles) else title,
                             "page": pb.get("n") if pb is not None else None})
    tocf = ROOT / "data" / "structure" / f"{vol}-toc.json"     # scripts/toc.py: the printed TOC's topics
    if tocf.exists():
        toc = json.loads(tocf.read_text())
        if len(toc) != len(chapters):
            raise SystemExit(f"{vol}: {len(toc)} chapters in {tocf.name}, {len(chapters)} in the TEI")
        for c, tc in zip(chapters, toc):
            c["topics"] = [{k: tp[k] for k in ("topic", "page", "note") if k in tp} for tp in tc["topics"]]
    resp = [pb.get("resp") for pb in t.iter(f"{TEI}pb")]
    return {"vol": vol, "part": VOLUMES[vol]["part"], "year": VOLUMES[vol]["year"],
            "file": f"data/schuck-{vol}.xml", "chapters": chapters, "pages": len(used),
            "verified_pages": resp.count("#gt")}


def build_register(vols, out):
    """site/data/register.json for the reader: the entries of tei/register.xml with their
    identifiers and mention counts per volume (so lists work without loading every volume)."""
    src = ROOT / "tei" / "register.xml"
    if not src.exists():
        return None
    shutil.copy2(src, out / "data" / "register.xml")
    counts = {}
    for v in vols:
        t = etree.parse(str(ROOT / "tei" / f"schuck-{v}.xml"))
        for tag in ("persName", "placeName"):
            for e in t.iter(f"{TEI}{tag}"):
                ref = (e.get("ref") or "").split("#")[-1]
                if ref:
                    counts.setdefault(ref, {}).setdefault(v, 0)
                    counts[ref][v] += 1
    r = etree.parse(str(src))
    res = {"persons": [], "places": []}
    for kind, tag, name in (("persons", "person", "persName"), ("places", "place", "placeName")):
        for e in r.iter(f"{TEI}{tag}"):
            eid = e.get(XML_ID)
            item = {"id": eid, "name": "".join(e.find(f"{TEI}{name}").itertext()), "counts": counts.get(eid, {})}
            for k, child in (("birth", "birth"), ("death", "death")):
                c = e.find(f"{TEI}{child}")
                if c is not None:
                    item[k] = c.get("when")
            note = e.find(f"{TEI}note")
            if note is not None:
                item["desc"] = note.text
            ids = {i.get("type"): i.text for i in e.iter(f"{TEI}idno")}
            if ids:
                item["ids"] = ids
            if item["counts"]:
                res[kind].append(item)
    (out / "data" / "register.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", default="")
    ap.add_argument("--out", default=str(ROOT / "site"))
    a = ap.parse_args()
    out = ROOT / a.out if not a.out.startswith("/") else __import__("pathlib").Path(a.out)
    vols = a.vol.split(",") if a.vol else [v for v in VOLUMES if (ROOT / "tei" / f"schuck-{v}.xml").exists()]
    out.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "web" / "reader").iterdir():
        if f.is_file():
            shutil.copy2(f, out / f.name)
    (out / ".nojekyll").write_text("")
    meta = [build_volume(v, out) for v in vols]
    from to_epub import Book                       # one EPUB per volume, rebuilt when the TEI is newer
    for m in meta:
        ep = out / "epub" / f"schuck-{m['vol']}.epub"
        tei = ROOT / "tei" / f"schuck-{m['vol']}.xml"
        if not ep.exists() or ep.stat().st_mtime < tei.stat().st_mtime:
            b = Book(m["vol"])
            b.build()
            b.write(ep)
        m["epub"] = f"epub/{ep.name}"
        m["epub_mb"] = round(ep.stat().st_size / 1e6, 1)
    (out / "data" / "volumes.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    reg = build_register(vols, out)
    if reg:
        print(f"register: {len(reg['persons'])} persons, {len(reg['places'])} places")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    for m in meta:
        print(f"{m['vol']} ({m['part']}): {m['pages']} pages, {len(m['chapters'])} chapters, {m['verified_pages']} verified")
    print(f"-> {out}  ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
