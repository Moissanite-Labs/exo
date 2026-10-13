"""CPU cost of the corner exosphere (D-062): the app headless, after a first message, with the figure on and off.

    .venv/bin/python scripts/exosphere_cpu.py [seconds]
Measures process CPU over wall time in two states: idle (the slow breath) and a turn in flight (the inner pulse),
each with EXO_EXOSPHERE on and off. Headless, so it counts the app's own work, not the terminal's drawing.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

from exo.core.engine import CoreEngine
from exo.protocol import schema as s
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient
from exo_tui.widgets import Composer

FAKE = {"rules": [{"match": "hello", "steps": ["hi"]}]}


async def run(secs: float, on: bool, busy: bool) -> float:
    os.environ["EXO_EXOSPHERE"] = "on" if on else "off"
    tmp = Path(tempfile.mkdtemp(prefix="exo-cpu"))
    eng = CoreEngine(tmp / "home", {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp), env={})
    app = ExoApp(LocalClient(eng), seat="exo-razi", home=str(tmp / "ui"))
    async with app.run_test(size=(112, 42)) as pilot:
        await asyncio.wait_for(app.ready.wait(), 5)
        app.query_one(Composer).text = "hello"
        await pilot.press("enter")
        while app.busy or app.query("#hero"):
            await pilot.pause(0.05)
        await pilot.pause(2.0)  # past the "done" bloom
        if busy:
            eng.hub.publish(s.TurnStarted(turn_id="t9", model="fake:x"), app.session)
            await pilot.pause(0.3)
        assert app.sphere.on == on, (app.sphere_on, app.size, app.query_one("#left").size, app.composing, bool(app.query("#hero")))
        c0, w0 = time.process_time(), time.monotonic()
        await asyncio.sleep(secs)
        c1, w1 = time.process_time(), time.monotonic()
    return 100 * (c1 - c0) / (w1 - w0)


async def main(secs: float) -> None:
    for busy in (False, True):
        what = "turn in flight" if busy else "idle"
        on = await run(secs, True, busy)
        off = await run(secs, False, busy)
        print(f"{what:15s} exosphere on {on:5.2f}%  off {off:5.2f}%  difference {on - off:+.2f}% of one core")


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 20))
