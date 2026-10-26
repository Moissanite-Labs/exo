"""The terminal client's side of permissions bypass (D-065): the launch flag and the banner."""
from __future__ import annotations

import asyncio
from pathlib import Path

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo_tui import parse
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient
from exo_tui.widgets import Composer, StatusStrip

REPO = Path(__file__).resolve().parents[3]


def engine(tmp_path) -> CoreEngine:
    pm = PluginManager(config={"exo-gate": {"decision_model": False}})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()
    return CoreEngine(tmp_path / "home", {"fake": {"reply": "ok"}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                      plugins=pm, cwd=str(tmp_path), env={})


def run(tmp_path, scenario, **kw):
    eng = engine(tmp_path)

    async def main():
        app = ExoApp(LocalClient(eng), seat="tester", **kw)
        async with app.run_test(size=(140, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await scenario(app, pilot, eng)

    asyncio.run(main())


async def until(cond, pilot, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "timed out"
        await pilot.pause(0.02)


def strip_text(app) -> str:
    return app.query_one(StatusStrip).render().plain


def test_launch_flag_parses_with_both_spellings():
    assert parse(["--skip-permissions"]).skip_permissions
    assert parse(["--dangerously-skip-permissions"]).skip_permissions
    assert not parse([]).skip_permissions


def test_no_flag_no_banner(tmp_path):
    async def sc(app, pilot, eng):
        assert app.perm.get("mode") == "ask"
        assert "permissions" not in app.status_text() and "permissions" not in strip_text(app)
    run(tmp_path, sc)


def test_flag_turns_bypass_on_and_the_banner_shows(tmp_path):
    async def sc(app, pilot, eng):
        assert app.bypass
        assert app.status_text().startswith("permissions: bypass (0 auto-approved)")
        assert strip_text(app).startswith("permissions: bypass")
        gate_state = eng.plugins.commands["permissions"].handler("json")
        assert gate_state["mode"] == "bypass" and gate_state["via"] == "launch flag" and gate_state["by"] == "principal:tui"
        assert "auto-approves everything except changes to Exo's own records" in app.transcript_text()
    run(tmp_path, sc, skip_permissions=True)


def test_slash_permissions_switches_and_the_banner_follows(tmp_path):
    async def sc(app, pilot, eng):
        c = app.query_one(Composer)
        c.text = "/permissions bypass"
        await pilot.press("enter")
        await until(lambda: app.bypass, pilot)
        assert "permissions: bypass" in strip_text(app)
        c.text = "/permissions ask"
        await pilot.press("enter")
        await until(lambda: not app.bypass, pilot)
        assert "permissions" not in strip_text(app)
    run(tmp_path, sc)


def test_banner_counts_auto_approved_calls(tmp_path):
    async def sc(app, pilot, eng):
        await eng.plugins.hooks.afire("pre_tool_call", tool_name="bash", args={"command": "rm -rf build"})
        await app.load_permissions()
        assert "(1 auto-approved)" in app.status_text() and "1 auto-approved" in strip_text(app)
    run(tmp_path, sc, skip_permissions=True)
