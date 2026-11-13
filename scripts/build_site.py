"""Build the static reader site for GitHub Pages.

  python scripts/build_site.py [--vol 1932,1933a] [--out site]
  python3 -m http.server 8000 -d site          # preview at http://localhost:8000/

For every volume with a tei/schuck-<vol>.xml:
  site/data/schuck-<vol>.xml   the TEI, with <graphic> pointing at the web images
  site/img/<vol>/pNNNN.webp    the page images referenced by a <pb> (binding/scan images are
                               left out), 1200 px wide, WebP q70 (~150 KB; the whole edition
                               must stay under GitHub Pages' 1 GB site limit)
plus site/data/volumes.json and the app files from web/reader/. Images are only rebuilt
when missing. The site is published from its own branch (gh-pages), so the images never
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
    resp = [pb.get("resp") for pb in t.iter(f"{TEI}pb")]
    return {"vol": vol, "part": VOLUMES[vol]["part"], "year": VOLUMES[vol]["year"],
            "file": f"data/schuck-{vol}.xml", "chapters": chapters, "pages": len(used),
            "verified_pages": resp.count("#gt")}


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
    (out / "data" / "volumes.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    for m in meta:
        print(f"{m['vol']} ({m['part']}): {m['pages']} pages, {len(m['chapters'])} chapters, {m['verified_pages']} verified")
    print(f"-> {out}  ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
