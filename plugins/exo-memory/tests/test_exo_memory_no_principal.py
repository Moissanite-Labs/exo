"""Memory with no principal is a setup step not taken yet, not a harness fault: one quiet notice that names
`exo init`, no harness.error, no finding, no memory tools the model could only fail with. With the principal
that the installer and `exo init` write to config.json, memory and learning both find the same store."""
import asyncio
import json
import logging

import pytest
from conftest import PLUGIN_ROOT

from exo import diagnostics
from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.core.slots import NotConfigured
from exo.learn.learner import Learner
from exo.protocol import schema as s
from exo.store import PrincipalStore

from exo_memory import ExoMemoryProvider

MODELS = {"fake": {"reply": "ok"}, "roles": {"main": ["fake:scripted"], "aux": ["fake:scripted"], "decision": []}}


def _engine(home, initialize=None):
    pm = PluginManager(config={"exo-memory": {"compact_enabled": False}})
    rec = pm.load_folder(PLUGIN_ROOT)
    assert rec.ok, rec.error
    if initialize is not None:
        pm.slots.memory.initialize = initialize
    return CoreEngine(home, MODELS, plugins=pm, env={})


async def _new_thread(eng):
    return (await eng.handle(s.Command.of(s.SessionCreate(title="t")), eng.hub))["session"]["id"]


def test_provider_without_a_principal_is_off_and_offers_no_tools(tmp_path):
    p = ExoMemoryProvider({"compact_enabled": False})
    assert not p.is_available() and "exo init" in p.unavailable_reason()
    assert p.get_tool_schemas()  # before initialize nothing is known: a lazy host still sees the tools
    with pytest.raises(NotConfigured) as err:
        p.initialize("s1", exo_home=str(tmp_path / "exo-home"))
    assert str(err.value) == p.unavailable_reason()
    assert "memory is off" in str(err.value) and "exo init <id>" in str(err.value)
    assert p.get_tool_schemas() == []
    assert p.system_prompt_block() == ""
    assert "exo init" in json.loads(p.handle_tool_call("exo_recall", {"query": "x"}))["error"]


def test_core_without_a_principal_shows_one_quiet_notice_and_files_nothing(tmp_path, caplog):
    home = tmp_path / "exo-home"
    with caplog.at_level(logging.DEBUG):
        eng = _engine(home)

        async def go():
            sid = await _new_thread(eng)
            return sid, await eng.handle(s.Command.of(s.CommandRun(name="memory_status", session=sid)), eng.hub)

        sid, status = asyncio.run(go())
    assert eng.memory_init_error == "" and "exo init" in eng.memory_off
    events = eng.hub.since(0)
    assert not [e for e in events if isinstance(e.body, s.HarnessError)]
    notices = [e.body for e in events if isinstance(e.body, s.Notice) and "memory" in e.body.text]
    assert [(n.level, n.text) for n in notices] == [("info", eng.memory_off)]
    assert eng.findings.list() == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "memory" in r.getMessage()]
    assert not {"exo_recall", "exo_note", "exo_get", "exo_should_have_been"} & set(eng.registry.names())
    assert status["output"] == {"state": "off", "provider": "exo-memory", "text": eng.memory_off}


def test_installer_principal_turns_on_memory_and_learning_on_one_store(tmp_path):
    """`install.sh --principal sam` and `exo init sam` write only config.json `principal`."""
    home = tmp_path / "exo-home"
    PrincipalStore.create(home / "principals" / "sam")
    (home / "config.json").write_text(json.dumps({"version": 1, "principal": "sam"}))
    eng = _engine(home)
    try:
        asyncio.run(_new_thread(eng))
        assert eng.memory_off == "" and eng.memory_init_error == ""
        assert diagnostics.memory_status(eng)["state"] == "on"
        assert "exo_recall" in eng.registry.names()
        mem_root = eng.plugins.slots.memory._state().rt.root
        lr = Learner(eng, env={})
        assert lr.runtime is not None and lr.notices == []
        assert lr.runtime.root.resolve() == mem_root == (home / "principals" / "sam").resolve()
        lr.close()
    finally:
        eng.plugins.slots.memory.shutdown()


def test_a_real_start_failure_is_still_loud(tmp_path):
    """Only the setup state is quiet: an error while starting stays a warning, a harness.error and a finding."""
    home = tmp_path / "exo-home"
    PrincipalStore.create(home / "principals" / "sam")
    (home / "config.json").write_text(json.dumps({"version": 1, "principal": "sam"}))

    def broken(session_id, **kw):
        raise OSError("ledger is locked")

    eng = _engine(home, broken)
    asyncio.run(_new_thread(eng))
    assert eng.memory_off == "" and "ledger is locked" in eng.memory_init_error
    assert diagnostics.memory_status(eng)["state"] == "failed"
    events = eng.hub.since(0)
    assert [e.body.where for e in events if isinstance(e.body, s.HarnessError)] == ["memory.initialize"]
    assert any(isinstance(e.body, s.Notice) and e.body.level == "warn" and "did not start" in e.body.text
               for e in events)
    assert [f["signature"] for f in eng.findings.list()] == ["harness.error:memory.initialize:OSError"]
