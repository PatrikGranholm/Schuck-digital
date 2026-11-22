# Schück digital

A digital edition of Henrik Schück, *Kgl. Vitterhets historie och antikvitets akademien: dess förhistoria och historia*, I–VIII (Stockholm 1932–1944; [Libris](https://libris.kb.se/cvn7fkgp3mft1s5)). The work is in the public domain. See [PLAN.md](PLAN.md) for the design and the progress log, [TODO.md](TODO.md) for open tasks, and [gt/CONVENTIONS.md](gt/CONVENTIONS.md) for the transcription rules.

**Read it online:** https://patrikgranholm.github.io/Schuck-digital/

License: code (`scripts/`, `web/`) MIT; edition data (transcriptions, TEI, register) CC0 1.0. See [LICENSE](LICENSE).

Scripts run with the schlyter-digital venv (Python 3.12, with kraken, lxml, edlib and rapidfuzz). VLM OCR needs a vLLM server; see `scripts/vlm_ocr.py` and the HS-nominalkatalog README for setup.

```bash
P=../schlyter-digital/.venv/bin/python
$P scripts/extract.py --vol all          # PDFs (data/pdf/) -> data/img/<vol>/pNNNN.jpg
$P scripts/textlayer.py --vol all        # PDF text layer -> ocr/textlayer/ (ALTO)
$P scripts/pagemap.py --vol all          # -> data/pagemap/<vol>.json (image -> printed page)
$P scripts/sample.py                     # -> gt/sample.tsv (24 bake-off pages)
$P scripts/vlm_ocr.py --mode dots-layout --pages gt/sample.tsv --max-tokens 3500 --workers 2
$P scripts/gt_draft.py                   # -> gt/drafts/ (draft + disagreement checklist)
$P scripts/evaluate.py --engines textlayer,dots-layout,lighton   # vs gt/pages/ -> report/ocr-bakeoff.md
```

Volume ids are the PDF suffixes: 1932 (I), 1933a (II), 1933b (III), 1935 (IV), 1936 (V), 1943a (VI), 1943b (VII) and 1944 (VIII). Page files use the 1-based PDF image index; `data/pagemap/` maps it to the printed page.

## Ground-truth editor

```bash
$P scripts/gt_server.py --vol 1932      # every image of vol. I; or no --vol for the 24 sample pages
# open http://localhost:8765/   (press ? in the editor for keys and conventions)
$P scripts/evaluate.py --engines textlayer,dots-layout --vol 1932   # score against verified vol. I pages
```

On the left is the scan, on the right the region text. Disagreements are found by aligning your text word by word with the PDF text layer, and each one shows the readings of all engines. With *follow cursor* on, the word at the caret is boxed on the scan.
- **Save** writes to `gt/wip/`.
- **Verified ✓** writes to `gt/pages/`, which `evaluate.py` uses.
- If a page has no draft, one is made from dots.mocr, or from the text layer if dots.mocr has not run on it.

## TEI

```bash
$P scripts/to_tei.py --vol 1932 --validate    # -> tei/schuck-1932.xml (jing + tei_all from Oxygen)
$P scripts/toc.py --vol 1932                  # sidenotes vs the table of contents -> report/sidenotes-1932.md
```

Each volume needs `data/structure/<vol>.json`, written by hand from its table of contents. It holds the chapters and their start pages, front and back matter, binding and scan images, duplicates, and errata.

## Reader (GitHub Pages)

```bash
$P scripts/build_site.py                  # -> site/ (TEI + 1200 px WebP page images + the app from web/reader/)
python3 -m http.server 8000 -d site       # preview at http://localhost:8000/
scripts/deploy_pages.sh                   # publish site/ to the gh-pages branch of origin
```

`site/` is gitignored on `main`. It is its own git repository on the `gh-pages` branch, so the page images (about 150 KB each, roughly 650 MB for all eight volumes, under GitHub Pages' 1 GB site limit) never enter `main`. The reader has four views:
- **Läs:** the text, with the facsimile following the scroll position, footnotes as popovers and sidenotes in the margin.
- **Citat:** all quotations.
- **Källor:** source citations grouped by repository.
- **Sök:** full-text search, with optional folding of historical spelling and search across all volumes.

Links: `#1932/s52` (page), `#1932/ch2` (chapter), `#1932/q-1932-00010` (quotation).
