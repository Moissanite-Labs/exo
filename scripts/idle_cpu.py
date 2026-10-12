"""Idle CPU of the empty start: runs the app headless for N seconds and reports process CPU / wall.

    .venv/bin/python scripts/idle_cpu.py [seconds]
Compares the empty start with the hero motif against one without it. Headless, so it counts the app's own
work (timers, widget updates, layout, SVG-free rendering) but not the terminal emulator's drawing.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

from exo.core.engine import CoreEngine
from exo_tui import app as app_mod
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient

FAKE = {"rules": [{"match": "hello", "steps": ["hi"]}]}


async def run(secs: float, hero: bool) -> float:
    if not hero:
        app_mod.hero_rows = lambda h: 0
    tmp = Path(tempfile.mkdtemp(prefix="exo-cpu"))
    eng = CoreEngine(tmp / "home", {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp), env={})
    app = ExoApp(LocalClient(eng), seat="exo-razi", home=str(tmp / "ui"))
    async with app.run_test(size=(112, 42)) as pilot:
        await asyncio.wait_for(app.ready.wait(), 5)
        await pilot.pause(1.0)
        c0, w0 = time.process_time(), time.monotonic()
        await asyncio.sleep(secs)
        c1, w1 = time.process_time(), time.monotonic()
        assert bool(app.query("#hero")) == hero
    return 100 * (c1 - c0) / (w1 - w0)


async def main(secs: float) -> None:
    orig = app_mod.hero_rows
    with_hero = await run(secs, True)
    app_mod.hero_rows = orig
    print(f"idle CPU, empty start with hero: {with_hero:.2f}% of one core")
    without = await run(secs, False)
    print(f"idle CPU, empty start without hero: {without:.2f}% of one core")


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 10))
