"""Issue #97: inspect, steer and stop a running subagent from its parent (D-040 authors, D-052 who may steer).

Everything goes through the existing ``command.run`` built-in ``subagent`` (no protocol command was added) or
the ``exo.subagents`` functions a parent-session tool calls. Fake models only."""

from __future__ import annotations

import asyncio

import pytest

from exo import provenance, subagents
from exo.core import trace
from exo.core.ingress import Ingress
from exo.core.models.fake import tool_turn
from exo.core.types import ToolSpec
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


# ---------------------------------------------------------------- the child's session state is the engine's

def turns(eng, sid):
    return [type(e.body).__name__ for e in eng.hub.since(0, sid) if isinstance(e.body, (s.TurnStarted, s.TurnCompleted))]


def turn_send(d, child, text, mode):
    return d.run(s.Command.of(s.TurnSend(session=child, text=text, mode=mode)))


def test_typing_into_a_running_child_steers_it(tmp_path):
    """D-070: the principal typing into a child's session steers it through turn.send, whatever the client's mode.
    The child shows live and runs one turn; its own Loop no longer looks idle, so no second turn starts."""
    async def go():
        eng, gate = make(tmp_path)
        parent, child, turn = await start(eng)
        assert eng.loop.state(child).running
        listed = (await eng._session_list(s.SessionList()))["sessions"]
        assert [r["status"] for r in listed if r["id"] == child] == ["live"]
        d = Dispatcher(eng, caller="principal:tui")
        for mode in ("send", "queue", "steer"):
            r = await turn_send(d, child, f"CHILD-A note by {mode}", mode)
            assert r.ok and r.result["steered"] and r.result["message_id"], r.error
        gate.set()
        await turn
        assert turns(eng, child) == ["TurnStarted", "TurnCompleted"]
        for mode in ("send", "queue", "steer"):
            assert (f"CHILD-A note by {mode}", "principal") in users(eng, child)
        assert eng.agents.list()[0]["status"] == "done"

    asyncio.run(go())


def test_only_the_principal_can_type_into_a_running_child(tmp_path):
    """D-052 on the turn.send path too: a local caller gets forbidden and nothing lands in the child."""
    async def go():
        eng, gate = make(tmp_path)
        parent, child, turn = await start(eng)
        before = users(eng, child)
        local = Dispatcher(eng, caller="local")
        for mode in ("send", "queue", "steer"):
            r = await turn_send(local, child, "CHILD-A from a script", mode)
            assert not r.ok and r.error.code == "forbidden"
        assert eng.loop.state(child).steer == []
        gate.set()
        await turn
        assert users(eng, child) == before and turns(eng, child) == ["TurnStarted", "TurnCompleted"]

    asyncio.run(go())


def test_resuming_a_running_child_leaves_its_tool_call_alone(tmp_path):
    """session.resume on a child whose tool is still running writes no 'not run' result for that call."""
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        rules = [("PARENT", [tool_turn("delegate", {"goal": "CHILD-A work", "role": "builder"}), "parent done"]),
                 ("CHILD-A", [tool_turn("child_hold"), "child done"])]
        eng = engine(tmp_path, rules)[0]

        async def hold(args, ctx):
            entered.set()
            await release.wait()
            return "held result"

        eng.registry.register(ToolSpec(name="child_hold", description="held fake tool", risk="low", toolset="test"), hold)
        parent, child, turn = await start(eng)
        await asyncio.wait_for(entered.wait(), 2)
        got = await eng._session_resume(s.SessionResume(session=child))
        assert not [m for m in got["messages"] if m["role"] == "tool"]
        release.set()
        await turn
        assert [(r.call_id, r.text()) for m in eng.store.messages(child) for r in m.tool_results] == [
            ("call_1", "held result")]

    asyncio.run(go())


def test_an_unaddressed_peer_message_never_lands_in_a_running_child(tmp_path):
    """A running child now reads as running; it is still never a peer's pick (only its principal or parent steers it)."""
    async def go():
        eng, gate = make(tmp_path)
        parent, child, turn = await start(eng)
        target = eng._peer_target
        assert target.state(child) == "running"
        assert child not in {r["id"] for r in target.live_sessions()}
        assert target.pick_session(Ingress("peer", "x", "trusted", "hi")) == parent
        gate.set()
        await turn

    asyncio.run(go())


def test_a_harness_error_in_a_running_grandchild_is_still_filed(tmp_path):
    """A grandchild reads as running in the engine's table, yet no turn-end check reads its errors (child loops have
    none, and the parent's reads only direct children): its harness error becomes a finding at once."""
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        rules = [("GRAND", [tool_turn("grand_hold"), "grand done"]),
                 ("PARENT", [tool_turn("delegate", {"goal": "CHILD-A work", "role": "builder"}), "parent done"]),
                 ("CHILD-A", [tool_turn("delegate", {"goal": "GRAND work", "role": "builder"}), "child done"])]
        eng = engine(tmp_path, rules, max_depth=2)[0]

        async def hold(args, ctx):
            entered.set()
            await release.wait()
            return "held result"

        eng.registry.register(ToolSpec(name="grand_hold", description="held fake tool", risk="low", toolset="test"), hold)
        info = await eng.loop.create_session(title="parent")
        turn = asyncio.ensure_future(eng.loop.send(info.id, "PARENT go"))
        await asyncio.wait_for(entered.wait(), 5)
        (grand,) = [sid for sid in eng.agents.loops if any("GRAND" in m.text() for m in eng.store.messages(sid))]
        assert eng.loop.state(grand).running
        trace.report("grand plugin", RuntimeError("boom in a grandchild"), session=grand)
        release.set()
        await turn
        assert [f["session"] for f in eng.findings.list() if "grand plugin" in f["signature"]] == [grand]

    asyncio.run(go())
