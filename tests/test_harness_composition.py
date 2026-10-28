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


@pytest.fixture(autouse=True)
def _owner_only_umask():
    """The guidance files these tests write must not be writable by a group: the loader would skip them."""
    import os
    previous = os.umask(0o022)
    yield
    os.umask(previous)


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
        assert engine.workspace_instructions is None and engine.work_checkpoint is not None  # main's default
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


# ---- review round of 2026-10-26 (#500 reviews) ----

class _Recorder:
    name = "fake"

    def __init__(self):
        self.calls = []

    async def stream(self, messages, tools, **opts):
        from exo.core.models.fake import FakeProvider
        self.calls.append({"system": messages[0].text(),
                           "user": next(m.text() for m in reversed(messages) if m.role == "user")})
        async for event in FakeProvider(["done"]).stream(messages, tools):
            yield event


def test_composed_instructions_follow_each_session_and_child_not_the_config_cwd(tmp_path, monkeypatch):
    """compose.create() is the production path: two sessions in two repositories and a delegated child each read
    their own cwd's guidance, never the config cwd's."""
    from exo.core.tools.registry import ToolContext
    repos = {}
    for name in ("config", "alpha", "beta"):
        root = tmp_path / name
        (root / ".git").mkdir(parents=True)
        (root / "AGENTS.md").write_text(f"{name.upper()} GUIDANCE")
        repos[name] = root
    configured(tmp_path, monkeypatch, "coding", cwd=str(repos["config"]), learn={"enabled": False})

    async def run():
        engine = compose.create()
        engine.loop.provider = recorder = _Recorder()
        try:
            a = await engine.loop.create_session(cwd=str(repos["alpha"]))
            b = await engine.loop.create_session(cwd=str(repos["beta"]))
            first, second = await engine.loop.base_system(a.id), await engine.loop.base_system(b.id)
            assert "ALPHA GUIDANCE" in first and "BETA GUIDANCE" not in first and "CONFIG GUIDANCE" not in first
            assert "BETA GUIDANCE" in second and "ALPHA GUIDANCE" not in second and "CONFIG GUIDANCE" not in second
            result = await engine.agents.tool({"goal": "CHILD_TASK", "role": "scout"},
                                              ToolContext(session=b.id, cwd=str(repos["beta"])))
            assert "Done:" in result
            [child] = [call for call in recorder.calls if "CHILD_TASK" in call["user"]]
            assert "BETA GUIDANCE" in child["system"] and "CONFIG GUIDANCE" not in child["system"]
        finally:
            await engine.close()

    asyncio.run(run())


def test_an_explicit_profile_without_instructions_overrides_the_legacy_setting(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch, "lean", workspace_instructions={"enabled": True})
    (tmp_path / "AGENTS.md").write_text("MUST NOT APPEAR")

    async def run():
        engine = compose.create()
        try:
            session = await engine.loop.create_session(cwd=str(tmp_path))
            assert "MUST NOT APPEAR" not in await engine.loop.base_system(session.id)
            assert engine.workspace_instructions is not None and not engine.workspace_instructions.enabled
        finally:
            await engine.close()

    asyncio.run(run())


def test_a_gate_that_registers_no_hook_is_refused_beside_an_allow_all_hook(tmp_path, monkeypatch):
    plugins = tmp_path / "stub-plugins"
    for name, body in (("exo-gate", "def register(ctx):\n    pass\n"),
                       ("shim", "def register(ctx):\n    ctx.register_hook('pre_tool_call', lambda **kw: None)\n")):
        (plugins / name).mkdir(parents=True)
        (plugins / name / "plugin.yaml").write_text(f"name: {name}\n")
        (plugins / name / "__init__.py").write_text(body)
    cfg = configured(tmp_path, monkeypatch, core_plugins=["shim"])
    monkeypatch.setenv("EXO_PLUGIN_DIRS", str(plugins))
    with pytest.raises(ConfigError, match="working exo-gate"):
        compose.create()
    assert not (cfg.home / "sessions.sqlite").exists()


