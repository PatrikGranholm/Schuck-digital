# Ground-truth conventions (page level)

One UTF-8 file per sampled page: `gt/pages/<vol>/pNNNN.txt` (NNNN = PDF image index,
see `gt/sample.tsv`). Drafts are in `gt/drafts/<vol>/pNNNN.txt` (from `scripts/gt_draft.py`);
copy a draft to `gt/pages/` only after checking every line against the image.

## Regions
Each region starts with a marker line; the lines that follow belong to it until the next marker.

| marker | content |
|---|---|
| `#head` | running head |
| `#body` | main text, including block quotations and small-type asides, in reading order |
| `#margin` | one marginal sidenote per line group; separate sidenotes with a blank line |
| `#foot` | footnotes |
| `#pageno` | printed page number |
| `#other` | captions, plate text, anything else |

## Transcription
- **One paragraph per line.** End-of-line hyphenation is removed (`kolle-|giet` → `kollegiet`). A hyphen that belongs to the word is kept (`1660-talet`, `skrif- och`); check the image when the break falls on a hyphen. Each block quotation, numbered item and footnote is its own paragraph. A paragraph continued from the previous page starts the `#body`. Line-level ground truth for kraken comes later, from aligning this text to segmented lines (cf. schlyter-digital `align_text.py`).
- **Spelling** is kept exactly as printed, including 17th–18th c. forms (`hafwer`, `uhr`, `Tullfrijheet`), `æ`, `œ` and the editor's `[ ]`. Nothing is normalised.
- **Quotation marks** are typed as printed: `»…»` (Swedish style; the closing mark is also `»`), and `’` for the apostrophe.
- **Figures:** oldstyle figures are typed as ordinary digits (`1674`, `15)`).
- **Italics:** `{…}` around the italic run, e.g. `{inom}`. Spaces and punctuation inside a run belong to it.
- **Letterspacing** (spärrning): `<…>`, e.g. `<Hadorph>`. The text inside is typed without the extra spaces.
- **Small type** (asides and quotation commentary set smaller) is not marked; the region is enough.
- **Footnote references:** `^1` at the anchor in `#body`. The footnote in `#foot` starts with its number: `1 Hoephæus …`.
- **Illegible:** `?` for one character, `[...]` for more. Use these only when the image itself is unreadable.

`scripts/evaluate.py` scores at two levels:
- **diplomatic:** all characters, with markup removed.
- **normalised:** case, quote and dash style folded; whitespace collapsed.

Italics and letterspacing are scored separately, as F1 over marked characters, for engines that output them.
