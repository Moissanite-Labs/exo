"""Render the public TUI screenshots (docs/media/tui-*.png) from a scripted demo, no network.

    .venv/bin/python scripts/promo_shots.py
SVGs go to a temp dir, then scripts/svg_to_png.sh makes the PNGs. Not part of the test suite.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

from exo.core.engine import CoreEngine
from exo.core.types import ToolCall
from exo.protocol import schema as s
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient
from exo_tui.widgets import Composer

MODEL = "anthropic:claude-sonnet-4-5"
FAKE = {"rules": [{"match": "failing test", "steps": ["I'll read the test and the slugify code first, then run it."]}]}
SIZE = (112, 42)


async def settle(pilot, secs: float = 0.3) -> None:
    await pilot.pause(secs)


async def shoot(out: Path, name: str, body, *, boot: bool = False) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="slugify"))
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)  # a git cwd, so no "not inside a git repo" notice
    eng = CoreEngine(tmp / "home", {"fake": FAKE, "roles": {"main": [MODEL], "aux": [MODEL]}},
                     cwd=str(tmp), env={})
    eng.router.instances["anthropic"] = eng.router.instances["fake"]  # the demo model answers under a public id
    app = ExoApp(LocalClient(eng), seat="atlas", home=str(tmp / "ui"), boot_anim=boot)
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


PROMPT = "fix the failing test in tests/test_slugify.py"


async def turn_start(app, pilot, eng):
    app.query_one(Composer).text = PROMPT
    await pilot.press("enter")
    await settle(pilot, 0.8)
    pub(eng, app, s.TurnStarted(turn_id="t9", model=MODEL))


async def tools_running(app, pilot, eng):
    await turn_start(app, pilot, eng)
    from exo.core.types import ContentPart, ToolResult
    for cid, name, args, out, ms in [
        ("c1", "read_file", {"path": "src/slugify/core.py"}, "def slugify(text):\n    ...\n", 31),
        ("c2", "code_find", {"query": "where non-ASCII characters are handled"}, "2 files\n", 640),
    ]:
        pub(eng, app, s.ToolStarted(call=ToolCall(cid, name, args)))
        pub(eng, app, s.ToolOutput(call_id=cid, text=out))
        pub(eng, app, s.ToolCompleted(result=ToolResult(call_id=cid, content=[ContentPart.of_text(out)]),
                                      duration_ms=ms))
        await settle(pilot, 0.1)
    pub(eng, app, s.ToolStarted(call=ToolCall("c3", "bash", {"command": "pytest -q tests/test_slugify.py"})))
    pub(eng, app, s.ModelCall(requested=MODEL, route=MODEL, usage=s.Usage(input_tokens=2000, cache_read_tokens=9000)))
    pub(eng, app, s.UsageUpdated(usage=s.Usage(input_tokens=2000, cache_read_tokens=9000),
                                 session_total=s.Usage(cost_usd=0.14), model=MODEL))
    await settle(pilot, 0.5)


async def approval(app, pilot, eng):
    await tools_running(app, pilot, eng)
    pub(eng, app, s.ApprovalRequested(request_id="r1", summary="Push the fix to the remote repository?",
                                      risk="public, irreversible",
                                      call=ToolCall("c4", "bash", {"command": "git push origin fix/unicode-slugs"})))
    await settle(pilot, 0.9)


async def context(app, pilot, eng):
    app.query_one(Composer).text = PROMPT
    await pilot.press("enter")
    await settle(pilot, 1.2)
    pub(eng, app, s.TasteUpdated(rule_id="r1", action="add", text="Keep diffs small and add a test for each fix.",
                                 path="taste/code.md"))
    pub(eng, app, s.MemorySummarized(node_id="n1", level=0, start=1, end=16, before_tokens=1781, after_tokens=153))
    app.query_one(Composer).text = "/context"
    await pilot.press("enter")
    await settle(pilot, 1.0)


async def job(app, pilot, eng):
    await tools_running(app, pilot, eng)
    pub(eng, app, s.JobUpdated(job_id="j1", state="done", command="pytest -q", exit_code=0, elapsed_ms=12400,
                               detail="12.4s"))
    await settle(pilot, 0.8)


SHOTS = [("tui-01-start", empty), ("tui-02-turn", tools_running), ("tui-03-approval", approval),
         ("tui-04-context", context), ("tui-05-job", job)]


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in SHOTS:
        await shoot(out, name, fn)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="exo-svg"))))