def test_legacy_job_and_delegation_switches_stay_inert_as_before(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch, None, core_plugins=["exo-gate"], learn={"enabled": False},
               jobs={"enabled": False}, delegation={"enabled": False})
    engine = compose.create()
    try:
        assert not isinstance(engine.jobs, DisabledJobs) and not isinstance(engine.agents, DisabledDelegator)
        assert {"delegate", "jobs"} <= set(engine.registry.names())
    finally:
        asyncio.run(engine.close())


def test_an_explicit_profile_honours_job_and_delegation_switches(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch, "coding", learn={"enabled": False},
               jobs={"enabled": False}, delegation={"enabled": False})
    engine = compose.create()
    try:
        assert isinstance(engine.jobs, DisabledJobs) and isinstance(engine.agents, DisabledDelegator)
    finally:
        asyncio.run(engine.close())


def test_a_required_executor_plugin_outside_the_profile_names_core_plugins(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from exo.core.tools.executor import ExecutorConfigurationError
    cfg = configured(tmp_path, monkeypatch)
    broker = SimpleNamespace(trusted_plugin_dirs=(str(REPO / "plugins"),), required_plugins=("vendor-audit",))
    monkeypatch.setattr(compose, "load_executor", lambda: (broker, True))
    with pytest.raises(ExecutorConfigurationError, match=r"disables a required executor plugin \(vendor-audit\).*core_plugins"):
        compose.create()
    assert not (cfg.home / "sessions.sqlite").exists()


# ---- main's continuity checkpoint (#761, D-086 amendment) under profiles ----

def test_child_loops_share_the_checkpoint_prompt_but_it_records_only_root_threads(tmp_path, monkeypatch):
    """The checkpoint follows its own key in every profile, as the work notebook does, so lean keeps it. It joins
    the workspace prompt that this PR gives scheduled and delegated loops, and it still records root threads only."""
    from exo.core.models.fake import FakeProvider
    cfg = configured(tmp_path, monkeypatch)
    cfg.set("harness.enable", ["scheduler"])
    cfg.save()

    async def run():
        engine = compose.create()
        engine.loop.provider = FakeProvider(["scheduled report", "hello back"])
        try:
            assert engine.work_checkpoint is not None
            job = engine.scheduler.add("every 1h", "Report on this workspace", title="Report")
            assert await engine.scheduler.run(job, "scheduled-test") == "done"
            scheduled = [row["id"] for row in engine.store._q("SELECT id FROM sessions WHERE kind='scheduled'")]
            assert len(scheduled) == 1 and engine.loop.workspace_extra(scheduled[0], str(tmp_path)) == ""
            thread = await engine.loop.create_session(cwd=str(tmp_path), agent=engine.agent)
            await engine.loop.send(thread.id, "hello")
            rows = engine.store._q("SELECT session FROM work_checkpoints")
            assert [row["session"] for row in rows] == [thread.id]
        finally:
            await engine.close()

    asyncio.run(run())


def test_the_workspace_prompt_keeps_main_s_order_instructions_notes_then_checkpoint(tmp_path, monkeypatch):
    """compose attaches the instructions, then the notebook, then the checkpoint, so the prompt reads in main's
    order. Each block wraps the one before it."""
    configured(tmp_path, monkeypatch, "coding", learn={"enabled": False}, work_notes={"enabled": True})
    (tmp_path / "AGENTS.md").write_text("WORKSPACE GUIDANCE")

    async def run():
        engine = compose.create()
        try:
            engine.work_notebook.render = lambda session: "NOTEBOOK BLOCK"
            engine.work_checkpoint.render = lambda session, cwd="": "CHECKPOINT BLOCK"
            session = await engine.loop.create_session(cwd=str(tmp_path), agent=engine.agent)
            text = engine.loop.workspace_extra(session.id, str(tmp_path))
            assert text.index("WORKSPACE GUIDANCE") < text.index("NOTEBOOK BLOCK") < text.index("CHECKPOINT BLOCK")
            assert text.endswith("NOTEBOOK BLOCK\n\nCHECKPOINT BLOCK")
        finally:
            await engine.close()

    asyncio.run(run())
