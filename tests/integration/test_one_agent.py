"""One agent, one memory through the engine (D-028): threads, private threads, the writer lock, memory_zoom,
restart, and the log view the memory provider gets."""

from __future__ import annotations

import asyncio

import pytest

from exo.core import agentlog
from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.core.tools.registry import ToolContext
from exo.core.types import ToolCall
from exo.protocol import schema as s
from exo.server.app import Dispatcher

MODELS = {"fake": {"rules": []}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def _engine(home, memory=None) -> CoreEngine:
    pm = PluginManager()
    if memory is not None:
        pm.slots.memory = memory
    return CoreEngine(home, MODELS, plugins=pm, cwd=str(home))


async def _turn(d: Dispatcher, eng: CoreEngine, sid: str, text: str) -> None:
    assert (await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))).ok
    while eng._tasks:
        await asyncio.sleep(0.01)


async def _new(d: Dispatcher, **kw) -> dict:
    resp = await d.run(s.Command.of(s.SessionCreate(**kw)))
    assert resp.ok, resp.error
    return resp.result["session"]


def test_threads_of_one_agent_share_the_log_and_survive_a_restart(tmp_path):
    home = tmp_path / "home"
    eng = _engine(home)

    async def first():
        d = Dispatcher(eng)
        a = await _new(d, title="window A")
        assert a["agent"] == "default" and a["kind"] == "thread"
        await _turn(d, eng, a["id"], "remember the word heliotrope")
        b = await _new(d, title="window B")
        await _turn(d, eng, b["id"], "what word?")
        p = await _new(d, kind="private")
        assert p["kind"] == "private"
        bad = await d.run(s.Command.of(s.SessionCreate(kind="subagent")))
        assert not bad.ok and bad.error.code == "bad_request"
        msgs = (await d.run(s.Command.of(s.SessionGet(session=b["id"])))).result["messages"]
        note = [m for m in msgs if (m.get("meta") or {}).get("note")]
        assert note and "heliotrope" in note[0]["content"][0]["text"]
        await eng.close()
        return a, b
    a, b = asyncio.run(first())

    eng2 = _engine(home)  # restart: same memory

    async def second():
        d = Dispatcher(eng2)
        rows = (await d.run(s.Command.of(s.SessionList()))).result["sessions"]
        assert {r["id"] for r in rows} >= {a["id"], b["id"]}
        c = await _new(d)
        await _turn(d, eng2, c["id"], "and now?")
        await eng2.close()
    asyncio.run(second())
    assert eng2.store.verify_log("default")["ok"]
    assert eng2.store.last_pos("default") == 6


def test_one_writer_per_agent(tmp_path):
    fh = agentlog.claim_writer(tmp_path, "razi")
    with pytest.raises(agentlog.WriterBusy):
        agentlog.claim_writer(tmp_path, "razi")
    other = agentlog.claim_writer(tmp_path, "canary")  # another agent is another writer
    agentlog.release_writer(fh)
    again = agentlog.claim_writer(tmp_path, "razi")
    agentlog.release_writer(again)
    agentlog.release_writer(other)


def test_memory_zoom_and_the_view_the_memory_provider_gets(tmp_path):
    got = {}

    class Mem:
        def initialize(self, session_id, **kw):
            got.update(kw)

    eng = _engine(tmp_path / "home", Mem())
    view = got["agent_log"]
    assert isinstance(view, agentlog.AgentLogView) and view.agent == "default"

    async def go():
        d = Dispatcher(eng)
        a = await _new(d)
        await _turn(d, eng, a["id"], "the ferry leaves at nine")
        res = await eng.registry.call(ToolCall(id="z", name="memory_zoom", arguments={"start": 1}),
                                      ToolContext(cwd=str(tmp_path), session=a["id"]))
        await eng.close()
        return res.text()
    text = asyncio.run(go())
    assert "ferry leaves at nine" in text and text.startswith("#1 ")
    hits = view.search("ferry")
    assert hits and hits[0]["id"] == 1 and hits[0]["kind"] == "user"
    assert len(view) == 2 and view.date(1)
    assert "memory_zoom" in {sp.name for sp in eng._specs()}


def test_exo_memory_verify_and_threads(tmp_path, monkeypatch, capsys):
    from exo.cli import main
    from exo.core.sessions import SessionStore
    from exo.core.types import Message

    st = SessionStore(tmp_path / "sessions.sqlite")
    t = st.create_session(agent="razi", title="w1")
    st.append_message(t.id, Message.user("hi"))
    st.close()
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    assert main(["memory"]) == 0
    assert "razi: 1 entries, ok" in capsys.readouterr().out
    assert main(["memory", "threads", "--agent", "razi"]) == 0
    assert t.id in capsys.readouterr().out
