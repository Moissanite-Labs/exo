"""The exosphere: Exo's evolving lattice figure, drawn in braille for the terminal (D-062).

The exosphere is the name of the figure; its forms are x0 (the egg), then x, then 3xo, and more to come (FORMS is an
open list). The terminal draws x0, the same figure as ``site/brand/egg.js`` at stage 0 (docs/BRAND.md): the centre
cell's six incentre dots (an octahedron, the yolk) inside an egg-shaped shell cut from the same honeycomb, one ring
further out. The seed, the top dot of the yolk, is always orange.

Two uses:
- ``render(...)`` at intro size: the start motif (``hero.py``) and the boot screen.
- ``Exosphere``: the small persistent figure in the bottom-right corner of the chat. It animates gently with the
  agent's state (``STATES``), at a low frame rate, from frames cached per state. Every state loops in a quarter
  turn, which the figure's 90 degree symmetry makes seamless, so a cycle is a fixed set of frames.

Reduced motion (EXO_REDUCED_MOTION) and NO_COLOR give one still frame per state, and no timer.
"""

from __future__ import annotations

import math
import time
from functools import lru_cache

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from exo_tui import theme as th

FORMS = ("x0", "x", "3xo")  # the exosphere's forms in order; principal A is still naming the later ones
FORM = "x0"  # the form every install shows today: the egg
K = 2 - math.sqrt(2)  # incentre distance from the cell centre (cube side 2), as in egg.js
FPS = 4.0  # the corner figure: slow on purpose (idle repaints at half this)
CYCLE = 48  # frames in one loop (12 s at 4 fps): a quarter turn, two breaths
BLOOM_FRAMES = 6  # "done" plays once
STATES = ("idle", "thinking", "tool", "approval", "error", "done")
STILL_STATES = ("approval",)  # a steady glow: one frame, no timer
TRANSIENT = {"done": 1.6, "error": 3.0}  # seconds a passing state shows before the live one returns
ART_COLS, ART_ROWS = 14, 6  # the corner figure in braille cells (28 x 24 dots)
WIDTH, HEIGHT = ART_COLS + 2, ART_ROWS + 1  # the box: one column either side, one row for the label

_BIT = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))  # [row][col] within a 2x4 braille cell
# draw priority: the strongest dot in a cell sets its colour
P_BACK, P_SHELL, P_EDGE, P_DOT, P_HOT, P_SEED = 1, 2, 3, 4, 5, 6


