"""The site's hero motif in braille: a shell of linked nodes around an orange core, slowly turning.

Taken from the hero canvas in site/assets/main.js (Fibonacci sphere, neighbour links, hot nodes with faint
spokes to the core, a hexagon core). All frames are computed once; playing them is one Text swap at 3 fps,
and only while the widget is on screen. Under EXO_REDUCED_MOTION or NO_COLOR it is one still frame.
"""

from __future__ import annotations

import math

from rich.text import Text
from textual.widgets import Static

from exo_tui.theme import ACCENT, no_color, reduced_motion, rich_style

FRAMES = 48  # one full turn
FPS = 3.0  # 16 s a turn: slow on purpose
NODES = 56
_BRAILLE_BIT = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))  # [row][col] within a 2x4 cell
SPOKE = "#5c260f"  # the accent at about a third over the background: a faint spoke

# priority per cell; the strongest dot in a cell sets its colour
EDGE_BACK, EDGE_FRONT, NODE_BACK, NODE_FRONT, SPOKE_P, HOT = 1, 2, 3, 4, 5, 6


def _style(p: int) -> str:
    if no_color():
        return "bold" if p == HOT else ("dim" if p <= NODE_BACK else "")
    return {EDGE_BACK: rich_style("line"), EDGE_FRONT: rich_style("line2"), NODE_BACK: rich_style("fg3"),
            NODE_FRONT: rich_style("fg2"), SPOKE_P: SPOKE, HOT: ACCENT}[p]


def _sphere() -> tuple[list[tuple[float, float, float, bool]], list[tuple[int, int]]]:
    ga = math.pi * (3 - math.sqrt(5))
    pts = []
    for k in range(NODES):
        y = 1 - k / (NODES - 1) * 2
        r = math.sqrt(1 - y * y)
        th = ga * k
        pts.append((math.cos(th) * r, y, math.sin(th) * r, k % 9 == 4))  # fixed pattern, not random: stable frames
    edges = set()  # each node links to its two nearest neighbours: a clean mesh, not a hairball
    for a in range(NODES):
        near = sorted(range(NODES), key=lambda b: sum((pts[a][i] - pts[b][i]) ** 2 for i in range(3)))[1:3]
        edges.update((min(a, b), max(a, b)) for b in near)
    return pts, sorted(edges)


_PTS, _EDGES = _sphere()


def _line(grid: dict[tuple[int, int], int], w: int, h: int, x0: float, y0: float, x1: float, y1: float, p: int) -> None:
    n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    for i in range(n + 1):
        t = i / n
        x, y = round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t)
        if 0 <= x < w and 0 <= y < h:
            grid[(x, y)] = max(grid.get((x, y), 0), p)


def render_frame(i: int, cols: int, rows: int) -> Text:
    """Frame ``i`` of the turn on a ``cols`` x ``rows`` braille canvas (2x4 dots per cell)."""
    w, h = cols * 2, rows * 4
    cx, cy, R = (w - 1) / 2, (h - 1) / 2, min(w, h) / 2 * 0.95
    rot_y, rot_x = 0.6 + 2 * math.pi * i / FRAMES, -0.35
    cY, sY, cX, sX = math.cos(rot_y), math.sin(rot_y), math.cos(rot_x), math.sin(rot_x)
    proj = []
    for x0, y0, z0, hot in _PTS:
        x, z = x0 * cY + z0 * sY, -x0 * sY + z0 * cY
        y2, z2 = y0 * cX - z * sX, y0 * sX + z * cX
        s = 1.6 / (2.6 - z2)
        proj.append((cx + x * R * s * 0.9, cy + y2 * R * s * 0.9, z2, hot))
    grid: dict[tuple[int, int], int] = {}
    for a, b in _EDGES:
        A, B = proj[a], proj[b]
        depth = (A[2] + B[2]) / 2
        if depth > -0.3:  # the back of the shell stays dots only
            _line(grid, w, h, A[0], A[1], B[0], B[1], EDGE_FRONT if depth > 0.1 else EDGE_BACK)
    for x, y, z, hot in proj:
        if hot and z > -0.1:
            _line(grid, w, h, x, y, x + (cx - x) * 0.5, y + (cy - y) * 0.5, SPOKE_P)  # a spoke fading toward the core
    for x, y, z, hot in proj:
        if hot:
            px, py = round(x), round(y)
            if 0 <= px < w and 0 <= py < h:
                grid[(px, py)] = HOT
        else:
            px, py = round(x), round(y)
            if 0 <= px < w and 0 <= py < h:
                grid[(px, py)] = max(grid.get((px, py), 0), NODE_FRONT if z > 0.2 else NODE_BACK)
    cr = R * 0.2  # the core: a filled hexagon and a counter-turning outline, as on the site
    spin = 2 * math.pi * i / FRAMES * 3
    for k in range(6):
        a1, a2 = spin + k * math.pi / 3, spin + (k + 1) * math.pi / 3
        _line(grid, w, h, cx + math.cos(a1) * cr * 1.9, cy + math.sin(a1) * cr * 1.9 * 0.96,
              cx + math.cos(a2) * cr * 1.9, cy + math.sin(a2) * cr * 1.9 * 0.9, HOT)
    for yy in range(int(cy - cr), int(cy + cr) + 1):
        for xx in range(int(cx - cr), int(cx + cr) + 1):
            if (xx - cx) ** 2 + (yy - cy) ** 2 <= cr * cr:
                grid[(xx, yy)] = HOT
    out = Text(no_wrap=True)
    for r in range(rows):
        for c in range(cols):
            bits, p = 0, 0
            for dy in range(4):
                for dx in range(2):
                    q = grid.get((c * 2 + dx, r * 4 + dy), 0)
                    if q:
                        bits |= _BRAILLE_BIT[dy][dx]
                        p = max(p, q)
            out.append(chr(0x2800 + bits) if bits else " ", style=_style(p) if p else "")
        if r < rows - 1:
            out.append("\n")
    return out


def hero_rows(height: int) -> int:
    """How many rows the motif gets for a terminal this tall: 10, 7, or none below 30 rows."""
    return 10 if height >= 38 else (7 if height >= 30 else 0)


class Hero(Static):
    """The motif as a widget. Frames are built when it mounts; the timer runs only if motion is allowed."""

    def __init__(self, rows: int, **kw) -> None:
        super().__init__("", id=kw.pop("id", "hero"), **kw)
        self.rows, self.cols = rows, rows * 2 + 4
        self.frames: list[Text] = []
        self.i = 0
        self.animated = not (reduced_motion() or no_color())

    def on_mount(self) -> None:
        self.styles.height = self.rows
        self.styles.width = self.cols
        self.frames = [render_frame(k, self.cols, self.rows) for k in (range(FRAMES) if self.animated else [0])]
        self.update(self.frames[0])
        if self.animated:
            self.set_interval(1 / FPS, self.step)

    def step(self) -> None:
        self.i = (self.i + 1) % len(self.frames)
        self.update(self.frames[self.i])
