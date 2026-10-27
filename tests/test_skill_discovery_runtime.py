"""Real composition and model requests for opt-in local skill selection."""
import asyncio
import json
from pathlib import Path

import pytest

from exo import compose
from exo.config import Config, ConfigError
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.tools.executor import ExecutorConfigurationError

REPO = Path(__file__).resolve().parents[1]


def setup(tmp_path, monkeypatch, discovery=None, enabled=True, extra=()):
    monkeypatch.delenv("EXO_PROFILE", raising=False)
    monkeypatch.setenv("EXO_PLUGIN_DIRS", str(REPO / "plugins"))
    monkeypatch.setenv("EXO_SKILLS_PACK", str(tmp_path / "empty-pack"))
    home = tmp_path / "home"
    monkeypatch.setenv("EXO_HOME", str(home))
    principal = tmp_path / "principal"
    principal.mkdir()
    cfg = Config({"harness": {"profile": "lean", "enable": [*(["skills"] if enabled else []), *extra]},
        "models": {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, "cwd": str(tmp_path),
        "plugin_config": {"exo-gate": {"principal_store": str(principal),
            "key_path": str(tmp_path / "gate.key"), "decision_model": False}}}, home)
    if discovery is not None:
        cfg.data["skill_discovery"] = discovery
    cfg.save()
    for name, desc in [("sql", "Postgres migration"), ("art", "Image illustration")]:
        folder = home / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nBODY FOR {name}\n")
    return cfg


def test_composed_turn_selects_then_loads_unlisted_explicit_name(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, {"enabled": True, "top_k": 1, "max_chars": 240})

    async def run():
        engine = compose.create()
        engine.loop.provider = provider = FakeProvider([tool_turn("skill", {"name": "art"}), "done"])
        try:
            session = await engine.loop.create_session(cwd=str(tmp_path))
            base = await engine.loop.base_system(session.id)
            await engine.loop.send(session.id, "Fix the Postgres migration")
            prompt = provider.calls[0]["messages"][0].text()
            assert prompt.startswith(base)
            assert 0 < len(prompt) - len(base) <= 240
            assert "- sql:" in prompt and "- art:" not in prompt and "# Skills\n" not in prompt
            assert any("BODY FOR art" in r.text() for m in provider.calls[1]["messages"] for r in m.tool_results)
            assert "Postgres migration" not in engine.loop.system_prompt
        finally:
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("discovery", [None, {"enabled": False}])
def test_default_static_catalog_unchanged(tmp_path, monkeypatch, discovery):
    setup(tmp_path, monkeypatch, discovery)
    engine = compose.create()
    try:
        assert engine.skills.prompt() in engine.loop.system_prompt
        assert engine.loop.turn_extra is None
        assert asyncio.run(engine.loop._system("unused", "hello"))[0] == asyncio.run(engine.loop.base_system("unused"))
    finally:
        asyncio.run(engine.close())


@pytest.mark.parametrize("allowed", [[], ["sql"]])
def test_seat_skill_allowlist_applies_to_selection_and_loading(tmp_path, monkeypatch, allowed):
    cfg = setup(tmp_path, monkeypatch, {"enabled": True})
    seats = cfg.home / "seats"
    seats.mkdir()
    (seats / "worker.json").write_text(json.dumps({"name": "worker", "skills": {"load": allowed}}))
    engine = compose.create("worker")
    try:
        assert set(engine.skills.skills) == set(allowed)
        assert engine.skill_discovery.select("art image sql migration").names == tuple(allowed)
        assert "art" not in engine.skills.skills
    finally:
        asyncio.run(engine.close())


def test_zero_budget_keeps_system_bytes_unchanged(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, {"enabled": True, "max_chars": 0})
    engine = compose.create()
    try:
        assert asyncio.run(engine.loop._system("unused", "Postgres"))[0] == asyncio.run(engine.loop.base_system("unused"))
    finally:
        asyncio.run(engine.close())


def test_folder_skill_shadows_same_name_plugin_metadata(tmp_path, monkeypatch):
    cfg = setup(tmp_path, monkeypatch, {"enabled": True})
    plugin = cfg.home / "plugins" / "example"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: example\n")
    (plugin / "SKILL.md").write_text("---\ndescription: remotebeta\n---\nPLUGIN BODY\n")
    (plugin / "__init__.py").write_text(
        'from pathlib import Path\ndef register(ctx):\n'
        '    ctx.register_skill("review", Path(__file__).with_name("SKILL.md"), "remotebeta")\n')
    local = cfg.home / "skills" / "review"
    local.mkdir()
    (local / "SKILL.md").write_text("---\nname: example:review\ndescription: localalpha\n---\nLOCAL BODY\n")
    cfg.set("core_plugins", ["exo-gate", "example"])
    cfg.save()
    engine = compose.create()
    try:
        assert engine.skill_discovery.select("localalpha").names == ("example:review",)
        assert engine.skill_discovery.select("remotebeta").names == ()
        assert "LOCAL BODY" in engine.skills.load("review")
    finally:
        asyncio.run(engine.close())


def test_soul_and_metadata_refresh_preserve_bounded_turn_selection(tmp_path, monkeypatch):
    cfg = setup(tmp_path, monkeypatch, {"enabled": True})
    seat = cfg.home / "seats" / "worker"
    seat.mkdir(parents=True)
    (seat / "seat.toml").write_text('[seat]\nname = "worker"\n')
    soul = seat / "SOUL.md"
    soul.write_text("OLD SOUL")

    async def run():
        engine = compose.create("worker")
        engine.loop.provider = provider = FakeProvider(["done"], repeat=True)
        try:
            session = await engine.loop.create_session(cwd=str(tmp_path))
            await engine.loop.send(session.id, "Postgres migration")
            soul.write_text("REVISED SOUL GUIDANCE")
            path = cfg.home / "skills" / "sql" / "SKILL.md"
            path.write_text(path.read_text().replace("Postgres migration", "SQLite maintenance"))
            await engine.loop.send(session.id, "SQLite maintenance")
            prompt = provider.calls[-1]["messages"][0].text()
            assert "REVISED SOUL GUIDANCE" in prompt and "OLD SOUL" not in prompt
            assert "- sql: SQLite maintenance" in prompt and "# Skills\n" not in prompt
        finally:
            await engine.close()
    asyncio.run(run())


def test_disabled_skill_capability_does_not_construct_selector(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, {"enabled": True}, enabled=False)
    import exo.skill_discovery as module
    monkeypatch.setattr(module, "SkillDiscovery", lambda *a, **k: pytest.fail("disabled discovery constructed"))
    engine = compose.create()
    try:
        assert engine.loop.turn_extra is None and engine.skill_discovery is None
        assert not engine.skills.skills and "skill" not in engine.registry.names()
    finally:
        asyncio.run(engine.close())


def test_invalid_settings_and_required_executor_refused_before_discovery(tmp_path, monkeypatch):
    cfg = setup(tmp_path, monkeypatch, {"enabled": "yes"})
    with pytest.raises(ConfigError, match="enabled"):
        compose.create()
    assert not (cfg.home / "sessions.sqlite").exists()
    cfg.set("skill_discovery.enabled", True)
    cfg.save()
    monkeypatch.setattr(compose, "load_executor", lambda: (None, True))
    with pytest.raises(ExecutorConfigurationError, match="skill discovery needs executor-backed"):
        compose.create()
    assert not (cfg.home / "sessions.sqlite").exists()


def test_concurrent_turns_and_scheduled_query_have_own_selection(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, {"enabled": True, "top_k": 1}, extra=["scheduler"])

    async def run():
        engine = compose.create()
        engine.loop.provider = provider = FakeProvider(["done"], repeat=True)
        # The scheduler uses the existing route API and a separate Loop instance.
        engine.agents.route = lambda tier: (provider, "fake:x")
        try:
            first = await engine.loop.create_session(cwd=str(tmp_path))
            second = await engine.loop.create_session(cwd=str(tmp_path))
            await asyncio.gather(engine.loop.send(first.id, "Postgres migration"),
                                 engine.loop.send(second.id, "Image illustration"))
            for call in provider.calls:
                query = next(m.text() for m in reversed(call["messages"]) if m.role == "user")
                prompt = call["messages"][0].text()
                assert ("- sql:" in prompt) == ("Postgres" in query)
                assert ("- art:" in prompt) == ("Image" in query)
            job = engine.scheduler.add("every 1h", "Image illustration", title="draw")
            assert await engine.scheduler.run(job, "skill-selection-test") == "done"
            prompt = provider.calls[-1]["messages"][0].text()
            assert "- art:" in prompt and "- sql:" not in prompt
        finally:
            await engine.close()
    asyncio.run(run())


def test_delegated_child_keeps_skills_out_of_its_prompt_and_tools(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, {"enabled": True}, extra=["delegation"])

    async def run():
        engine = compose.create()
        engine.loop.provider = provider = FakeProvider([
            tool_turn("delegate", {"goal": "Image illustration", "role": "scout"}), "child done", "parent done"])
        try:
            session = await engine.loop.create_session(cwd=str(tmp_path))
            await engine.loop.send(session.id, "Postgres migration")
            assert len(provider.calls) == 3
            assert "- sql:" in provider.calls[0]["messages"][0].text()
            assert "# Relevant skills" not in provider.calls[1]["messages"][0].text()
            assert "skill" not in {tool.name for tool in provider.calls[1]["tools"]}
        finally:
            await engine.close()
    asyncio.run(run())
