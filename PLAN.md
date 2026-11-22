# Schück digital: OCR + TEI edition of *Kgl. Vitterhets historie och antikvitets akademien* (8 vols, 1932–44)

## Context
Build a digital edition of Henrik Schück's 8-volume history (public domain), from the PDFs in `data/pdf/`. The first job is to find the best OCR engine and produce a clean, structured TEI text with a static web reader. **Linking quotations to the source manuscripts on manuscripta.se is deferred** (the user's decision). The edition will still mark quotations and source citations so that linking can be added later without re-OCR.

### What the sources are (checked)
| File | Pages | Image px (200 dpi JPX, RGB) | Words (text layer) | » marks |
|---|---|---|---|---|
| Schück_1932 | 327 | 1875×2493 | 101k | 1,345 |
| Schück_1933a | 501 | 1630×2282 | 164k | 1,963 |
| Schück_1933b | 437 | 1658×2324 | 145k | 1,805 |
| Schück_1935 | 645 | 1658×2268 | 216k | 2,650 |
| Schück_1936 | 763 | 1770×2390 | 242k | 2,441 |
| Schück_1943a | 465 | 1656×2310 | 145k | 2,090 |
| Schück_1943b | 517 | 1680×2368 | 163k | 2,465 |
| Schück_1944 | 501 | 1714×2392 | 164k | 2,633 |
≈ 4,150 pages, ≈ 1.34 M words.

- **There is already a text layer.** The fonts are invisible Times, so it probably comes from ABBYY. It is fairly good, but sample errors include `jörsta` for *första*, `i 5)` for *15)* (oldstyle numerals), `Geographies` for *Geographicæ* and `PfeifFs`. Italics are lost, and the **marginal sidenotes are merged into the body lines** (`… täm- Oenighet inom`).
- **Layout:** one column of Antiqua with oldstyle figures. Each page has an italic, letterspaced running head and a page number. There are italic **sidenotes** in the outer margin, block quotations, small-type editorial asides (`(20 utländska arbeten uppräknas)`), italic emphasis, æ/œ, and `[ ]` for supplied text.
- **The quotations are in 17th–18th c. spelling** (`hafwer`, `uhr`, `Tullfrijheet`). An OCR engine that normalises spelling ruins them, so we score it with *diplomatic* CER.
- **Source citations are inline**, for example `K. B. sign. E IV`, `R. A. Kanslikoll.`, `R. A. Skrivelser till De la Gardie`, `Palmskiöld vol.` and `U. U. B.`. They can be parsed into repository + shelfmark.

### What we reuse from your other repos
- **schlyter-digital:** this is the template for the whole project.
  - Kraken print-OCR fine-tuning: `schlyter-print-r1…r8`, 99.6–99.7% on held-out pages. It encodes italics as PUA codepoints (`scripts/gt_tools.py`) and handles region and sidenote segmentation (`print_regions.py`, `print_seg.py`).
  - TEI build (`to_tei.py`), validated with jing against tei_all, and a static web reader (`web/`, `build_web.py`, OpenSeadragon).
  - `align_text.py` / `edition_lines.py`: text↔line alignment, kept for the deferred HTR phase.
- **OCR-editor:** browser proofreading of ALTO/PAGE.
  - `tools/llm-ocr/llm_ocr.py` writes per-line VLM sidecars, and the editor shows them as accept/reject suggestions.
  - The dots.mocr session showed that it **keeps historical spelling**, where LightOnOCR modernises.
- **HS-nominalkatalog:** the vLLM setup on the RTX 3060 (`.venv-vllm`, vLLM 0.31, `VLLM_USE_FLASHINFER_SAMPLER=0`), `pipeline/ocr.py` engines, and `pipeline/evaluate.py` CER/WER reporting.

## OCR engine candidates (bake-off, not assumption)
1. **Existing PDF text layer.** This is the baseline, with word boxes from `pdftotext -bbox-layout`. It costs nothing.
2. **`rednote-hilab/dots.mocr`** via vLLM. It has the best diplomatic CER on the BHL 18th–19th c. leaderboard (5.0%), keeps the original spelling, and loops rarely. It is the **expected primary page-level VLM**.
3. **`lightonai/LightOnOCR-3-0.8B`**, in grounding mode. It is fast and gives block coordinates. It tends to modernise spelling, so it serves as the second opinion.
4. **Kraken, fine-tuned from `schlyter-print-r8`** (or CATMuS-Print). This is the line-level engine, with exact line geometry for ALTO, character-level italics via PUA, and cheap fine-tuning on 5–10 corrected pages. It is the **expected primary engine if italics and line coordinates matter**, which they do for the reader and for later linking.
5. *(Optional)* A larger VLM (e.g. LightOnOCR-3-4B or Qwen-VL class, quantised) only if 2–4 fall short. Use Claude only for post-hoc structuring checks, not for bulk OCR.

**Decision rule:** pick the primary engine by diplomatic CER on ground truth, split into body text, quotations and sidenotes. Use disagreement between the two best engines as the review signal in OCR-editor. The existing llm_ocr sidecar flow works unchanged on ALTO.

## Plan

### Stage 0: repo setup
- Set up `pyproject`/uv with a Python 3.12 venv for kraken. vLLM stays in HS-nominalkatalog's `.venv-vllm`, used via an OpenAI-compatible endpoint.
- Add `.gitignore` entries for `data/pdf`, `data/img`, `cache` and model weights.
- Write a `PLAN.md` and a progress log, following the schlyter-digital convention.
- Layout: `scripts/`, `data/img/<vol>/`, `ocr/<engine>/<vol>/`, `gt/`, `models/`, `tei/`, `web/`, `report/`.

### Stage 1: page images and the existing text layer
- `scripts/extract.py`: run `pdfimages -png` per volume to get `data/img/<vol>/<page>.png`, at native 200 dpi with no resampling.
- Map PDF page index → printed page number, using the page-number OCR and checking it is monotone.
- `scripts/textlayer.py`: run `pdftotext -bbox-layout` and convert to ALTO, so the baseline goes through the same evaluation and editor.
- Check whether KB/Litteraturbanken has higher-resolution scans or IIIF for libris `cvn7fkgp3mft1s5`. If so, the reader uses IIIF instead of self-hosted images.

### Stage 2: ground truth and the OCR bake-off
- **Sample:** pick 24 pages, 3 per volume, stratified so they include block quotations, sidenotes, a chapter opening, title/index pages and a page with tables or lists.
- **Ground truth:** draft from the best engine, then correct in OCR-editor against the image (PAGE-XML). Write the conventions down:
  - Keep ſ, æ, œ and historical spelling. Italics use the `gt_tools.py` PUA convention.
  - Letterspacing is collapsed but tagged.
  - Sidenotes go in their own region. Hyphenation is kept as printed, with line-end `-`.
- **Run:**
  - engines 1–3 on all 24 pages;
  - engine 4 after fine-tuning `schlyter-print-r8` on 16 GT pages, with 8 held out.
- `scripts/evaluate.py`, adapted from HS-nominalkatalog `pipeline/evaluate.py`, writes `report/ocr-bakeoff.md`. It reports:
  - CER/WER, both diplomatic and normalised, for body, quotations and sidenotes;
  - digit errors (oldstyle 1/i, 5/s), italic F1, loops or empty output, and seconds per page.

### Stage 3: full OCR run
- Run the chosen primary engine on all ≈4,150 pages. For reference: dots.mocr took ~5 s/page in the C-katalogen test (≈6 h total). Kraken is faster.
- Run the second engine for disagreement sidecars.
- Output ALTO/PAGE per page, with regions: running head, page number, body, block quote, sidenote, small-type aside.
- Proofread in OCR-editor, prioritised by disagreement density. Spot-check CER on 2 new pages per volume.

### Stage 4: TEI encoding (`scripts/to_tei.py`, `tei/schuck-<year>.xml`)
- **Structure:** `TEI/text/body` > `div[@type=chapter]` (chapter titles are recovered from the running heads and the table of contents) > `p`.
  - `<pb n facs>` for each page, `<lb>` where useful, and `<lb break="no"/>` for dehyphenation. Real compound hyphens are kept, using a lexicon check.
- **Quotations:** `»…«` inline becomes `<quote>`, and block quotations become `<quote rend="block">`. Each gets a stable `xml:id` (`q-<vol>-<page>-<n>`).
- **Sidenotes:** `<note place="margin">` anchored at the line where they start.
- **Source citations:** a regex grammar (`K. B. sign. …`, `R. A. …`, `U. U. B. …`, `Palmskiöld vol. …`, `fol. …`) produces `<bibl type="source"><orgName>` + `<idno type="shelfmark">` + `<citedRange>`.
  - Each `<quote>` is linked to its nearest citation via `@source`. This is the hook for the deferred manuscripta.se linking.
- **Header:** the libris record, the volume's printed title and the PD statement.
- **Validation:** jing against tei_all, as in schlyter-digital.

### Stage 5: web reader (`web/`, `scripts/build_web.py`)
- Copy the schlyter-digital reader pattern: text pane with page image (OpenSeadragon) and volume switch.
- Add full-text search (MiniSearch, as in HS-nominalkatalog) and a filterable list of quotations and source citations by repository and shelfmark.
- Person index: a later, optional step via NER, with names like Hadorph, Peringskiöld, Rudbeck.

