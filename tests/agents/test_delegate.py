"""The delegate tool on the standalone core: child sessions, receipts, tasks, limits, cancel. Fake models only."""

from __future__ import annotations

import asyncio
import json

from exo import crypto
from exo.core.agents import BLOCKED_TOOLS, Delegator
from exo.core.agents.delegate import CHILD_PROMPT
from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.types import ToolSpec, Usage
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo.wire import Receipt, decode, verify


class Keyed:
    """Fake provider keyed by the latest user message: the first rule whose key it contains gives the steps;
    the number of assistant replies since that message picks the step. An asyncio.Event step holds the call
    until set (or forever), counting how many calls are held at once."""

    name = "fake"

    def __init__(self, rules):
        self.rules = rules
        self.active = self.peak = 0
        self.calls: list[dict] = []

    async def stream(self, messages, tools, **opts):
        users = [i for i, m in enumerate(messages) if m.role == "user"]
        text = messages[users[-1]].text() if users else ""
        step = sum(1 for m in messages[users[-1] + 1:] if m.role == "assistant") if users else 0
        system = messages[0].text() if messages and messages[0].role == "system" else ""
        self.calls.append({"user": text, "tools": sorted(t.name for t in tools), "system": system})
        spec = "fallback"
        for key, steps in self.rules:
            if key in text:
                spec = steps[min(step, len(steps) - 1)]
                break
        if isinstance(spec, asyncio.Event):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await spec.wait()
            finally:
                self.active -= 1
            spec = "released"
        async for ev in FakeProvider([spec]).stream(messages, tools):
            yield ev


def engine(tmp_path, rules, **limits) -> tuple[CoreEngine, Keyed]:
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={},
                     delegation=limits or None)
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    fake = Keyed(rules)
    eng.loop.provider = fake
    return eng, fake


async def parent_turn(eng: CoreEngine, text: str):
    info = await eng.loop.create_session(title="parent")
    res = await eng.loop.send(info.id, text)
    return info.id, res


def task_events(eng: CoreEngine) -> list[s.Event]:
    return [ev for ev in eng.hub.since(0) if isinstance(ev.body, s.TaskUpdated)]


def tool_text(eng: CoreEngine, sid: str) -> str:
    msgs = [m for m in eng.store.messages(sid) if m.role == "tool"]
    return msgs[0].tool_results[0].text()


def test_single_child_round_trip(tmp_path):
    rules = [("PARENT", [tool_turn("delegate", {"goal": "CHILD-A find the config loader", "role": "scout",
                                                "context": "Python repo", "accept": "name the file"}),
                         "parent done"]),
             ("CHILD-A", [tool_turn("glob", {"pattern": "*.nothing"}), "It is in config.py."])]
    eng, fake = engine(tmp_path, rules)
    sid, res = asyncio.run(parent_turn(eng, "PARENT go"))
    assert res.stop_reason == "end" and res.message.text() == "parent done"

    out = tool_text(eng, sid)
    assert out.startswith("Done: task k_") and "by a scout" in out and "Result: It is in config.py." in out
    assert "|" not in out  # the parent model reads English, never the packet

    evs = task_events(eng)
    assert [e.body.status for e in evs] == ["queued", "running", "done"]
    assert all(e.session == sid for e in evs)  # board events belong to the parent session
    done = evs[-1].body
    r = decode(done.packet)
    assert isinstance(r, Receipt) and r.status == "done" and r.child_session == done.child_session
    assert r.tool_calls == 1 and r.rounds == 1 and r.input_tokens > 0 and r.summary == "It is in config.py."
    assert done.detail == out and "2|" not in done.detail

    child = eng.store.get_session(done.child_session)
    assert child.parent == sid and child.cwd == str(tmp_path)
    assert child.kind == "subagent" and eng.store.messages(child.id)  # its own log, not the parent's (D-028)
    assert all(e.session != child.id for e in eng.store.log(eng.agent))
    first = next(c for c in fake.calls if "CHILD-A" in c["user"])
    assert [t for t in first["tools"] if t != "code_find"] == ["glob", "grep", "read_file"]  # scout (code_find is low risk, present when jg runs): low risk only, no delegate, no bash
    assert first["system"].startswith(CHILD_PROMPT) and str(tmp_path) in first["system"]
    assert first["system"].count(str(tmp_path)) == 1 and f"Your working directory is {child.cwd}." in first["system"]
    assert "Done when: name the file." in first["user"] and "Context: Python repo" in first["user"]

    async def listed():
        return (await Dispatcher(eng).run(s.Command.of(s.TasksList(session=sid)))).result["tasks"]
    rows = asyncio.run(listed())
    assert len(rows) == 1 and rows[0]["status"] == "done" and rows[0]["detail"] == out
    assert decode(rows[0]["brief"]).goal == "CHILD-A find the config loader"


