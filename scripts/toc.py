"""Parse a volume's table of contents and check the TEI's marginal summaries against it.

  python scripts/toc.py --vol 1932     -> data/structure/<vol>-toc.json, report/sidenotes-<vol>.md

Each topic matched to a sidenote gets "note": its index among the TEI's margin notes in document order.

The TOC lists each chapter followed by its marginal topics with page numbers
("Fornminnenas förstöring 1, Den börjande göticismen 5, ..."). These are the sidenotes,
so the TOC is an independent check of the TEI's <note place="margin" type="summary">:
  found      a sidenote in the TEI matches the topic and sits on the TOC page (±1)
  misplaced  matched, but on another page
  in body    not a sidenote, but the topic text occurs in body text on that page
             (a sidenote OCR merged into the paragraph)
  missing    nowhere on the page
Text comes from dots.mocr's reading of the TOC images listed in the structure file, or from the PDF
text layer for a page dots.mocr could not read (a reply that looped on dot leaders) or with
--source textlayer. dots.mocr gives clean lines; the text layer runs chapter titles into topics and
garbles page numbers (vol. VIII: "Expenser4i5-", "Tillägg till staten 4x8").
"""
import argparse
import json
import re

from lxml import etree
from rapidfuzz import fuzz

from common import REPORT, ROOT
from common import OCR
from regions import dehyphenate, dots_blocks, from_dots, from_textlayer

NS = {"t": "http://www.tei-c.org/ns/1.0"}
DASH = str.maketrans("–—", "--")          # dots.mocr writes "1797-1800" where the sidenote has "1797—1800"
FOLD = str.maketrans({"i": "1", "I": "1", "l": "1", "o": "0", "O": "0", "°": "0", ";": "5", ">": "1", "?": "9",
                      "’": "9", "Ö": "6", "s": "5", "S": "5"})


def toc_page(vol, i, source="auto"):
    """(source used, page regions) for one TOC image."""
    if source != "textlayer":
        f = OCR / "dots-layout" / vol / f"p{i:04d}.json"
        if f.exists() and json.loads(f.read_text()).get("finish") == "stop" and dots_blocks(vol, i):
            return "dots", from_dots(vol, i)
        if source == "dots":
            raise SystemExit(f"no dots.mocr reading of image {i}")
    return "textlayer", from_textlayer(vol, i)


