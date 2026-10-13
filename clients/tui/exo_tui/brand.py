"""The terminal mark and wordmark, read from the brand files (docs/BRAND.md), never drawn by hand here.

Source of truth: ``site/brand/tui-mark.{txt,html}`` and ``site/brand/tui-wordmark.{txt,html}``, written by the brand
generator. The ``.html`` twin wraps the cells that are orange (the V and the corner bisectors) in ``<b>``. In a checkout the files
are read from ``site/brand`` directly, so a new mark shows without a rebuild; an installed client reads the copies
packaged in ``exo_tui/brand/`` (a test keeps them identical to the site's).

The name as a graphic is "3 X 0" (E drawn as 3, the X with its orange V, O as a zero). Plain text stays "Exo".
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from rich.text import Text

from exo_tui.theme import rich_style

PACKAGED = Path(__file__).with_name("brand")
SITE = Path(__file__).resolve().parents[3] / "site" / "brand"  # present in a checkout only
FALLBACK_WORDMARK = "3 X 0"
_CELL = re.compile(r"<b>(.*?)</b>|([^<])", re.S)


def _read(name: str) -> str | None:
    for d in (SITE, PACKAGED):
        try:
            return (d / name).read_text(encoding="utf-8")
        except OSError:
            continue
    return None


@lru_cache(maxsize=8)
def _rows(stem: str) -> tuple[tuple[tuple[str, bool], ...], ...]:
    """The file as rows of (character, orange) cells. The .html twin carries the colour; the .txt is the fallback."""
    html = _read(f"{stem}.html")
    if html is not None:
        rows = []
        for line in html.rstrip("\n").split("\n"):
            cells = [(m.group(1) if m.group(1) is not None else m.group(2), m.group(1) is not None)
                     for m in _CELL.finditer(line)]
            rows.append(tuple((ch, hot) for ch, hot in cells if ch))
        return tuple(rows)
    txt = _read(f"{stem}.txt")
    if txt is None:
        return ()
    return tuple(tuple((ch, False) for ch in line) for line in txt.rstrip("\n").split("\n"))


def _styled(stem: str, base: str, hot: str) -> Text:
    out = Text(no_wrap=True)
    rows = _rows(stem)
    for r, row in enumerate(rows):
        for ch, orange in row:
            out.append(ch, style=hot if orange else base)
        if r < len(rows) - 1:
            out.append("\n")
    return out


def mark() -> Text:
    """The braille mark (several rows), the V and the corner bisectors in orange. Empty if no brand file can be found."""
    return _styled("tui-mark", rich_style("fg2"), rich_style("accent"))


def wordmark(bold: bool = True) -> Text:
    """The name as a graphic, one line: "3 X 0" with the X in orange (its V; a cell cannot be half orange)."""
    t = _styled("tui-wordmark", rich_style("fg", "bold" if bold else ""), rich_style("accent", "bold" if bold else ""))
    if not t.plain.strip():
        t = Text(FALLBACK_WORDMARK, style=rich_style("fg", "bold" if bold else ""))
    return t