def test_parallel_children_respect_the_concurrency_cap(tmp_path):
    gates = {k: asyncio.Event() for k in ("C1", "C2", "C3")}
    tasks = [{"goal": f"{k} work", "role": "builder"} for k in gates]
    rules = [("PARENT", [tool_turn("delegate", {"tasks": tasks}), "merged"])] + [(k, [g]) for k, g in gates.items()]
    eng, fake = engine(tmp_path, rules, max_concurrent=2)

    async def main():
        run = asyncio.ensure_future(parent_turn(eng, "PARENT fan out"))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if fake.active == 2:
                break
        assert fake.active == 2 and sum(e.body.status == "running" for e in task_events(eng)) == 2
        for g in gates.values():
            g.set()
        return await run

    sid, res = asyncio.run(main())
    assert res.message.text() == "merged" and fake.peak == 2
    out = tool_text(eng, sid)
    assert out.count("Done: task") == 3 and out.index("Task 1 of 3: C1") < out.index("Task 2 of 3: C2") < out.index("C3")
    receipts = [decode(e.body.packet) for e in task_events(eng) if e.body.status == "done"]
    assert len(receipts) == 3 and all(r.summary == "released" for r in receipts)


def test_interrupting_the_parent_cancels_every_child(tmp_path):
    hold = asyncio.Event()  # never set
    tasks = [{"goal": "HOLD-1"}, {"goal": "HOLD-2"}]
    eng, fake = engine(tmp_path, [("PARENT", [tool_turn("delegate", {"tasks": tasks}), "x"]),
                                  ("HOLD", [hold])])

    async def main():
        info = await eng.loop.create_session()
        run = asyncio.ensure_future(eng.loop.send(info.id, "PARENT go"))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if fake.active == 2:
                break
        assert fake.active == 2
        assert eng.loop.interrupt(info.id)
        return await asyncio.wait_for(run, 5)

    res = asyncio.run(main())
    assert res.stop_reason == "interrupted" and fake.active == 0
    final = [e.body for e in task_events(eng) if e.body.status not in ("queued", "running")]
    assert [b.status for b in final] == ["cancelled", "cancelled"]
    assert all(decode(b.packet).error_code == "cancelled" for b in final)
    assert not eng.agents.loops


def test_interrupting_one_child_from_a_client(tmp_path):
    hold = asyncio.Event()
    eng, fake = engine(tmp_path, [("PARENT", [tool_turn("delegate", {"goal": "HOLD"}), "parent saw it"]),
                                  ("HOLD", [hold])])

    async def main():
        d = Dispatcher(eng)
        run = asyncio.ensure_future(parent_turn(eng, "PARENT go"))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if fake.active == 1:
                break
        child = next(e.body.child_session for e in task_events(eng) if e.body.status == "running")
        resp = await d.run(s.Command.of(s.TurnInterrupt(session=child)))
        assert resp.ok and resp.result["interrupted"] == 1
        return await asyncio.wait_for(run, 5)

    sid, res = asyncio.run(main())
    assert res.message.text() == "parent saw it"
    r = decode(task_events(eng)[-1].body.packet)
    assert r.status == "cancelled" and r.error_code == "interrupted"


