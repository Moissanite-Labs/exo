# Bounded DOCX main-body conversion prototype

Refs #503. `exo.docx_preview.docx_markdown(data)` converts explicitly supplied DOCX bytes into inert Markdown
and source-located blocks. It uses only Python's standard ZIP/XML libraries, adds no dependencies, performs
no filesystem or network access and never starts a worker. It is not registered as a runtime file tool.

```python
from exo.docx_preview import docx_markdown

# Host obtains these bytes through its authorized executor/gate; this function does not read a path.
result = docx_markdown(authorized_document_bytes, max_output=10_000)
print(result["markdown"])
print(result["blocks"][0]["location"])
```

The supported subset is UTF-8 DOCX `word/document.xml`: ordinary main-body paragraphs, visible hyperlink
labels and simple table cells. Markdown preserves table rows and escapes source Markdown syntax/raw HTML.
Paragraph/table/row coordinates and a SHA-256 of the supplied document identify sources. Page numbers
cannot be reconstructed from DOCX XML and are explicitly unavailable. Headers, footnotes, layout, merged
cells, tracked changes and text boxes are not faithfully reconstructed. Warnings report these limits;
image-only content reports that OCR is unavailable. Links, relationships and embedded objects are never
fetched or executed. Original document bytes are untouched.

The parser caps input at 2 MB, archive entries at 128, declared expanded content at 8 MB, document XML at
2 MB, XML elements at 50,000, table columns at 32 and output at 50,000 characters/200 blocks. Highly
compressed entries beyond 200:1, duplicate names, unsafe paths, symlink members, encrypted entries and
unsupported compression are refused. XML DTD/entities and non-UTF-8 encodings are refused. Output truncation
is explicit. No archive member is extracted to disk, so malformed input has no temporary-file cleanup.
A cancellation callback is checked before archive reads and at bounded conversion checkpoints. It is
cooperative: it does not interrupt the ZIP/XML library calls or provide hard wall-time/RSS isolation.

Remaining #503 work includes PDF/PPTX/XLSX/HTML/CSV adapters, an evaluated optional converter such as
MarkItDown, separately optional OCR, page/sheet mapping, faithful complex tables, executor/gate tool wiring
and required-executor acceptance, owned-worker termination, hard runtime/memory enforcement and measured
startup/RSS/conversion benchmarks. Disabled runtime startup is unchanged because nothing imports or
registers this module at startup. This draft makes one parser slice testable; it does not claim the full
bounded document-conversion capability or close the broader issue.
