"""NER phase 3 (rules) + the register as TEI: link every mention to a register entry.

  python scripts/link.py --vol 1932      -> data/ner/<vol>-links.json, tei/register.xml

Inputs: data/ner/<vol>.json (scripts/ner.py), data/register/persons.json, places.json,
ambiguous-<vol>.json (scripts/register.py), and data/ner/<vol>-decisions.json (manual
decisions from the review screens, when it exists: they override everything else).

Persons, in order:
  1. rejected forms (God, works, chancery words: register.REJECT) are not tagged at all
  2. a printed form that belongs to one entry -> that entry. cert "high"; "medium" when
     the bare surname was assigned to the entry by dominance (register: bare_probable)
  3. an ambiguous bare surname ("Bureus") -> best guess, cert "low":
       a. the nearest earlier full-name mention of one of the candidates in the same text unit,
       b. else in the same chapter (nearest earlier unit),
       c. else the candidate with most full-name mentions in the chapter,
       d. else no @ref (candidates kept in the links file for the review screen)
Places: printed form -> entry; institutions and skipped forms keep the tag without @ref.

A link is keyed by the text unit's hash + character offsets, so to_tei.py can place it in
the paragraph whose plain text has that hash. After OCR corrections, rerun ner.py (it
caches by hash), register.py --offline and this script.

register.xml (shared by all volumes) lists the persons and places that any volume links to,
with Wikidata, VIAF, SBL, Libris and manuscripta identifiers for entries whose Wikidata match
is accepted (status auto or checked).
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

import register as reg

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"


def load_json(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", required=True)
    a = ap.parse_args()
    ner = json.loads((D / "ner" / f"{a.vol}.json").read_text())
    persons = {e["id"]: e for e in load_json(D / "register" / "persons.json", [])}
    places = {e["id"]: e for e in load_json(D / "register" / "places.json", [])}
    ambiguous = load_json(D / "register" / f"ambiguous-{a.vol}.json", {})
    decisions = load_json(D / "ner" / f"{a.vol}-decisions.json", {})
    units = {u["id"]: u for u in ner["units"]}
    order = {u["id"]: k for k, u in enumerate(ner["units"])}

    # each volume links by its own forms: a bare surname unique in one volume may be ambiguous in another
    own = lambda e: e["vols"].get(a.vol) if "vols" in e else e
    per_form = {}
    for e in persons.values():
        if e.get("status") == "rejected" or not own(e):
            continue
        for f in own(e)["forms"]:
            per_form.setdefault(f, e["id"])
    plc_form = {}
    for e in places.values():
        if e.get("status") == "rejected" or not own(e):
            continue
        for f in own(e)["forms"]:
            plc_form.setdefault(f, e["id"])

    ms = [m for m in ner["mentions"] if m["label"] in ("PER", "LOC")]
    vocab = reg.build_vocab(Counter(re.sub(r"[’']s?$", "", m["text"]) for m in ms if m["label"] == "PER"))
    links, todo_amb = [], []
    for m in ms:
        key = f"{units[m['unit']]['sha']}:{m['start']}:{m['end']}"
        link = {"unit": m["unit"], "sha": units[m["unit"]]["sha"], "start": m["start"], "end": m["end"],
                "text": m["text"], "page": m["page"], "chapter": m["chapter"],
                "type": "persName" if m["label"] == "PER" else "placeName", "ref": None, "cert": None}
        if key in decisions:                                   # manual decision
            dcs = decisions[key]
            if dcs.get("reject"):
                continue
            link.update(ref=dcs.get("ref"), cert="high", how="manual")
            links.append(link)
            continue
        if m["label"] == "LOC":
            link.update(ref=plc_form.get(m["text"]), cert="high" if m["text"] in plc_form else None, how="form")
            links.append(link)
            continue
        nf = reg.normal_form(m["text"], vocab)
        if not nf or reg.REJECT.search(nf) or len(nf) < 3:
            continue
        eid = per_form.get(m["text"])
        if eid:
            e = own(persons[eid])
            bare = " " not in nf or nf.split()[0] in reg.PARTICLES
            link.update(ref=eid, cert="medium" if bare and e.get("bare_probable") else "high", how="form")
        elif nf in ambiguous:
            link.update(candidates=ambiguous[nf]["candidates"], how="ambiguous")
            todo_amb.append(link)
        links.append(link)

    # best guess for ambiguous bare surnames
    by_chapter = defaultdict(list)
    for l in links:
        if l["type"] == "persName" and l.get("ref") and l["cert"] == "high":
            by_chapter[l["chapter"]].append(l)
    for l in todo_amb:
        cands = set(l["candidates"])
        same_unit = [x for x in by_chapter[l["chapter"]] if x["unit"] == l["unit"] and x["start"] < l["start"]
                     and x["ref"] in cands]
        earlier = [x for x in by_chapter[l["chapter"]] if order[x["unit"]] < order[l["unit"]] and x["ref"] in cands]
        if same_unit:
            guess, how = max(same_unit, key=lambda x: x["start"])["ref"], "guess: same paragraph"
        elif earlier:
            guess, how = max(earlier, key=lambda x: (order[x["unit"]], x["start"]))["ref"], "guess: earlier in chapter"
        else:
            c = Counter(x["ref"] for x in by_chapter[l["chapter"]] if x["ref"] in cands)
            guess, how = (c.most_common(1)[0][0], "guess: chapter majority") if c else (None, "unresolved")
        l.update(ref=guess, cert="low" if guess else None, how=how)

    out = D / "ner" / f"{a.vol}-links.json"
    out.write_text(json.dumps(links, ensure_ascii=False, indent=0))
    write_register_tei(persons, places)
    c = Counter((l["type"], l.get("how", "").split(":")[0], l["cert"]) for l in links)
    for k, n in sorted(c.items(), key=lambda kv: (-kv[1])):
        print(f"  {n:5}  {k[0]:9} {k[1]:10} cert={k[2]}")
    print(f"-> {out} ({len(links)} links), tei/register.xml")


def write_register_tei(persons, places):
    """All volumes' links decide which entries are listed."""
    used = set()
    for f in (D / "ner").glob("*-links.json"):
        used |= {l["ref"] for l in json.loads(f.read_text()) if l.get("ref")}

    def ids(e):
        if e.get("status") not in ("auto", "checked") or not e.get("wikidata"):
            return ""
        c = next((c for c in e.get("candidates", []) if c["qid"] == e["wikidata"]), {})
        out = [f'<idno type="wikidata">https://www.wikidata.org/entity/{e["wikidata"]}</idno>']
        if c.get("viaf"):
            out.append(f'<idno type="VIAF">https://viaf.org/viaf/{c["viaf"]}</idno>')
        if c.get("sbl"):
            out.append(f'<idno type="SBL">https://sok.riksarkivet.se/sbl/Presentation.aspx?id={c["sbl"]}</idno>')
        if c.get("libris"):
            out.append(f'<idno type="Libris">https://libris.kb.se/auth/{c["libris"]}</idno>')
        if e.get("manuscripta"):
            out.append(f'<idno type="manuscripta">{e["manuscripta"]}</idno>')
        return "".join(out)

    def chosen(e):
        return next((c for c in e.get("candidates", []) if c["qid"] == e.get("wikidata")), {}) \
            if e.get("status") in ("auto", "checked") else {}

    P = []
    for eid in sorted(used & set(persons)):
        e, c = persons[eid], chosen(persons[eid])
        dates = (f'<birth when="{c["birth"]:04d}"/>' if c.get("birth") and c["birth"] > 0 else "") + \
                (f'<death when="{c["death"]:04d}"/>' if c.get("death") and c["death"] > 0 else "")
        note = f'<note>{escape(c["desc"])}</note>' if c.get("desc") else ""
        P.append(f'<person xml:id="{eid}"><persName>{escape(e["name"])}</persName>{dates}{note}{ids(e)}</person>')
    L = []
    for eid in sorted(used & set(places)):
        e, c = places[eid], chosen(places[eid])
        note = f'<note>{escape(c["desc"])}</note>' if c.get("desc") else ""
        L.append(f'<place xml:id="{eid}"><placeName>{escape(e["name"])}</placeName>{note}{ids(e)}</place>')
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<?xml-model href="http://www.tei-c.org/release/xml/tei/custom/schema/relaxng/tei_all.rng" type="application/xml" schematypens="http://relaxng.org/ns/structure/1.0"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:lang="sv">
<teiHeader><fileDesc><titleStmt><title>Schück digital: register över personer och orter</title></titleStmt>
<publicationStmt><authority>Schück digital (generated by scripts/link.py; identifications are partly automatic)</authority></publicationStmt>
<sourceDesc><p>Built from named-entity recognition (KB-BERT) on the edition's text, with Wikidata matching. Entries without identifiers are not yet identified.</p></sourceDesc></fileDesc></teiHeader>
<text><body><listPerson>{"".join(P)}</listPerson><listPlace>{"".join(L)}</listPlace></body></text>
</TEI>
"""
    (ROOT / "tei" / "register.xml").write_text(xml, encoding="utf-8")


if __name__ == "__main__":
    main()
