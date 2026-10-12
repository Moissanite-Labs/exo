"""A short boot sequence: the brand, then four checks that go green as the real steps finish.

Under a second on a healthy core, any key skips it, and it never runs under reduced motion
(the app decides; see ExoApp.boot_anim). It reports real steps: a check turns ok only when
the app marks it, so a slow core shows a slow boot rather than a fake one.
"""

from __future__ import annotations

import time

from rich.console import Group
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static

from exo_tui.hero import Hero, hero_rows
from exo_tui.theme import DOT, rich_style

STEPS = [("core", "connect"), ("agent", "open thread"), ("memory", "summary tree"), ("model", "route")]
MIN_SECONDS = 0.7  # the sequence is never shorter than this, so it reads as a sequence
STEP_GAP = 0.12  # each line is drawn this long after the one before


class BootScreen(ModalScreen):
    def __init__(self) -> None:
        super().__init__(id="boot")
        self.done: dict[str, str] = {}
        self.t0 = time.monotonic()
        self.closed = False

    def compose(self) -> ComposeResult:
        rows = hero_rows(self.app.size.height)
        if rows:
            yield Hero(rows, id="boot-hero")
        yield Static("", id="boot-art")

    def on_mount(self) -> None:
        self.set_interval(0.05, self.draw)
        self.draw()

    def mark(self, step: str, detail: str = "") -> None:
        self.done.setdefault(step, detail)

    def draw(self) -> None:
        age = time.monotonic() - self.t0
        out = [Text.assemble(("◢ ", rich_style("accent")), ("E X O", rich_style("fg", "bold"))),
               Text("a personal agent core", style=rich_style("fg3")), Text("")]
        shown = int(age / STEP_GAP)
        finished = True
        for i, (key, what) in enumerate(STEPS):
            if i >= shown:
                finished = False
                break
            line = Text(no_wrap=True)
            if key in self.done:
                line.append("✓ ", style=rich_style("ok"))
                line.append(f"{key:<8}", style=rich_style("fg2"))
                line.append(self.done[key] or what, style=rich_style("fg3"))
            else:
                finished = False
                line.append(f"{DOT} ", style=rich_style("accent"))
                line.append(f"{key:<8}", style=rich_style("fg2"))
                line.append(what, style=rich_style("fg3"))
            out.append(line)
        self.query_one("#boot-art", Static).update(Group(*out))
        if finished and age >= MIN_SECONDS:
            self.finish()

    def finish(self) -> None:
        if not self.closed:
            self.closed = True
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.finish()  # any key skips