def test_timeout_turn_and_token_limits(tmp_path):
    hold = asyncio.Event()
    loop_forever = [tool_turn("glob", {"pattern": "*.none"})]
    rules = [("PARENT", [tool_turn("delegate", {"tasks": [{"goal": "SLOW"}, {"goal": "BUSY"}]}), "ok"]),
             ("SLOW", [hold]), ("BUSY", loop_forever)]
    eng, _ = engine(tmp_path, rules, timeout_s=3, max_turns=2, max_tokens=10_000_000)  # wide margin (issue #6)
    asyncio.run(parent_turn(eng, "PARENT go"))
    by_goal = {decode(e.body.packet).brief: decode(e.body.packet) for e in task_events(eng)
               if e.body.status == "failed"}
    codes = sorted(r.error_code for r in by_goal.values())
    assert codes == ["timeout", "turns"]

    eng2, _ = engine(tmp_path / "b", [("PARENT", [tool_turn("delegate", {"goal": "BUSY"}), "ok"]),
                                      ("BUSY", loop_forever)], max_tokens=1)
    sid, _ = asyncio.run(parent_turn(eng2, "PARENT go"))
    r = decode(task_events(eng2)[-1].body.packet)
    assert r.status == "failed" and r.error_code == "budget" and r.rounds == 1
    assert "Reason: budget" in tool_text(eng2, sid)



class Metered(Keyed):
    """Keyed, but every call reports the same fixed usage, as a provider with prompt caching would."""

    def __init__(self, rules, usage):
        super().__init__(rules)
        self.usage = usage

    async def stream(self, messages, tools, **opts):
        async for ev in super().stream(messages, tools, **opts):
            if ev.usage is not None:
                ev.usage = Usage(**vars(self.usage))
            yield ev


def test_child_budget_counts_new_tokens_not_cache_reads(tmp_path):
    """D-080: the per-child budget counts what each call newly processes (uncached input, cache writes, output),
    the same on every transport. Re-reading a cached context does not spend it; writing one to the cache does."""
    busy = [tool_turn("glob", {"pattern": "*.none"})]
    rules = [("PARENT", [tool_turn("delegate", {"goal": "BUSY"}), "ok"]), ("BUSY", busy)]

    def child(home, usage):
        eng, _ = engine(tmp_path / home, rules, max_turns=3, max_tokens=100_000)
        eng.loop.provider = Metered(rules, usage)
        asyncio.run(parent_turn(eng, "PARENT go"))
        return decode(task_events(eng)[-1].body.packet)

    reads = child("reads", Usage(input_tokens=10, cache_read_tokens=500_000, output_tokens=10))
    # three rounds and the wrap-up call at the round cap (#173); the cache reads are free
    assert reads.error_code == "turns" and reads.rounds == 3 and reads.input_tokens == 4 * 10
    writes = child("writes", Usage(input_tokens=10, cache_write_tokens=150_000, output_tokens=10))
    assert writes.status == "failed" and writes.error_code == "budget" and writes.rounds == 1
    assert writes.input_tokens == 150_010 and writes.output_tokens == 10  # the receipt says what the budget counted

def test_toolsets_narrow_and_never_widen(tmp_path):
    eng, _ = engine(tmp_path, [], max_depth=2)
    eng.registry.register(ToolSpec(name="memory", description="", toolset="memory"), lambda a, c: "")
    eng.registry.register(ToolSpec(name="bus_post", description="", toolset="bus", risk="medium"), lambda a, c: "")
    d: Delegator = eng.agents
    root = d._tools("", 1, "builder", None)
    assert {"bash", "write_file", "read_file", "delegate"} <= root and not (root & (BLOCKED_TOOLS - {"delegate"}))
    assert d._tools("", 1, "scout", ["read_file", "bash"]) == {"read_file", "delegate"}
    assert d._tools("", 1, "builder", ["bash"]) == {"bash", "delegate"}
    d.allow["s_child"] = {"read_file", "grep", "delegate"}
    assert d._tools("s_child", 2, "builder", ["write_file", "grep"]) == {"grep"}  # depth 2 = max: no delegate
    eng3, _ = engine(tmp_path / "flat", [])
    assert "delegate" not in eng3.agents._tools("", 1, "builder", None)  # flat by default


