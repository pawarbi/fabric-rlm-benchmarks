---
applies_when:
  keywords:
  - docx
  - word
  - document
  - report
  - paragraph
  - heading
excludes: []
depends_on:
- validation
specificity: domain
---
# docx_documents
Summary: Read and write Word (.docx) documents with python-docx, including tables that carry the real data.
Dependencies: validation

## Dependency check - do this first

`python-docx` is not a fabric-rlm dependency, because most tasks never touch
Word. It is installed in the notebook environment, not by you:

```python
# notebook cell, before running the RLM
%pip install python-docx
```

**You cannot install it yourself.** `subprocess` and `pip` are blocked inside
the sandbox, so attempting a runtime install wastes a turn and fails with
`SecurityPolicyViolation`. Check once, then take the right branch:

```python
try:
    from docx import Document
    HAVE_DOCX = True
except ImportError:
    HAVE_DOCX = False
print("python-docx available:", HAVE_DOCX)
```

If `HAVE_DOCX` is False you can still **read** a .docx with the standard
library, because the format is a zip of XML. You cannot write one this way.

```python
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def docx_tables(path):
    """Return each table as a list of rows of cell strings, stdlib only."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    tables = []
    for tbl in root.iter(f"{W}tbl"):
        rows = []
        for tr in tbl.findall(f"{W}tr"):
            cells = []
            for tc in tr.findall(f"{W}tc"):
                cells.append("".join(t.text or "" for t in tc.iter(f"{W}t")).strip())
            rows.append(cells)
        tables.append(rows)
    return tables
```

Parse the XML with ElementTree, never with regex. Word nests `w:tbl` inside
table cells for nested tables, and a regex for `<w:tr>...</w:tr>` matches
across those boundaries and silently merges rows from different tables.

If the task requires *writing* a .docx and python-docx is unavailable, say so
in your final answer rather than returning an empty result that looks real.

## Gotchas

- Most business data in a .docx lives in **tables**, not paragraphs. Reading only
  `doc.paragraphs` silently returns headings and prose while missing every number.
- Merged cells repeat their text in each underlying cell. Deduplicate before
  counting rows, or a merged header inflates your record count.
- A table cell can hold multiple paragraphs. `cell.text` joins them with newlines;
  strip and normalize before comparing to spreadsheet values.
- Nested tables exist. `cell.tables` is non-empty when a cell contains one.
- `.doc` (legacy binary) is NOT readable by python-docx. Detect it and say so
  rather than emitting an empty result that looks like a real answer.
- Numbers in Word are text. `"1,234.50"`, `"1 234,50"`, and `"(500)"` all need
  explicit parsing; the last means negative in accounting style.

## Reading

```python
from docx import Document

doc = Document(path)
print("paragraphs:", len(doc.paragraphs), "tables:", len(doc.tables))
for ti, t in enumerate(doc.tables):
    print(f"table {ti}: {len(t.rows)} rows x {len(t.columns)} cols")
    for r in t.rows[:3]:
        print("   ", [c.text.strip() for c in r.cells])
```

Inspect first, then extract. Print the shape and the first rows of every table
before deciding which one holds the data the task refers to.

To pull a table into pandas:

```python
import pandas as pd

rows = [[c.text.strip() for c in r.cells] for r in doc.tables[i].rows]
df = pd.DataFrame(rows[1:], columns=rows[0])
```

Watch for duplicate column names produced by merged header cells; rename before
using them as keys.

## Writing

```python
from docx import Document

doc = Document()
doc.add_heading("Title", level=1)
doc.add_paragraph("Body text.")

t = doc.add_table(rows=1, cols=len(headers))
t.style = "Table Grid"          # without a style the table has no visible borders
for j, h in enumerate(headers):
    t.rows[0].cells[j].text = str(h)
for record in records:
    cells = t.add_row().cells
    for j, v in enumerate(record):
        cells[j].text = "" if v is None else str(v)

doc.save(out_path)
```

- Set `t.style = "Table Grid"` unless the task says otherwise; a borderless table
  usually reads as broken.
- Write every value as a string. python-docx does not coerce, and assigning a
  non-str to `cell.text` raises.
- Preserve the source's own text verbatim. Do not title-case, re-round, or
  reformat values that came from the input.

## Editing an existing document

Open the original and modify in place rather than rebuilding, so styles, headers,
and footers survive:

```python
doc = Document(src_path)
for t in doc.tables:
    for row in t.rows:
        for cell in row.cells:
            if cell.text.strip() == old:
                cell.text = new
doc.save(dst_path)
```

Deleting a row needs XML surgery, since python-docx has no public API:

```python
row._element.getparent().remove(row._element)
```

## Correctness spot-check before SUBMIT

1. Reopen the file you wrote with `Document(path)` and print table shapes.
2. Confirm the row count matches what the task asked for, after removing the
   header row and any merged-cell duplicates.
3. Confirm column order matches the order named in the instruction, not the order
   that happened to be convenient.
4. Spot-check two or three values back against the source record they came from,
   rather than recomputing them the same way you produced them.
5. If the task named an output filename, confirm that exact name exists on disk.
