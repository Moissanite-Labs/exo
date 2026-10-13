"""Issue #97: inspect, steer and stop a running subagent from its parent (D-040 authors, D-052 who may steer).

Everything goes through the existing ``command.run`` built-in ``subagent`` (no protocol command was added) or
the ``exo.subagents`` functions a parent-session tool calls. Fake models only."""

from __future__ import annotations

import asyncio

import pytest

from exo import provenance, subagents
from exo.core.models.fake import tool_turn
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo.server.engine import CommandFailed

from .test_delegate import engine, tool_text


def make(tmp_path):
    gate = asyncio.Event()
    rules = [("PARENT", [tool_turn("delegate", {"goal": "CHILD-A work", "role": "builder"}), "parent done"]),
             ("CHILD-A", [gate, "child done"])]
    return engine(tmp_path, rules)[0], gate


async def start(eng):
    """Run a parent turn that delegates one child; return (parent session, child session, parent turn task)."""
    info = await eng.loop.create_session(title="parent")
    turn = asyncio.ensure_future(eng.loop.send(info.id, "PARENT go"))
    for _ in range(200):
        if eng.agents.loops:
            break
        await asyncio.sleep(0.02)
    (child,) = eng.agents.loops
    for _ in range(200):
        if eng.loop.state(child).running:
            break
        await asyncio.sleep(0.02)
    return info.id, child, turn


def run_cmd(d, args, session=None):
    return d.run(s.Command.of(s.CommandRun(name="subagent", args=args, session=session)))


def task_id(eng):
    return eng.agents.list()[0]["task_id"]


def users(eng, child):
    return [(m.text(), provenance.author_of(m)) for m in eng.store.messages(child) if m.role == "user"]


def test_principal_steer_is_authored_by_principal_and_recorded(tmp_path):
    async def go():
        eng, gate = make(tmp_path)
        parent, child, turn = await start(eng)
        d = Dispatcher(eng, caller="principal:tui")
        r = await run_cmd(d, f"send {task_id(eng)} CHILD-A use the other file")
        assert r.ok, r.error
        assert r.result["output"]["steered"] and r.result["output"]["author"] == "principal"
        gate.set()
        await turn
        assert ("CHILD-A use the other file", "principal") in users(eng, child)
        receipt = tool_text(eng, parent)
        assert "steered by principal" in receipt
        assert eng.store.get_session(child) is not None and eng.agents.marks == {}

    asyncio.run(go())


def test_parent_session_steer_is_authored_by_delegate_not_principal(tmp_path):
    async def go():
        eng, gate = make(tmp_path)
        parent, child, turn = await start(eng)
        out = await subagents.steer(eng, task_id(eng), "CHILD-A narrow it down", by=parent)
        assert out["author"] == provenance.DELEGATE
        gate.set()
        await turn
        authors = dict(users(eng, child))
        assert authors["CHILD-A narrow it down"] == provenance.DELEGATE
        assert not provenance.is_principal_author(authors["CHILD-A narrow it down"])
        assert "steered by parent" in tool_text(eng, parent)

    asyncio.run(go())


def test_only_principal_or_the_parent_may_steer_or_stop(tmp_path):
    async def go():
        eng, gate = make(tmp_path)
        parent, child, turn = await start(eng)
        tid = task_id(eng)
        local = Dispatcher(eng, caller="local")
        for args in (f"send {tid} CHILD-A hi", f"stop {tid}"):
            r = await run_cmd(local, args)
            assert not r.ok and r.error.code == "forbidden"
        other = await eng.loop.create_session(title="someone else")
        for fn in (lambda: subagents.steer(eng, tid, "x", by=other.id), lambda: subagents.stop(eng, tid, by=other.id)):
            with pytest.raises(CommandFailed) as e:
                await fn()
            assert e.value.code == "forbidden"
        assert eng.loop.state(child).steer == [] and eng.agents.loops  # nothing landed, nothing stopped
        gate.set()
        await turn
        assert [t for t, _ in users(eng, child)] == [users(eng, child)[0][0]]

    asyncio.run(go())


def test_stop_running_child_records_on_receipt(tmp_path):
    async def go():
        eng, gate = make(tmp_path)
        parent, child, turn = await start(eng)
        d = Dispatcher(eng, caller="principal:web")
        r = await run_cmd(d, f"stop {task_id(eng)}")
        assert r.ok and r.result["output"]["stopped"]
        await turn
        row = eng.agents.list()[0]
        assert row["status"] == "cancelled" and "stopped by principal" in row["detail"]
        assert "stopped by principal" in tool_text(eng, parent)
        gate.set()

    asyncio.run(go())


def test_finished_child_is_a_clear_noop(tmp_path):
    async def go():
        eng, gate = make(tmp_path)
        parent, child, turn = await start(eng)
        gate.set()
        await turn
        tid, before = task_id(eng), len(eng.store.messages(child))
        d = Dispatcher(eng, caller="principal:tui")
        for args in (f"send {tid} CHILD-A late", f"stop {tid}"):
            r = await run_cmd(d, args)
            assert not r.ok and r.error.code == "conflict" and "finished" in r.error.message and "done" in r.error.message
        assert len(eng.store.messages(child)) == before and eng.agents.marks == {}
        assert eng.agents.list()[0]["status"] == "done"

    asyncio.run(go())


def test_list_and_open_inspect_a_running_child(tmp_path):
    async def go():
        eng, gate = make(tmp_path)
        parent, child, turn = await start(eng)
        d = Dispatcher(eng, caller="principal:tui")
        lst = (await run_cmd(d, "list", parent)).result["output"]
        (row,) = lst["tasks"]
        assert row["child_session"] == child and row["live"] is True and row["status"] == "running"
        view = (await run_cmd(d, f"open {task_id(eng)[:8]}")).result["output"]  # a unique prefix is enough
        assert view["child_session"] == view["session"]["id"] == child and view["steerable"] is True
        assert any("CHILD-A" in m["text"] for m in view["messages"])
        miss = await run_cmd(d, "open k_nope")
        assert not miss.ok and miss.error.code == "not_found"
        bad = await run_cmd(d, "frobnicate")
        assert not bad.ok and bad.error.code == "bad_request"
        gate.set()
        await turn
        assert (await run_cmd(d, f"open {task_id(eng)}")).result["output"]["steerable"] is False

    asyncio.run(go())
