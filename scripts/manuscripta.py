"""Index of the manuscripta.se records of Kungl. biblioteket and Uppsala universitetsbibliotek, for
linking Schück's shelfmarks (K. B. sign. F h. 28, U. B. sign. R 705) to the digitised manuscripts.

  python scripts/manuscripta.py [--src ../manuscripta/data]   -> data/sources/manuscripta.tsv

Source: the production dump in the manuscripta repository (data/msDescs/**/*.xml, one TEI msDesc
per manuscript; data/iiif/<id>/manifest.json where it is digitised). The index is kept in this
repository so that to_tei.py does not depend on the other checkout.

Also the academy's own minute books and royal letters, now in Riksantikvarieämbetet's archive (ATA,
Ämbetsarkiv 2, A 1 a/1-10 "Kungliga Vitterhetsakademiens protokoll" 1773-1838 and E 1/1-2 "Kungliga brev"
1786-1835; digitised by the RiViH project), as holder RAA with key "A 1 a/3" and the years from the
record's title in the date column ("1800-1805"). to_tei.py links Schück's "Prot. <date>" and "Kungl. brev
<date>" in vols VI-VIII to the volume whose years cover the cited one.

Columns: holder (KB, UUB, RAA), key (shelf_key below), shelfmark as manuscripta writes it, id, record URL,
IIIF manifest URL (empty when not digitised), images, date.

shelf_key normalises both spellings to one form: Schück writes "F h. 28", "F. a 13", "D G 8",
manuscripta "Fh 28", "Fa 13", "DG 8" -> "FH 28", "FA 13", "DG 8".
"""
import argparse
import csv
import json
import re
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
NS = {"t": "http://www.tei-c.org/ns/1.0"}
RAA_SERIES = re.compile(r"Ämbetsarkiv 2, ((?:A 1 a|E 1)/\d+)$")   # the academy's minutes and royal letters
HOLDERS = {"Riksantikvarieämbetet": "RAA", "Kungliga biblioteket": "KB", "National Library of Sweden": "KB",
           "Uppsala universitet": "UUB", "Uppsala University": "UUB"}


def shelf_key(s):
    """'sign. F h. 28' / 'Fh 28' -> 'FH 28'; None when it is not series letters + number."""
    s = re.sub(r"^(?:sign|vol)\.?\s*", "", s.strip(), flags=re.I)
    s = re.sub(r"\s+", " ", re.sub(r"[.,]", " ", s)).strip().upper()
    m = re.match(r"([A-ZÅÄÖ]{1,2})(?: ([A-Z]))? ?(\d+)(?: ?([A-Z]))?(?=\s|$)", s)
    if not m:
        return None
    return f"{m.group(1)}{m.group(2) or ''} {m.group(3)}{m.group(4) or ''}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT.parent / "manuscripta" / "data"))
    a = ap.parse_args()
    src = Path(a.src)
    rows = {}
    for f in sorted(src.glob("msDescs/**/*.xml")):
        try:
            t = etree.parse(str(f))
        except etree.XMLSyntaxError:
            continue
        mi = t.find(".//t:msDesc/t:msIdentifier", NS)
        if mi is None:
            continue
        holder = HOLDERS.get((mi.findtext("t:institution", namespaces=NS) or "").strip())
        sm = (mi.findtext("t:idno[@type='shelfmark']", namespaces=NS) or "").strip()
        if holder == "RAA":
            m = RAA_SERIES.match(sm)
            key = m.group(1) if m else None
        else:
            key = shelf_key(sm) if holder else None
        if not key:
            continue
        mid = f.stem
        url = t.findtext(".//t:idno[@type='URI'][@subtype='Manuscripta']", namespaces=NS) \
            or f"https://www.manuscripta.se/ms/{mid}"
        manifest, images = "", 0
        mf = src / "iiif" / mid / "manifest.json"
        if mf.exists():
            m = json.loads(mf.read_text())
            manifest = m.get("@id") or m.get("id") or ""
            images = sum(len(s.get("canvases", [])) for s in m.get("sequences", [])) or len(m.get("items", []))
        d = t.find(".//t:msDesc//t:origDate", NS)
        date = re.sub(r"\s+", " ", " ".join(d.itertext())).strip() if d is not None else ""
        if holder == "RAA":             # "Kungliga Vitterhetsakademiens protokoll 1800-1805" -> "1800-1805"
            head = " ".join(t.find(".//t:msDesc/t:head", NS).itertext())
            y = re.findall(r"1[6-9]\d\d", head)
            date = f"{y[0]}-{y[-1]}" if y else date
        if (holder, key) in rows:       # several units under one shelfmark: keep the digitised one
            if rows[(holder, key)][5] or not manifest:
                continue
        rows[(holder, key)] = [holder, key, sm, mid, url.strip(), manifest, images, date]
    out = ROOT / "data" / "sources" / "manuscripta.tsv"
    with out.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["holder", "key", "shelfmark", "id", "url", "manifest", "images", "date"])
        w.writerows(sorted(rows.values()))
    print(f"{len(rows)} shelfmarks ({sum(1 for r in rows.values() if r[5])} digitised) -> {out}")


if __name__ == "__main__":
    main()
