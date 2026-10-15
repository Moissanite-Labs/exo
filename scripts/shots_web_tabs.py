"""Screenshot every web tab (Tasks, Schedule, Command, Providers, and Chat for reference) over demo data.

    .venv/bin/python scripts/shots_web_tabs.py OUTDIR

No real core and no Exo home: the page is served by `WebServer` over a stand-in source that answers the routes
from tables below, the snapshot and the provider table are stubs (no keyring or key is read). Each tab is shot
with data and empty, at 1440 and 400 px wide, full page. Chrome runs headless with a throwaway profile and is
always closed. Not part of the test suite.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "exo-command"))
sys.path.insert(0, str(ROOT / "plugins" / "exo-command" / "tests"))

from exo_command import providers as prov  # noqa: E402
from exo_command import server  # noqa: E402

from browser import Browser, Served  # noqa: E402

NOW = time.time()
MODEL = "anthropic:claude-sonnet-4-5"


def iso(minutes: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW + minutes * 60))


TASKS = [
    {"task_id": "t-7f3a91c2", "title": "Run the full test suite on the slugify branch", "status": "running",
     "detail": "pytest -q · 412 of 1180 collected", "updated_at": NOW - 40},
    {"task_id": "t-1b22d0e4", "title": "Summarise yesterday's research notes into the weekly digest", "status": "running",
     "detail": "reading 14 notes", "updated_at": NOW - 300},
    {"task_id": "t-9c81aa07", "title": "Draft the 2.1 release notes", "status": "queued", "updated_at": NOW - 900},
    {"task_id": "t-44e0b5f1", "title": "Re-index the docs folder for code_find", "status": "queued", "updated_at": NOW - 1200},
    {"task_id": "t-0d6f2c88", "title": "Check the staging deploy for broken links", "status": "failed",
     "detail": "HTTP 502 from staging.example.com after 3 tries", "updated_at": NOW - 3600},
    {"task_id": "t-5a7e1d39", "title": "Add type hints to slugify.core", "status": "done", "detail": "1 file changed, tests pass",
     "updated_at": NOW - 7200},
    {"task_id": "t-2e9b4f60", "title": "Nightly backup of the memory store", "status": "done", "detail": "38 MB in 4.1 s",
     "updated_at": NOW - 30000},
    {"task_id": "t-6c3d8a15", "title": "Rename the old feature flags", "status": "cancelled", "updated_at": NOW - 90000},
]

JOBS = [
    {"job_id": "j-morning", "title": "Morning brief", "when": "0 7 * * 1-5", "tier": "standard",
     "prompt": "Read my calendar, inbox and open decisions, then write a one-page brief for today.",
     "next_run": iso(14 * 60), "last_run": iso(-10 * 60)},
    {"job_id": "j-tests", "title": "Nightly tests", "when": "every 6h", "tier": "fast",
     "prompt": "Run the test suite on main and open a task for each new failure.", "next_run": iso(95), "last_run": iso(-265)},
    {"job_id": "j-digest", "title": "Weekly research digest", "when": "0 17 * * 5", "tier": "deep", "paused": True,
     "prompt": "Collect this week's research notes and write a digest with sources.", "last_run": iso(-6 * 24 * 60)},
]

SESSIONS = [
    {"id": "s-demo", "title": "Fix the failing test in tests/test_slugify.py", "agent": "atlas", "model": MODEL,
     "status": "idle", "updated": iso(-2), "message_count": 24},
    {"id": "s-2", "title": "Draft the 2.1 release notes", "agent": "atlas", "model": MODEL, "status": "idle", "updated": iso(-95)},
]


def card(i, status, title, **kw):
    base = {"session_id": f"sess-{i:04d}-a1b2c3d4", "status": status, "kind": kw.pop("kind", "thread"), "seat": "atlas",
            "model": MODEL, "title": title, "goal": title, "messages": kw.pop("messages", 12), "tool_calls": kw.pop("tools", 7),
            "cost_usd": kw.pop("cost", 0.31), "last_activity_at": NOW - kw.pop("ago", 60)}
    base.update(kw)
    return base


SNAP = {
    "settings": {}, "seq": 4182, "connected": True, "error": None,
    "approvals": {"ok": True, "counts": {"pending": 1}, "pending": [
        {"request_id": "r1", "summary": "Push the fix to the remote repository?", "risk": "high", "requested_at": NOW - 45,
         "session_title": "Fix the failing test", "call": {"name": "bash", "arguments": {"command": "git push origin fix/unicode-slugs"}},
         "options": ["allow_once", "allow_always", "deny"]}]},
    "decisions": {"ok": True, "configured": True, "path": "principal/decisions.jsonl", "counts": {"waiting": 2, "overdue": 1}, "waiting": [
        {"title": "Ship 2.1 this week or hold for the parser fix?", "priority": "high", "overdue": True, "reversible": True,
         "context": "The parser fix is 80% done. Shipping now means a 2.1.1 within ten days.",
         "options": [{"key": "1", "label": "ship now"}, {"key": "2", "label": "hold a week"}], "opened_at": NOW - 26 * 3600,
         "asked_by": {"agent": "atlas"}},
        {"title": "Move nightly tests to the cheaper fast tier?", "priority": "normal", "overdue": False,
         "options": [{"key": "y", "label": "yes"}, {"key": "n", "label": "no"}], "opened_at": NOW - 3 * 3600}]},
    "sessions": {"ok": True, "live_window_seconds": 900, "counts": {"active": 3, "running": 2, "subagents": 1},
                 "active": [card(1, "running", "Fix the failing test in tests/test_slugify.py", tool="bash · pytest -q tests/test_slugify.py", ago=8),
                            card(2, "running", "Summarise research notes", kind="subagent", ago=40, cost=0.08,
                                 output="Read 9 of 14 notes. Two cite the same benchmark; merging them."),
                            card(3, "waiting", "Draft the 2.1 release notes", ago=400, cost=0.12)],
                 "past": [card(4, "done", "Re-index the docs folder", kind="subagent", ago=3000, cost=0.02)]},
    "tasks": {"ok": True, "open": 4, "needs_attention": 1, "columns": [
        {"status": "running", "total": 2, "tasks": [dict(t, finished_at=None) for t in TASKS if t["status"] == "running"]},
        {"status": "queued", "total": 2, "tasks": [t for t in TASKS if t["status"] == "queued"]},
        {"status": "failed", "total": 1, "tasks": [t for t in TASKS if t["status"] == "failed"]}]},
    "projects": {"ok": True, "counts": {"projects": 3, "sessions": 41}, "lanes": [
        {"id": "waiting_on_you", "label": "Waiting on you", "cards": [
            {"name": "slugify 2.1", "band": "build", "session_count": 14, "lifecycle_name": "release", "last_activity": NOW - 120,
             "next_action": "Decide whether 2.1 ships this week."}]},
        {"id": "ongoing", "label": "Ongoing", "cards": [
            {"name": "research digest", "band": "research", "session_count": 19, "lifecycle_name": "habit", "last_activity": NOW - 3600,
             "next_action": "Merge duplicate benchmark notes."},
            {"name": "docs site", "band": "build", "session_count": 8, "lifecycle_name": "maintain", "last_activity": NOW - 86400}]}]},
    "costs": {"ok": True, "window_days": 7, "total_usd": 18.42, "priced_sessions": 37,
              "by_seat": [{"seat": "atlas", "cost_usd": 12.1}, {"seat": "scout", "cost_usd": 4.9}, {"seat": "scribe", "cost_usd": 1.42}],
              "by_model": [{"model": "claude-sonnet-4-5", "cost_usd": 13.8}, {"model": "gpt-5-mini", "cost_usd": 3.2},
                           {"model": "qwen3-coder (local)", "cost_usd": 0}],
              "top": [{"session_id": "sess-0001", "title": "Fix the failing test in tests/test_slugify.py", "seat": "atlas",
                       "models": ["claude-sonnet-4-5"], "input_tokens": 1_240_000, "output_tokens": 38_000, "messages": 112,
                       "priced": True, "cost_usd": 4.12, "last_activity": NOW - 60},
                      {"session_id": "sess-0002", "title": "Summarise research notes", "seat": "scout", "models": ["gpt-5-mini"],
                       "input_tokens": 410_000, "output_tokens": 22_000, "messages": 48, "priced": True, "cost_usd": 0.91,
                       "last_activity": NOW - 3600},
                      {"session_id": "sess-0003", "title": "Local refactor experiments", "seat": "atlas", "models": ["qwen3-coder"],
                       "input_tokens": 96_000, "output_tokens": 9_000, "messages": 20, "priced": False, "cost_usd": 0,
                       "last_activity": NOW - 86400}]},
}

EMPTY_SNAP = {
    "settings": {}, "seq": 12, "connected": True, "error": None,
    "approvals": {"ok": True, "counts": {"pending": 0}, "pending": []},
    "decisions": {"ok": True, "configured": False, "counts": {}, "waiting": []},
    "sessions": {"ok": True, "live_window_seconds": 900, "counts": {"active": 0, "running": 0, "subagents": 0}, "active": [], "past": []},
    "tasks": {"ok": True, "open": 0, "needs_attention": 0, "columns": []},
    "projects": {"ok": True, "counts": {"projects": 0, "sessions": 0}, "lanes": []},
    "costs": {"ok": True, "window_days": 7, "total_usd": 0, "priced_sessions": 0, "by_seat": [], "by_model": [], "top": []},
}

PROVIDERS = [
    {"provider": "anthropic", "needs": "key", "key": "found", "source": "keychain", "key_env": ["ANTHROPIC_API_KEY"], "models": 9,
     "serves": ["main", "deep"], "verified": True, "last_test": {"latency_ms": 412}, "status": "verified", "default": True, "ready": True,
     "transport": "http", "roles": []},
    {"provider": "openai", "needs": "key", "key": "found", "source": "env", "key_env": ["OPENAI_API_KEY"], "models": 14,
     "serves": ["fast"], "status": "key found", "ready": True, "transport": "http", "roles": []},
    {"provider": "openrouter", "needs": "key", "key": "missing", "key_env": ["OPENROUTER_API_KEY"], "models": None,
     "status": "no key", "ready": False, "transport": "http", "roles": []},
    {"provider": "groq", "needs": "key", "key": "found", "source": "keychain", "key_env": ["GROQ_API_KEY"], "models": 6,
     "status": "failed: auth", "ready": False, "transport": "http", "roles": []},
    {"provider": "claude-cli", "needs": "cli", "ready": True, "models": 3, "status": "cli installed", "transport": "cli", "roles": []},
    {"provider": "ollama", "local": True, "reachable": True, "models": 4, "status": "local", "ready": True, "transport": "http", "roles": []},
    {"provider": "lmstudio", "local": True, "reachable": False, "models": None, "status": "local", "ready": False, "transport": "http", "roles": []},
]
MODELS = {"models": [{"route": "anthropic:claude-sonnet-4-5"}, {"route": "anthropic:claude-opus-4-1"}, {"route": "openai:gpt-5-mini"}],
          "available": [{"route": "ollama:qwen3-coder"}]}


class Demo:
    def __init__(self, full: bool):
        self.full, self.centre = full, None

    async def call(self, body):
        t = type(body).TYPE
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        if t == "session.list":
            return {"sessions": SESSIONS if self.full else []}
        if t == "session.get":
            return {"session": {"id": "s-demo", "model": MODEL, "agent": "atlas"}, "messages": []}
        if t == "tasks.list":
            return {"tasks": TASKS if self.full else []}
        if t == "schedule.edit":
            return {"jobs": JOBS if self.full else []}
        if t == "models.list":
            return MODELS
        if t == "command.run":
            return {"output": "", "open": 0}
        return {}

    async def stream(self, since=0):
        import asyncio
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


def stub_providers(full: bool):
    async def handle(centre, method, path, body, router_factory=None):
        if path == "/api/providers":
            rows = PROVIDERS if full else []
            return 200, {"providers": rows, "ready": full, "store": "keychain"}
        if path == "/api/providers/models":
            return 200, MODELS if full else {"models": [], "available": []}
        return 200, {}
    return handle


TABS = ("chat", "tasks", "schedule", "command", "providers")


def main(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    real = prov.handle
    try:
        for full in (True, False):
            prov.handle = stub_providers(full)
            srv = Served(Demo(full), token="demo")
            snap = SNAP if full else EMPTY_SNAP
            srv.centre.snapshot = lambda *a, **k: snap
            try:
                for width in (1440, 400):
                    with Browser(width=width, height=900) as b:
                        for tab in TABS:
                            if not full and tab == "chat":
                                continue
                            b.goto("about:blank")
                            b.goto(srv.url(tab))
                            b.wait("document.querySelector('.xc-main') && !document.querySelector('.xc-loading')", 15)
                            time.sleep(1.4)
                            h = int(b.eval("Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)") or 900)
                            h = max(900 if width > 500 else 860, min(h, 4000))
                            b.call("Emulation.setDeviceMetricsOverride", width=width, height=h, deviceScaleFactor=1, mobile=False)
                            time.sleep(0.4)
                            name = f"{tab}-{'data' if full else 'empty'}-{width}.png"
                            b.shot(outdir / name)
                            b.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=False)
                            print("wrote", outdir / name)
            finally:
                srv.close()
    finally:
        prov.handle = real
        server.STATIC = server.STATIC


if __name__ == "__main__":
    main(Path(sys.argv[1]))
