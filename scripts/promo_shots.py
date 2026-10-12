"""Render the public TUI screenshots (docs/media/tui-*.png) from a scripted demo, no network.

    .venv/bin/python scripts/promo_shots.py
SVGs go to a temp dir, then scripts/svg_to_png.sh makes the PNGs. Not part of the test suite.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from exo import complete as cm
from exo.core.engine import CoreEngine
from exo.core.types import ToolCall
from exo.protocol import schema as s
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient
from exo_tui.widgets import Composer

MODEL = "anthropic:claude-sonnet-4-5"
FAKE = {"rules": [{"match": "failing test", "steps": ["I'll read the test and the slugify code first, then run it."]}]}
SIZE = (112, 42)
DEMO_FILES = ("README.md", "pyproject.toml", "src/slugify/__init__.py", "src/slugify/core.py",
              "tests/test_slugify.py", "docs/usage.md")


async def settle(pilot, secs: float = 0.3) -> None:
    await pilot.pause(secs)


async def shoot(out: Path, name: str, body, *, boot: bool = False, layout: str = "classic") -> None:
    os.environ["EXO_LAYOUT"] = layout  # the older shots show the classic order; the D-046 shots say which they are
    tmp = Path(tempfile.mkdtemp(prefix="slugify"))
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)  # a git cwd, so no "not inside a git repo" notice
    for rel in DEMO_FILES:  # neutral files for the @ menu
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text("demo\n")
    state = Path(tempfile.mkdtemp(prefix="exo-state"))  # outside the demo folder, so the @ menu shows only demo files
    eng = CoreEngine(state / "home", {"fake": FAKE, "roles": {"main": [MODEL], "aux": [MODEL]}},
                     cwd=str(tmp), env={})
    eng.router.instances["anthropic"] = eng.router.instances["fake"]  # the demo model answers under a public id
    app = ExoApp(LocalClient(eng), seat="atlas", home=str(state / "ui"), boot_anim=boot)
    app.files = cm.FileIndex(tmp)  # the @ menu lists the demo folder, not wherever this script runs
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


async def type_in(app, pilot, text: str) -> None:
    """Type like a person: one key at a time, so the menu follows."""
    app.composer.focus()
    for ch in text:
        await pilot.press("space" if ch == " " else ch)
    await settle(pilot, 0.4)


async def a_turn(app, pilot, eng):
    app.query_one(Composer).text = PROMPT
    await pilot.press("enter")
    await settle(pilot, 1.2)
    pub(eng, app, s.TurnCompleted(turn_id="t1"))
    await settle(pilot, 0.3)


async def slash_menu(app, pilot, eng):  # D-037
    await a_turn(app, pilot, eng)
    await type_in(app, pilot, "/co")


async def arg_menu(app, pilot, eng):
    await a_turn(app, pilot, eng)
    demo = ["anthropic:claude-sonnet-4-5", "anthropic:claude-opus-4-5", "openai:gpt-5", "openai:gpt-5-mini"]
    app.arg_ctx["models"] = [cm.Item(m, m, "role main" if i == 0 else "") for i, m in enumerate(demo)]
    app.arg_at["models"] = time.monotonic() + 3600
    await type_in(app, pilot, "/model ")


async def file_menu(app, pilot, eng):
    await a_turn(app, pilot, eng)
    await type_in(app, pilot, "look at @s")


async def guidance_line(app, pilot, eng):
    await a_turn(app, pilot, eng)
    app.tip, app.gstate.first_run, app.guide.last_at = None, False, None
    pub(eng, app, s.JobUpdated(job_id="j1", state="done", command="pytest -q", exit_code=0, elapsed_ms=12400,
                               detail="12.4s"))
    await settle(pilot, 0.8)


async def composer_pane(app, pilot, eng):  # D-046
    await a_turn(app, pilot, eng)
    app.composer.focus()
    await pilot.press("ctrl+g")
    app.composer.text = ("## Goal\nMake slugify handle non-ASCII letters without dropping them.\n\n"
                         "## Context\n- `src/slugify/core.py` strips anything outside a-z today\n"
                         "- tests live in `tests/test_slugify.py`\n\n"
                         "## Constraints\n- keep the public API as it is\n- no new dependencies\n\n"
                         "## Done when\n- the failing test passes and a test covers German and Turkish input\n")
    await settle(pilot, 0.5)


async def inverse_turn(app, pilot, eng):
    await tools_running(app, pilot, eng)


async def long_thread(app, pilot, eng):
    msgs = []
    for i in range(1, 15):
        msgs.append({"id": f"u{i}", "role": "user", "content": [{"type": "text", "text": f"step {i}: what changed in slugify?"}]})
        msgs.append({"id": f"a{i}", "role": "assistant", "content": [{"type": "text", "text": f"Step {i} is done. The tests still pass."}]})
    app.clear_transcript()
    app.render_history(msgs)
    await settle(pilot, 0.8)


SHOTS = [("tui-01-start", empty, "classic"), ("tui-02-turn", tools_running, "classic"),
         ("tui-03-approval", approval, "classic"), ("tui-04-context", context, "classic"), ("tui-05-job", job, "classic"),
         ("tui-06-slash", slash_menu, "classic"), ("tui-07-args", arg_menu, "classic"),
         ("tui-08-files", file_menu, "classic"), ("tui-09-guidance", guidance_line, "classic"),
         ("tui-10-composer", composer_pane, "inverse"), ("tui-11-inverse", inverse_turn, "inverse"),
         ("tui-12-classic-bottom", long_thread, "classic")]


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    only = sys.argv[2:]  # optional names, to redo one shot
    for name, fn, layout in SHOTS:
        if not only or name in only:
            await shoot(out, name, fn, layout=layout)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="exo-svg"))))
