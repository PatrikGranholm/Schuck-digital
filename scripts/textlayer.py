"""Existing PDF text layer -> ALTO 4, one file per page (the OCR baseline).

  python scripts/textlayer.py --vol all

Reads `pdftotext -bbox-layout` (PDF points) and scales to image pixels (200 dpi).
pdftotext merges marginal sidenotes into body lines. Words wholly outside the
body column (median left/right edge of full lines) go to a "margin" TextBlock;
body lines with a large internal gap are split into separate TextLines.
Output: ocr/textlayer/<vol>/pNNNN.xml
"""
import argparse
import statistics
import subprocess
from xml.sax.saxutils import quoteattr

from lxml import etree

from common import OCR, img_path, parse_vols, pdf_path

XH = "{http://www.w3.org/1999/xhtml}"
SCALE = 200 / 72
GAP_FACTOR = 4  # a gap > GAP_FACTOR x median inter-word gap starts a new line segment


def words_of(line):
    return [(float(w.get("xMin")) * SCALE, float(w.get("yMin")) * SCALE,
             float(w.get("xMax")) * SCALE, float(w.get("yMax")) * SCALE, w.text or "")
            for w in line.iter(XH + "word")]


def split_line(ws, med_gap):
    segs, cur = [], [ws[0]]
    for a, b in zip(ws, ws[1:]):
        if b[0] - a[2] > max(GAP_FACTOR * med_gap, 15):
            segs.append(cur)
            cur = []
        cur.append(b)
    segs.append(cur)
    return segs


def page_alto(page, vol, idx):
    W = round(float(page.get("width")) * SCALE)
    H = round(float(page.get("height")) * SCALE)
    lines = [ws for ln in page.iter(XH + "line") if (ws := words_of(ln))]
    gaps = [b[0] - a[2] for ws in lines for a, b in zip(ws, ws[1:]) if b[0] > a[2]]
    med_gap = statistics.median(gaps) if gaps else 10
    # body column = median left/right edge of full-width lines (justified text);
    # words wholly outside it are marginal sidenotes (outer margin: right on rectos, left on versos)
    wide = [ws for ws in lines if ws[-1][2] - ws[0][0] > W * 0.4]
    body_left = statistics.median(ws[0][0] for ws in wide) if wide else 0
    body_right = statistics.median(ws[-1][2] for ws in wide) if wide else W
    body, margin = [], []
    for ws in lines:
        inner = [w for w in ws if body_left - 5 <= w[0] and w[2] <= body_right + 5]
        outer = [w for w in ws if w not in inner]
        # a line entirely outside the column (e.g. a short line) is body unless it starts beyond an edge
        if not inner:
            (margin if ws[0][0] > body_right - 5 or ws[-1][2] < body_left + 5 else body).append(ws)
            continue
        body.extend(split_line(inner, med_gap))
        if outer:
            left = [w for w in outer if w[2] <= body_left + 5]
            right = [w for w in outer if w[0] >= body_right - 5]
            margin.extend(x for x in (left, right) if x)
            rest = [w for w in outer if w not in left and w not in right]
            if rest:
                body.append(rest)

    def textline(s, lid):
        x0, y0 = min(w[0] for w in s), min(w[1] for w in s)
        x1, y1 = max(w[2] for w in s), max(w[3] for w in s)
        out = [f'<TextLine ID="{lid}" HPOS="{x0:.0f}" VPOS="{y0:.0f}" WIDTH="{x1 - x0:.0f}" HEIGHT="{y1 - y0:.0f}">']
        for i, w in enumerate(s):
            if i:
                out.append(f'<SP HPOS="{s[i - 1][2]:.0f}" VPOS="{y0:.0f}" WIDTH="{w[0] - s[i - 1][2]:.0f}"/>')
            out.append(f'<String ID="{lid}_w{i}" HPOS="{w[0]:.0f}" VPOS="{w[1]:.0f}" WIDTH="{w[2] - w[0]:.0f}" '
                       f'HEIGHT="{w[3] - w[1]:.0f}" CONTENT={quoteattr(w[4])}/>')
        out.append("</TextLine>")
        return "".join(out)

    def block(segs, bid, kind):
        if not segs:
            return ""
        x0, y0 = min(w[0] for s in segs for w in s), min(w[1] for s in segs for w in s)
        x1, y1 = max(w[2] for s in segs for w in s), max(w[3] for s in segs for w in s)
        inner = "\n".join(textline(s, f"{bid}_l{i}") for i, s in enumerate(segs))
        return (f'<TextBlock ID="{bid}" TAGREFS="{kind}" HPOS="{x0:.0f}" VPOS="{y0:.0f}" '
                f'WIDTH="{x1 - x0:.0f}" HEIGHT="{y1 - y0:.0f}">\n{inner}\n</TextBlock>')

    fn = img_path(vol, idx).name
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
<Description><MeasurementUnit>pixel</MeasurementUnit>
<sourceImageInformation><fileName>{fn}</fileName></sourceImageInformation>
<OCRProcessing ID="ocr"><ocrProcessingStep><processingSoftware><softwareName>PDF text layer (pdftotext -bbox-layout)</softwareName></processingSoftware></ocrProcessingStep></OCRProcessing>
</Description>
<Tags><LayoutTag ID="body" LABEL="body"/><LayoutTag ID="margin" LABEL="margin"/></Tags>
<Layout><Page ID="p{idx}" PHYSICAL_IMG_NR="{idx}" WIDTH="{W}" HEIGHT="{H}"><PrintSpace>
{block(body, "b", "body")}
{block(margin, "m", "margin")}
</PrintSpace></Page></Layout>
</alto>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", default="all")
    a = ap.parse_args()
    for vol in parse_vols(a.vol):
        xml = subprocess.run(["pdftotext", "-bbox-layout", str(pdf_path(vol)), "-"],
                             capture_output=True, check=True).stdout
        doc = etree.fromstring(xml, etree.XMLParser(huge_tree=True, recover=True))
        out = OCR / "textlayer" / vol
        out.mkdir(parents=True, exist_ok=True)
        pages = list(doc.iter(XH + "page"))
        for i, page in enumerate(pages, 1):
            (out / f"p{i:04d}.xml").write_text(page_alto(page, vol, i), encoding="utf-8")
        print(f"{vol}: {len(pages)} pages -> {out}")


if __name__ == "__main__":
    main()
