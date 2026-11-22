# Hand-encoded passages

`to_tei.py` uses a file here instead of OCR for the pages it covers (pb/@resp="#edit"): quoted
documents whose structure OCR loses, and pages dots.mocr could not read. Text is the OCR's,
corrected against the page image; the structure (entries, item numbers, later additions) is encoded
by hand. Pictures and plates are not transcribed, only their printed captions. One file per
passage: `<vol>-<name>.txt`. A page may be in the body, a plate, or a table-of-contents page.

## Format
One line per element. Blank lines end a table; lines starting with `#` are comments.

| line | meaning |
|---|---|
| `=== IDX PAGE` | a new page: image index (4 digits) and printed page number; `[48]` for a page in the count but without a printed number, `-` for a plate or other unnumbered leaf |
| `head TEXT` | running head |
| `sig TEXT` | printer's signature at the foot (`4-31736.`) |
| `fig TEXT` | a picture, with its printed caption (may be empty). In the body it goes inside a paragraph that runs past it; on a plate the caption is the figure's head |
| `+ TEXT` | continues the paragraph or entry from the previous page (first line of a page only) |
| `p TEXT` | prose paragraph (Schück's text) |
| `h TEXT` | heading line |
| `t CELL \| CELL` | table row; consecutive rows form one table |
| `e NUM \| YEAR \| TEXT` | an entry of a quoted inventory: item number as printed (`[N. 6.]`, `[392.]`, may be empty), the year printed beside it in normal type (`[1709.]`, `[(1703)]`, may be empty), and its text |
| `foot N TEXT` | footnote N; its anchor is `^N` in the text |
| `q+` / `q-` | a quoted document starts / stops: every paragraph, heading and table cell between them is a `<quote>`, the parts chained with `@next`/`@prev` like any quotation that runs over several paragraphs. Schück's own interjections go outside (`q-`, `p …`, `q+`) |

Inline markup (besides `{italic}`, `<letterspaced>` and `^1` as in gt/CONVENTIONS.md):

| markup | meaning |
|---|---|
| `⟦…⟧` | small type in the text column: a later addition (TEI `<add place="inline">`) |
| `⦃…⦄` | small type in the margin: a later addition (TEI `<add place="margin">`), placed where its first line stands beside the text |
| `//` | a printed line break that starts a new line on purpose (not wrapping) |
| `⦗…⦘` | Schück's own marginal summary (TEI `<note place="margin" type="summary">`) |

End-of-line hyphenation is removed; spelling is kept as printed.

## Files
- `1932-figures.txt`: vol. I, the four pages dots.mocr failed on: pp. 48–49 (Bureus' runic alphabet
  tables, captions only), the first leaf of Bureus' Runahäfd (plate, image 82, no caption) and the
  first page of the table of contents (image 314).
- `1936-inventory.txt`: vol. V pp. 364–385, Berch's inventory of the antiquities
  ("Upteckning på de uti Kongl. Antiqv. Archivo förwarade gamla Minnesmärcken"). Schück p. 364:
  "Tillägg, nedskrivna efter Berchs tid, äro här tryckta med mindre stil."