### Deferred: manuscripta.se linking and HTR ground truth (record only)
- The target set is manuscripta.se project **org-100172 "RiViH – Riksantikvarieämbetets och Vitterhetsakademiens äldsta historia"**:
  - **398 MSS at the Swedish National Heritage Board** (`place-100272`), plus 280 at KB.
  - It can be queried as JSON at `https://www.manuscripta.se/modules/get_search.xql?fs=project:org-100172;repo:place-100272`.
- **Later method:**
  1. Resolve the parsed `<bibl type="source">` citations to manuscripta ids.
  2. Fuzzy-align each `<quote>` against the HTR text of the cited MS (edlib infix, as in `align_text.py`).
  3. Measure how faithfully Schück transcribes, i.e. whether he normalises silently.
  4. Only then decide whether quote spans are usable as HTR ground truth.
- Many citations are to Riksarkivet (`R. A.`), which is outside manuscripta.se.

## Verification
- `report/ocr-bakeoff.md` compares the engines with the metrics above on the 8 held-out GT pages. Target: diplomatic CER < 1% on body text and < 2% on quotations before the full run.
- Each stage has a re-runnable script that appends to `report/metrics.json`.
- The TEI validates with jing. Round-trip check: the TEI plain text vs the corrected OCR text has CER ≈ 0, which catches losses in dehyphenation and structuring.
- Spot checks:
  - 50 random `<quote>` elements for correct start and end;
  - 50 `<bibl type="source">` for correct parsing;
  - sidenotes not leaking into body text, checked by searching for known sidenote strings in `<p>`.
- Open the web reader locally (`npm run dev` or a static server) and click through one chapter per volume, checking that the image and text stay in sync.

## Progress log
- **2026-11-13, Stage 0–1 done:**
  - **Page images:** `scripts/extract.py` renders all 4,156 pages at native 200 dpi (JPEG q92, 2.6 GB, `data/img/`). Pages are rendered rather than extracted because some are composites with CCITT masks. Only about 19 GB of disk is free.
  - **Text layer:** `scripts/textlayer.py` converts it to ALTO in `ocr/textlayer/`. Sidenotes, which pdftotext merges into body lines, are split out by the body-column edges and found on about 30% of pages. The layer marks hyphenation with soft hyphens (U+00AD).
  - **Page map:** `scripts/pagemap.py` gives PDF index → printed page as a monotone chain of read page numbers.
    - Plates, covers and engraved facsimiles are detected by the share of word-like tokens, since the text layer contains OCR garbage on them.
    - 48 images are unresolved, mostly front matter.
    - Vol. I is scanned from a bound copy, with page edges visible. Each volume ends with its back cover.
- **2026-11-13, Stage 2 started:**
  - **Sample:** `gt/sample.tsv`, 24 pages, quote/footnote/body × 8 volumes, with 8 test pages.
  - **Engines:** `scripts/vlm_ocr.py` runs dots.mocr (layout prompt) and LightOnOCR-3-0.8B through vLLM.
  - **Scoring:** `scripts/regions.py` converts every engine to the GT region format, and `scripts/evaluate.py` scores it.
  - **dots.mocr, layout mode:**
    - It returns categorised blocks with boxes: Section-header/Page-header = running head, Footnote, Page-footer = page number, Table for account lists, and sidenotes as separate Text blocks at the margin.
    - æ is right (the text layer has `as`/`es`), and it parsed on 24 of 24 pages with no loops.
    - Speed is about 29 s/page with 2 parallel requests on the RTX 3060, roughly 17 h for the corpus, and it needs `--max-model-len 10240`.
    - It gives no italics. It made single-letter errors (`emotog` for *emottog*, `fördensskull` for *fördenskull*).
  - **LightOnOCR-3-0.8B (plain):** about 14 s/page with 8 parallel. It has no regions, so sidenotes and running heads are inlined. It looped on the account-table page (1936 p0417) even after a retry at 0.2.
  - **Agreement with dots.mocr** (`report/agreement-dots-layout.md`; this is not accuracy): text layer 2.05% dipl CER and LightOnOCR about 1–3% on normal pages. Most text-layer differences are æ, oldstyle digits and letterspaced running heads.
  - **GT drafts:** `scripts/gt_draft.py` writes `gt/drafts/` (dots text in GT format) plus `*.check.txt`, which lists the 198 word differences against the text layer.
  - **Next:** check the drafts against the images and copy them to `gt/pages/`, starting with the 8 test pages. Then run `evaluate.py --engines textlayer,dots-layout,lighton`, then kraken fine-tuning (engine 4).
