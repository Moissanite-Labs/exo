"""Capture the public web screenshots (docs/media/web-*.png) from a scripted demo, no network, no real core.

    .venv/bin/python scripts/promo_shots_web.py [outdir]

Serves the static web client over a stand-in source that answers the chat routes and replays protocol events
for one scripted turn, then captures each state with headless Chrome (macOS path below; set CHROME to change).
Not part of the test suite.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "exo-command"))

from exo_command import api, server  # noqa: E402
from exo_command.config import load_settings  # noqa: E402
from exo_command.server import WebServer  # noqa: E402
from exo_command.service import CommandCentre  # noqa: E402

CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
MODEL = "anthropic:claude-sonnet-4-5"
SID = "s-demo"
PROMPT = "fix the failing test in tests/test_slugify.py"

CONTEXT = """Next call for thread s-demo of agent atlas (estimates, about 4 characters per token):
  system            1204   prompt and memory block
  summary tree      2310   6 summaries over 9 nodes, levels 0 to 1
  other threads      410   2 stored
  recent tail       1730   14 messages of this thread
  total             5654
Tree nodes: 9 done, 0 pending (0 running), 0 skipped (kept verbatim: too small or not shorter), 0 failed.
Log: 58 entries; summarized up to 44. /compact would fold 0 leaf chunk(s) now."""


def now_iso(minutes_ago: int = 0) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def text(t: str) -> list:
    return [{"type": "text", "text": t}]


def tool(cid: str, name: str, args: dict, out: str, ms: int) -> list:
    return [("tool.started", {"call": {"id": cid, "name": name, "arguments": args}}),
            ("tool.output", {"call_id": cid, "text": out}),
            ("tool.completed", {"result": {"call_id": cid, "content": text(out)}, "duration_ms": ms})]


USAGE = {"input_tokens": 2100, "cache_read_tokens": 9400}
SCRIPT = {
    "start": [],
    "turn": [("turn.started", {"turn_id": "t1", "model": MODEL}),
             ("message.delta", {"message_id": "m1", "text": "I'll read the module and find where non-ASCII text is handled, then run the test."}),
             *tool("c1", "read_file", {"path": "src/slugify/core.py"}, "def slugify(text, sep='-'):\n    ...\n", 31),
             *tool("c2", "code_find", {"query": "where non-ASCII characters are handled"}, "2 files\n", 640),
             ("tool.started", {"call": {"id": "c3", "name": "bash", "arguments": {"command": "pytest -q tests/test_slugify.py"}}}),
             ("model.call", {"requested": MODEL, "route": MODEL, "usage": USAGE, "ttft_ms": 410, "total_ms": 2600}),
             ("usage.updated", {"usage": USAGE, "session_total": {"cost_usd": 0.14}, "model": MODEL})],
}
SCRIPT["approval"] = SCRIPT["turn"] + [
    ("approval.requested", {"request_id": "r1", "summary": "Push the fix to the remote repository?", "risk": "high",
                            "call": {"id": "c4", "name": "bash", "arguments": {"command": "git push origin fix/unicode-slugs"}}})]
SCRIPT["memory"] = [
    ("turn.started", {"turn_id": "t1", "model": MODEL}),
    *tool("c1", "read_file", {"path": "src/slugify/core.py"}, "def slugify(text, sep='-'):\n    ...\n", 31),
    *tool("c2", "edit_file", {"path": "src/slugify/core.py"}, "edited 1 file\n", 52),
    *tool("c3", "bash", {"command": "pytest -q tests/test_slugify.py"}, "1 passed in 0.08s\n", 1900),
    ("message.completed", {"message": {"id": "m1", "role": "assistant", "content": text(
        "Fixed. slugify now transliterates non-ASCII letters before building the slug, and the test passes.")}}),
    ("model.call", {"requested": MODEL, "route": MODEL, "usage": USAGE, "ttft_ms": 410, "total_ms": 2600}),
    ("usage.updated", {"usage": USAGE, "session_total": {"cost_usd": 0.19}, "model": MODEL}),
    ("taste.updated", {"rule_id": "r1", "action": "add", "text": "Keep diffs small and add a test for each fix.", "path": "taste/code.md"}),
    ("memory.summarized", {"node_id": "n1", "level": 0, "start": 29, "end": 44, "before_tokens": 1781, "after_tokens": 153}),
    ("turn.completed", {"turn_id": "t1"})]


class DemoSource:
    """Answers the chat routes from a table; replays the scripted events once the page has opened the thread."""

    def __init__(self, state: str, cwd: str = ""):
        self.state, self.centre, self.cwd = state, None, cwd
        self.fed = False

    async def call(self, body):
        t = type(body).TYPE
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        if t == "session.list":
            return {"sessions": [
                {"id": SID, "title": PROMPT if self.state != "start" else "New thread", "agent": "atlas", "model": MODEL,
                 "status": "live" if self.state in ("turn", "approval") else "idle", "updated": now_iso(0)},
                {"id": "s-2", "title": "Add type hints to slugify.core", "agent": "atlas", "model": MODEL, "status": "idle",
                 "updated": now_iso(95)},
                {"id": "s-3", "title": "Draft the 2.1 release notes", "agent": "atlas", "model": MODEL, "status": "idle",
                 "updated": now_iso(60 * 26)}]}
        if t == "session.get":
            msgs = [] if self.state == "start" else [{"id": "u1", "role": "user", "content": text(PROMPT)}]
            if not self.fed:
                self.fed = True
                asyncio.get_running_loop().call_later(0.6, self.feed)
            return {"session": {"id": SID, "model": MODEL, "agent": "atlas", "cwd": self.cwd}, "messages": msgs}
        if t == "command.run":
            return {"output": CONTEXT} if body.name == "context" else {"open": 0}
        return {}

    def feed(self):
        for i, (typ, b) in enumerate(SCRIPT.get(self.state, SCRIPT["memory"]), 1):  # 0.6 s after the thread opens
            self.centre.record({"seq": i, "type": typ, "body": b, "session": SID, "ts": now_iso()})

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


SCRIPT["done"] = SCRIPT["memory"] + [
    ("job.updated", {"job_id": "j1", "state": "done", "command": "pytest -q", "exit_code": 0, "elapsed_ms": 12400})]
TOKEN = "demo-token"
DEMO_FILES = ("README.md", "pyproject.toml", "src/slugify/__init__.py", "src/slugify/core.py", "tests/test_slugify.py",
              "docs/usage.md", "docs/changelog.md")
# What a person would type, applied by a script injected into the copied page (D-037). The product has no such hook.
TYPED = {"guidance": "", "slash": "/", "files": "read @sl"}
# state, typed text; the three completion shots start from a finished turn with a job that just ended
SHOTS = [("web-01-start", "start", None), ("web-02-turn", "turn", None), ("web-03-approval", "approval", None),
         ("web-04-memory", "memory", None), ("web-05-guidance", "done", "guidance"), ("web-06-slash", "done", "slash"),
         ("web-07-files", "done", "files")]


async def capture(url: str, out: Path) -> None:
    prof = tempfile.mkdtemp(prefix="exo-chrome")
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
           "--window-size=1440,900", f"--user-data-dir={prof}",
           "--use-mock-keychain", "--password-store=basic", "--no-first-run", "--no-default-browser-check",
           f"--screenshot={out}", url]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.wait(), 30)
    except asyncio.TimeoutError:
        proc.kill()


HOLD = 3.0


STATIC0, REAL_DISPATCH = server.STATIC, api.dispatch


async def dispatch0(c, method, path, q, body):
    if path == "/api/hold":
        await asyncio.sleep(HOLD)
        return 200, {}
    return await REAL_DISPATCH(c, method, path, q, body)


def type_script(kind: str) -> str:
    """A snippet that types into the composer once the scripted events have landed (it sits in the copied page)."""
    if not kind or not TYPED[kind]:
        return ""
    return ('addEventListener("DOMContentLoaded", () => setTimeout(() => { const i = document.querySelector(".xc-input");'
            f' i.focus(); i.value = {TYPED[kind]!r}; i.setSelectionRange(i.value.length, i.value.length);'
            ' i.dispatchEvent(new Event("input")); }, 2000));')


def hold_load(kind: str = "") -> None:
    """Chrome shoots at the load event. Serve a copy of the client with one slow image so the load event waits for
    the scripted events and the fade animations to finish. The repo's static files are not touched. The page's
    Content-Security-Policy refuses inline scripts, so the added script is a file in the copy, loaded by src."""
    import shutil
    dest = Path(tempfile.mkdtemp(prefix="exo-static")).resolve() / "static"
    shutil.copytree(STATIC0, dest)
    pin = 'localStorage.setItem("xc-layout", "classic");'  # the older shots show the classic order (D-046)
    (dest / "promo.js").write_text(pin + "\n" + type_script(kind) + "\n")
    index = dest / "index.html"
    index.write_text(index.read_text().replace(
        "</body>", '<script src="/static/promo.js"></script><img src="/api/hold" alt="" hidden></body>'))
    server.STATIC = dest
    api.dispatch = dispatch0


async def main(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    demo = Path(tempfile.mkdtemp(prefix="slugify"))
    subprocess.run(["git", "init", "-q", str(demo)], check=True)
    for rel in DEMO_FILES:
        (demo / rel).parent.mkdir(parents=True, exist_ok=True)
        (demo / rel).write_text("demo\n")
    for name, state, kind in SHOTS:
        hold_load(kind or "")
        src = DemoSource(state, str(demo))
        centre = CommandCentre(load_settings({}), src)
        centre.connected = True
        src.centre = centre
        web = WebServer(centre, token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        await capture(f"http://127.0.0.1:{port}/?boot=0#token={TOKEN}&chat", outdir / f"{name}.png")
        await web.close()
        print("wrote", outdir / f"{name}.png")


class LongDemo(DemoSource):
    """A thread with many finished turns, to show where a chat opens."""

    async def call(self, body):
        if type(body).TYPE == "session.get":
            msgs = []
            for i in range(1, 15):
                msgs.append({"id": f"u{i}", "role": "user", "content": text(f"step {i}: what changed in slugify?")})
                msgs.append({"id": f"a{i}", "role": "assistant", "content": text(f"Step {i} is done. The tests still pass.")})
            return {"session": {"id": SID, "model": MODEL, "agent": "atlas", "cwd": self.cwd}, "messages": msgs, "seq": 0}
        return await super().call(body)


def layout_shots(outdir: Path) -> None:
    """D-046: the inverse layout, and a classic chat that opens at its newest message. Driven over DevTools."""
    import time
    sys.path.insert(0, str(ROOT / "plugins" / "exo-command" / "tests"))
    from browser import Browser, Served
    demo = Path(tempfile.mkdtemp(prefix="slugify"))
    for name, state, layout, cls in (("web-08-inverse", "turn", "inverse", DemoSource), ("web-09-classic-bottom", "memory", "classic", LongDemo)):
        srv = Served(cls(state, str(demo)), token=TOKEN)
        with Browser() as b:
            b.goto(srv.url())
            b.eval(f'localStorage.setItem("xc-layout", "{layout}")')
            b.goto(srv.url())
            b.wait("document.querySelectorAll('.xc-msg').length > 3", 20)
            time.sleep(2.5)
            b.shot(outdir / f"{name}.png")
        srv.close()
        print("wrote", outdir / f"{name}.png")


if __name__ == "__main__":
    if sys.argv[1:2] == ["layout"]:
        layout_shots(Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "docs" / "media"))
        sys.exit(0)
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "media")))
