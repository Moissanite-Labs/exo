"""Design tokens and small drawing helpers for the Exo terminal client.

The language comes from the project site (site/assets/style.css, the `figure.term` surface section):
hairlines, mono labels, orange as the one signal colour, steel blue for what the system knows.
Everything colourful goes through ``rich_style`` so NO_COLOR can strip it in one place.
"""

from __future__ import annotations

import os

from rich.text import Text
from textual.theme import Theme

# Site tokens, verbatim from :root in style.css.
BG = "#08090a"
BG_2 = "#0e1012"
BG_3 = "#15181b"
DEEP = "#050606"  # .term background
LINE = "#24282d"
LINE_2 = "#343a41"
FG = "#eceae4"
FG_2 = "#b4b3ad"
FG_3 = "#8b8b86"
ACCENT = "#ff5b14"
ACCENT_2 = "#ff8a4c"
COLD = "#9fb4c4"
OK = "#4ade80"
ERR = "#e5534b"  # not on the site: errors and removed diff lines need one more colour

ROLES = {"bg": BG, "bg2": BG_2, "bg3": BG_3, "deep": DEEP, "line": LINE, "line2": LINE_2, "fg": FG, "fg2": FG_2,
         "fg3": FG_3, "accent": ACCENT, "accent2": ACCENT_2, "cold": COLD, "ok": OK, "err": ERR}
# With NO_COLOR the shape of the screen still has to read: orange becomes bold, dim stays dim.
PLAIN = {"accent": "bold", "accent2": "bold", "cold": "", "ok": "", "err": "bold", "fg": "", "fg2": "",
         "fg3": "dim", "line": "dim", "line2": "dim", "bg": "", "bg2": "", "bg3": "", "deep": ""}

DOT = "·"  # the middle dot, the separator everywhere
PROMPT = "›"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no")


def no_color() -> bool:
    return bool(os.environ.get("NO_COLOR"))


def reduced_motion() -> bool:
    return _truthy("EXO_REDUCED_MOTION")


def rich_style(role: str, extra: str = "") -> str:
    """A Rich style string for a palette role, e.g. ``rich_style("accent", "bold")``."""
    base = PLAIN.get(role, "") if no_color() else ROLES[role]
    return f"{extra} {base}".strip()


def fg(role: str) -> str:
    """Hex colour for a role, for places that cannot take a style string (blank under NO_COLOR)."""
    return "" if no_color() else ROLES[role]


EXO_THEME = Theme(
    name="exo",
    primary=ACCENT,
    secondary=COLD,
    accent=ACCENT,
    warning=ACCENT_2,
    error=ERR,
    success=OK,
    foreground=FG,
    background=BG,
    surface=BG_2,
    panel=BG_3,
    boost="#0e1012",
    dark=True,
    variables={
        "deep": DEEP, "line": LINE, "line-2": LINE_2, "fg-2": FG_2, "fg-3": FG_3, "cold": COLD,
        "accent-2": ACCENT_2, "ok": OK, "block-cursor-background": ACCENT, "block-cursor-foreground": "#0a0a0a",
        "input-selection-background": f"{ACCENT} 35%", "input-cursor-background": ACCENT,
        "input-cursor-foreground": "#0a0a0a", "footer-background": BG_2,
    },
)


def header_rule(label: str, role: str, width: int, right: str = "") -> Text:
    """`APPROVAL . PUBLIC` then a hairline to the edge and a clipped-corner mark, like the site's panel head."""
    t = Text(no_wrap=True, overflow="crop")
    t.append(label.upper(), style=rich_style(role, "bold"))
    if right:
        t.append("  " + right, style=rich_style("fg3"))
    fill = max(0, width - t.cell_len - 2)  # one space before the hairline, one cell for the corner
    if fill:
        t.append(" " + "─" * fill, style=rich_style("line2"))
    t.append("◥", style=rich_style(role))
    return t


def chips(pairs: list[tuple[str, str]], role: str = "accent") -> Text:
    """Key chips, the site's outlined squares: `[o] once  [d] deny`."""
    t = Text(no_wrap=True)
    for i, (key, word) in enumerate(pairs):
        if i:
            t.append("  ")
        t.append("[", style=rich_style("line2"))
        t.append(key, style=rich_style(role, "bold"))
        t.append("]", style=rich_style("line2"))
        t.append(" " + word, style=rich_style("fg2"))
    return t


def gauge(frac: float, width: int = 10, role: str = "cold") -> Text:
    """A thin bar: heavy line for the filled part, hairline for the rest."""
    frac = max(0.0, min(1.0, frac))
    n = round(frac * width)
    t = Text(no_wrap=True)
    t.append("━" * n, style=rich_style(role))
    t.append("─" * (width - n), style=rich_style("line2"))
    return t


def human_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


# NO_COLOR: no paint at all. Every surface is the terminal's own background and text, so nothing is invented.
PLAIN_THEME = Theme(
    name="exo-plain",
    primary="ansi_default", secondary="ansi_default", accent="ansi_default", warning="ansi_default",
    error="ansi_default", success="ansi_default", foreground="ansi_default", background="ansi_default",
    surface="ansi_default", panel="ansi_default", boost="ansi_default", dark=True,
    variables={"deep": "ansi_default", "line": "ansi_default", "line-2": "ansi_default", "fg-2": "ansi_default",
               "fg-3": "ansi_default", "cold": "ansi_default", "accent-2": "ansi_default", "ok": "ansi_default"},
)
