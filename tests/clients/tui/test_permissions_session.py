"""The terminal requests explicit session consent before showing its bypass banner."""
import asyncio
from pathlib import Path

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.store import PrincipalStore
from exo_tui import parse
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient
from exo_tui.widgets import StatusStrip


def test_launch_flag_requests_consent_and_new_clears_banner(tmp_path):
    assert parse(["--skip-permissions"]).skip_permissions
    assert parse(["--dangerously-skip-permissions"]).skip_permissions
    store = PrincipalStore.create(tmp_path / "store")
    (store.root / "policies" / "gates.yaml").write_text("version: 1\ncategories: {local_write: ask}\n")
    pm = PluginManager(config={"exo-gate": {"decision_model": False, "principal_store": str(store.root),
                                            "key_path": str(tmp_path / "gate.key")}})
    assert pm.load_folder(Path(__file__).resolve().parents[3] / "plugins" / "exo-gate").ok
    eng = CoreEngine(tmp_path / "home", {"fake": {"reply": "fixture"}, "roles": {"main": ["fake:x"]}},
                     plugins=pm, cwd=str(tmp_path), env={})

    async def main():
        app = ExoApp(LocalClient(eng), skip_permissions=True)
        async with app.run_test(size=(140, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            async def until(cond):
                for _ in range(150):
                    if cond():
                        return
                    await pilot.pause(0.02)
                raise AssertionError("terminal permission state timed out")
            await until(lambda: app.card is not None)
            assert "Enable bypass for this session?" in app.card.req.summary
            assert not app.bypass and "permissions: bypass" not in app.status_text()
            app.card.focus()
            await pilot.press("o")
            await until(lambda: app.bypass)
            assert app.status_text().startswith("permissions: bypass")
            assert app.query_one(StatusStrip).render().plain.startswith("permissions: bypass")
            sid = app.session
            app.session = "fixture-other-session"
            assert not app.bypass and "permissions: bypass" not in app.status_text()
            app.session = sid
            assert app.bypass
            original_call = app.client.call
            async def failed_refresh(*args, **kwargs):
                raise OSError("fixture connection failure")
            app.client.call = failed_refresh
            await app.load_permissions()
            assert app.bypass  # a transient status failure never hides the warning
            app.client.call = original_call
            assert await eng.plugins.hooks.afire("pre_tool_call", tool_name="write_file",
                         args={"path": "fixture.txt"}, session_id=sid) == []
            await app.load_permissions()
            assert "1 auto-approved" in app.status_text()
            await app.new_session()
            assert app.session != sid and not app.bypass
            assert "permissions: bypass" not in app.query_one(StatusStrip).render().plain
            await app.load_session(sid)
            assert app.bypass  # resuming the same session retains its in-memory mode
            await app.request_permissions("ask")
            assert not app.bypass
            app.run_worker(app.request_permissions("bypass"), exit_on_error=False)
            await until(lambda: app.card is not None)
            app.card.focus()
            await pilot.press("d", "enter")
            await until(lambda: app.card is None)
            assert not app.bypass
        await eng.close()

    asyncio.run(main())
