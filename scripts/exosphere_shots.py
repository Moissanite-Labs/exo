"""Screenshots of the exosphere (D-062): the intro, a turn with the corner figure, approval, a narrow terminal;
and the choice card (D-066): a choice in both layouts, one waiting behind a draft, a question with options.

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


CHOICE = {"prompt": "The fix changes slug output for existing posts. How should I ship it?", "options": [
    {"id": "flag", "label": "Behind a flag", "description": "old slugs stay; new posts get the fix"},
    {"id": "migrate", "label": "Migrate all slugs", "description": "rewrites URLs, adds redirects"},
    {"id": "new", "label": "Only new posts"},
    {"id": "wait", "label": "Hold until review"}]}


async def choice(app, pilot, eng):
    await done_turn(app, pilot, eng)
    await ps.settle(pilot, 1.6)  # the prompt was typed a moment ago: a fresh key keeps focus in the composer
    ps.pub(eng, app, s.UiPanel(panel_id="p1", action="open", kind="choice", title="ship the slug fix",
                               text="How should I ship it?", data=CHOICE))
    await ps.settle(pilot, 2.0)  # past the fade-in
    await pilot.press("down")


async def choice_draft(app, pilot, eng):
    await done_turn(app, pilot, eng)
    await ps.type_in(app, pilot, "also check the RSS feed")
    ps.pub(eng, app, s.UiPanel(panel_id="p1", action="open", kind="choice", title="ship the slug fix",
                               text="How should I ship it?", data=CHOICE))
    await ps.settle(pilot, 2.0)  # past the fade-in


async def question(app, pilot, eng):
    await done_turn(app, pilot, eng)
    await ps.settle(pilot, 1.6)
    ps.pub(eng, app, s.QuestionAsked(question_id="q1", text="Which Python versions should the test matrix cover?",
                                     options=["3.11 only", "3.11 and 3.12", "3.10 to 3.13"]))
    await ps.settle(pilot, 2.0)  # past the fade-in


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
    ("choice-01-classic", choice, "classic", (112, 42), False),
    ("choice-02-inverse", choice, "inverse", (112, 42), False),
    ("choice-03-behind-draft", choice_draft, "classic", (112, 42), False),
    ("choice-04-question", question, "inverse", (112, 42), False),
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