def _hex(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def mix(a: str, b: str, t: float) -> str:
    """Colour ``t`` of the way from ``a`` to ``b`` (hex strings)."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    x, y = _hex(a), _hex(b)
    return "#%02x%02x%02x" % tuple(round(p + (q - p) * t) for p, q in zip(x, y))


# ---------------------------------------------------------------- geometry, computed once
def _yolk() -> tuple[list[tuple[float, float, float]], list[tuple[int, int]]]:
    six = [(K, 0.0, 0.0), (-K, 0.0, 0.0), (0.0, K, 0.0), (0.0, -K, 0.0), (0.0, 0.0, K), (0.0, 0.0, -K)]
    edges = [(i, j) for i in range(6) for j in range(i + 1, 6) if i // 2 != j // 2]  # every pair not on one axis
    return six, edges


def _shell() -> tuple[list[tuple[float, float, float]], list[tuple[int, int]]]:
    """The honeycomb one ring out, pushed onto an egg (egg.js buildShell), in units where the yolk dot sits at K."""
    raw: list[tuple[float, ...]] = []
    pts: list[tuple[float, float, float]] = []
    for i in range(-3, 4):
        for j in range(-3, 4):
            for k in range(-3, 4):
                for a in range(3):
                    for sg in (-1, 1):
                        p = [2.0 * i, 2.0 * j, 2.0 * k]
                        p[a] += K * sg
                        r = math.sqrt(sum(v * v for v in p))
                        if 4.4 <= r <= 5.6:
                            raw.append(tuple(p))
    for q in raw:
        r = math.sqrt(sum(v * v for v in q))
        x, y, z = q[0] / r, q[1] / r, q[2] / r
        w = 0.94 + 0.1 * y  # wider at the bottom (y is down on screen)
        pts.append((x * w * 1.36, y * 1.2 * 1.36, z * w * 1.36))  # 1.36: shell radius over yolk scale, as on the site
    lim = 2 * (math.sqrt(2) - 1) + 1e-3
    edges = [(m, n) for m in range(len(raw)) for n in range(m + 1, len(raw))
             if math.dist(raw[m], raw[n]) < lim]
    return pts, edges


YOLK, YOLK_EDGES = _yolk()
SHELL, SHELL_EDGES = _shell()
SEED = 3  # (0, -K, 0): the top dot of the yolk


# ---------------------------------------------------------------- the canvas
class _Canvas:
    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows, self.w, self.h = cols, rows, cols * 2, rows * 4
        self.dots: dict[tuple[int, int], tuple[int, str]] = {}

    def dot(self, x: float, y: float, p: int, colour: str) -> None:
        px, py = round(x), round(y)
        if 0 <= px < self.w and 0 <= py < self.h:
            old = self.dots.get((px, py))
            if old is None or p >= old[0]:
                self.dots[(px, py)] = (p, colour)

    def line(self, x0: float, y0: float, x1: float, y1: float, p: int, colour: str, dash: int = 0) -> None:
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for i in range(n + 1):
            if dash and i % dash == dash - 1:
                continue
            t = i / n
            self.dot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, p, colour)

    def disc(self, cx: float, cy: float, r: float, p: int, colour: str) -> None:
        for yy in range(int(cy - r) - 1, int(cy + r) + 2):
            for xx in range(int(cx - r) - 1, int(cx + r) + 2):
                if (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r:
                    self.dot(xx, yy, p, colour)

    def text(self) -> Text:
        plain = th.no_color()
        out = Text(no_wrap=True)
        for r in range(self.rows):
            for c in range(self.cols):
                bits, best = 0, (0, "")
                for dy in range(4):
                    for dx in range(2):
                        q = self.dots.get((c * 2 + dx, r * 4 + dy))
                        if q:
                            bits |= _BIT[dy][dx]
                            if q[0] >= best[0]:
                                best = q
                if not bits:
                    out.append(" ")
                    continue
                p, colour = best
                style = ("bold" if p >= P_HOT else "dim" if p <= P_SHELL else "") if plain else colour
                out.append(chr(0x2800 + bits), style=style)
            if r < self.rows - 1:
                out.append("\n")
        return out


# ---------------------------------------------------------------- one frame
def _project(p: tuple[float, float, float], ry: float, rx: float) -> tuple[float, float, float]:
    x, y, z = p
    cY, sY, cX, sX = math.cos(ry), math.sin(ry), math.cos(rx), math.sin(rx)
    x, z = x * cY + z * sY, -x * sY + z * cY
    y, z = y * cX - z * sX, y * sX + z * cX
    return x, y, z


def _hash(*a: int) -> float:
    h = 2166136261
    for v in a:
        h = ((h ^ (v & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF
    return h / 4294967296


def render(cols: int, rows: int, state: str = "idle", i: int = 0, lattice: bool = False) -> Text:
    """Frame ``i`` of ``state`` on a ``cols`` x ``rows`` braille canvas. ``lattice`` adds the shell's honeycomb
    dots on a big canvas (off by default: in braille they read as noise more than as a lattice)."""
    cv = _Canvas(cols, rows)
    w, h = cv.w, cv.h
    big = w >= 40
    phase = (i % CYCLE) / CYCLE
    still = state in STILL_STATES
    ry = 0.62 + (0.0 if still else phase * math.pi / 2)  # a quarter turn per loop: seamless by symmetry
    rx = -0.38
    breath = 1.0 + (0.0 if still else 0.035 * math.sin(2 * math.pi * 2 * phase))  # two breaths per loop
    bloom = 0.0
    if state == "done":
        bloom = math.sin(math.pi * min(i, BLOOM_FRAMES - 1) / (BLOOM_FRAMES - 1)) if BLOOM_FRAMES > 1 else 0.0
    beat = max(0.0, math.cos(2 * math.pi * 4 * phase)) ** 4 if state == "thinking" else 0.0  # 3 s inner pulse

    # fit the egg (shell height 2 * 1.2 * 1.36 * 1.03 units at most) into the canvas, with a dot of margin
    scale = min((h - 2) / (2 * 1.2 * 1.36 * 1.04), (w - 2) / (2 * 1.12 * 1.36 * 1.04))
    cx, cy = (w - 1) / 2, (h - 1) / 2 + 0.5

    dim = state == "error"
    shell_front = th.FG_3 if not dim else mix(th.FG_3, th.DEEP, 0.4)
    shell_back = th.LINE_2 if not dim else th.LINE
    edge_col = th.FG_2
    dot_col = th.FG
    if state == "thinking":
        edge_col = mix(edge_col, th.ACCENT, beat * 0.85)
        dot_col = mix(th.FG, th.ACCENT_2, beat)
    elif state == "approval":
        edge_col, dot_col = th.ACCENT, th.ACCENT_2
    elif state == "done":
        edge_col, dot_col = mix(edge_col, th.ACCENT_2, bloom), mix(th.FG, th.ACCENT_2, bloom)
        shell_front = mix(shell_front, th.FG_2, bloom)
    elif dim:
        edge_col, dot_col = mix(th.FG_3, th.DEEP, 0.35), th.FG_3
    seed_col = th.ERR if dim else th.ACCENT

    # 1. the shell. Small: the egg's outline as a ring of lattice dots that drifts with the turn. Big: the honeycomb
    #    itself, back faint and front brighter, with its outline.
    if big and lattice:
        for k, p in enumerate(SHELL):
            x, y, z = _project(p, ry, rx)
            s = 3.2 / (3.2 - z * 0.35) * scale * breath * (1 + 0.06 * bloom)
            if dim and _hash(k, i // 2) < 0.22:
                continue
            if z > -0.35:
                cv.dot(cx + x * s, cy + y * s, P_SHELL if z > 0.25 else P_BACK, shell_front if z > 0.25 else shell_back)
    rs = scale * 1.36 * breath * (1 + 0.06 * bloom)
    n_ring = max(24, int(2 * math.pi * rs * 1.1 / 2.2))  # a dot about every two braille dots
    drift = phase * 4  # the ring's dots slide a step per quarter of the loop: the turn, read on the outline
    for k in range(n_ring):
        a = 2 * math.pi * (k + drift) / n_ring
        x = math.cos(a) * 1.0 * (1 + 0.1 * math.sin(a))
        y = math.sin(a) * 1.2
        if dim and _hash(k, i // 2) < 0.25:  # the dim flicker: a few dots drop out, a different few each beat
            continue
        cv.dot(cx + x * rs, cy + y * rs * 0.98, P_SHELL, shell_front)

    # 2. the yolk: the octahedron, scaled up by a bloom
    # the yolk drawn larger than on the site (0.43 of the shell there): in braille a true-scale yolk is one blob
    ys = scale * (1.45 if w < 32 else 1.5) * breath * (1 + 0.4 * bloom) * (1 + 0.08 * beat)
    yp = []
    for p in YOLK:
        x, y, z = _project(p, ry, rx)
        s = 3.2 / (3.2 - z * 0.5) * ys
        yp.append((cx + x * s, cy + y * s, z))
    if state in ("thinking", "approval", "done") and (beat > 0.15 or state != "thinking"):
        glow = {"thinking": beat, "approval": 0.7, "done": bloom}[state]
        spoke = mix(th.LINE_2, th.ACCENT, glow)  # the drawing's lines to the centre, lit when the core is busy
        for x, y, _ in yp:
            cv.line(cx, cy, x, y, P_EDGE, spoke)
        cv.disc(cx, cy, 0.6 + 1.2 * glow * (scale / 8), P_HOT, mix(th.ACCENT, th.ACCENT_2, 0.4))
    for a, b in YOLK_EDGES:
        A, B = yp[a], yp[b]
        front = (A[2] + B[2]) / 2 > -0.05
        cv.line(A[0], A[1], B[0], B[1], P_EDGE, edge_col if front else mix(edge_col, th.DEEP, 0.5))
    for k, (x, y, z) in enumerate(yp):
        if k == SEED:
            continue
        cv.dot(x, y, P_DOT, dot_col if z > -0.1 else mix(dot_col, th.DEEP, 0.4))
        if scale >= 10:  # big canvases: the dots are 2x2, like the site's squares
            cv.dot(x + 1, y, P_DOT, dot_col)
    sx, sy, _ = yp[SEED]
    cv.dot(sx, sy, P_SEED, seed_col)
    if scale >= 10:
        cv.disc(sx, sy, 1.1, P_SEED, seed_col)

    # 3. packets: a running tool sends information out of the core, along the six axes, to the shell
    if state == "tool":
        for k, (x, y, z) in enumerate(yp):
            f = (phase * 6 + k / 6 * 2) % 1  # each axis has its own phase: a steady, uneven traffic
            r = 0.2 + f * 2.3
            px, py = cx + (x - cx) * r, cy + (y - cy) * r
            tx, ty = cx + (x - cx) * max(0.2, r - 0.35), cy + (y - cy) * max(0.2, r - 0.35)
            cv.line(tx, ty, px, py, P_HOT, th.ACCENT_2 if z > -0.1 else mix(th.ACCENT_2, th.DEEP, 0.4))
        cv.dot(cx, cy, P_HOT, th.ACCENT)
    return cv.text()


@lru_cache(maxsize=512)
def frame(state: str, i: int, cols: int = ART_COLS, rows: int = ART_ROWS) -> Text:
    """A cached frame: each state is a fixed loop, so the corner figure is drawn once and then only replayed."""
    return render(cols, rows, state, i)


def frames_in(state: str) -> int:
    return 1 if state in STILL_STATES else BLOOM_FRAMES if state == "done" else CYCLE


def label(form: str = FORM, level: int | None = None) -> str:
    """``x0`` or ``x0 · lv 3``. No level source exists yet (the XP design is not built), so callers pass None."""
    return form if level is None else f"{form} {th.DOT} lv {level}"


# ---------------------------------------------------------------- the corner widget
class Exosphere(Static):
    """The persistent corner figure. It never takes focus and never handles a key; the app places and shows it.

    ``set_state`` takes the live state the app derives from its events; ``flash`` plays a passing one ("done",
    "error") over it for a moment. The timer runs only while the figure is shown, moving, and allowed to move.
    """

    can_focus = False
    DEFAULT_CSS = """
    Exosphere { width: %d; height: %d; background: $deep; padding: 0 1; }
    """ % (WIDTH, HEIGHT)

    def __init__(self, form: str = FORM, level: int | None = None) -> None:
        super().__init__("", id="exosphere", markup=False)
        self.form, self.level = form, level
        self.live = "idle"
        self.passing = ""
        self.passing_until = 0.0
        self.i = 0
        self.animated = not (th.reduced_motion() or th.no_color())
        self.timer: Timer | None = None
        self.shown_key: tuple[str, int] | None = None
        self.on = False  # shown on screen right now (the app's call: preference, width, layout)

    @property
    def state(self) -> str:
        if self.live == "approval":  # the user is being waited on: that outranks a passing beat
            return "approval"
        if self.passing and time.monotonic() < self.passing_until:
            return self.passing
        return self.live

    def on_mount(self) -> None:
        self.timer = self.set_interval(1 / FPS, self.step, pause=True)
        self.paint()

    def set_state(self, state: str) -> None:
        if state not in STATES or state == self.live:
            return
        self.live = state
        if state != "idle":  # new work cuts a passing beat short: the figure shows what is happening now
            self.passing = ""
        self.paint()

    def flash(self, state: str) -> None:
        """Play a passing state ("done" or "error") for its few seconds, then fall back to the live one."""
        if state not in TRANSIENT:
            return
        self.passing, self.passing_until, self.i = state, time.monotonic() + TRANSIENT[state], 0
        self.set_timer(TRANSIENT[state] + 0.05, self.paint)  # a still figure still has to come back
        self.paint()

    def paint(self) -> None:
        st = self.state
        if self.passing and st != self.passing:
            self.passing = ""
        n = frames_in(st)
        i = self.i if st != "idle" else self.i // 2 * 2  # idle breathes at half the rate: 2 repaints a second
        k = (st, 0 if not self.animated else min(i, n - 1) if st == "done" else i % n)
        if k != self.shown_key:
            self.shown_key = k
            art = frame(*k)
            out = Text(no_wrap=True)
            out.append_text(art)
            out.append("\n")
            mark = st == "approval"
            tag = label(self.form, self.level)
            room = ART_COLS - (2 if mark else 0)
            out.append(tag[:room].center(room), style=th.rich_style("fg3"))
            if mark:  # the attention mark: the user is being waited on
                out.append(" !", style=th.rich_style("accent", "bold"))
            self.update(out, layout=False)  # fixed size: a repaint of the box, no layout pass
        moving = self.animated and self.on and st not in STILL_STATES and not (st == "done" and self.i >= n - 1)
        if self.timer is not None:
            self.timer.resume() if moving else self.timer.pause()

    def step(self) -> None:
        self.i += 1
        self.paint()

    def show(self, on: bool) -> None:
        """The app decides where and whether the figure is shown; hidden, its timer stops."""
        if on != self.on:
            self.on = on
            if self.is_mounted:
                self.paint()
