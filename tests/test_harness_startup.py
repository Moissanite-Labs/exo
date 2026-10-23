"""Failed composition releases resource ownership without running a nested event loop."""
import asyncio
import sqlite3

import pytest

from exo import compose
from exo.application import CoreEngine
from exo.config import Config
from exo.core import agentlog
from exo.core.engine import CoreRuntime


def setup(tmp_path, monkeypatch, **settings):
    home = tmp_path / "home"
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.delenv("EXO_PROFILE", raising=False)
    monkeypatch.delenv("EXO_TOOL_EXECUTOR_REQUIRED", raising=False)
    plugins = tmp_path / "plugins"
    monkeypatch.setenv("EXO_PLUGIN_DIRS", str(plugins))
    resource = plugins / "resource"
    resource.mkdir(parents=True)
    marker = tmp_path / "released"
    (resource / "plugin.yaml").write_text("name: resource\n")
    (resource / "__init__.py").write_text(
        "def register(ctx):\n"
        "    def release():\n"
        f"        with open({str(marker)!r}, 'a') as stream: stream.write('closed\\n')\n"
        "    ctx.register_cleanup(release)\n")
    Config({"core_plugins": ["resource"], "learn": {"enabled": False},
            "models": {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
            **settings}, home).save()
    return home, marker


def track_engines(monkeypatch):
    engines = []

    class TrackingEngine(CoreEngine):
        def __init__(self, *args, **kwargs):
            engines.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(compose, "CoreEngine", TrackingEngine)
    return engines


def fail(*args, **kwargs):
    raise ValueError("startup failed")


def test_model_resolution_failure_releases_loaded_plugin_resources(tmp_path, monkeypatch):
    home, marker = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(Config, "models", fail)
    with pytest.raises(ValueError, match="startup failed"):
        compose.create()
    assert marker.read_text() == "closed\n"
    assert not (home / "sessions.sqlite").exists()


def test_replaced_constructor_failure_still_releases_plugin_resources(tmp_path, monkeypatch):
    home, marker = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(compose, "CoreEngine", fail)
    with pytest.raises(ValueError, match="startup failed"):
        compose.create()
    assert marker.read_text() == "closed\n"
    assert not (home / "sessions.sqlite").exists()


def test_partial_constructor_failure_closes_opened_store_and_worker_pool(tmp_path, monkeypatch):
    _, marker = setup(tmp_path, monkeypatch)
    engines = track_engines(monkeypatch)
    monkeypatch.setattr(CoreRuntime, "_main_ref", fail)
    with pytest.raises(ValueError, match="startup failed"):
        compose.create()
    engine = engines[0]
    assert marker.read_text() == "closed\n"
    assert engine.store._conn is None
    assert engine.jobs._pool._shutdown and engine.jobs.target.closed
    engine.abort_startup()  # a second rollback is harmless
    assert marker.read_text() == "closed\n"


def test_writer_busy_closes_only_the_failed_runtime_inside_running_event_loop(tmp_path, monkeypatch):
    _, marker = setup(tmp_path, monkeypatch)
    engines = track_engines(monkeypatch)

    async def run():
        live = compose.create()
        try:
            with pytest.raises(agentlog.WriterBusy):
                compose.create()
            failed = engines[1]
            assert marker.read_text() == "closed\n"
            assert failed.store._conn is None
            assert failed.jobs._pool._shutdown
            with pytest.raises(sqlite3.ProgrammingError):
                failed.scheduler.db.execute("SELECT 1")
            assert live._writer is not None and not live._writer.closed
            assert live.scheduler.db.execute("SELECT 1").fetchone()[0] == 1
            assert not live.jobs._pool._shutdown
        finally:
            await live.close()
        assert marker.read_text() == "closed\nclosed\n"

    asyncio.run(run())


def test_failure_after_writer_claim_releases_lock_and_all_resources(tmp_path, monkeypatch):
    home, marker = setup(tmp_path, monkeypatch)
    engines = track_engines(monkeypatch)
    monkeypatch.setattr(compose, "fitting_peers_dir", fail)
    with pytest.raises(ValueError, match="startup failed"):
        compose.create()
    engine = engines[0]
    assert engine._writer is None and engine.store._conn is None
    assert engine.jobs._pool._shutdown
    assert marker.read_text() == "closed\n"
    # Reacquiring the actual lock proves rollback released ownership.
    writer = agentlog.claim_writer(home, engine.agent)
    agentlog.release_writer(writer)
    with pytest.raises(sqlite3.ProgrammingError):
        engine.scheduler.db.execute("SELECT 1")


def test_startup_abort_refuses_a_started_engine(tmp_path, monkeypatch):
    _, marker = setup(tmp_path, monkeypatch)

    async def run():
        engine = compose.create()
        try:
            await engine.start()
            with pytest.raises(RuntimeError, match="unstarted engine"):
                engine.abort_startup()
            assert not marker.exists()
            assert not engine.jobs._pool._shutdown
        finally:
            await engine.close()
        assert marker.read_text() == "closed\n"

    asyncio.run(run())


def test_null_legacy_learning_setting_keeps_startup_usable(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, learn=None)
    engine = compose.create()
    assert engine.harness_plan.profile == "legacy"
    asyncio.run(engine.close())
