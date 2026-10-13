"""Screenshots of the exosphere (D-062): the intro, a turn with the corner figure, approval, a narrow terminal.

    .venv/bin/python scripts/exosphere_shots.py <out-dir> [names...]
Uses the scripted demo of scripts/promo_shots.py (no network). SVGs, then scripts/svg_to_png.sh makes PNGs.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import promo_shots as ps  # noqa: E402
from exo.core.types import ToolCall  # noqa: E402
from exo.protocol import schema as s  # noqa: E402


async def held_approval(app, pilot, eng):
    """An approval the core really holds (as tests/clients/tui/test_tui.pending_approval does), so the client's
    reconciliation with the core keeps the card, unlike a bare published event."""
    await tool_turn(app, pilot, eng)
    req = s.ApprovalRequested(request_id="r1", summary="Push the fix to the remote repository?",
                              risk="public, irreversible",
                              call=ToolCall("c4", "bash", {"command": "git push origin fix/unicode-slugs"}))
    eng._approvals[req.request_id] = (asyncio.get_running_loop().create_future(), app.session)
    eng._open_approvals[req.request_id] = req
    eng._approval_rules[req.request_id] = ([], "")
    eng.hub.publish(req, app.session)
    await ps.settle(pilot, 1.5)


async def boot(app, pilot, eng):
    await pilot.pause(0.35)  # mid-sequence: the mark, the wordmark, the checks going green


async def thinking_long(app, pilot, eng):
    await ps.turn_start(app, pilot, eng)
    long = ("I'll read the test and the slugify code first. The failing case is the German sharp s: `slugify` "
            "lowercases and then drops every character outside a-z, so `Straße` becomes `strae`. ") * 3
    ps.pub(eng, app, s.MessageDelta(message_id="m1", text=long))
    await ps.settle(pilot, 0.6)


async def tool_turn(app, pilot, eng):
    await thinking_long(app, pilot, eng)
    await ps.tools_running(app, pilot, eng)


async def done_turn(app, pilot, eng):
    await thinking_long(app, pilot, eng)
    ps.pub(eng, app, s.TurnCompleted(turn_id="t9"))
    await ps.settle(pilot, 0.5)


SHOTS = [
    ("exo-01-boot", boot, "inverse", (112, 42), True),
    ("exo-02-intro", ps.empty, "inverse", (112, 42), False),
    ("exo-03-turn-inverse", tool_turn, "inverse", (112, 42), False),
    ("exo-04-turn-classic", tool_turn, "classic", (112, 42), False),
    ("exo-05-approval", held_approval, "classic", (112, 42), False),
    ("exo-06-approval-inverse", held_approval, "inverse", (112, 42), False),
    ("exo-07-done", done_turn, "classic", (112, 42), False),
    ("exo-08-menu", ps.slash_menu, "classic", (112, 42), False),
    ("exo-09-narrow", tool_turn, "classic", (84, 36), False),
]


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    only = sys.argv[2:]
    for name, fn, layout, size, is_boot in SHOTS:
        if only and name not in only:
            continue
        ps.SIZE = size
        await ps.shoot(out, name, fn, boot=is_boot, layout=layout)
        subprocess.run(["sh", str(Path(__file__).parent / "svg_to_png.sh"), str(out / f"{name}.svg"),
                        str(out / f"{name}.png")], check=False)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="exo-sphere"))))
