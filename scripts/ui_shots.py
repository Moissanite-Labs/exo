"""Render the Exo TUI key states to SVG (docs/ui/). Uses the in-process core with the scripted fake model.

    .venv/bin/python scripts/ui_shots.py docs/ui
Then convert with rsvg-convert, or any SVG renderer. Not part of the test suite.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from exo.core.engine import CoreEngine
from exo.core.types import ToolCall
from exo.protocol import schema as s
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient
from exo_tui.widgets import Composer

FAKE = {"rules": [{"match": "hello", "steps": ["Draft ready. Short sentences, result first, no long dashes."]}]}
SIZE = (112, 42)


async def settle(pilot, secs: float = 0.3) -> None:
    await pilot.pause(secs)


async def shoot(out: Path, name: str, body, *, boot: bool = False) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="exo-shot"))
    eng = CoreEngine(tmp / "home", {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                     cwd=str(tmp), env={})
    app = ExoApp(LocalClient(eng), seat="exo-razi", home=str(tmp / "ui"), boot_anim=boot)
    async with app.run_test(size=SIZE) as pilot:
        if not boot:
            await asyncio.wait_for(app.ready.wait(), 5)
        await body(app, pilot, eng)
        if not boot:
            await settle(pilot)
        app.save_screenshot(filename=f"{name}.svg", path=str(out))
    print("wrote", out / f"{name}.svg")


def pub(eng, app, body) -> None:
    eng.hub.publish(body, app.session)


async def empty(app, pilot, eng):
    await settle(pilot)


async def boot(app, pilot, eng):
    await pilot.pause(0.4)


async def tools_running(app, pilot, eng):
    app.query_one(Composer).text = "draft the release note for the store migration @docs/STORE-V2.md"
    await pilot.press("enter")
    await settle(pilot, 0.8)
    pub(eng, app, s.TurnStarted(turn_id="t9", model="fake:x"))
    for cid, name, args, out, ms in [
        ("c1", "read_file", {"path": "docs/STORE-V2.md"}, "# Store v2\n...\n", 31),
        ("c2", "code_find", {"query": "where is the migration written"}, "3 files\n", 640),
    ]:
        pub(eng, app, s.ToolStarted(call=ToolCall(cid, name, args)))
        pub(eng, app, s.ToolOutput(call_id=cid, text=out))
        from exo.core.types import ContentPart, ToolResult
        pub(eng, app, s.ToolCompleted(result=ToolResult(call_id=cid, content=[ContentPart.of_text(out)]),
                                      duration_ms=ms))
        await settle(pilot, 0.1)
    pub(eng, app, s.ToolStarted(call=ToolCall("c3", "bash", {"command": "pytest -q tests/store"})))
    pub(eng, app, s.ModelCall(requested="fake:x", route="fake:y", usage=s.Usage(input_tokens=2000, cache_read_tokens=9000)))
    pub(eng, app, s.UsageUpdated(usage=s.Usage(input_tokens=2000, cache_read_tokens=9000),
                                 session_total=s.Usage(cost_usd=0.14), model="fake:x"))
    await settle(pilot, 0.5)


async def approval(app, pilot, eng):
    await tools_running(app, pilot, eng)
    pub(eng, app, s.ApprovalRequested(request_id="r1", summary="Post release note to the project channel?",
                                      risk="public, irreversible",
                                      call=ToolCall("c4", "slack_post", {"channel": "#exo", "text": "Store v2 is live"})))
    await settle(pilot, 0.9)


async def answer(app, pilot, eng):
    app.tutorial = True
    app.query_one(Composer).text = "hello"
    await pilot.press("enter")
    await settle(pilot, 1.2)
    pub(eng, app, s.TasteUpdated(rule_id="r1", action="add", text="Lead with the result, then the detail.",
                                 path="~/.exo/taste/writing.md"))
    pub(eng, app, s.MemorySummarized(node_id="n1", level=0, start=1, end=16, before_tokens=1781, after_tokens=153))
    await settle(pilot, 0.9)


async def context(app, pilot, eng):
    app.query_one(Composer).text = "hello"
    await pilot.press("enter")
    await settle(pilot, 1.2)
    app.query_one(Composer).text = "/context"
    await pilot.press("enter")
    await settle(pilot, 1.0)


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    await shoot(out, "01-empty", empty)
    await shoot(out, "01b-boot", boot, boot=True)
    await shoot(out, "02-tools", tools_running)
    await shoot(out, "03-approval", approval)
    await shoot(out, "04-answer", answer)
    await shoot(out, "05-context", context)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/ui")))