def test_depth_limit_and_bad_requests(tmp_path):
    rules = [("PARENT", [tool_turn("delegate", {"goal": "CHILD tries to recurse"}), "end"]),
             ("CHILD", [tool_turn("delegate", {"goal": "GRANDCHILD"}), "child gave up"])]
    eng, fake = engine(tmp_path, rules)
    sid, _ = asyncio.run(parent_turn(eng, "PARENT go"))
    child = decode(task_events(eng)[-1].body.packet).child_session
    child_tool = [m for m in eng.store.messages(child) if m.role == "tool"][0].tool_results[0]
    assert child_tool.is_error and "not available to this subagent" in child_tool.text()
    assert not any("GRANDCHILD" in c["user"] for c in fake.calls)

    for args, msg in (({"goal": " "}, "non-empty goal"), ({"goal": "x", "role": "boss"}, "must be one of"),
                      ({"tasks": [{"goal": "a"}] * 3}, "At most 2 tasks")):
        e2, _ = engine(tmp_path / msg.split()[0], [("PARENT", [tool_turn("delegate", args), "end"])],
                       max_children=2)
        sid2, _ = asyncio.run(parent_turn(e2, "PARENT go"))
        res = [m for m in e2.store.messages(sid2) if m.role == "tool"][0].tool_results[0]
        assert res.is_error and msg in res.text(), res.text()
        assert not task_events(e2)


def test_nested_delegation_when_depth_allows(tmp_path):
    rules = [("PARENT", [tool_turn("delegate", {"goal": "MID split this"}), "top done"]),
             ("MID", [tool_turn("delegate", {"goal": "LEAF do a part", "role": "scout"}), "mid done"]),
             ("LEAF", ["leaf done"])]
    eng, fake = engine(tmp_path, rules, max_depth=2)
    sid, res = asyncio.run(parent_turn(eng, "PARENT go"))
    assert res.message.text() == "top done"
    rows = eng.agents.list()
    assert [(r["depth"], r["status"]) for r in rows] == [(1, "done"), (2, "done")]
    assert rows[1]["parent"] == rows[0]["child_session"]
    leaf = next(c for c in fake.calls if "LEAF" in c["user"])
    assert "delegate" not in leaf["tools"]
    mid = next(c for c in fake.calls if "MID" in c["user"])
    assert "delegate" in mid["tools"] and "may use the `delegate` tool" in mid["system"]


def test_receipts_are_signed_and_restart_fails_stale_tasks(tmp_path):
    eng, _ = engine(tmp_path, [("PARENT", [tool_turn("delegate", {"goal": "CHILD"}), "end"]), ("CHILD", ["ok"])])
    signer = crypto.Signer("seat-a", crypto.keygen())
    eng.agents.signer = signer
    asyncio.run(parent_turn(eng, "PARENT go"))
    row = eng.agents.list()[0]
    assert row["sig"]["by"] == "seat-a" and verify(row["receipt"], row["sig"], signer.pub)

    eng.store._q("UPDATE tasks SET status='running', receipt=NULL")
    again = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path))
    assert [r["status"] for r in again.agents.list()] == ["running"]  # building a core is not owning the agent
    again.claim_writer()  # the writer sweeps the agent's leftover tasks
    assert [r["status"] for r in again.agents.list()] == ["failed"]
    asyncio.run(again.close())


def test_task_events_render_for_clients(tmp_path):
    from exo import cli_chat

    eng, _ = engine(tmp_path, [("PARENT", [tool_turn("delegate", {"goal": "CHILD x"}), "end"]),
                               ("CHILD", ["fine"])])
    asyncio.run(parent_turn(eng, "PARENT go"))
    out: list[str] = []
    for ev in task_events(eng):
        cli_chat.render(s.Event.from_json(ev.to_json(), strict=False), out.append)
    assert out[0].startswith("[task running] Task k_") and out[1].startswith("[task done] Done: task k_")
    old_client = json.loads(task_events(eng)[-1].to_json())
    old_client["body"]["future_field"] = 1
    assert s.Event.from_json(json.dumps(old_client), strict=False).body.status == "done"


def test_child_usage_rolls_into_the_parent_total(tmp_path):
    rules = [("PARENT", [tool_turn("delegate", {"goal": "CHILD work"}), "done"]), ("CHILD", ["child reply"])]
    eng, _ = engine(tmp_path, rules)
    sid, res = asyncio.run(parent_turn(eng, "PARENT go"))
    receipt = decode(task_events(eng)[-1].body.packet)
    ups = [e.body for e in eng.hub.since(0) if isinstance(e.body, s.UsageUpdated) and e.session == sid]
    assert receipt.input_tokens > 0 and len(ups) == 3  # parent call 1, the child roll-up, parent call 2
    assert ups[1].usage.input_tokens == receipt.input_tokens
    own = sum(u.usage.input_tokens for u in (ups[0], ups[2]))
    assert ups[2].session_total.input_tokens == own + receipt.input_tokens
    assert eng.loop.state(sid).total.input_tokens == ups[2].session_total.input_tokens


