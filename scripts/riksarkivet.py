"""Riksarkivet volumes cited by Schück in the Kanslikollegium archive, with their catalogue record and,
where digitised, IIIF manifest.

  python scripts/riksarkivet.py      -> data/sources/riksarkivet.tsv

Schück cites Kanslikollegium's incoming letters as "R. A. Kanslikoll. Inkomna skrivelser sign. E IV. 9"
(about 190 citations in vols II-V, about 20 volumes). In Riksarkivet's catalogue these are the volumes
of the fonds Kanslikollegium (SE/RA/1411), series E IV, E IX, E XII, E XIII, with reference codes
such as SE/RA/1411/E/E IV/9. Series E IV 8-10 b are the letters from the heads of the Antikvitetskollegium
and Antikvitetsarkivet, E IV 13-14 those from its officials.

Input: data/sources/citations.tsv (scripts/sources.py). A citation counts when its text names
Kanslikollegium ("Kanslikoll.", "kanslikollegiet") and its shelfmark is a series + volume
(ra_key: "sign. E IV. 10 b" -> "E IV 10 b", "E IX. vol. 24" -> "E IX 24", a bare "IV. 13" -> "E IV 13").
Each volume is looked up once in Riksarkivet's search API (data.riksarkivet.se/api/records, see
lookup(); responses cached in cache/riksarkivet/, about 1 request per second, generic
User-Agent). Volumes that are not in today's catalogue (vol. V "E XIII. 21": the series has 16
volumes) are listed with an empty id.

Columns: archive, key, referenceCode, id, url (sok.riksarkivet.se record page), manifest (IIIF,
empty when not digitised), date, note, citations.
"""
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from collections import Counter

from common import ROOT

API = "https://data.riksarkivet.se/api/records"
UA = "schuck-digital-sources/0.1 (research script; Python urllib)"
CACHE = ROOT / "cache" / "riksarkivet"
FONDS = {"kanslikollegium": "SE/RA/1411"}
KANSLI = re.compile(r"[Kk]ansli-?koll|[Kk]anslikollegi")


def ra_key(shelf):
    """'sign. E IV. 10 b' -> 'E IV 10 b'; 'E IX. vol. 24' -> 'E IX 24'; 'IV. 13' -> 'E IV 13'; else None."""
    s = re.sub(r"^sign\.?\s*", "", shelf.strip())
    m = re.match(r"(?:E\.?\s*)?([IVX]+)[.:]?\s*(?:vol\.\s*)?(\d+)(?:\s?([a-cA-C])\b)?", s)   # "7 A" (vol. IV): OCR case
    if not m:
        return None
    return f"E {m.group(1)} {m.group(2)}" + (f" {m.group(3).lower()}" if m.group(3) else "")


def lookup(ref, query):
    """The API record whose referenceCode is ref, or None (cached by query). The search tokenises a
    reference code ("SE/RA/1411/E/E IV/9" finds ~970,000 records, not that one first), so the query is
    the fonds name + series + volume ("Kanslikollegium E IV 9"), which finds it as the only hit."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (re.sub(r"[^\w]+", "_", query) + ".json")
    if f.exists():
        data = json.loads(f.read_text())
    else:
        url = f"{API}?{urllib.parse.urlencode({'text': query, 'max': 100})}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
            data = json.loads(r.read())
        f.write_text(json.dumps(data, ensure_ascii=False))
        time.sleep(1)
    return next((x for x in data.get("items", []) if x.get("metadata", {}).get("referenceCode") == ref), None)


def main():
    cites = Counter()
    with (ROOT / "data" / "sources" / "citations.tsv").open() as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if KANSLI.search(r["citation"]) and r["shelfmark"]:
                for sm in r["shelfmark"].split("; "):
                    if k := ra_key(sm):
                        cites[k] += 1
    rows = []
    for key, n in sorted(cites.items()):
        series, vol = re.match(r"(E [IVX]+) (.+)", key).groups()
        ref = f"{FONDS['kanslikollegium']}/E/{series}/{vol}"
        x = lookup(ref, f"Kanslikollegium {series} {vol}")
        links = (x or {}).get("_links", {})
        md = (x or {}).get("metadata", {})
        rows.append(["kanslikollegium", key, ref, (x or {}).get("id", ""), links.get("html", ""),
                     (links.get("image") or [""])[0], md.get("date", ""), re.sub(r"\s+", " ", md.get("note", ""))[:200], n])
    out = ROOT / "data" / "sources" / "riksarkivet.tsv"
    with out.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["archive", "key", "referenceCode", "id", "url", "manifest", "date", "note", "citations"])
        w.writerows(rows)
    found = [r for r in rows if r[3]]
    print(f"{len(rows)} volumes ({sum(r[8] for r in rows)} citations): {len(found)} in the catalogue, "
          f"{sum(1 for r in found if r[5])} digitised -> {out}")
    for r in rows:
        if not r[3]:
            print(f"  not found: {r[2]} ({r[8]} citations)")


if __name__ == "__main__":
    main()
