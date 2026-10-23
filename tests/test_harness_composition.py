"""Profiles select real runtime resources, including across seat overrides."""
import asyncio
import json
from pathlib import Path

import pytest

from exo import compose
from exo.config import Config, ConfigError
from exo.core.types import ToolCall
from exo.harness_runtime import DisabledDelegator, DisabledJobs

REPO = Path(__file__).resolve().parents[1]


def configured(tmp_path, monkeypatch, profile="lean", **extra):
    monkeypatch.delenv("EXO_PROFILE", raising=False)
    monkeypatch.setenv("EXO_PLUGIN_DIRS", str(REPO / "plugins"))
    home = tmp_path / "home"
    monkeypatch.setenv("EXO_HOME", str(home))
    store = tmp_path / "principal"
    store.mkdir()
    data = {"models": {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
            "plugin_config": {"exo-gate": {"principal_store": str(store),
                            "key_path": str(tmp_path / "gate.key"), "decision_model": False}},
            "cwd": str(tmp_path), **extra}
    if profile is not None:
        data["harness"] = {"profile": profile}
    cfg = Config(data, home)
    cfg.save()
    return cfg


def test_lean_composition_runs_a_turn_without_optional_services(tmp_path, monkeypatch):
    cfg = configured(tmp_path, monkeypatch, mcp={"bad": {"command": "/does-not-exist"}},
                     channels={"bus": {}}, schedule=True, learn={"enabled": True})
    before = cfg.path.read_bytes()

    async def run():
        engine = compose.create()
        try:
            assert set(engine.plugins.plugins) == {"exo-gate"}
            assert isinstance(engine.jobs, DisabledJobs) and isinstance(engine.agents, DisabledDelegator)
            assert engine.scheduler is None and engine.peers is None
            assert not engine.skills.skills and not engine._channel_cfg
            assert not {"skill", "delegate", "jobs", "job_output", "job_kill", "peer_send"} & set(engine.registry.names())
            assert {"bash", "read_file", "write_file", "edit_file"} <= set(engine.registry.names())
            await engine.start()
            session = await engine.loop.create_session(cwd=str(tmp_path))
            await engine.loop.send(session.id, "hello")
            assert engine.store.messages(session.id)
            assert not (cfg.home / "schedule.sqlite").exists()
            assert not (cfg.home / "run" / "jobs").exists()
            assert cfg.path.read_bytes() == before
        finally:
            await engine.close()

    asyncio.run(run())


def test_seat_cannot_restart_disabled_services(tmp_path, monkeypatch):
    cfg = configured(tmp_path, monkeypatch)
    seats = cfg.home / "seats"
    seats.mkdir()
    (seats / "worker.json").write_text(json.dumps({"name": "worker",
        "mcp": {"bad": {"command": "/must-not-start"}}, "channels": {"bus": {}},
        "jobs": {"enabled": True}, "peers": {"enabled": True}}))
    engine = compose.create("worker")
    try:
        assert engine.peers is None and engine.scheduler is None
        assert isinstance(engine.jobs, DisabledJobs)
        assert not engine.seat.mcp and not engine._channel_cfg
        assert engine.mcp.configs == {}
    finally:
        asyncio.run(engine.close())


@pytest.mark.parametrize("plugins", [["exo-memory", "exo-gate"], []])
def test_seat_plugin_conflicts_fail_before_import(tmp_path, monkeypatch, plugins):
    cfg = configured(tmp_path, monkeypatch)
    seats = cfg.home / "seats"
    seats.mkdir()
    (seats / "worker.json").write_text(json.dumps({"name": "worker", "plugins": plugins}))
    with pytest.raises(ConfigError, match="seat plugins conflict"):
        compose.create("worker")
    assert not (cfg.home / "sessions.sqlite").exists()


def test_profile_missing_gate_fails_before_opening_store(tmp_path, monkeypatch):
    cfg = configured(tmp_path, monkeypatch)
    monkeypatch.setenv("EXO_PLUGIN_DIRS", str(tmp_path / "missing"))
    with pytest.raises(ConfigError, match="working exo-gate"):
        compose.create()
    assert not (cfg.home / "sessions.sqlite").exists()


def test_coding_can_read_workspace_and_keep_existing_coding_features(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch, "coding", learn={"enabled": False})
    (tmp_path / "AGENTS.md").write_text("Run the repository test command.")
    engine = compose.create()
    try:
        assert engine.scheduler is None and engine.peers is None
        assert {"delegate", "jobs", "skill"} <= set(engine.registry.names())
        session = asyncio.run(engine.loop.create_session(cwd=str(tmp_path)))
        assert "Run the repository test command." in asyncio.run(engine.loop.base_system(session.id))
        assert engine.plugins.plugins["exo-memory"].ok
    finally:
        asyncio.run(engine.close())


def test_legacy_keeps_discovery_and_existing_runtime_defaults(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch, None, core_plugins=["exo-gate"], learn={"enabled": False})
    engine = compose.create()
    try:
        assert not isinstance(engine.jobs, DisabledJobs) and not isinstance(engine.agents, DisabledDelegator)
        assert engine.scheduler is not None and engine.peers is not None
        assert engine.loop.workspace_extra is None
        assert "skill" in engine.registry.names()
    finally:
        asyncio.run(engine.close())


def test_disabled_tool_calls_fail_even_if_not_advertised(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch)
    engine = compose.create()
    try:
        result = asyncio.run(engine.run_tool(ToolCall("x", "delegate", {"goal": "hello"}), {}))
        assert result.is_error and "Unknown tool" in result.text()
    finally:
        asyncio.run(engine.close())


@pytest.mark.parametrize("instructions", [False, True])
def test_scheduler_runs_without_delegation_and_inherits_workspace_scope(tmp_path, monkeypatch, instructions):
    from exo.core.models.fake import FakeProvider

    (tmp_path / ".git").mkdir()
    work = tmp_path / "scheduled-work"
    work.mkdir()
    sibling = tmp_path / "other-work"
    sibling.mkdir()
    (tmp_path / "AGENTS.md").write_text("SHARED WORKSPACE GUIDANCE")
    (work / "AGENTS.md").write_text("SCHEDULED WORK GUIDANCE")
    (sibling / "AGENTS.md").write_text("UNRELATED WORK GUIDANCE")
    cfg = configured(tmp_path, monkeypatch, cwd=str(work))
    cfg.set("harness.enable", ["scheduler", *(["instructions"] if instructions else [])])
    cfg.save()

    async def run():
        engine = compose.create()
        engine.loop.provider = recorder = FakeProvider(["scheduled report"])
        try:
            assert isinstance(engine.agents, DisabledDelegator)
            assert isinstance(engine.jobs, DisabledJobs)
            assert "delegate" not in engine.registry.names()
            job = engine.scheduler.add("every 1h", "Report on this workspace", title="Report")
            assert await engine.scheduler.run(job, "scheduled-test") == "done"
            assert engine.scheduler.get(job["id"])["last_status"] == "done"
            assert not engine.store._q("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
            assert not (cfg.home / "run" / "jobs").exists()
            system = recorder.calls[0]["messages"][0].text()
            assert "UNRELATED WORK GUIDANCE" not in system
            assert ("SHARED WORKSPACE GUIDANCE" in system) is instructions
            assert ("SCHEDULED WORK GUIDANCE" in system) is instructions
        finally:
            await engine.close()

    asyncio.run(run())