def test_finished_child_session_is_locked(tmp_path):
    import pytest

    from exo.core.sessions import SessionLocked
    eng, _ = engine(tmp_path, [("PARENT", [tool_turn("delegate", {"goal": "CHILD work"}), "ok"]),
                               ("CHILD", ["fine"])])
    sid, _ = asyncio.run(parent_turn(eng, "PARENT go"))
    child = decode(task_events(eng)[-1].body.packet).child_session
    assert eng.store.status(child) == "locked:done" and eng.store.status(sid) == "open"
    with pytest.raises(SessionLocked, match="finished"):
        eng.store.assert_open(child)
    with pytest.raises(SessionLocked):
        asyncio.run(eng.loop.send(child, "one more thing"))
    assert not [m for m in eng.store.messages(child) if "one more thing" in m.text()]
    asyncio.run(eng.loop.send(sid, "PARENT again"))  # the parent is not locked


def test_delegate_to_another_seat_posts_a_signed_bus_message(tmp_path):
    class FakeBus:
        def __init__(self):
            self.sent = []

        def post_sym2p(self, to, line, kind="msg", thread=None, **kw):
            from exo.bus import wire  # the real relay answers 422 to any kind outside this grammar

            if kind not in wire.KINDS:
                raise RuntimeError(f"422 unknown kind {kind!r}")
            self.sent.append((to, line, kind, thread))
            return {"seq": 7, "id": "x"}

    bus = FakeBus()
    rules = [("PARENT", [tool_turn("delegate", {"goal": "do it elsewhere", "seat": "scout"}), "ok"])]
    eng, fake = engine(tmp_path, rules)
    eng.agents.bus = bus
    sid, _ = asyncio.run(parent_turn(eng, "PARENT go"))
    assert len(bus.sent) == 1 and bus.sent[0][0] == ["scout"] and bus.sent[0][2] == "directive"
    assert decode(bus.sent[0][1]).goal == "do it elsewhere"
    assert not any("do it elsewhere" in c["user"] for c in fake.calls)  # nothing ran locally
    row = eng.agents.list(sid)[0]
    assert row["status"] == "queued" and row["child_session"] is None
    assert "queued" in tool_text(eng, sid).lower()
    eng.agents.__init__(eng)  # a restart keeps the remote task queued
    assert eng.agents.list(sid)[0]["status"] == "queued"


def test_runtime_close_finishes_children_and_receipts_before_store_close(tmp_path):
    async def main():
        hold, entered = asyncio.Event(), asyncio.Event()
        eng, fake = engine(tmp_path, [('PARENT', [tool_turn('delegate', {'tasks': [{'goal': 'HOLD-1'},
                                                                                 {'goal': 'HOLD-2'}]}), 'unused']),
                                      ('HOLD', [hold])])
        original_stream = fake.stream
        seen = 0
        async def stream(messages, tools, **opts):
            nonlocal seen
            if 'HOLD' in next((m.text() for m in reversed(messages) if m.role == 'user'), ''):
                seen += 1
                if seen == 2: entered.set()
            async for event in original_stream(messages, tools, **opts):
                yield event
        fake.stream = stream
        snapshot = {}
        original_close = eng.store.close
        def close_store():
            snapshot['active'] = fake.active
            snapshot['receipts'] = [e.body.status for e in task_events(eng)
                                    if e.body.status not in ('queued', 'running')]
            original_close()
        eng.store.close = close_store
        sid = eng.store.create_session(agent=eng.agent).id
        eng.start_turn(sid, 'PARENT go')
        await asyncio.wait_for(entered.wait(), 2)
        assert fake.active == 2
        await asyncio.wait_for(eng.close(), 3)
        assert snapshot == {'active': 0, 'receipts': ['cancelled', 'cancelled']}
        assert not eng.agents.loops and not eng._tasks
    asyncio.run(main())
