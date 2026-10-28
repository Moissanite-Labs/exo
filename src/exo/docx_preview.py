"""Bounded, bytes-only DOCX main-body extraction prototype (#503), with no parser extras or I/O."""
from __future__ import annotations

import hashlib
import html
import io
import posixpath
import re
import stat
import zipfile
import zlib
from typing import Any, Callable
from xml.etree import ElementTree as ET

MAX_FILE = 2_000_000
MAX_EXPANDED = 8_000_000
MAX_XML = 2_000_000
MAX_MEMBERS = 128
MAX_OUTPUT = 50_000
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentError(ValueError):
    pass


def _escape(text: str) -> str:
    return re.sub(r"([\\`*_\[\]|])", r"\\\1", html.escape(text, quote=False))


def docx_markdown(data: bytes, *, max_output: int = MAX_OUTPUT,
                  cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Extract inert main-story text and simple table rows; no filesystem or relationship access.

    Call only after the host's authorized executor has returned bounded bytes. Not a registered tool.
    Cancellation is cooperative at bounded work checkpoints, not a hard worker/runtime/RSS guarantee.
    """
    if not isinstance(data, bytes) or len(data) > MAX_FILE:
        raise DocumentError("document exceeds input bound")
    if type(max_output) is not int or not 256 <= max_output <= MAX_OUTPUT:
        raise DocumentError("invalid output bound")

    def check() -> None:
        if cancelled and cancelled():
            raise DocumentError("conversion cancelled")

    warnings = ["Main document body only; headers, footnotes, styles and page layout are not reconstructed.",
                "Relationships, links, embedded objects and images are not fetched or executed.",
                "Page numbers are unavailable; locations identify body paragraphs and table rows."]
    check()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise DocumentError("archive member bound exceeded")
            names = set()
            total = 0
            for member in members:
                check()
                name = member.filename
                mode = member.external_attr >> 16
                if (name in names or "\\" in name or name.startswith("/")
                        or posixpath.normpath(name).startswith("../") or ".." in name.split("/")
                        or stat.S_ISLNK(mode) or member.flag_bits & 1
                        or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise DocumentError("unsafe, encrypted or unsupported archive member")
                names.add(name)
                total += member.file_size
                if (total > MAX_EXPANDED or member.file_size > MAX_EXPANDED
                        or member.file_size > max(1, member.compress_size) * 200):
                    raise DocumentError("archive expansion bound exceeded")
            if "word/document.xml" not in names:
                raise DocumentError("DOCX main document missing")
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_XML:
                raise DocumentError("document XML bound exceeded")
            with archive.open(info) as stream:
                raw = stream.read(MAX_XML + 1)
            if len(raw) > MAX_XML:
                raise DocumentError("document XML bound exceeded")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error) as exc:
        raise DocumentError("malformed or unsupported DOCX archive") from exc
    try:
        text = raw.decode("utf-8-sig")
        if "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
            raise DocumentError("DTD/entities and non-UTF-8 XML are unsupported")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError, RecursionError) as exc:
        raise DocumentError("malformed document XML") from exc
    if root.tag != W + "document" or root.find(W + "body") is None:
        raise DocumentError("unsupported document XML root")
    if sum(1 for _ in root.iter()) > 50_000:
        raise DocumentError("XML element bound exceeded")
    check()
    blocks: list[dict[str, Any]] = []
    used, truncated = 0, False
    paragraph, table = 0, 0

    def body_text(element: Any) -> str:
        # Text in tracked deletions/insertions and text boxes is not silently treated as settled body text.
        out = []
        children = []
        for child in element:
            children.extend(list(child) if child.tag == W + "hyperlink" else [child])
        for child in children:
            if child.tag == W + "r":
                for run in child:
                    if run.tag == W + "t":
                        out.append(run.text or "")
                    elif run.tag in (W + "br", W + "cr"):
                        out.append("\n")
                    elif run.tag == W + "tab":
                        out.append("\t")
        return "".join(out)

    def separator(previous: dict[str, Any], location: dict[str, int]) -> str:
        return "\n" if "table" in location and previous["location"].get("table") == location["table"] else "\n\n"

    def add(markdown: str, location: dict[str, int]) -> bool:
        nonlocal used, truncated
        check()
        joiner = separator(blocks[-1], location) if blocks else ""
        remaining = max_output - used - len(joiner)
        if remaining <= 0 or len(blocks) >= 200:
            truncated = True
            return False
        if len(markdown) > remaining:
            markdown = markdown[:remaining]
            truncated = True
        blocks.append({"location": location, "markdown": markdown})
        used += len(markdown) + len(joiner)
        return not truncated

    for element in root.find(W + "body"):
        check()
        if element.tag == W + "p":
            paragraph += 1
            if not add(_escape(body_text(element)), {"paragraph": paragraph}):
                break
        elif element.tag == W + "tbl":
            table += 1
            for row_index, row in enumerate(element.findall(W + "tr"), 1):
                cells = row.findall(W + "tc")
                if len(cells) > 32:
                    raise DocumentError("table column bound exceeded")
                rendered = ["<br>".join(_escape(body_text(p)).replace("\n", "<br>").replace("\t", " ") for p in cell.findall(W + "p")) for cell in cells]
                line = "| " + " | ".join(rendered) + " |"
                if row_index == 1:
                    line += "\n| " + " | ".join("---" for _ in cells) + " |"
                if not add(line, {"table": table, "row": row_index}):
                    break
            if truncated:
                break
        elif element.tag != W + "sectPr":
            warnings.append("Unsupported main-body element omitted.")
    if any(e.tag in (W + "del", W + "ins", W + "txbxContent") for e in root.iter()):
        warnings.append("Tracked changes/text boxes are not reconstructed; extracted text is incomplete.")
    if not any(b["markdown"].strip() for b in blocks):
        warnings.append("No supported text found; document may be image-only. OCR is not available.")
    if truncated:
        warnings.append("Output truncated at the configured character/block bound.")
    return {"format": "docx", "source_sha256": hashlib.sha256(data).hexdigest(),
            "markdown": "".join((separator(blocks[i - 1], b["location"]) if i else "") + b["markdown"]
                                for i, b in enumerate(blocks)), "blocks": blocks,
            "truncated": truncated, "warnings": list(dict.fromkeys(warnings))}
