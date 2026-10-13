"""The start motif: the exosphere in its x0 form (the lattice egg), large, slowly turning (D-062).

The figure is the one in ``exosphere.py`` (the egg of ``site/brand/egg.js`` at stage 0, docs/BRAND.md): the yolk's
six incentre dots with the seed in orange, inside the egg's ring of lattice dots. All frames are computed once;
playing them is one Text swap at 3 fps, and only while the widget is on screen. Under EXO_REDUCED_MOTION or NO_COLOR
it is one still frame. When the first message arrives the motif goes and the small exosphere in the corner stays.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from exo_tui.exosphere import CYCLE, render
from exo_tui.theme import no_color, reduced_motion

FRAMES = CYCLE  # one loop: a quarter turn, which the figure's symmetry makes seamless
FPS = 3.0  # 16 s a loop: slow on purpose


def render_frame(i: int, cols: int, rows: int) -> Text:
    """Frame ``i`` of the loop on a ``cols`` x ``rows`` braille canvas (2x4 dots per cell)."""
    return render(cols, rows, "idle", i % FRAMES)


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
