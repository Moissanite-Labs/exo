"""/context and /compact through the engine (D-028). They are built-in `command.run` commands, so the
protocol keeps its 30 commands; the core that holds the agent lock does the writing."""

from __future__ import annotations

import asyncio

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.protocol import schema as s
from exo.protocol.schema import COMMANDS
from exo.server.app import Dispatcher

MODELS = {"fake": {"rules": []}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
TREE = {"leaf_size": 2, "tail_messages": 4, "min_compact_tokens": 0}


def _engine(home) -> CoreEngine:
    return CoreEngine(home, MODELS, plugins=PluginManager(), cwd=str(home), tree_cfg=TREE)


async def _say(d, eng, sid, text):
    assert (await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))).ok
    while eng._tasks:
        await asyncio.sleep(0.01)


async def _cmd(d, name, sid=None, **kw):
    return await d.run(s.Command.of(s.CommandRun(name=name, session=sid, **kw)))


def test_no_new_protocol_command():
    assert len(COMMANDS) == 30
    assert "context" not in COMMANDS and "compact" not in COMMANDS
    assert "built-in" in (s.CommandRun.__doc__ or "")


def test_context_and_compact_run_through_command_run(tmp_path):
    eng = _engine(tmp_path / "home")

    async def go():
        d = Dispatcher(eng)
        names = {c["name"] for c in (await d.run(s.Command.of(s.CommandsList()))).result["commands"]}
        assert {"context", "compact"} <= names
        sid = (await d.run(s.Command.of(s.SessionCreate(title="A")))).result["session"]["id"]
        for i in range(6):
            await _say(d, eng, sid, f"fact {i} " + "word " * 40)
        before = eng.store.last_seq(sid)
        r = await _cmd(d, "context", sid)
        text = r.result["output"]
        for part in ("system", "summary tree", "other threads", "recent tail", "total", "pending", "skipped"):
            assert part in text, part
        assert eng.store.last_seq(sid) == before  # read-only
        c = await _cmd(d, "compact", sid)
        assert c.ok and "Compacted:" in c.result["output"] and "tokens" in c.result["output"]
        again = (await _cmd(d, "compact", sid)).result["output"]
        assert "0 node(s) folded" in again
        await eng.close()
    asyncio.run(go())


def test_context_refuses_without_a_thread(tmp_path):
    eng = _engine(tmp_path / "home")

    async def go():
        d = Dispatcher(eng)
        r = await _cmd(d, "context")
        assert not r.ok and r.error.code == "bad_request"
        await eng.close()
    asyncio.run(go())