def parse_toc(vol, images, chapters, parts=(), source="auto"):
    """Topics ("name page, name page, ...") from the TOC text layer, assigned to chapters by page range.
    Lines that are only (part of) a chapter title, dot leaders or a page number are dropped first:
    the text layer splits hanging chapter titles over lines and out of order ('medeltidsskrifter' /
    'Svenska') and files the chapter page numbers at the foot of the page."""
    titles = [re.sub(r"\W", "", c["title"]).lower() for c in chapters]
    # every word of a chapter or part title; a line of such words with a year or "Innehåll" is a title
    # fragment (vol. V: "INNEHALL. BERCHS TID. Antikvitetsarkivet", "1747—1751")
    vocab = {w for c in list(chapters) + list(parts) for w in re.findall(r"\w+", c["title"].lower())} | {"innehåll", "innehall"}
    lines, used = [], []
    for i in images:
        src, page = toc_page(vol, i, source)
        used.append(src)
        for l in page["body"].split("\n"):
            if re.match(r"\s*R[ÄA]TTELSER", l):      # the errata, printed after the TOC (vol. II image 497)
                break
            if src == "dots":
                # dots.mocr gives a chapter or part title as a line of its own, ending in its page or page
                # range ("Amanuens och sekreterare 3-22", "De första månaderna 57"), sometimes split over
                # two lines ("Levernesbeskrivningar," / "inträdestal och avhandlingar 216"): drop it
                bare = re.sub(r"\s+\d{1,3}(?:\s*[-–—]\s*\d{1,3})?\.?\s*$", "", l.strip())
                if set(re.findall(r"\w+", bare.lower())) <= vocab:
                    continue
                # a title and its page still at the start of a line of topics: "Statuter och Stat 1 Inledning 1"
                # (only with the page: "Fornvården före Liljegren 270" is a topic that starts with a title)
                lw = re.findall(r"\w+", l.lower())
                for t in sorted((c["title"] for c in list(chapters) + list(parts)), key=len, reverse=True):
                    tw = re.findall(r"\w+", t.lower())
                    if lw[:len(tw)] == tw and len(lw) > len(tw) and lw[len(tw)].isdigit() and len(lw[len(tw)]) <= 3:
                        l = re.sub(rf"^(?:\W*\w+){{{len(tw)}}}[\s.]*\d{{1,3}}(?:\s*[-–—]\s*\d{{1,3}})?\.?\s+", "", l.strip())
                        break
            l = re.sub(r"^\s*(?:\.\s*){1,2}[\diIlo°O]{0,4}(?=\s|$)", "", l.strip())   # leader remnants: ". .", ".235 Tjänste…"
            core = re.sub(r"[ .…]{4,}\s*[\diIlo°O ]*$", "", l.strip())
            key = re.sub(r"\W", "", core).lower()
            if len(key) <= 4 or key == "innehåll" or any(key in t for t in titles) \
                    or (set(re.findall(r"\w+", core.lower())) <= vocab and re.search(r"\d|innehå?a?ll", core.lower())):
                continue
            lines.append(l)
    text = dehyphenate(lines)
    # a chapter line still on a line with topics = title + dot leaders + page: cut it out
    # (its first word may be missing: chapter lines hang left of the column and the text layer
    # files those words as margin, so a title may start lowercase after the previous full stop)
    text = re.sub(r"(?:^|(?<=\. ))([^.,»]{3,80}?)\s*[ .…,]{6,}\s*[\diIlo°]{0,4}", ", ", text)
    text = re.sub(r"(?<!\d)(1[5-8]) ?(\d) (\d)(?!\d)", r"\1\2\3", text)   # a year split by the text layer: "17 7 7"
    # a year run together with its page number (vol. VII: "1797—180346.", "1810277. 1811280."):
    # pages have at most three digits, so a longer number starting with a year is year + page
    text = re.sub(r"(?<!\d)(1[5-8]\d\d)(\d{1,3})(?=\s*[,.;]|\s*$)", r"\1 \2", text)
    starts = [c["page"] for c in chapters]
    out = [{"chapter": c["title"], "topics": []} for c in chapters]
    pwords = {w for p in parts for w in re.findall(r"\w+", p["title"].lower())}   # "AVVECKLINGEN." before a chapter
    words = [set(re.findall(r"\w+", c["title"].lower())) | pwords for c in chapters]
    found = []
    head = None              # a series heading with a year and no page: "Fynd 1786. Styrafyndet 276" (vol. VI)
    month = r"(?:Jan|Febr|Mars|Apr|Maj|Juni|Juli|Aug|Sept|Okt|Nov|Dec)\w*\.?\s+1\d{3}"
    # a lone letter other than I/l is not a page number ("Miracuia S. Erici 245": S is not page 5)
    for t in re.finditer(r"([^,]+?)\s+((?![oOsS°;>?’Ö]\s*(?:[,.>?’»;]|$))[\diIloOsS°;>?’Ö]{1,3}(?: ?\d)?)"
                         r"\s*(?=[,.>?’»;]|$)", text):
        name, raw = t.group(1).strip(" .,»>’"), t.group(2).replace(" ", "")
        name = re.sub(r"s’(?=[a-zåäö])", "s’ ", name)      # text layer: "Bureus’ställning" (vol. I)
        name = name.lstrip("?; ")                          # a comma read as "?": "lexikograf 79? Bureus’ …"
        num = raw.translate(FOLD)
        if not (num.isdigit() and name):
            continue
        if len(num) == 4 and 1500 <= int(num) < 1900:      # a year, not a page
            if text[t.end():t.end() + 1] == ".":           # the heading of what follows: "Fynd 1786. Styrafyndet 276"
                head = f"{name} {num}"
            elif found:                                    # a topic whose page OCR lost: "Riksdagen 1668, Rannsakningarna …"
                found.append((f"{name} {num}", found[-1][1], None, True))
            continue
        m = re.search(r"\s(\d{1,2})$", name)               # "Birkenius 1 51": a page number split by OCR
        if m and int(m.group(1) + num) > (found[-1][1] if found else 0):
            name, num = name[:m.start()], m.group(1) + num
        # a missing comma ("… 120 Passet 1599 …"), but not a date ("Kungörelsen den 10 Maj 1787 324")
        parts = re.split(rf"(?<=\s\d{{3}})\s|(?<=\s\d{{2}})\s(?=[A-ZÅÄÖ])(?!{month})", name)
        for i, part in enumerate(parts):
            m = re.match(r"(.+)\s(\d{2,3})$", part)
            if i == len(parts) - 1:
                # the page is num; a number before it is a misread year ("Norrlandsresan 227 229" for 1827, vol. VIII)
                found.append((m.group(1) if m else part, int(num), head, False))
            else:
                found.append((m.group(1), int(m.group(2)), head, False) if m else (part, int(num), head, False))
            head = None
    for name, n, head, nopage in found:
        k = max((j for j, s0 in enumerate(starts) if s0 <= n), default=0)
        # a chapter-title fragment before the first topic ("Urkundsutgivning Recesserna och landslagen"):
        # strip title words only when a capitalised topic follows ("Bureus' ungdom" keeps its name)
        w = name.split()
        j = 0
        while j < len(w) - 1 and set(re.findall(r"\w+", w[j].lower())) <= words[k]:
            j += 1
        tp = {"topic": name.strip(" ,"), "page": n}
        if j and w[j][:1].isupper():          # keep the full form too: "Bureus och Kabbala" is a real topic
            tp = {"topic": " ".join(w[j:]).strip(" ,"), "page": n, "full": tp["topic"]}
        if head:                              # matched also as "Fynd 1786 Styrafyndet"
            tp["full"] = f"{head} {tp['topic']}"
        if nopage:                            # page of the topic before; main() takes its sidenote's page
            tp["nopage"] = True
        out[k]["topics"].append(tp)
    return out, used


