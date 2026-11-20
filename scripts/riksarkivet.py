"""Riksarkivet volumes cited by Schück in the Kanslikollegium archive and the De la Gardie collection, with their catalogue record and,
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

De la Gardie collection (De la Gardieska samlingen, SE/RA/720222; not digitised): Schück's "R. A.
Skrivelser till De la Gardie. Ser. C 1" is series C I, letters to M. G. De la Gardie in volumes
ordered by writer, and "Antikvitetskollegiets skrivelser till De la Gardie" is the Antikvitetskollegium
part of series C IX (vol. E 1626, letters 1668-1677). dlg_key() names the volume where the citation
says which it is: the Antikvitetskollegium, or a writer in DLG_WRITERS (the volume holding that
writer, from the fonds' EAD). About 40 "Ser. C 1" citations name no writer and are not linked.
These citations carry no "sign." shelfmark, so to_tei.py puts the link on the <bibl>.

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
FONDS = {"kanslikollegium": "SE/RA/1411", "delagardie": "SE/RA/720222"}
KANSLI = re.compile(r"[Kk]ansli-?koll|[Kk]anslikollegi")
# De la Gardie collection: key -> (reference code below the fonds, search query that finds it)
DLG_VOLUMES = {"antikvitetskollegium": ("01/C/IX/E 1626", "Antiqvitetskollegium 1668-1670"),
               "Hadorph": ("01/C/I/E 1425", "Haak - Hadorph"),
               "Axehiälm": ("01/C/I/E 1338", "Aspegren - Axehielm")}
DLG_AK = re.compile(r"Antikvitetskoll(?:egi(?:et|ets))?\.?\s+(?:skrivelser\s+)?till\s+(?:M\.\s?G\.\s+)?De la Gardie")
DLG_WRITERS = re.compile(r"(?:[Ss]krivelser till (?:M\.\s?[GJ]\.\s+)?De la Gardie|De la Gardies brevsamling)\b.*?"
                         r"\b(Hadorph|Axehi[äe]lm)")


def dlg_key(citation):
    """The De la Gardie collection volume a citation names: 'antikvitetskollegium', a writer, or None."""
    if DLG_AK.search(citation):
        return "antikvitetskollegium"
    m = DLG_WRITERS.search(citation)
    return m.group(1).replace("Axehielm", "Axehiälm") if m else None


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
    dlg = Counter(k for r in csv.DictReader((ROOT / "data" / "sources" / "citations.tsv").open(), delimiter="\t")
                  if (k := dlg_key(r["citation"])))
    rows = []

    def row(archive, key, ref, x, n):
        links = (x or {}).get("_links", {})
        md = (x or {}).get("metadata", {})
        rows.append([archive, key, ref, (x or {}).get("id", ""), links.get("html", ""),
                     (links.get("image") or [""])[0], md.get("date", ""), re.sub(r"\s+", " ", md.get("note", ""))[:200], n])
    for key, n in sorted(cites.items()):
        series, vol = re.match(r"(E [IVX]+) (.+)", key).groups()
        ref = f"{FONDS['kanslikollegium']}/E/{series}/{vol}"
        row("kanslikollegium", key, ref, lookup(ref, f"Kanslikollegium {series} {vol}"), n)
    for key, n in sorted(dlg.items()):
        sub, query = DLG_VOLUMES[key]
        ref = f"{FONDS['delagardie']}/{sub}"
        row("delagardie", key, ref, lookup(ref, query), n)
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