- **2026-11-13, vol. I GT selection:** `gt/vol1932.tsv`, 21 pages.
  - **Test (9):** 8 random text pages (≥ 20 lines, seed 1932) plus sample test page #235. These are never used for training, and they give the unbiased accuracy estimate for vol. I.
  - **Train (12), targeted:** the densest quotation page (#66, p. 47), two other quotation pages, Latin-heavy pages (#28, #232, #25), two footnote pages, a sidenote page, the chapter opening p. 1 (#18; image #11, picked first, is a blank leaf the old page map called p. 1), a short chapter-end page, and #316 (colophon + errata).
  - Editor: `gt_server.py --pages gt/vol1932.tsv`.
- **2026-11-13, vol. I OCR:** dots.mocr ran on all 327 images (about 33 s/page, 2 parallel). Four pages loop to the token limit even after the retry and fall back to the text layer: #67–68 (runic alphabet tables, pp. 48–49), #82 (runic plate) and #314 (TOC, dot leaders).
- **2026-11-13, page-map fix:** page numbers now count only on pages with running text. Image 11, a blank leaf, had been taken as p. 1. Pages before the first anchor are back-filled down to p. 1. Vol. I p. 1 is image 18. Vols VII and VIII still start at p. 3, to be checked with their structure files.
- **2026-11-13, Stage 4 TEI v0 (vol. I):** `scripts/to_tei.py --vol 1932 --validate` writes `tei/schuck-1932.xml`, which is valid against tei_all (jing from Oxygen).
  - **Inputs:** `data/structure/<vol>.json` holds the chapters and start pages from the TOC, plus front and back matter, binding/scan pages and duplicates. It is hand-checked: vol. I has 7 chapters.
  - **Text per page:** verified GT (`gt/pages`), else dots.mocr, else the text layer. The source is recorded in `pb/@resp`.
  - **Encoding:**
    - Paragraphs are stitched across pages, with a new paragraph where the text layer's first line is indented. A word split by a page break gets `<pb break="no"/>`.
    - `<fw>` marks running heads, page numbers and printer's signatures.
    - Front matter: `<titlePage>` (half-title and title) and `<div type="dedication">`.
    - Plates: `<figure type="plate">` with the caption as `<head>`, and engraved text as `<ab type="plateText">`.
    - Footnotes are placed at their `^n` anchors. Marginal summaries become `<note place="margin" type="summary">`, placed at the text-layer line nearest to them.
    - `»…»` becomes `<quote>`, split across paragraphs and linked with `@next`/`@prev`. « also closes a quotation, after a runaway quote of 43 paragraphs.
    - Source citations: `<bibl type="source">` with `<orgName>`/`<idno type="shelfmark">`. Tables become `<table>`.
  - **Vol. I counts:** 744 paragraphs, 699 quote parts (the longest chain, 22 paragraphs, is Gustav Adolf's royal letter), 92 footnotes (5 unanchored, `cert="low"`), 92 marginal summaries (the TOC lists about 100 topics) and 28 source citations.
  - **Round trip:** TEI body text vs dots.mocr body text, 2.1% CER, mostly from moved notes. The only pages that differ fully are plates and blank leaves, which is by design.
  - **Next for TEI:**
    - Check the sidenote positions visually.
    - Parse the TOC topics as a check on the sidenotes.
    - Apply the errata (#316) as `<choice><sic/><corr/></choice>`.
    - Hand-encode the 4 failed pages.
    - Write structure files for vols II–VIII.
- **2026-11-13, first GT figures (vol. I, test pages #42 and #121):**
  - dots.mocr: 0.04% diplomatic CER, 0.28% WER. Its errors were `Fiolmus` for *Fjolmus* and `vttaghet` for *vthtaghet*.
  - Text layer: 0.22% CER, 0.98% WER. Its errors are Old Norse þ/á/ó (`J^åttr`, `Trojumanna`) and split words.
  - Two pages only; this is not yet a conclusion.
- **2026-11-13, sidenotes checked against the TOC:** `scripts/toc.py --vol 1932` parses the TOC topics into `data/structure/1932-toc.json` and writes `report/sidenotes-1932.md`.
  - **Merged sidenotes:** `to_tei.rescue_margins` cuts sidenotes that dots.mocr merged into a paragraph back out, using the text layer's margin column. It rescued 16.
  - **Illustrations:** a page with more than four "sidenotes" is an illustration printed in the text (p. 58, the Jelling runic stone). Its text goes into `<figure><ab type="figText">`.
  - **Result:** 91 of 96 TOC topics are found as sidenotes on their page. 2 are still in the body (pp. 120, 274), 2 are missing (pp. 56, 116), and 1 has a TOC misprint (54 for 154).
- **2026-11-13, errata:** the errata list on the slip in image #316 is in `data/structure/1932.json` (`errata`). Both items are applied as `<choice><sic/><corr resp="#errata" source="#corrigenda"/></choice>`.
- **2026-11-13, Stage 5 reader v0:** `web/reader/` is a static app, built into `site/` by `scripts/build_site.py`, previewed locally, and published with `scripts/deploy_pages.sh` to the gh-pages branch.
  - **Images:** self-hosted, since there is no IIIF source. They are 1200 px WebP at q70, about 145 KB per page: 44 MB for vol. I, and an estimated 650 MB for all eight volumes, under the 1 GB Pages limit.
  - **Views:**
    - Läs: TEI rendered in the browser, with the OpenSeadragon facsimile following the scroll position, footnote popovers, sidenotes in the margin, and errata shown on hover.
    - Citat: 572 quotation chains in vol. I.
    - Källor: 28 citations, grouped by repository.
    - Sök: diacritic-preserving search with an optional historical-spelling fold (w=v, i=j, æ=ä …) and across all volumes.
    - Om: about the edition.
  - **Page provenance:** each page marker shows ✓ for verified GT, dashed for text-layer pages, plain for OCR.
  - **Tested in headless Chromium** (desktop and 390 px mobile, no horizontal scroll): no console errors, and vol. I loads and renders in about 1.9 s.
  - **Not yet published:** the repository has no commits and no GitHub remote.
- **2026-11-13, Stage 6 (NER) planned:** see [docs/ner-plan.md](docs/ner-plan.md).
  - Scope: persons and places, detected with KB-BERT plus a gazetteer.
  - A shared register links to Wikidata and manuscripta.se/person.
  - No LLM: rules resolve what they can, and the rest goes to manual review in the local editor.
  - Not started.
- **2026-11-13, GT note:** dots.mocr marks italics only partially. On #215 it gives `antikvarien *Stiernhielm*` where both words are italic. In the drafts `{…}` is a hint, not a complete marking, so italics need checking on every GT page. This counts towards using kraken if italics are wanted in the edition.
- **2026-11-13, OCR bake-off result (vol. I, 9 verified test pages, 3,064 words):**
  - **Scores:**
    - dots.mocr: 0.04% dipl CER, 0.26% WER, 0 errors in sidenotes, footnotes, running heads and digits.
    - Text layer: 0.43% CER, with 14–28% in sidenotes, footnotes and heads.
    - **dots.mocr is the primary engine.**
  - **dots.mocr's errors:** all 8 are modernised spelling inside 17th c. quotations (`vårt` for *wårt*, `Fiolmus` for *Fjolmus*, `skrifter` for *skriffter*, `cch`→`och`). The text layer had the right reading at all 8, so a review of the dots.mocr/text-layer disagreements would have caught every one.
  - **Rule for historical spelling:** where the words differ only by w/v, j/i or a doubled letter (not case), prefer the text layer. It was right 6 of 6 on the GT pages and fires 78 times in vol. I. Limit it to quotations and to letters, not punctuation.
  - **Next:**
    - the disagreement-review mode for the rest of vol. I (1,868 spots, about 6.6 per page);
    - the italics decision (GT has few italic words marked);
    - the training pages, only if kraken is wanted for italics or line coordinates.
- **2026-11-13, italics decided:**
  - Running-text italics are rare: only one passage (`{antikvarien Stiernhielm}`, #215) on the 9 test pages.
  - The structural italics (running heads, sidenotes) are covered by `<fw>` and `<note place="margin">`.
  - dots.mocr's partial `*…*` marking is accepted. No kraken model is needed for italics, so engine 4 is shelved unless line coordinates become a requirement.
- **2026-11-13, NER phase 1 (vol. I):** `scripts/ner.py` (run in the HS-nominalkatalog venv) writes `data/ner/1932.json` and `report/ner-1932.md`.
  - **Run:** 1,034 text units, about 97k words, 20 s on the GPU.
  - **Mentions:** 2,952 PER (1,019 forms, 838 surnames), 956 LOC (270 forms), 93 ORG. 316 were dropped by the rules (low score, inside source citations, collections).
  - **Main disambiguation case:** 290 bare "Bureus" against Laurentius 33, Johannes 15, Jonas 3 and others.
  - **Phase 2 normalisation needs:**
    - genitives without an apostrophe (`Magnis`, `Rugmans`);
    - Latin case forms (`Olaum`, `Johannis`, `Gabrieli`, `Messenio`);
    - prefixes to strip (`Wij`, `Hertig`, `Drottning`, `Doctor`);
    - rejects: works as persons (`Codex Wormianus`, `Bosas Saga`), chancery words (`Cantzelie`, `Cammar Colleg`), `Gudz`/`Gudh`, the printer `ALMQVIST & WIKSELLS`, `EDERS`;
    - for places: institutions (`Kungl. biblioteket`) and spelling variants (`Upsala`/`Uppsala`).
- **2026-11-13, EPUB export:** `scripts/to_epub.py --vol 1932 [--check]` writes `site/epub/schuck-1932.epub` (2.4 MB). `build_site.py` rebuilds it when the TEI is newer, and the reader links it in the header and in "Om".
  - **Content:** EPUB 3 with one XHTML file per chapter, plus the title page, dedication, printed table of contents, errata and an "Om denna utgåva" page.
    - Footnotes are popup notes.
    - Sidenotes are inline italic subheadings placed before their paragraph.
    - 14 plates are included as images; blank plate backs are skipped, and only short printed captions are kept.
    - Printed page numbers form a page list. The scanned title page is the cover.
  - **Round trip:** the EPUB chapter text equals the TEI body text, with 0 character differences.
  - **Validation:** epubcheck 3.0.1 (TEIC Stylesheets) reports only `belongs-to-collection`/`collection-type`, which postdate that checker (EPUB 3.0.1+).
    - epubcheck 5.3.0 is not yet run. The release is unzipped in `cache/tools/`, but auto mode blocked executing downloaded code.
    - Run it with `EPUBCHECK_JAVA="/home/patrik/Oxygen XML Editor 28/jre/bin/java" scripts/to_epub.py --vol 1932 --check` once allowed.
- **2026-11-13, Kraken test (vol. I):** `scripts/kraken_lines.py` (silver / ocr), model `models/kraken-schuck-r1` (gitignored).
  - **Training data:** text-layer line boxes. 7,750 of 11,299 lines on non-GT pages (69%) have dots.mocr and the text layer agreeing word for word; 5,700 train and 300 validation were used, with the GT pages excluded.
  - **Training:** fine-tuned from `schlyter-print-r8`, 10 epochs at about 43 s each, 99.99% validation accuracy.
  - **Scores on the 9 GT pages:**
    - zero-shot r8: 4.91% CER (diacritics lost);
    - fine-tuned: 0.34% (the text layer's region errors included);
    - recognition only: about 10 word errors, against dots.mocr's 8.
  - **Kraken's errors:** Old Norse letters (þ, á, ó, ð, never in the silver data because the text layer cannot read them), o/0 in abbreviations, one sidenote.
  - **Complementary to dots.mocr:** Kraken read 7 of dots.mocr's 8 modernisation errors correctly.
  - **Speed:** all of vol. I in 2.5 min (dots.mocr: about 2.5 h).
  - **Review queue in vol. I** (dots.mocr vs the second engine, after the auto filters): text layer 1,150 places (catches 8/8 dots.mocr errors), Kraken 669 places (7/8; misses the `cch` misprint, which Kraken also "corrects").
  - **Three-way vote:**
    - On the GT pages: all agree, 3,038 words with 0 dots.mocr errors; Kraken = text layer ≠ dots.mocr, 7 words, all dots.mocr errors.
    - In vol. I the vote fires 347 times, but it is wrong on Nordic special characters (105: `Hólar`→`Hölar`, `Guðmundur`→`Guömundur`), which both engines lose. It is reliable only for historical spelling (77), which is the spelling rule.
  - **Next step if wanted:** round 2 with silver lines for þ/ð/á/ó/ø, taking dots.mocr's text where it differs from the text layer only in those characters.
- **2026-11-13, NER phase 2 register draft (vol. I):** `scripts/register.py` writes `data/register/persons.json`, `places.json`, `ambiguous-1932.json` and `report/register-1932.md`.
  - **Persons:** 786 entries. 56 matched to Wikidata automatically (e.g. Laurentius Bureus Q5590212, Johannes Bureus Q633094), 79 for review, 139 without a candidate, and 512 single-mention entries skipped.
  - **Ambiguous bare surnames:** 10 surnames with 336 mentions, of which Bureus has 294.
  - **manuscripta links via VIAF:** only 4, since the local `id/person` dump has 486 records.
  - **Places:** 233 entries; 33 auto, 47 review.
- **2026-11-13, NER in TEI and reader (phase 4, before review):**
  - **Linking:** `scripts/link.py` links each mention to the register and writes `data/ner/<vol>-links.json` and `tei/register.xml` (valid against tei_all; Wikidata, VIAF, SBL, Libris and manuscripta idnos where the Wikidata match is accepted).
    - Rules: a printed form gives the entry (cert high, or medium when assigned by dominance). Ambiguous bare surnames get a guess (cert low) from the same paragraph, then earlier in the chapter, then the chapter majority. Manual decisions in `data/ner/<vol>-decisions.json` override everything.
    - Vol. I: 3,839 links. Bare "Bureus" guessed as Johannes 171, Laurentius 93, Jonas 13.
  - **TEI:** `to_tei.py` adds `<persName>`/`<placeName ref="register.xml#…" cert>` through the span machinery, matched by the unit-text hash. 3,780 of 3,839 links land; the rest are in the errata paragraphs.
  - **Site:** `build_site.py` writes `site/data/register.json` (with mention counts per volume) and copies `register.xml`.
  - **Reader:**
    - names are marked in the text; a click gives a popover with dates, description, counts, an uncertainty note and identifier links;
    - a "Namn" switch highlights names (off by default);
    - "Personer" and "Orter" views, filterable and sortable, with mentions as snippets linking into the text;
    - register matches lead the search results;
    - `#person/<id>` and `#place/<id>` links.
    - Tested headless on desktop and at 390 px: no errors, no horizontal scroll.
  - `register.py` now strips "Monsieur"/"Mons."/"Mr" (Monsieur Hadorph → Johan Hadorph).
- **2026-11-13, Kraken round 2:** `models/kraken-schuck-r2`, fine-tuned from r1.
  - **Training data:** `kraken_lines.py silver --special` adds lines where the text layer misreads only þ/ð/á/ó/ø/æ… and uses dots.mocr's word there (326 lines with special characters, three times the weight); 7,477 train + 393 validation lines.
  - **On the 9 GT pages:** CER 0.34 → 0.30%, WER 0.56 → 0.36%, real word errors 12 → 7. It now reads þ, `Trójumanna` and the `cch` misprint; á (`þåttr`) and o/0 in `2:do` remain.
  - **As the comparison engine:** it flags 8/8 dots.mocr errors (r1: 7/8; text layer: 8/8, but with a queue of 1,150). The vol. I review queue is 602 (r1: 669).
  - **Three-way vote (Kraken r2 = text layer ≠ dots.mocr):** 8/8 right on the GT pages. In vol. I, wrong votes on special characters fell from 108 to 15. Use it as the pre-selected answer in review, not as an automatic correction.
- **2026-11-13, vol. II:**
  - **OCR:** dots.mocr ran on all 501 images (vLLM on port 8001; port 8000 is the reader preview). 3 pages loop on dot leaders and fall back to the text layer: the TOC (#496–497) and p. 309 (a quotation with a row of dots). Kraken r2 read the volume in 3.7 min.
  - **Comparison fix:** `regions.from_dots` treats a dots.mocr block as one paragraph and joins its printed line breaks as `to_tei.py` does. Before this, vol. II's line breaks inflated the review queue with `haf- wer`. `dots_blocks` also joins inline `word- word` (not before a conjunction): 43 cases, which changes the TEI.
  - **Review queues:** vol. I 501 places (1.8/page), vol. II 1,293 (2.8/page).
  - **Structure file:** `data/structure/1933a.json` has 6 chapters; the 6th, p. 343, is missing from the garbled TOC lines and was found from the dots.mocr headings. Front matter: binding 1–3, half-title 4, title 6. Back: contents 496–497 (with the errata), colophon 498. Erratum: p. 97 "Hadorph 1675" → 1676.
  - **TEI:** `tei/schuck-1933a.xml`, valid: 1,273 paragraphs, 1,568 quote parts, 134 source citations, 155 sidenotes (59 rescued), 81 footnotes, 1 erratum.
    - Sidenotes vs TOC: 131 of 152 topics found on their page.
    - New rule in `quote_spans`: a quotation carried over from the previous paragraph ends when the new paragraph opens a quotation in mid-text. This split a 69-paragraph runaway; the longest chain is now 37 (the Aschanius inventory), and vol. I is unchanged.
  - **Site:** both volumes are in the reader and as EPUBs (vol. II 4.2 MB); the site is 126 MB.
  - **Kraken round 3** (vol. I + II silver lines, from r2) is training.
- **2026-11-13, Kraken round 3:** `models/kraken-schuck-r3`, fine-tuned from r2 on the vol. I r2 lines plus 8,000 vol. II silver lines (400 with special characters); 6 epochs.
  - **On the vol. I GT pages:** CER 0.28% (r2 0.30), WER 0.26% (r2 0.36; equal to dots.mocr), digit tokens 0% (r2 1.3%).
  - **As the comparison engine:** it still flags 8/8 dots.mocr errors.
  - **Review queues:** vol. I 466 (1.6/page), vol. II 1,182 (2.6/page).
  - Vol. II stays higher per page, mainly because of more 17th c. quotations (rule: 282) and the inventories and tables.
- **2026-11-14, reader: search scope and mention snippets:**
  - **Search scope:** a selector limits search to "i all text", "endast i citat" (one hit per quotation chain, linking to the quote) or "utanför citat" (paragraph text with its quotations removed). Register matches show only for all text. "Bureus" in vol. I: 462 = 19 in quotations + 443 outside.
  - **Mention snippets** in the Personer/Orter views are cut around the mention's own position (not the first occurrence of the name in the paragraph), without the paragraph's sidenotes and footnote numbers. Mentions in sidenotes and footnotes are labelled as such; a click on a footnote mention scrolls to the reference and opens the note.
  - Tested headless at 1280 and 390 px: no errors, no horizontal scroll.
- **2026-11-14, old-spelling rule:** `to_tei.py` (`spelling_candidates`, applied in `flush_para`).
  - **Rule:** align each dots.mocr page's body words with the body tokens of the text layer and of Kraken r3 (key: lowercase, w=v, j=i, doubled letters collapsed). Where both engines read the same word and it differs from dots.mocr's only by w/v, j/i or doubled letters (same case, letters only), and the word is inside a `<quote>`, take their reading: `<app type="ocr"><lem wit="#textlayer" resp="#spelling">wårt</lem><rdg wit="#dots">vårt</rdg></app>`.
  - **Why Kraken too:** on vol. II pp. 339–440 the text layer alone gave 34 suspect forms (`tilll`, `SSist`, `sspråch`, `elller`). With the Kraken guard the rule fires 189 → 155 times in vol. II and 60 → 59 in vol. I. What remains on those pages mostly matches the scan (`steenar`, `agreerat`, `miin` are printed so: a 17th c. letter set with some damaged type).
  - **Checks:** 6/6 right in position on the 9 GT pages (`Fjolmus`, `fördenskull`, `befalning`, `skriffter`, `wårt`, `wåld`). The TEI is otherwise identical to a build without the rule, and both volumes are valid against tei_all.
  - **Spot-check lists:** `report/spelling-1932.md`, `report/spelling-1933a.md`.
  - **NER:** `ner.py` skips `<lem>`, so unit hashes stay on dots.mocr's text, which `to_tei.py` matches. Vol. I: 547/552 link hashes match, the same as before; reading `<lem>` would give 515.
  - **Reader:** corrected words have a dotted underline, and hovering shows dots.mocr's reading. Search and lists use the old form. Explained in "Om". **EPUB:** the old form only.
  - `regions.textlayer_tokens` takes an `engine` argument (an ALTO on the text layer's lines, e.g. `kraken-ft3`).
- **2026-11-14, reader anchor fix:** a `#person/…` or `#place/…` link scrolls the entry to the top, below the sticky filter bar, once the whole list is built. Before, it was scrolled while still the last item, which left it at the bottom of the viewport.
- **2026-11-14, vol. III OCR:** dots.mocr on all 437 page images in about 45 min (2 workers). p0142 needed one retry. p0426 (the TOC) loops on dot leaders even after the retry and will use the text-layer fallback. Not yet done: Kraken r3, structure file, TEI. The vLLM server is stopped. Session summary: [docs/session-2026-11-14.md](docs/session-2026-11-14.md).
- **2026-11-15, vol. III (1933b):**
  - **Kraken r3:** 402 pages (all text pages), about 4 min.
  - **Page map fixes (`pagemap.py`):**
    - In a gap with more text images than missing page numbers, real pages next to an anchor are now recognised by their running head (fuzzy match, spaces ignored, against the head of the same-parity page p±2/p±4). Once the count is met, the remaining images in the gap become plates (unpaginated inserts).
      - Vol. III: image 141 is p. 128 (no page number in the text layer). Images 142–149 are plates: Stiernhielm's annotated *Runa Suethica* in facsimile (142–145) and a typeset reprint of it (146–149).
      - Vol. II: images 240–243 are pp. 209–212 (page numbers split as `2 10`). They were encoded as plates; they are now body text.
    - Back matter: numbering after the last anchor stops at the first non-text image (blank leaf, dot-leader TOC). Before, later pages got wrong numbers: vol. I colophon "p. 281", vol. III TOC p. 404 as "402".
  - **Structure file:** 10 chapters from the TOC (images 426–427), all confirmed by dots.mocr headings. Front: binding 1–4, half-titles 8 and 10, title 12. Back: contents 426–427, colophon 428 (no errata), binding 435–437.
  - **TEI:** valid; 982 paragraphs, 1,319 quote parts, 125 source citations, 176 sidenotes (103 rescued), 87 footnotes, 115 old-spelling changes.
  - **`toc.py`:** topics are now assigned to chapters by page range from the structure file, after dropping lines that are only chapter-title fragments or page numbers (vol. III's second TOC page splits titles over lines). Matching is two-pass: on the TOC page (±1) first, then anywhere, so recurring topics (Verelius 131/262) no longer swap. Vol. II: 131 → 139 found, 8 → 0 misplaced; vol. I unchanged; vol. III 135 of 152 found.
  - **Site:** three volumes, 186 MB; the vol. III EPUB passes epubcheck.
- **2026-11-15, NER review screens:** `scripts/ner_review.py` + `web/ner/`, served by `gt_server.py` at `/ner/` (plan step 3b).
  - **Register:** entries by status and frequency; keys 1–9 choose a Wikidata candidate, Enter confirms an automatic match, 0 = checked without a match, X = rejected. Also rename, merge into another entry (status `merged` + `merged_into`), a new Wikidata search under another name, a manual QID, and a split: tick mentions and move them to another entry or a new one (`manual: true`).
  - **Mentions:** ambiguous mentions grouped by candidate set and chapter, the rule's guess pre-selected; one key per mention, Shift+digit for the rest of a group, Shift+Enter accepts all guesses. Each mention has a scan crop (word found in the text layer's boxes) and a paragraph view in which a missed name can be selected and added (`{"add": true}` decision).
  - **Storage:** entry decisions in `data/register/*.json` (kept by `register.py` reruns), mention decisions in `data/ner/<vol>-decisions.json`. `link.py` now follows merges (refs and candidates), applies added mentions and type changes, and drops merged entries from `register.xml`.
  - **Tests:** on a scratch copy of the vol. I data, in headless Chromium: every action wrote the expected file; `link.py` on the result gave 160 manual links, the added mention, no link to the rejected one, and a merge moving 8 mentions. No console errors, no horizontal scroll at 390 px.
  - **Fix:** `register.merge_existing` crashed on entries new to the register (no `vols` yet); the first vol. III run lost its result (the Wikidata replies were cached).
- **2026-11-20, reader: TOC panel with marginal topics.**
  - **Panel:** the `Innehåll` button opens the printed TOC beside the text: chapters with their marginal topics. Only the current chapter is open; the others fold out with ▸. A scrollspy highlights the topic being read and keeps it in view in the panel. Open by default from 1400 px; below 1100 px the old drop-down is used. The choice is remembered.
  - **Links:** `toc.py` writes `"note": k` for each topic matched to the k-th margin note (document order); `build_site.py` gives those notes `xml:id="sn<k>"` in the site TEI and copies the topics into `volumes.json` (it stops if the chapter counts differ). Topics with no sidenote in the TEI (OCR lost it) are grey italic and link to their page: vol. I 3 of 97, vol. VII 33 of 240.
  - **Fixes:** a sidenote target now scrolls to the top of the pane, not the centre, so the scrollspy picks the clicked topic rather than the one before. `toc.py`: genitive `s’` run into the next word in vol. I's text-layer TOC (`Bureus’ställning`, `Rhezelius’resor`, `Bureus’sista`) and a comma read as `?` that started the next topic (`? Bureus’ laghandskrifter`). Sidenote counts unchanged in all volumes.
  - **Tests:** headless Chromium at 1600, 1280 and 390 px, vols I and VII: no console errors, no horizontal scroll; clicking a topic scrolls to its sidenote and highlights it.
- **2026-11-20, `toc.py`: better topic–sidenote matching and TOC parsing.** Found 1,203 → 1,236 of 1,284 topics; "in body" 31 → 19; misplaced 4 → 2; no topic matched before lost or changed its sidenote.
  - **Matching** (on the TOC page ±1 only):
    - the TOC elides the end of a series ("Inträdestalen. Fredenheims 65, Tengströms 67" for `Fredenheims inträdestal`);
    - a heading in front of the sidenote (`Tävlingarna 1820 och 1821` → `1820 och 1821`);
    - whole-word or truncated prefixes (`1792 och 1793` → `1792`, `Setons arbete för`);
    - one topic printed as two consecutive sidenotes (`Eldsvådan` + `1802`, `Grandel vald` + `Brinkman vald`), used only where no single sidenote matches on its own;
    - two neighbouring topics share one merged sidenote (`1806 och 1807` and `Ydrefyndet`).
    - Hyphenated words are joined before comparing.
  - **Vol. VII's 17 "in body"** were mostly elided series whose sidenotes the TEI does have; the 2026-11-16 notes had taken them for sidenotes dots.mocr dropped. 6 remain.
  - **Parsing:**
    - a year followed by a full stop is a series heading, not a page (`Fynd 1786. Styrafyndet 276`: bogus topics on "pp. 1786–1791" in vol. VI), and the heading is kept as a match form (`Fynd 1786 Styrafyndet`);
    - a year followed by a comma is a topic whose page the OCR lost (vol. II `Riksdagen 1668,`): it takes its sidenote's page (354);
    - no missing-comma split before a date (`Kungörelsen den 10 Maj 1787 324`);
    - vol. VIII `Norrlandsresan 227 229`: the first number is a misread year (1827), not the page.
  - **What is left** in vols VI–VIII "missing" is sidenotes absent from the TEI (vol. VI pp. 1, 112, 115, 302).
- **2026-11-20, `to_tei.py` `rescue_inserted`: sidenotes dots.mocr dropped altogether.**
  - **Rule:** after `rescue_margins` and `rescue_toc`, a TOC topic of the page that is still not a sidenote is looked for among the text layer's margin groups. It matches when:
    - the whole group matches (ratio ≥ 80);
    - the group is a lead plus the topic, with the lead words from the TOC (`Fynd 1755` for the series topic `1755`);
    - or the topic is at the start of a group the text layer ran together.
    - Years must agree.
  - **The note:** the TOC's wording (the text layer's is poor: `Stockkoim`, `tortratt`, `FlemtnS`), placed by the group's box through `place_margin`, with `resp="#toc"` (new respStmt).
  - **Result:** 23 inserted (II 4, III 3, IV 1, V 9, VI 2, VII 2, VIII 2). No paragraph text changed, so the NER links are unaffected; names in the new sidenotes are not linked yet. Placement checked on the scan for vol. II p. 312, vol. V p. 345 and vol. VIII p. 31: right paragraph and line.
  - **`toc.py`:** found 1,236 → 1,259 of 1,284; missing 27 → 10, in body 19 → 15 (vol. III now complete). What is left:
    - TOC wording that differs from the sidenote (vol. VII `Deras uppläsning i akademien` / `Levernesbeskrivningarnas uppläsning i akademien`);
    - years the text layer misreads (`i792`);
    - parse junk from table text (vol. II `Rdr`);
    - vol. IV p. 5, which has no sidenote in print.
  - All eight TEI files are valid. Site and EPUBs rebuilt; epubcheck clean for vols II and V.
- **2026-11-20, NER for the 23 inserted sidenotes:** `ner.py` (22 new units through KB-BERT, the rest cached) → `register.py --offline` → `link.py` → `to_tei.py` for vols II–VIII.
  - **Register:** unchanged (6,841 persons, 2,235 places; no entry or status changed). The only content changes are the new mentions (`Liljegren` +1, `Örnhielm` +1, `Stockholm` +1); the `vols` diffs are unit ids renumbered after each new sidenote.
  - **TEI:** 7 names added, none removed: `Stockholm`, `Örnhielm`, `Liljegren`, `Bergman` (vol. V, cert low, for review), `Biberg` and `Fleming` (vol. VIII p. 31, cert low; right per the paragraph). `Norbergs` (vol. VII) was not tagged by KB-BERT. All valid; `toc.py` counts unchanged; site rebuilt.
- **2026-11-20, manuscripta.se links for K. B. and U. B. shelfmarks.**
  - **Index:** `scripts/manuscripta.py` reads the production dump in `../manuscripta/data` (msDescs + `iiif/<id>/manifest.json`) into `data/sources/manuscripta.tsv`: 864 KB/UUB shelfmarks, 763 digitised. Shelfmarks are normalised to one key (`F h. 28` / `Fh 28` → `FH 28`, `D G 8` → `DG 8`).
  - **TEI:** `to_tei.py` gives a shelfmark of K. B. or U. B. with a digitised record `<idno type="shelfmark" corresp="https://www.manuscripta.se/ms/<id>" facs="<IIIF manifest>">`. 35 citations, 27 manuscripts:
    - KB `Fa 13`, `Fb 5`, `Fb 9`, `Fc 1`, `Fd 10`, `Fe 2`, `Fe 14`, `Fe 21`, `Fe 23`–`25`, `Ff 1`, `Ff 4`, `Ff 5`, `Fh 3`, `Fh 28` (Bureus's and the antiquaries' papers), and `A 53` (Skokloster hymnal), `B 42`, `B 58`, `D 2`;
    - UUB `DG 8` (Olof saga) and `R 683`–`R 715`.
    - Context checked against each record's content and date; all right.
  - **Citation grammar:** `(i K. B. sign. A 53)` is now a citation too, adding 18 `<bibl>` (vol. I 41 → 47). Names unchanged, all valid.
  - **Reader:** the shelfmark is a link (↗) and the Källor list shows `manuscripta.se` beside the citation; explained in "Om".
  - **EPUB:** the shelfmark is a link; epubcheck clean (vol. III).
  - **Tests:** headless at 1600 and 390 px (the site served through Playwright's request routing, no server): 6 links on vol. IV p. 173 ff., no errors, no horizontal scroll.
  - **Known flaw:** `sign. F e. 14 och M 11` is one `<idno>`, linked to Fe 14 only.
  - **Not matched:** most of Schück's K. B./U. B. shelfmarks (`U 89`, `M 11`, UUB `U 111`–`U 126`, `X 211`, Palmskiöld volumes) are not on manuscripta.se.
- **2026-11-20, Riksarkivet links for Kanslikollegium volumes.**
  - **Lookup:** `scripts/riksarkivet.py` takes the citations naming Kanslikollegium with a series + volume shelfmark (`sign. E IV. 9`, `E IX. vol. 24`, a bare `IV. 13` → `E IV 13`; 196 citations, 19 volumes) and looks each volume up in Riksarkivet's search API (data.riksarkivet.se/api/records).
    - The query is `Kanslikollegium E IV 9`: the reference code itself is tokenised and does not find the record.
    - Responses are cached in `cache/riksarkivet/`, one request a second, generic User-Agent. Result in `data/sources/riksarkivet.tsv`.
  - **Found:** 18 of 19 volumes are in the catalogue as `SE/RA/1411/E/<series>/<vol>`. Six are digitised, with IIIF manifests on lbiiif.riksarkivet.se: E IV 8, 9, 10 a, 10 b, 13 and 14, the letters from the heads and officials of the Antikvitetskollegium and Antikvitetsarkivet (169 of the 196 citations).
    - `E XIII. 21` (vol. V, 4 citations) is not in today's catalogue: the series has 16 volumes.
    - All 14 citations with a year fall inside their volume's date range.
  - **TEI:** `to_tei.py` gives the `<idno>` `@corresp` (the sok.riksarkivet.se record) and, where digitised, `@facs` (the manifest). 207 shelfmarks: vol. II 2, IV 120, V 85; all valid; names unchanged.
  - **Reader:** the shelfmark links out. The tooltip says whether the volume is digitised, and the Källor list labels it "Riksarkivet"; explained in "Om".
  - **Checks:** epubcheck clean (vol. IV). Headless test of vol. IV: 120 links, no errors. One manifest fetched: E IV 9, 381 images.
  - **Note:** the record URL redirects scripts to a captcha; browsers reach the NAD record.
  - **Not covered:** `Skrivelser till De la Gardie Ser. C` (56 citations, vols I–III) is in another archive and still needs mapping; so do `Riksregistraturet` and `Kanslikoll. prot.` (no volume numbers cited).
- **2026-11-20, De la Gardie letters at Riksarkivet.**
  - **Where they are:** De la Gardieska samlingen is `SE/RA/720222`; its EAD was read through OAI-PMH.
    - Schück's "Skrivelser till De la Gardie. Ser. C 1" is series C I: 280 volumes of letters to M. G. De la Gardie, ordered by writer (`E 1425` Haak – Hadorph, `E 1338` Aspegren – Axehielm, …).
    - "Antikvitetskollegiets skrivelser till De la Gardie" is C IX vol. `E 1626`: Antiqvitetskollegium 1668–1670, 1672, 1674–1675 and 1677. The dated citations fit.
    - None of these volumes is digitised.
  - **Code:** `riksarkivet.py` `dlg_key()` names the volume where the citation says which it is: the Antikvitetskollegium, or the writer Hadorph or Axehiälm. `DLG_VOLUMES` gives the reference code and the search query for each.
  - **TEI:** these citations have no `sign.` shelfmark, so `to_tei.py` puts `@corresp` on the `<bibl>`. 28 citations (vol. II 19, vol. III 9).
  - **Reader:** a ↗ link after the citation, "Riksarkivet" in the Källor list, a sentence in "Om".
  - **Not linked:** about 40 "Ser. C 1" citations that name no writer (the writer is in the surrounding sentence, if anywhere), and "De la Gardies koncept" (series A I 1, 88 volumes).
  - **Checks:** all valid; names unchanged; epubcheck clean (vol. II); headless test of vol. II: 19 links, no errors.
- **2026-11-21, the academy's archive (V. H. A. A.): minutes and royal letters linked.**
  - **Where it is:** in Riksantikvarieämbetet's archive (ATA), not in Riksarkivet's catalogue (searched: only SE/RA/757/03 "Antikvitetskollegiet och antikvitetsarkivet" and Kanslikollegium extracts).
    - Ämbetsarkiv 1 is the Antikvitetskollegium and Antikvitetsarkivet: minutes 1744–1751 (A 1/1), outgoing letters, incoming letters, inventories, accounts, publishing, finds (F 2).
    - Ämbetsarkiv 2 is the academy from 1786: minutes A 1 a/1–80 (1773–1922), draft minutes A 1 c/1 (1786–1837), agendas, drafts B 1, royal letters E 1/1–18 (1786–1924), prize essays, members, the manuscript collection F 16.
    - The RiViH project (RAÄ + KB, 1630–1880) has digitised them. 398 records are on manuscripta.se, all with IIIF, and are also in RAÄ's Arkivsök (`SE/ATA/ARK2_1-1/...`).
  - **Index:** `scripts/manuscripta.py` also takes RAÄ's A 1 a and E 1 volumes (holder RAA, key `A 1 a/3`, years from the record title). That adds 98 rows; KB/UUB are unchanged.
  - **TEI:** in vols VI–VIII, a `<bibl>` that opens with `Prot.`, `Akademiens protokoll`/`akad. prot.` or `Kungl. brev` and gives a year gets `@corresp` (the manuscripta record) and `@facs` (the manifest) of the volume covering the first year. 83 citations: vol. VI 11, VII 39, VIII 33. Each was checked by year against its volume's range. Nothing outside `<bibl>` changed; all valid.
    - Not in vol. V: there "Prot." is Kanslikollegium's minutes (context: "kollegiet beslöt", 1752, 1782–1785). In vols III–IV it is also not the academy's ("Prot. i rådskammaren").
  - **Not linked:**
    - 13 citations with no year (`Prot. 12 okt`).
    - Two that span volumes link only to the first (`Prot. 1825 22 nov., 1828 17 jan.`).
    - Schück's old shelfmarks for Ämbetsarkiv 1 (`Antikvitetskoll. Handl. V–XI`, `sign. Aa 11`, `Ab 3`, `Högre tjänstemän`, `Rumförhållanden`): there is no concordance to today's series. Vol. II has 89 such citations.
  - **EPUB:** a `<bibl>` with `@corresp` gets a ↗ link after it (this also covers the De la Gardie letters). epubcheck clean (VI, VII, VIII).
  - **Reader:** "Om" explains the links. Headless test of vol. VIII at 1600 and 390 px: 30 links on the first rendered stretch, no errors, no horizontal scroll.
- **2026-11-21, the remaining sidenotes: every TOC topic matched.**
  - **Result:** `toc.py` found / topics per volume, before → after: I 94 → 97/97, II 150 → 150/150 (two junk topics gone), III 153/153, IV 141 → 142/143, V 166 → 168/170, VI 174 → 177/177, VII 230 → 240/240, VIII 151 → 152/152.
    - Left: vol. IV p. 5 (no sidenote in print); vol. V `Hemse stavkyrka 419` (p. 519) and `Wessmans resa 1756 448` (p. 446), both TOC errors, checked on the scans.
  - **Column edges (`regions.py`):** `dots_blocks` takes the text column's left and right edges from blocks at least half as wide as the widest, not from all Text blocks. On pages with few paragraphs the sidenotes had pulled the median into the margin (vol. VII p. 27: right edge 1220, so the sidenote at x 1173 counted as body, printed as `Den arbe-` plus a stray `tande klassen`).
    - Across all volumes, 98 blocks moved body → margin (real sidenotes, several formerly cut short: `Flintenberg efter`, `Övriga`, `Ekdahl och Kammar-`).
    - 34 moved margin → body: letter signatures and datelines (`Johannes Hadorphius.»`, `Upsala den 9 Junij A:o 1668.`), which had been false sidenotes.
  - **Ground-truth pages:** their sidenotes were dropped, because GT paragraphs have no boxes for `place_margin`. They are now placed by the first words of the text-layer line beside them, with the position taken from the dots.mocr margin block of the same text or from the text layer's margin words. Vol. I: `Verelius' biografi` (p. 217), `Rhezelius` (p. 159; added to the GT file, which had missed it).
  - **Years (`rescue_margins`):**
    - The text layer's oldstyle figures are read as years: `i 797 — i 799`, `J793`, `Fynd ijjo`, `Fynd IJJ7` (`oldstyle_years`).
    - A bare-year sidenote is cut from the body only where the text layer's body matches at least 5 points better without it (any gain at a block start). Otherwise it is inserted from the text layer (`resp="#textlayer"`).
    - Gains: merged sidenotes +8 to +17 (`prisämne 1789—1792 för`, `Den 17 januari 1804 skrives`); the text's own years −1.5 to −9 (`för 1793 måste`, `under 1818`, `till 1792`).
    - Fixed: vol. VI pp. 112 (`1792` inserted), 113 (`1793`, formerly cut from `för 1793 måste`), 124, 105 (year at a block end), 115; vol. VII p. 46, p. 301 (`1818`).
  - **Prices:** text-layer margin groups without a word of three letters (prices `Dr 1.8.`, inventory numbers `[N. 39`) are no longer rescued. They had been cut out of quoted accounts and inventories (vol. IV pp. 586–587, vol. V pp. 365–385). On those pages the remaining marginal annotations of the quoted inventories are now sidenotes, not figure text (TODO).
  - **`rescue_inserted`:**
    - A text-layer group that is the start of a TOC topic (`Utomståendes` → `Utomståendes avhandlingar`).
    - A TOC genitive in a series (`Regnérs` → sidenote `Regnér`).
    - A garbled one with the topic's first word (`Det Gustavigravk°-`).
    - Series sidenotes that differ only in their year are no longer taken as already present (`Fynd 1776` / `Fynd 1777`).
  - **`rescue_toc`:** a TOC topic whose first words open a paragraph is cut there when the text layer agrees (vol. I p. 116 `Riksarkivets »1590»` → sidenote `Riksarkivets första tid`, `resp="#toc"`). A bare year may also be cut at a block end.
  - **`place_margin`:** when the sidenote's y is on the boundary of two blocks, it takes the block that holds the text-layer line's first word (vol. VII p. 174, `Regnér` was one line early).
  - **`toc.py`:**
    - Stops at `RÄTTELSER` (the errata on vol. II's last TOC page).
    - Matches a sidenote that holds two topics as sentences, abbreviations or with the series word (`Fornvården. Ekdahl …`, `De arbetandes. Sundels`, `Antikviteter 1794` for `I antikviteter`).
    - Also matches `1807 och 1809` = `1807—1809`, a pronoun for the head noun (`Deras uppläsning` = `Levernesbeskrivningarnas uppläsning`), a shared verb across a pair (`Blad vald` + `Palin vald` = `Blad och Palin väljas`), and a year span over two sidenotes (`1797-1803`).
  - **NER chain rerun for all volumes:** only changed units went through KB-BERT. Names are equal or slightly more (vol. V +20, from inventory text no longer in figures). The register swaps 14 unreviewed single-mention or unmatched entries for 13 of the same kind; reviewed entries are unchanged.
  - **Checks:** all TEI valid; epubcheck clean (I, V, VII, VIII). Headless reader at 1600 and 390 px: every TOC topic in vols I and VII links to an existing sidenote, no errors, no horizontal scroll.
- **2026-11-21, overhanging line ends are not sidenotes.**
  - **The problem:** the user pointed out that vol. II p. 321's "sidenotes" `oinbundne».` and `utskurit.` belong to the quotation. That page has no sidenotes at all.
    - The text layer files the end of a line that runs past the column as margin; here four lines of a quoted inventory.
    - `rescue_margins` cut those words out of the quotation.
  - **Fix:** `tl_margin_groups` keeps only text-layer margin groups set smaller than the body, as sidenotes are.
    - Real sidenotes measure 0.69–0.80 of the body's line height (`Regner` 22/28, `Rhezelius` 22/30, `Fynd ijjo` 24/30); line ends measure 0.93–1.0.
    - Groups with a year may be up to 0.97, because oldstyle figures make the box taller (`i792` 0.94, `Consignât™- nen 1673` 0.90).
    - Used by `rescue_margins`, `rescue_inserted` and the GT placement.
  - **Result:** 42 false sidenotes removed, their words back in the text.
    - Vol. II: p. 321 (4), `Eders`, `in folio.`.
    - Vol. III: a quoted list's left column (`H. Rålambz`, `Joh. Magnus`), `XII)`, `måste`, `förfärdigas`.
    - Vol. IV: `Elias` (a hanging indent), a plate caption.
    - Vol. V: `Secr.` ×7, `Ass.` ×3, `(Inv. N. 1)` ….
    - Vol. VII: `Brynte`, `kallad.`; vol. VIII: `VII.`.
    - The doubtful ones were checked on the scans. No TOC topic lost; all valid; the NER chain was rerun (5 unreviewed single-mention entries gone).
  - **Kept:** vol. V pp. 373, 385 have real marginal annotations to a quoted inventory, set small in the margin. They are tagged `type="summary"` (TODO).

### 2026-11-21, Berch's inventory hand-encoded
- User: the margin notes on vol. V pp. 373 and 385 are additions in the quoted manuscript ("Tillägg, nedskrivna efter Berchs tid, äro här tryckta med mindre stil", p. 364). Most additions had floated into the running text, and the entries were badly segmented.
- New hand-encoding layer: `data/edit/<vol>-*.txt`, read by `to_tei.py` `load_edit`. Its pages replace the OCR blocks (`resp="#edit"`, a new respStmt written only where used).
  - Entry line `e NUM | YEAR | TEXT`; markup `⟦…⟧` / `⦃…⦄` → `<add place="inline|margin">`, `//` → `<lb/>`.
  - Spans go through `serialize`, so NER hashes still match. A name may not cross an addition's edge (the mention "Kannenberg K." was dropped).
- `1936-inventory.txt`: pp. 364–385, 22 pages, 353 entries, 8 margin additions. Encoded from the dots.mocr text against the scans by four parallel forks, about 5 pages each.
- Reader: `label` and `add` (margin additions float as sidenotes, set smaller and not in italics), and a ✎ page marker. The EPUB gets the same; "Om" explains it.
- Vol. V's TOC is unchanged (168 + 2 TOC errors). TEI valid, epubcheck clean. The other seven TEI files are byte-identical.
- Register: 7 OCR-junk persons gone (Agnos, Christaller, Alaska, Susters …), 3 junk added (Pateen, Smörjelse Kahr, Kannenberg K); all unreviewed, none with a QID.
- User: the years printed in the outer margin had been run into the text. They are now `<label type="year" place="margin">`; the reader floats them into the margin as sidenotes (set in roman), on mobile right-aligned on the entry's first line, and the EPUB floats them right. Valid, epubcheck clean.
- Inventory quoted: `q+`/`q-` lines in the edit format. Every paragraph, heading and table cell between them is a `<quote>` part, chained by `@next`/`@prev` as for multi-paragraph quotations. Headings go through `flush_para` (`para_el`), cells through `quoted()`. Schück's note on p. 384 stays outside, so there are 2 chains: 416 + 16 parts. "Citat" shows it as one entry; quotation-only search finds e.g. "Wadstena" in it. Vol. V's quotes: 2,209 → 2,641 parts. Register unchanged (unit renumbering only).

### 2026-11-22, vol. I's unreadable pages hand-encoded; figure captions
- `data/edit/1932-figures.txt` covers the four pages dots.mocr failed on, which had text-layer junk in the TEI (running heads `Kosim`, `~~— C m i Srâp. —`, plateText from the Runahäfd leaf, a run-together TOC). pp. 48–49 (runic alphabet tables) are figures with their captions only; image 82 (Runahäfd's first leaf) an empty plate figure; image 314 (TOC, first page) transcribed. User: pictures and plates are not transcribed.
- Edit format: `fig CAPTION`, `sig TEXT`, `=== IDX [48]` (page in the count, number not printed), `=== IDX -` (plate). The plate and contents branches of `to_tei.py` now read edit pages too. The `#edit` respStmt and editorialDecl are worded for both uses.
- Captions of pictures printed in the text were never output: `regions.py` tagged them `caption`, the body path rendered only `figure`. Now a Caption block, or Page-footer text dots.mocr put just under a picture (vol. I p. 50 `Sidopartierna i Runtavlan 1599.`), becomes the figure's `<head>`; one caption under several pictures makes them one figure. 7 captions restored (vol. I 3, vol. II(a) 2, vol. IV 3 incl. the Peringskiöld portrait).
- A page of pictures puts its `<pb>` before the figure, inside the open paragraph, so the paragraph continues on the next text page: vol. I pp. 46→51 (`återgivit` … `»det ulfilanska alfabetet»`), vol. IV pp. 4→6.
- `ner.py`: a `<figure>` inside a paragraph was read into the paragraph's unit, so neither matched its hash in `to_tei.py` and both lost their names. Its head and abs are now units of their own. NER chain rerun for vols I, II(a), II(b), IV, V: names 3,783 → 3,955, 4,399 → 4,499, 5,029 → 6,214 (1,041 of them in the ten figText figures of vol. II(b), uncorrected OCR of lists of signatories), 5,866 → 6,006, 7,321 → 7,392. Register: TOC junk gone (`Johannes Bureus’sista`, `N TORT`, `Barfrid Vestibulum`); `Arngrímur Jónsson` and `Brynjólfur Sveinsson` in the TOC now link to the text's entries. All eight TEI files valid; vols VI–VIII byte-identical; site built.
- User: the plate and figure OCR text goes. `to_tei.py` no longer writes `<ab type="plateText">` (plates whose lettering OCR read, ≥ 300 characters) or `<ab type="figText">` (in-text pictures with many "sidenotes"); the figure keeps its printed caption only. The many-sidenotes test stays, so such lettering is not taken for sidenotes. NER chain rerun for all eight volumes, then register/link/TEI once more (stable). Names: vol. I 3,852, II(a) 4,427, II(b) 5,043, IV 6,003, V 7,392, VI 4,662, VII 5,956, VIII 5,749; vols VI–VIII gained names in paragraphs that hold a plate, which had the same `ner.py` unit mismatch. All valid; site built.
- `toc.py` reads a hand-encoded TOC page (data/edit) before dots.mocr and the text layer, as clean lines like dots.mocr's. Vol. I image 314: topics now as printed (`goticismen`, not the text layer's `göticismen`; `Johan III:s`; `Aschaneus’ biografi`; `Arngrímur Jónsson`, `Brynjólfur Sveinsson`), still 97/97 found. A topic loses an end `»` only when unbalanced, so `Runorna »extra patriam»` keeps its closing one. The edit file uses `’` as printed. Other volumes' topics and reports unchanged. NER chain for vol. I: `Westhius` in the TOC now tagged. Note: `register.py` overwrites `context_year` (and candidates) of skipped entries with the last-run volume's, so the register diff depends on run order (46 entries this time); not fixed.
- `register.py`: `context_year` no longer depends on which volume ran last. Each volume's slice keeps its year counts (`vols[vol].years`, years 1300–1899 in the units that mention the entry); the entry's `context_year` is their median over all volumes, recomputed at every merge, and the Wikidata lookup uses the same combined year (other volumes' years read from the existing register). `vols` is kept sorted. Checked: all eight volumes run forward and then in reverse give byte-identical persons.json and places.json. 804 of 6,785 context years changed (e.g. Gustav Adolf 1630 → 1680 over seven volumes); no status, candidate or QID changed; TEI unchanged. persons.json 3.8 → 4.4 MB. Still per-volume: the `--min-mentions` test counts only this volume's mentions, so a person named once in each of several volumes is never looked up.
- `register.py`: the `--min-mentions` test counts an entry's mentions in all volumes (the other volumes' slices read from the register), for persons and places. To keep the result independent of run order, (1) an entry is looked up under its most frequent normalised name over all volumes (new slice `names`; the register's name follows it, except for reviewed entries, whose name stays), (2) machine decisions (skipped/none/review/auto) take each run's lookup, which uses all volumes' counts, names and years; reviewed ones (checked/rejected/merged) are never touched. Offline, a lookup whose Wikidata replies are not cached leaves the entry `skipped` instead of deciding `none` on no data (several old `none` were such misses). Checked: forward and reverse runs over the eight volumes give byte-identical persons.json and places.json. Persons: auto 423 → 479, review 635 → 681, none 1,258 → 1,175, 94 QIDs changed (mostly gains: Berzelius, N. J. Ekdahl, Brenner, Olof Celsius, Anders af Botin; some losses: Isak Collijn and Augustin Ehrensvärd auto → review, `Jan Andersson` → Jan Andersson i Jönvik 1819–1903 against context year 1716); 62 names changed. Places: auto 98 → 117, 19 QIDs changed. TEI unchanged. Pending an online run (skipped, ≥ 2 mentions in all volumes, replies not cached): 268 persons, 139 places.
- Found on the way, not fixed: `pers-adolf-gustavus` merges Gustav II Adolf and Gustav IV Adolf (full names cluster on Latinised forename + surname; the regnal number is dropped). Its QID now goes to Gustav II Adolf.

### 2026-11-22, abbreviated forenames in NER; duplicate entities
- `ner.py` `join_abbreviated`: KB-BERT ends a person at an abbreviated forename's stop ("Carl Reinh." | "Berch"; 17 signatures in vol. V made a junk entry `Carl Reinh`). A person mention now runs on to the capitalised word after `.` or `:` when its last word is an abbreviation: printed with a stop at least 4 times in 5 in the volume (so not "Dalin.", "Carl.", "Fredrik I."), not a title (Hr., Mag., Frih., Gr., Cantz., Exc., K. = Konung …), and a proper prefix of a forename (FORENAMES, or a forename seen ≥ 2 times inside the volume's person mentions; 2 letters if the model tagged the next word, else 3), or an initial after another part of the name ("J. G" + "Sparfwenfeldt"). A lone initial is not joined: it is often a title ("P. Verelius" = Professor, "M." = Magister). Not before a roman numeral or a sentence word (Den, Han, Men …). 212 joins over eight volumes; the covered mention is dropped.
- Duplicate entities: `run_model` ran every copy of a repeated text (a signature, "HENRIK SCHÜCK") and cached the hits once per copy; `to_tei.py` pooled links by text hash, so a name in a repeated text was wrapped in up to eight nested identical `persName`s (vol. V: 392 nested pairs, 642 tags for `Carl Reinh` alone). Now identical texts run once, `mentions()` drops repeated spans from old caches, `load_ner` keeps one hit per span. No nested names left; outermost names unchanged except for the joined pairs.
- `register.py`: names printed in capitals (plate captions, title pages) are title-cased before clustering ("JOHAN GUSTAF LILJEGREN" was its own entry); `ABBREV` takes up to five letters and `:` ("Claud.", "Reinh:"); an abbreviated cluster whose initials start no full name merges into the one whose initials contain them in order ("G. Liljegren" → Johan Gustaf, "J. Ekdahl" → N. J. Ekdahl); FORENAMES gains Joannes and Johann; "Professorn" is a title; an entry's name prefers forenames written out ("Joannes Schefferus", not "Joh. Schefferus"). Forward and reverse runs still byte-identical.
- Effect: links 42,081 → 41,714 (duplicates and joined pairs), unresolved 1,437 → 1,418, rule guesses 3,091 → 3,213. The extra guesses are ambiguities the capitals had hidden, where the old link was wrong or arbitrary: bare `Verelius` in vol. II(a) went to Samuel Verelius (167, cert medium), now mostly guessed Olaus; `Nordin` (vol. VI) to Carl Magnus, now C. G. Nordin competes; `Brenner` (Elias / Henrik), `Celsius` (Magnus / Johannes). Junk entries gone: Carl Reinh, Professorn Berzelius, Tiensttrogne Joh, Schefferus Joh, Alex, HENRIK SCHÜCK (→ Henrik Schück). Persons 6,785 → 6,759. Still a duplicate entry for review: `Claud. Örnhielm` (vol. II(b), 63) beside Claudius Örnhielm (vol. II(a)); per-volume clustering cannot see the full form in another volume. All TEI valid; site built.

### 2026-11-22, reader: Citat and Källor for all volumes
- User: Citat and Källor can list the whole work. Each has an "alla delar" checkbox (remembered per viewer in localStorage); ticked, the list loads every volume as the search's "alla delar" does, with a progress line, and labels each item with its volume. Källor groups by archive across volumes; Citat shows the first 1,000 of 8,229 and says so. A newer render (typing while volumes load) cancels the stale one. "V. H. A.A." groups with "V. H. A. A.". Tested in headless Chromium: 8,229 quotations and 1,342 citations in all; filter "Wadstena" finds 32 quotations in seven volumes; a vol. VIII citation clicked from vol. I opens vol. VIII at the citation; no overflow at 390 px; no script errors. Open: "U. B." (122) and "Uppsala universitetsbibliotek (U. U. B.)" (2) are separate groups; whether Schück's "U. B." is always Uppsala is unchecked.

### 2026-11-22 — Online Wikidata run
- `register.py --vol V` online for all eight volumes (13:04–14:35, cache 6079 → 6956 replies), then an offline pass. A second online pass fetched 11 lookups whose query name had changed when later volumes ran (`best_name` reads every volume). Offline, no lookups are left uncached.
- Persons: skipped 4459 → 4166; auto 477 → 500, review 673 → 763, none 1150 → 1330. Places: skipped 1702 → 1563; auto 117 → 142, review 240 → 305, none 175 → 224. No existing QID changed. The remaining skipped entries are under the min-mentions threshold.
- Links unchanged (41,714; 3,213 low, 1,418 unresolved). The TEI validates for all volumes and the site is rebuilt.
- New auto matches to check: Nescher → Caspar Netscher, Knut Eriksson (no context year) → Q5716814, Lars Olsson (1807) → Lars Olsson i Groröd, Henrik Schück (1673) → the author.

### 2026-11-22 — Published on GitHub Pages
- Public repository https://github.com/PatrikGranholm/Schuck-digital; `main` pushed with README, PLAN, TODO, `gt/CONVENTIONS.md` and a new `LICENSE` (code MIT, edition data CC0 1.0, source text and images public domain). `docs/` and `report/` stay untracked. History checked first: no secrets, largest blob 4.9 MB.
- `scripts/deploy_pages.sh` pushed `site/` (555 MB, 4,076 files) to `gh-pages`; GitHub enabled Pages on that branch by itself. Live at https://patrikgranholm.github.io/Schuck-digital/, which the user has checked.

### 2026-11-22 — Four auto matches reviewed
- Henrik Schück confirmed. Knut Eriksson: the politician Q5716814 → the king Q320977. Nescher: Caspar Netscher → Daniel Georg Nescher, Q16649975 (identified by the user), renamed. Lars Olsson (→ Lars Olsson i Groröd) split into Lars Olsson i Gröndal (vol. V) and Lars Olsson i Allmänninge (vol. VIII, new manual entry, `1944-decisions.json`), both with no QID. Applied with `ner_review.py`'s functions; `link.py` and `to_tei.py` were rerun for the six volumes (all valid; only vol. VIII's TEI and `register.xml` changed), and the site was redeployed.
- Cause, not fixed (TODO): `person_candidates` drops births before 1300, and `wbsearchentities` matches label prefixes, so a bare surname cannot find a full label. 83 bare-surname `auto` entries need rechecking.