def tei_notes(vol):
    t = etree.parse(str(ROOT / "tei" / f"schuck-{vol}.xml"))
    notes, body = [], {}
    page = None
    for el in t.iter():
        if el.tag == f"{{{NS['t']}}}pb" and el.get("n"):
            page = int(el.get("n"))
        elif el.tag == f"{{{NS['t']}}}note" and el.get("place") == "margin":
            # a trailing reference to the statutes ("Jettoner § 17", "Ledamöter §§ 25—28", vol. VI) is not in the TOC
            text = re.sub(r"[.,]?\s*§§?\s*[\d\s–—-]+\.?$", "", "".join(el.itertext()).strip())
            notes.append({"text": text, "page": page})
        elif el.tag == f"{{{NS['t']}}}p" and page:
            # body text per page (approximate: whole paragraph credited to its pages)
            body.setdefault(page, []).append("".join(x for x in el.itertext()))
    return notes, {p: " ".join(v) for p, v in body.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", required=True)
    ap.add_argument("--source", choices=("auto", "dots", "textlayer"), default="auto",
                    help="auto: dots.mocr where it read the page, else the text layer")
    a = ap.parse_args()
    st = json.loads((ROOT / "data" / "structure" / f"{a.vol}.json").read_text())
    toc, sources = parse_toc(a.vol, st["back"]["contents"], st["chapters"], st.get("parts", []), a.source)
    notes, body = tei_notes(a.vol)
    res = {"found": [], "misplaced": [], "in body": [], "missing": []}
    used = set()
    near = lambda tp, k: notes[k]["page"] is not None and abs(notes[k]["page"] - tp["page"]) <= 1
    topics = [tp for ch in toc for tp in ch["topics"]]
    match = {}

    def norm(x):
        x = re.sub(r"\s+", " ", x.lower().strip(" .,:»").translate(DASH))
        return re.sub(r"([a-zåäöæ])- ?([a-zåäöæ])", r"\1\2", x)      # "Örkeljunga-fyndet", "förgusta-vianska"

    def score(topic, note, strict):
        """Fuzzy ratio. On the TOC page (strict) also:
        - a TOC series elides its common part, at the start ("Consignationen 1750 321, 1751 324",
          "Tävlingarna 687, I skaldekonst 690") or at the end ("Inträdestalen. Fredenheims 65, Tengströms 67"
          for the sidenote "Fredenheims inträdestal"), so a sidenote that ends or starts with the topic counts;
        - the TOC puts a heading in front of a sidenote ("Tävlingarna 1820 och 1821" for "1820 och 1821");
        - OCR cut a sidenote short ("Setons arbete för" for "Setons arbete för Diplomatariet")."""
        t, n = norm(topic), norm(note)
        r = fuzz.ratio(t, n)
        # "1807 och 1809" in the TOC for the sidenote "1807—1809" (vol. VII p. 163)
        span = lambda x: re.sub(r"(\d{4}) (?:och|-) (\d{4})|(\d{4})-(\d{4})", lambda m: "-".join(g for g in m.groups() if g), x)
        if strict and re.search(r"\d{4}", t) and span(t) == span(n):
            r = max(r, 90)
        # a pronoun for the sidenote's head noun: "Deras uppläsning i akademien" for "Levernesbeskrivningarnas
        # uppläsning i akademien" (vol. VII p. 411)
        m = re.match(r"(?:deras|dess|hans|hennes) (.{10,})$", t)
        if strict and m and n.endswith(" " + m.group(1)):
            r = max(r, 85)
        if strict and len(t) >= 4 and (n.endswith(" " + t) or n.startswith(t + " ")):
            r = max(r, 85)
        if strict and ((len(n) >= 4 and (t.endswith(" " + n) or t.startswith(n + " "))) or (len(n) >= 8 and t.startswith(n))):
            r = max(r, 82)
        return r
    def pairs(minr, strict):
        """One TOC topic printed as two sidenotes on the same page ("Eldsvådan" + "1802", "1804" + "1805"
        for "1804 och 1805", "Örkeljunga-fyndet" + "Hållsjö-fyndet"); the topic links to the first."""
        for ti, tp in enumerate(topics):
            if ti in match:
                continue
            # only where no single sidenote will do ("Fynd" + "De Ribbing-ska guldring-arna" is not a pair)
            if any(score(tp["topic"], n["text"], False) >= 80 for k, n in enumerate(notes) if near(tp, k)):
                continue
            best = None
            for k in range(len(notes) - 1):
                for j in (k + 1, k + 2):
                    if j >= len(notes) or k in used or j in used or not near(tp, k) or notes[k]["page"] != notes[j]["page"]:
                        continue
                    n1, n2 = notes[k]["text"], notes[j]["text"]
                    r = max(score(tp["topic"], f"{n1}{sep}{n2}", strict) for sep in (" ", " och ")) if j == k + 1 else 0
                    # the same verb on both, plural in the TOC: "Blad vald" + "Palin vald" for "Blad och Palin
                    # väljas" (vol. VII p. 225, with "Ristell uteslutes" between them)
                    # a span of years the TOC runs together: "1797-1803" for "1797–1799" + "1800–1803" (vol. VII p. 46)
                    ys = re.fullmatch(r"(\d{4}) ?- ?(\d{4})", norm(tp["topic"]))
                    if strict and ys and j == k + 1 and norm(n1).startswith(ys.group(1)) and norm(n2).endswith(ys.group(2)) \
                            and re.fullmatch(r"[\d -]+", norm(n1) + norm(n2)):
                        r = max(r, 90)
                    w1, w2, wt = norm(n1).split(), norm(n2).split(), norm(tp["topic"]).split()
                    if strict and len(w1) > 1 and len(w2) > 1 and w1[-1] == w2[-1] and len(wt) >= 3:
                        r = max(r, fuzz.ratio(" ".join(w1[:-1] + ["och"] + w2[:-1]), " ".join(wt[:-1])))
                    if r >= minr and (best is None or r > best[0]):
                        best = (r, k, j)
            if best:
                match[ti] = best[1]
                used.update(best[1:])
    # pass 0: near-exact pairs, before a single note can claim half of one ("Hållsjö-fyndet")
    pairs(90, False)
    # pass 1: a sidenote on the TOC page (±1); pass 2: best ratio anywhere, nearer page breaking ties
    # (topics recur: Verelius 131 and 262; similar ones must not cascade: Resor 1669, Resor 1670, ...)
    for strict in (True, False):
        for ti, tp in enumerate(topics):
            if ti in match:
                continue
            cands = [(max(score(f, n["text"], strict) for f in (tp["topic"], tp.get("full", tp["topic"]))), k)
                     for k, n in enumerate(notes)
                     if k not in used and (near(tp, k) or not strict)]
            cands = [c for c in cands if c[0] >= 80]
            if cands:
                r, k = max(cands, key=lambda c: (c[0], -abs((notes[c[1]]["page"] or 10**6) - tp["page"])))
                match[ti] = k
                used.add(k)
    # pass 3: looser pairs ("Grandel vald" + "Brinkman vald" for "Grandel och Brinkman valda")
    pairs(85, True)
    # pass 4: two TOC topics printed as one sidenote ("1806 och 1807 Ydrefyndet", "1820 Lackalängafyndet"):
    # a topic next to a matched one shares its sidenote when the sidenote holds it word for word
    def holds(note, topic):
        """The sidenote holds the topic: word for word, or one of its sentences ("Fornvården. Ekdahl och
        grävningarna på Hven") starts like it ("De arbetandes. Sundels" for "De arbetande ledamöternas"),
        also without the TOC's series word ("I antikviteter" in "Antikviteter 1794")."""
        n, t = norm(note), re.sub(r"^i ", "", norm(topic))
        if re.search(rf"(?:^|[\s.]){re.escape(t)}(?:[\s.]|$)", n):
            return True
        return any(len(p) >= 8 and fuzz.ratio(p, t[:len(p)]) >= 88 for p in re.split(r"\.\s+", n))
    for ti, tp in enumerate(topics):
        for tj in (ti - 1, ti + 1):
            if ti not in match and tj in match and near(tp, match[tj]) and holds(notes[match[tj]]["text"], tp["topic"]):
                match[ti] = match[tj]
    for ti, tp in enumerate(topics):
        if ti in match:
            n = notes[match[ti]]
            res["found" if near(tp, match[ti]) else "misplaced"].append((tp, n))
        elif any(fuzz.partial_ratio(tp["topic"].lower(), body.get(p, "").lower()) >= 90
                 for p in (tp["page"], tp["page"] + 1)):
            res["in body"].append((tp, None))
        else:
            res["missing"].append((tp, None))
    for ti, k in match.items():          # the reader links a topic to its sidenote (build_site.py: xml:id sn<k>)
        topics[ti]["note"] = k
        if topics[ti].get("nopage") and notes[k]["page"]:
            topics[ti]["page"] = notes[k]["page"]
    (ROOT / "data" / "structure" / f"{a.vol}-toc.json").write_text(json.dumps(toc, ensure_ascii=False, indent=1))
    extra = [n for k, n in enumerate(notes) if k not in used]
    n_topics = sum(len(c["topics"]) for c in toc)
    md = [f"# Sidenotes vs table of contents, vol. {a.vol}", "",
          f"TOC topics: {n_topics} in {len(toc)} chapters; TEI sidenotes: {len(notes)}. "
          f"TOC text: {', '.join(f'image {i} {u}' for i, u in zip(st['back']['contents'], sources))}.", "",
          "| result | count |", "|---|---|"]
    md += [f"| {k} | {len(v)} |" for k, v in res.items()] + [f"| TEI sidenote not in TOC | {len(extra)} |", ""]
    for k in ("misplaced", "in body", "missing"):
        md += [f"## {k}", ""] + [f"- p. {tp['page']}: {tp['topic']}" + (f" → TEI p. {n['page']}: {n['text']}" if n else "")
                                 for tp, n in res[k]] + [""]
    md += ["## TEI sidenotes not in the TOC", ""] + [f"- p. {n['page']}: {n['text']}" for n in extra]
    out = REPORT / f"sidenotes-{a.vol}.md"
    out.write_text("\n".join(md) + "\n")
    print("\n".join(md[:12]))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
