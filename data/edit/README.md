# Hand-encoded passages

`to_tei.py` uses a file here instead of OCR for the pages it covers (pb/@resp="#edit").
Text is the OCR's, corrected against the page image; the structure (entries, item numbers,
later additions) is encoded by hand. One file per passage: `<vol>-<name>.txt`.

## Format
One line per element. Blank lines end a table; lines starting with `#` are comments.

| line | meaning |
|---|---|
| `=== IDX PAGE` | a new page: image index (4 digits) and printed page number |
| `head TEXT` | running head |
| `+ TEXT` | continues the paragraph or entry from the previous page (first line of a page only) |
| `p TEXT` | prose paragraph (Schück's text) |
| `h TEXT` | heading line |
| `t CELL \| CELL` | table row; consecutive rows form one table |
| `e NUM \| YEAR \| TEXT` | an entry of a quoted inventory: item number as printed (`[N. 6.]`, `[392.]`, may be empty), the year printed beside it in normal type (`[1709.]`, `[(1703)]`, may be empty), and its text |
| `foot N TEXT` | footnote N; its anchor is `^N` in the text |

Inline markup (besides `{italic}`, `<letterspaced>` and `^1` as in gt/CONVENTIONS.md):

| markup | meaning |
|---|---|
| `⟦…⟧` | small type in the text column: a later addition (TEI `<add place="inline">`) |
| `⦃…⦄` | small type in the margin: a later addition (TEI `<add place="margin">`), placed where its first line stands beside the text |
| `//` | a printed line break that starts a new line on purpose (not wrapping) |
| `⦗…⦘` | Schück's own marginal summary (TEI `<note place="margin" type="summary">`) |

End-of-line hyphenation is removed; spelling is kept as printed.

## Files
- `1936-inventory.txt`: vol. V pp. 364–385, Berch's inventory of the antiquities
  ("Upteckning på de uti Kongl. Antiqv. Archivo förwarade gamla Minnesmärcken"). Schück p. 364:
  "Tillägg, nedskrivna efter Berchs tid, äro här tryckta med mindre stil."
