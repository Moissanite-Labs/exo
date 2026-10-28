"""Turn loop, sessions and context model (W1-C). No network; local fakes only."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from exo.core import trace
from exo.core.context import DefaultCompressor, SummaryError, TreeContext, summary_prompt
from exo.core.loop import Loop
from exo.core.sessions import EventBus, SessionStore
from exo.core.types import Message, ProviderEvent, ToolCall, ToolResult, ToolSpec, Usage


def run(coro):
    return asyncio.run(coro)


class Script:
    """Fake provider. Each call pops one step: a list of ProviderEvents, or a
    callable(messages) returning one. Records the messages it was given."""

    name = "fake"

    def __init__(self, *steps, default="ok"):
        self.steps = list(steps)
        self.default = default
        self.calls: list[list[Message]] = []
        self.gate: asyncio.Event | None = None

    async def stream(self, messages, tools, **opts):
        self.calls.append(list(messages))
        step = self.steps.pop(0) if self.steps else reply(self.default)
        if callable(step):
            step = step(messages)
        for pe in step:
            if pe == "WAIT":
                await (self.gate or asyncio.Event()).wait()
                continue
            yield pe


def reply(text, calls=(), usage=None):
    out = [ProviderEvent(kind="text", text=text)] if text else []
    out += [ProviderEvent(kind="tool_call", tool_call=c) for c in calls]
    out.append(ProviderEvent(kind="usage", usage=usage or Usage(input_tokens=10, output_tokens=2)))
    msg = Message.assistant(text, tool_calls=list(calls))
    out.append(ProviderEvent(kind="done", stop_reason="tool_use" if calls else "end", message=msg))
    return out


class Summ:
    """Fake summarizer provider: returns a short marker; optional gate to hold it."""

    name = "summ"

    def __init__(self):
        self.gate: asyncio.Event | None = None
        self.prompts: list[str] = []

    async def stream(self, messages, tools, **opts):
        self.prompts.append(messages[0].text())
        if self.gate is not None:
            await self.gate.wait()
        n = len(self.prompts)
        yield ProviderEvent(kind="done", stop_reason="end", message=Message.assistant(f"SUMMARY{n}"))


def make(tmp_path, provider, **kw):
    store = SessionStore(tmp_path / "sessions.sqlite")
    seen = []
    bus = EventBus(store)
    bus.subscribe(seen.append)
    loop = Loop(store, provider, bus=bus, retry_delay=0, **kw)
    return store, loop, seen


SPEC = ToolSpec(name="echo", description="echo back", risk="medium")


async def echo_tool(call, ctx):
    await ctx["emit_output"]("partial")
    return ToolResult.of_text(call.id, "echo:" + call.arguments.get("x", ""))


def test_tool_rounds_and_events_written_before_publish(tmp_path):
    prov = Script(reply("", [ToolCall(id="c1", name="echo", arguments={"x": "a"})]),
                  reply("", [ToolCall(id="c2", name="echo", arguments={"x": "b"})]),
                  reply("done"))
    store, loop, seen = make(tmp_path, prov, tool_specs=[SPEC], run_tool=echo_tool)

    async def go():
        s = await loop.create_session(title="t")
        db_seqs = []
        loop.bus.subscribe(lambda e: db_seqs.append(store.last_event_seq() >= e.seq))
        res = await loop.send(s.id, "hi")
        return s, res, db_seqs

    s, res, db_seqs = run(go())
    assert res.stop_reason == "end" and res.message.text() == "done"
    assert res.usage.input_tokens == 30
    roles = [m.role for m in store.messages(s.id)]
    assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert store.messages(s.id)[2].tool_results[0].text() == "echo:a"
    # third call saw both tool rounds verbatim
    assert [m.role for m in prov.calls[2]][-5:] == ["user", "assistant", "tool", "assistant", "tool"]
    types = [e.type for e in seen]
    assert types[0] == "session.created"
    assert types.count("tool.started") == 2 and types.count("tool.output") == 2
    assert "turn.started" in types and types[-2:] == ["turn.completed", "session.updated"]
    assert next(e for e in seen if e.type == "tool.started").body.risk == "medium"
    assert all(db_seqs)  # every published event was already in the DB
    assert [e.seq for e in store.events_since(0)] == [e.seq for e in seen]


def test_max_iterations_and_retry(tmp_path):
    loop_call = [ToolCall(id="c", name="echo", arguments={})]
    prov = Script(*[reply("", loop_call) for _ in range(5)])
    store, loop, seen = make(tmp_path, prov, tool_specs=[SPEC], run_tool=echo_tool, max_iterations=3)

    async def go():
        s = await loop.create_session()
        return await loop.send(s.id, "spin")

    assert run(go()).stop_reason == "max_iterations"

    flaky = Script([ProviderEvent(kind="error", error="429", retryable=True, status=429)],
                   reply("after retry"))
    store, loop, seen = make(tmp_path / "b", flaky)

    async def go2():
        s = await loop.create_session()
        return await loop.send(s.id, "x")

    res = run(go2())
    assert res.message.text() == "after retry" and len(flaky.calls) == 2
    assert any(e.type == "notice" for e in seen)

    fatal = Script([ProviderEvent(kind="error", error="bad key", retryable=False, status=401)])
    store, loop, seen = make(tmp_path / "c", fatal)
    res = run(go2())
    assert res.stop_reason == "error" and len(fatal.calls) == 1
    assert any(e.type == "error" for e in seen)


def test_interrupt_mid_stream(tmp_path):
    prov = Script([ProviderEvent(kind="text", text="half "), "WAIT",
                   ProviderEvent(kind="text", text="never")])
    store, loop, seen = make(tmp_path, prov)

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "long"))
        while not any(e.type == "message.delta" for e in seen):
            await asyncio.sleep(0.01)
        assert loop.interrupt(s.id)
        return s, await asyncio.wait_for(task, 2)

    s, res = run(go())
    assert res.stop_reason == "interrupted"
    last = store.messages(s.id)[-1]
    assert last.text() == "half " and last.meta["interrupted"] is True
    assert seen[-2].type == "turn.completed" and seen[-2].body.stop_reason == "interrupted"


def test_interrupt_during_tool_closes_calls(tmp_path):
    prov = Script(reply("", [ToolCall(id="a", name="slow"), ToolCall(id="b", name="slow")]))
    async def slow(call, ctx):
        await asyncio.sleep(10)

    store, loop, seen = make(tmp_path, prov, run_tool=slow)

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "go"))
        while not any(e.type == "tool.started" for e in seen):
            await asyncio.sleep(0.01)
        loop.interrupt(s.id)
        return s, await asyncio.wait_for(task, 2)

    s, res = run(go())
    assert res.stop_reason == "interrupted"
    tool_msg = store.messages(s.id)[-1]
    assert tool_msg.role == "tool" and {r.call_id for r in tool_msg.tool_results} == {"a", "b"}
    assert all(r.is_error for r in tool_msg.tool_results)


def test_interrupted_tool_counts_its_time_up_to_the_interrupt(tmp_path):
    """f_8e7f52cd: a tool cut short by Esc still spent the time; turn.slow subtracts tool time from wall."""
    prov = Script(reply("", [ToolCall(id="a", name="pytest_run")]))

    async def slow(call, ctx):
        await asyncio.sleep(10)

    store, loop, seen = make(tmp_path, prov, run_tool=slow)

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "go"))
        while not any(e.type == "tool.started" for e in seen):
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.3)
        loop.interrupt(s.id)
        return await asyncio.wait_for(task, 2)

    res = run(go())
    assert res.stop_reason == "interrupted"
    timing = next(e.body.timing for e in seen if e.type == "turn.completed")
    assert 250 <= timing["tool_ms"] <= timing["wall_ms"]
    assert timing["interrupted_tool"] == "pytest_run"


def _stop_when(loop, seen, ready, after):
    """Run one turn, stop it ``after`` seconds once an event of type ``ready`` is seen; return (session, result)."""
    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "go"))
        while not any(e.type == ready for e in seen):
            await asyncio.sleep(0.01)
        await asyncio.sleep(after)
        assert loop.interrupt(s.id)
        return s, await asyncio.wait_for(task, 2)

    return run(go())


def test_interrupted_model_call_counts_as_model_time(tmp_path):
    """A model call cut short by a stop is recorded: the wait is model time, not harness overhead (turn.slow)."""
    prov = Script([ProviderEvent(kind="text", text="half "), "WAIT"])
    store, loop, seen = make(tmp_path, prov)
    s, res = _stop_when(loop, seen, "message.delta", after=0.5)
    assert res.stop_reason == "interrupted"
    assert store.messages(s.id)[-1].text() == "half "  # the text the user saw is still kept
    timing = next(e.body.timing for e in seen if e.type == "turn.completed")
    assert timing["model_calls"] == 1 and 450 <= timing["model_ms"] <= timing["wall_ms"]
    calls = [e.body for e in seen if e.type == "model.call"]
    assert len(calls) == 1
    call = calls[0]
    assert call.stop_reason == "interrupted" and call.error == "" and call.attempts == []
    assert call.ttft_ms is not None and call.total_ms >= 450 and call.iteration == 1
    types = [e.type for e in seen]
    assert types.index("model.call") < types.index("turn.completed")
    t = trace.last_turn(store, s.id)
    assert len(t.calls) == 1 and t.harness_ms < timing["model_ms"]  # on main all of it was harness time
    assert "turn.slow" not in [f.signature for f in trace.detect(t, trace.Thresholds(turn_wall_s=0.45))]


def test_interrupted_model_call_before_first_token_blames_the_route(tmp_path):
    """No output yet: the call is still recorded, so a long wait reads as model.wait on the route, not turn.slow."""
    prov = Script(["WAIT"])
    store, loop, seen = make(tmp_path, prov, model="fake-model")
    s, res = _stop_when(loop, seen, "turn.started", after=0.5)
    assert res.stop_reason == "interrupted"
    timing = next(e.body.timing for e in seen if e.type == "turn.completed")
    assert timing["model_calls"] == 1 and timing["fallbacks"] == 0 and timing["model_ms"] >= 450
    (call,) = [e.body for e in seen if e.type == "model.call"]
    assert call.stop_reason == "interrupted" and call.ttft_ms is None and call.total_ms >= 450
    assert call.requested == "fake-model" and call.route == ""
    flags = [f.signature for f in trace.detect(trace.last_turn(store, s.id),
                                               trace.Thresholds(turn_wall_s=0.4, model_wait_s=0.3))]
    assert "model.wait:fake-model" in flags and "turn.slow" not in flags


def test_stop_during_retry_backoff_records_the_failed_tries(tmp_path):
    """A stop during the backoff before a retry still records the call with the tries already made."""
    tried = [{"route": "p:m", "outcome": "error", "error": "overloaded", "ms": 5}]
    prov = Script([ProviderEvent(kind="error", error="overloaded", retryable=True, status=529, attempts=tried)])
    store, loop, seen = make(tmp_path, prov)
    loop.retry_delay = 5  # the stop must cut this backoff short
    s, res = _stop_when(loop, seen, "notice", after=0.3)
    assert res.stop_reason == "interrupted" and len(prov.calls) == 1  # the retry never ran
    timing = next(e.body.timing for e in seen if e.type == "turn.completed")
    assert timing["model_calls"] == 1 and timing["fallbacks"] == 0 and timing["model_ms"] >= 250
    (call,) = [e.body for e in seen if e.type == "model.call"]
    assert call.stop_reason == "interrupted" and call.retries == 1 and call.attempts == tried


def test_stop_before_the_model_call_records_no_call(tmp_path):
    """Guard for the record above: a stop that lands before the provider is called records no model.call."""
    class HeldMemory:
        def __init__(self):
            self.entered, self.gate = asyncio.Event(), asyncio.Event()

        async def prefetch(self, query, session_id=None):
            self.entered.set()
            await self.gate.wait()
            return ""

    mem = HeldMemory()
    store, loop, seen = make(tmp_path, Script(reply("never")), memory=mem)

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "go"))
        await asyncio.wait_for(mem.entered.wait(), 2)
        assert loop.interrupt(s.id)
        mem.gate.set()
        return await asyncio.wait_for(task, 2)

    assert run(go()).stop_reason == "interrupted"
    timing = next(e.body.timing for e in seen if e.type == "turn.completed")
    assert timing["model_calls"] == 0 and not [e for e in seen if e.type == "model.call"]


def test_steer_injects_between_tool_rounds(tmp_path):
    async def held(call, ctx):
        await asyncio.sleep(0.05)
        return ToolResult.of_text(call.id, "ok")

    prov = Script(reply("", [ToolCall(id="c", name="t")]), reply("seen steer"))
    store, loop, seen = make(tmp_path, prov, run_tool=held)

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "start"))
        while not any(e.type == "tool.started" for e in seen):
            await asyncio.sleep(0.005)
        assert await loop.send(s.id, "also this", mode="steer") is None
        return s, await task

    s, res = run(go())
    assert res.message.text() == "seen steer"
    texts = [m.text() for m in prov.calls[1] if m.role == "user"]
    assert "also this" in texts


def test_run_tool_sees_who_started_the_turn(tmp_path):
    """The turn's author (D-040) rides with every tool call, so a hook can read it (#598)."""
    authors: list[str | None] = []

    async def tool(call, ctx):
        authors.append(ctx.get("author"))
        return ToolResult.of_text(call.id, "ok")

    prov = Script(reply("", [ToolCall(id="c1", name="t")]), reply("", [ToolCall(id="c2", name="t")]), reply("done"))
    store, loop, _ = make(tmp_path, prov, run_tool=tool)

    async def go():
        s = await loop.create_session()
        return await loop.send(s.id, "hi", meta={"author": "peer:mallory"})

    run(go())
    assert authors == ["peer:mallory", "peer:mallory"]


def test_a_steer_reauthors_the_rounds_after_it(tmp_path):
    """A steer answers within the turn; the rounds after it run for the steer's author (#598), so bypass stops
    lowering once the turn takes a message from anyone else."""
    authors: list[str | None] = []

    async def held(call, ctx):
        authors.append(ctx.get("author"))
        await asyncio.sleep(0.05)
        return ToolResult.of_text(call.id, "ok")

    prov = Script(reply("", [ToolCall(id="c1", name="t")]), reply("", [ToolCall(id="c2", name="t")]), reply("done"))
    store, loop, seen = make(tmp_path, prov, run_tool=held)

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "start", meta={"author": "principal:tui"}))
        while not any(e.type == "tool.started" for e in seen):
            await asyncio.sleep(0.005)
        await loop.send(s.id, "also this", mode="steer", meta={"author": "peer:mallory"})
        return await task

    run(go())
    assert authors == ["principal:tui", "peer:mallory"]


def test_resume_after_restart_and_repair(tmp_path):
    prov = Script(reply("first"))
    store, loop, _ = make(tmp_path, prov)

    async def one():
        s = await loop.create_session(title="keep")
        await loop.send(s.id, "hello")
        # simulate a crash right after the model asked for a tool
        store.append_message(s.id, Message.assistant("", [ToolCall(id="z", name="echo")]))
        return s.id

    sid = run(one())
    store.close()

    prov2 = Script(reply("second"))
    store2, loop2, _ = make(tmp_path, prov2)

    async def two():
        info = await loop2.resume(sid)
        res = await loop2.send(sid, "again")
        return info, res

    info, res = run(two())
    assert info.title == "keep" and res.message.text() == "second"
    roles = [m.role for m in store2.messages(sid)]
    assert roles == ["user", "assistant", "assistant", "tool", "user", "assistant"]
    sent = [m.text() for m in prov2.calls[0]]
    assert "hello" in sent and "first" in sent
    assert store2.search("hello")[0]["session"] == sid


def test_resume_leaves_a_running_turns_tool_round_alone(tmp_path):
    """A second window opening a thread (session.resume) while its turn runs a tool: the open round belongs to that
    turn, so no 'not run' result is written and the model sees exactly one result per call. With no turn running,
    resume still closes a round a crash left open."""
    entered, gate = asyncio.Event(), asyncio.Event()

    async def held(call, ctx):
        entered.set()
        await gate.wait()
        return ToolResult.of_text(call.id, "the real result")

    prov = Script(reply("", [ToolCall(id="call_1", name="echo")]), reply("final answer"))
    store, loop, _ = make(tmp_path, prov, tool_specs=[SPEC], run_tool=held)

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "run the long thing"))
        await asyncio.wait_for(entered.wait(), 2)
        assert loop.state(s.id).running
        await loop.resume(s.id)
        assert store.messages(s.id)[-1].role == "assistant"  # the live call is still open, nothing was added
        gate.set()
        res = await asyncio.wait_for(task, 2)
        store.append_message(s.id, Message.assistant("", [ToolCall(id="z", name="echo")]))  # now a crash leftover
        await loop.resume(s.id)
        return s.id, res

    sid, res = run(go())
    assert res.message.text() == "final answer"
    tools = [m for m in store.messages(sid) if m.role == "tool"]
    assert [[(r.call_id, r.text()) for r in m.tool_results] for m in tools] == [
        [("call_1", "the real result")], [("z", "not run: session was interrupted")]]
    seen = [[r.call_id for r in m.tool_results] for m in prov.calls[1] if m.role == "tool"]
    assert seen == [["call_1"]]


def test_fork_lineage_and_undo(tmp_path):
    prov = Script(reply("r1"), reply("r2"), reply("child"))
    store, loop, _ = make(tmp_path, prov)

    async def go():
        s = await loop.create_session(title="root")
        await loop.send(s.id, "one")
        await loop.send(s.id, "two")
        first_reply = store.messages(s.id)[1]
        child = await loop.fork(s.id, at_message=first_reply.id)
        await loop.send(child.id, "branch")
        grand = await loop.fork(child.id)
        return s, child, grand

    s, child, grand = run(go())
    assert child.parent == s.id and grand.parent == child.id
    assert [m.text() for m in store.messages(child.id)] == ["one", "r1", "branch", "child"]
    assert store.messages(child.id)[0].meta["forked_from"] == store.messages(s.id)[0].id
    assert store.get_session(s.id).message_count == 4  # parent untouched
    assert store.undo(s.id) == 2 and [m.text() for m in store.messages(s.id)] == ["one", "r1"]


def _tree_loop(tmp_path, summ, **kw):
    store = SessionStore(tmp_path / "s.sqlite")
    ctx = TreeContext(store, DefaultCompressor(summ, retry_delay=0), leaf_size=2, tail_messages=2, **{"min_compact_tokens": 0, **kw})
    prov = Script(default="ok")
    loop = Loop(store, prov, context=ctx, retry_delay=0)
    seen = []
    loop.bus.subscribe(seen.append)
    return store, ctx, prov, loop, seen


def test_turn_is_fresh_call_from_tree_and_tail(tmp_path):
    summ = Summ()
    store, ctx, prov, loop, seen = _tree_loop(tmp_path, summ)

    async def go():
        s = await loop.create_session()
        for i in range(4):
            await loop.send(s.id, f"q{i}")
        return s

    _s = run(go())
    last = prov.calls[-1]
    assert last[0].role == "system" and "SUMMARY" in last[0].text()
    assert len(last) <= 1 + 2 + 2  # system, maybe a bridge, tail of at most leaf+tail
    assert last[-1].text() == "q3"
    assert "q0" not in [m.text() for m in last[1:]]
    assert any(e.type == "memory.summarized" for e in seen)  # tree upkeep, not a context compaction
    assert "TURNS TO SUMMARIZE" in summ.prompts[0] and "[USER] q0" in summ.prompts[0]


def test_merge_when_view_over_budget(tmp_path):
    summ = Summ()
    store, ctx, prov, loop, seen = _tree_loop(tmp_path, summ, view_budget=1)

    async def go():
        s = await loop.create_session()
        for i in range(6):
            await loop.send(s.id, f"q{i}")
        return s

    s = run(go())
    nodes = store.summaries(s.id)
    assert any(n.level >= 1 and n.status == "done" for n in nodes)
    assert any("PREVIOUS SUMMARY" in p for p in summ.prompts)
    top = ctx._top(s.id)
    assert top[0].level >= 1 and top[0].start == 1


def test_turn_does_not_wait_for_pending_summary(tmp_path):
    """Issue #99: the turn starts from verbatim; the summary lands in the background and the next turn uses it."""
    summ = Summ()
    store, ctx, prov, loop, seen = _tree_loop(tmp_path, summ)

    async def go():
        summ.gate = asyncio.Event()
        s = await loop.create_session()
        for i in range(2):
            store.append_message(s.id, Message.user(f"old{i}"))
            store.append_message(s.id, Message.assistant(f"ans{i}"))
        await asyncio.wait_for(loop.send(s.id, "new"), 2)  # not held by the gated summary
        assert "summary pending" in prov.calls[0][0].text() and "old0" in prov.calls[0][0].text()
        summ.gate.set()
        await asyncio.wait_for(ctx.settle(s.id), 2)
        await loop.send(s.id, "next")
        return s

    run(go())
    assert "SUMMARY1" in prov.calls[-1][0].text()


def test_settle_timeout_falls_back_to_verbatim(tmp_path):
    summ = Summ()
    store, ctx, prov, loop, seen = _tree_loop(tmp_path, summ, settle_timeout=0.05)

    async def go():
        summ.gate = asyncio.Event()  # never released
        s = await loop.create_session()
        for i in range(2):
            store.append_message(s.id, Message.user(f"old{i}"))
            store.append_message(s.id, Message.assistant(f"ans{i}"))
        await loop.send(s.id, "new")
        summ.gate.set()

    run(go())
    assert "summary pending" in prov.calls[0][0].text() and "old0" in prov.calls[0][0].text()


def test_import_lane_does_not_block_live_turns(tmp_path):
    summ = Summ()
    store, ctx, prov, loop, seen = _tree_loop(tmp_path, summ)

    async def go():
        s = await loop.create_session()
        ctx.pause_imports()
        ctx.import_messages(s.id, [Message.user(f"imp{i}") for i in range(6)])
        await asyncio.wait_for(loop.send(s.id, "live"), 2)  # not held by paused imports
        assert ctx.pending(s.id, "import")
        ctx.resume_imports()
        await asyncio.wait_for(asyncio.gather(*ctx.pending(s.id)), 2)
        return s

    s = run(go())
    assert "imported history, summary in progress" in prov.calls[0][0].text()
    assert {n.lane for n in store.summaries(s.id) if n.level == 0} >= {"import"}


def test_memory_slot_call_sites(tmp_path):
    class Mem:
        def __init__(self):
            self.log = []

        def system_prompt_block(self):
            return "MEMBLOCK"

        async def prefetch(self, query, session_id=""):
            self.log.append(("prefetch", query))
            return "RECALL"

        def sync_turn(self, user, assistant, session_id=""):
            self.log.append(("sync", user, assistant))

    mem = Mem()
    prov = Script(reply("hey"))
    store, loop, _ = make(tmp_path, prov, memory=mem, system_prompt="SYS")

    async def go():
        s = await loop.create_session()
        await loop.send(s.id, "hi")

    run(go())
    sys_text = prov.calls[0][0].text()
    assert sys_text.startswith("SYS") and "MEMBLOCK" in sys_text and "RECALL" in sys_text
    assert mem.log == [("prefetch", "hi"), ("sync", "hi", "hey")]


def test_private_thread_reads_memory_but_never_writes_it(tmp_path):
    """D-028: a private thread is not remembered. It still gets the prompt block and recall, but the loop never
    calls on_turn_start, sync_turn or checkpoint for it, or for a session under it (its delegate child, which runs
    a brief built from it). A normal thread of the same agent gets all three."""
    class Mem:
        def __init__(self):
            self.log = []

        def system_prompt_block(self):
            return "MEMBLOCK"

        def prefetch(self, query, session_id=""):
            self.log.append(("prefetch", session_id))
            return "RECALL"

        def on_turn_start(self, turn_number, message, session_id="", **kw):
            self.log.append(("on_turn_start", session_id))

        def sync_turn(self, user, assistant, session_id="", **kw):
            self.log.append(("sync_turn", session_id))

        def checkpoint(self, session_id="", messages=None, reason="turn"):
            self.log.append(("checkpoint", session_id))

    mem = Mem()
    prov = Script(reply("hush"), reply("brief done"), reply("noted"))
    store, loop, _ = make(tmp_path, prov, memory=mem, system_prompt="SYS")

    async def go():
        p = await loop.create_session(agent="a", kind="private")
        await loop.send(p.id, "a secret")
        k = await loop.create_session(agent="a", kind="subagent", parent=p.id)
        await loop.send(k.id, "a brief about the secret")
        t = await loop.create_session(agent="a")
        await loop.send(t.id, "a public fact")
        return p.id, k.id, t.id

    pid, kid, tid = run(go())
    assert "MEMBLOCK" in prov.calls[0][0].text() and "RECALL" in prov.calls[0][0].text()
    assert [e for e in mem.log if e[1] == pid] == [("prefetch", pid)]
    assert [e for e in mem.log if e[1] == kid] == [("prefetch", kid)]
    assert [e[0] for e in mem.log if e[1] == tid] == ["on_turn_start", "prefetch", "sync_turn", "checkpoint"]


def test_summary_prompt_is_lifted_text():
    p = summary_prompt("X", 100, previous="P")
    assert "You are a summarization agent creating a context checkpoint" in p
    assert "PREVIOUS SUMMARY:\nP" in p and chr(0x2014) not in p


def test_usage_updated_model_names_the_route_that_served_after_fallback(tmp_path):
    import asyncio

    from exo.core.loop import Loop
    from exo.core.models import FakeProvider, Router
    from exo.core.models.fake import rate_limited
    from exo.core.sessions import SessionStore
    from exo.protocol import schema as ev

    router = Router(roles={"main": ["p:big", "b:small"]}, catalog={"providers": {}, "models": {}, "roles": {}},
                    instances={"p": FakeProvider([rate_limited()], name="p"), "b": FakeProvider(["hi"], name="b")},
                    env={})
    store = SessionStore(tmp_path / "s.sqlite")
    loop = Loop(store, router.provider("main"), model="p:big")

    async def go():
        info = await loop.create_session()
        await loop.send(info.id, "hello")
    asyncio.run(go())
    usage = [e.body for e in store.events_since(0) if isinstance(e.body, ev.UsageUpdated)]
    assert usage and usage[-1].model == "b:small"
    notices = [e.body for e in store.events_since(0) if isinstance(e.body, ev.Notice)]
    calls = [e.body for e in store.events_since(0) if isinstance(e.body, ev.ModelCall)]
    assert notices and calls and calls[-1].call_id
    assert notices[0].source == "model_route" and notices[0].call_id == calls[-1].call_id



def test_session_updated_publishes_model_in_use(tmp_path):
    store, loop, seen = make(tmp_path, Script(reply("hi")), model="p:in-use")

    async def go():
        s = await loop.create_session(title="t", model="p:stale")
        await loop.send(s.id, "hello")
        return s.id

    sid = run(go())
    assert store.get_session(sid).model == "p:in-use"
    last = [e for e in seen if e.type == "session.updated"][-1]
    assert last.body.session.model == "p:in-use"


def test_model_call_records_the_route_from_the_ok_attempt_when_unstamped(tmp_path):
    from exo.protocol import schema as ev

    attempts = [{"route": "p:big", "outcome": "ok", "error": "", "ms": 1}]
    done = ProviderEvent(kind="done", stop_reason="end", attempts=attempts)  # no route stamped on the event
    store, loop, _ = make(tmp_path, Script([done]))

    async def go():
        s = await loop.create_session()
        await loop.send(s.id, "hi")
    run(go())
    calls = [e.body for e in store.events_since(0) if isinstance(e.body, ev.ModelCall)]
    assert calls and calls[-1].route == "p:big"


def test_model_call_with_real_router_success_on_first_attempt_sets_route(tmp_path):
    """Live path: real Router, fake provider stream, no fallback. The stored and published model.call names the
    serving route (field `route`; there is no field called `served` on the event)."""
    from exo.core.models import FakeProvider, Router
    from exo.protocol import schema as ev

    router = Router(roles={"main": ["p:big"]}, catalog={"providers": {}, "models": {}, "roles": {}},
                    instances={"p": FakeProvider(["hi"], name="p")}, env={})
    store, loop, seen = make(tmp_path, router.provider("main"), model="p:big")

    async def go():
        info = await loop.create_session()
        await loop.send(info.id, "hello")
    run(go())
    calls = [e.body for e in store.events_since(0) if isinstance(e.body, ev.ModelCall)]
    assert calls and calls[-1].route == "p:big" and calls[-1].attempts[0]["outcome"] == "ok"
    assert calls[-1].attempts[0]["route"] == "p:big"
    stored = [e for e in store.events_since(0) if e.type == "model.call"][-1]
    assert stored.body.route == "p:big"
    published = [e for e in seen if e.type == "model.call"]
    assert published and all(e.body.route == "p:big" for e in published)


def test_compaction_skips_tiny_spans_and_summaries_that_do_not_shrink():
    from exo.core.context import TreeContext

    async def fat(content, **kw):
        return content + " and more words that make the summary larger than its source"

    async def lean(content, **kw):
        return "short"

    def tree(summ, **kw):
        store = SessionStore(":memory:")
        s = store.create_session(title="t")
        for i in range(6):
            store.append_message(s.id, Message.user("word " * 900 if i < 5 else "tail"))
        ctx = TreeContext(store, type("C", (), {"summarize": staticmethod(summ)})(), leaf_size=2, tail_messages=2, **kw)

        async def go():
            ctx.note(s.id)
            await ctx.settle(s.id)
            return [(n.status, n.text) for n in store.summaries(s.id)]
        return run(go())

    assert {st for st, _ in tree(lean, min_compact_tokens=100000)} == {"skipped"}  # too small to be worth it
    assert {st for st, _ in tree(fat)} == {"skipped"}  # grew, so kept verbatim
    assert {st for st, _ in tree(lean)} == {"done"}  # big enough and shrinks


def test_steer_during_the_final_reply_gets_its_own_answer(tmp_path):
    prov = Script(["WAIT", *reply("first answer")], reply("answer to the steer"))
    prov.gate = asyncio.Event()
    store, loop, seen = make(tmp_path, prov)

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "start"))
        while not prov.calls:
            await asyncio.sleep(0.005)
        assert await loop.send(s.id, "one more thing", mode="steer") is None
        prov.gate.set()
        return s, await task

    s, res = run(go())
    assert res.message.text() == "answer to the steer"
    assert len(prov.calls) == 2 and "one more thing" in [m.text() for m in prov.calls[1]]
    roles = [(m.role, m.text()) for m in store.messages(s.id)]
    assert roles[-2:] == [("user", "one more thing"), ("assistant", "answer to the steer")]


def test_steer_after_the_last_call_still_gets_an_answer(tmp_path):
    prov = Script(reply("first answer"), reply("late answer"))
    store, loop, seen = make(tmp_path, prov)

    async def go():
        s = await loop.create_session()
        st = loop.state(s.id)

        async def late(*a, **k):  # arrives while the turn is closing, after the model's final reply
            st.steer.append(Message.user("late note"))
        orig = loop._rounds
        calls = []

        async def rounds(*a, **k):
            await orig(*a, **k)
            if not calls:
                calls.append(1)
                await late()
        loop._rounds = rounds
        return s, await loop.send(s.id, "start")

    s, res = run(go())
    assert res.message.text() == "late answer"
    assert [m.text() for m in store.messages(s.id)][-2:] == ["late note", "late answer"]


def test_mid_stream_error_keeps_the_text_the_user_saw(tmp_path):
    prov = Script([ProviderEvent(kind="text", text="half an answer "),
                   ProviderEvent(kind="error", error="upstream reset", retryable=True)])
    store, loop, seen = make(tmp_path, prov)

    async def go():
        s = await loop.create_session()
        return s, await loop.send(s.id, "long")

    s, res = run(go())
    assert res.stop_reason == "error" and len(prov.calls) == 1  # shown text: no retry
    last = store.messages(s.id)[-1]
    assert last.role == "assistant" and last.text() == "half an answer "
    assert last.meta["interrupted"] is True and last.meta["error"] == "upstream reset"


class _BrokenSummarizer:
    def __init__(self):
        self.calls = 0

    async def summarize(self, content, previous=None):
        self.calls += 1
        raise SummaryError("aux model refused", retryable=False)


def _failing_tree(tmp_path, **kw):
    from exo.core import trace

    store = SessionStore(tmp_path / "s.sqlite")
    comp = _BrokenSummarizer()
    ctx = TreeContext(store, comp, leaf_size=2, tail_messages=2, min_compact_tokens=0, **kw)
    s = store.create_session()
    for i in range(6):
        store.append_message(s.id, Message.user(f"q{i}"))
    reported = []
    trace.add_sink(lambda body, session: reported.append(body))
    return store, ctx, comp, s, reported


def test_failed_summary_backs_off_instead_of_retrying_every_turn(tmp_path):
    store, ctx, comp, s, _ = _failing_tree(tmp_path, retry_backoff=1000.0)

    async def go():
        ctx.note(s.id)
        await ctx.settle(s.id)
        first = comp.calls
        for _ in range(5):  # five more turns inside the backoff window
            ctx.note(s.id)
            await ctx.settle(s.id)
        return first

    first = run(go())
    assert first == 2 and comp.calls == first  # two leaves, one try each, no retry while backing off


def test_summary_gives_up_after_the_cap_and_reports_once(tmp_path):
    store, ctx, comp, s, reported = _failing_tree(tmp_path, retry_backoff=0.0, max_attempts=3)

    async def go():
        for _ in range(8):
            ctx.note(s.id)
            await ctx.settle(s.id)

    run(go())
    assert comp.calls == 2 * 3  # two leaves, three tries each, then never again
    nodes = store.summaries(s.id)
    assert {n.status for n in nodes} == {"skipped"} and all(n.attempts == 3 for n in nodes)
    assert sum(1 for b in reported if b.where == "summary") == 2  # one report per given-up leaf
    assert [m.text() for m in run(ctx.build(s.id, ""))][-1] == "q5"  # still in the context, verbatim


def test_round_cap_wraps_up_with_tool_use_off(tmp_path):
    spin = [ToolCall(id="c", name="echo", arguments={})]
    seen_tools, seen_choice = [], []

    class Spy(Script):
        async def stream(self, messages, tools, **opts):
            seen_tools.append(list(tools))
            seen_choice.append(opts.get("tool_choice"))
            async for pe in super().stream(messages, tools, **opts):
                yield pe

    prov = Spy(*[reply("", spin) for _ in range(3)], reply("did 3 rounds; next: finish X"))
    store, loop, seen = make(tmp_path, prov, tool_specs=[SPEC], run_tool=echo_tool, max_iterations=3)

    async def go():
        s = await loop.create_session()
        return s, await loop.send(s.id, "spin")

    s, res = run(go())
    assert res.stop_reason == "max_iterations"  # callers still see the cap
    assert res.message.text() == "did 3 rounds; next: finish X" and res.message.meta.get("wrap_up")
    assert len(prov.calls) == 4 and seen_tools == [[SPEC]] * 4  # the same tools stay declared...
    assert seen_choice == [None, None, None, "none"]  # ...and only the wrap-up turns tool use off
    assert "tool rounds" in prov.calls[-1][-1].text()  # transient prompt reached the model...
    assert not any("tool rounds" in m.text() for m in store.messages(s.id) if m.role == "user")  # ...not the store


def test_round_cap_wrap_up_off(tmp_path):
    spin = [ToolCall(id="c", name="echo", arguments={})]
    prov = Script(*[reply("", spin) for _ in range(3)])
    store, loop, seen = make(tmp_path, prov, tool_specs=[SPEC], run_tool=echo_tool, max_iterations=2,
                             wrap_up=False)

    async def go():
        s = await loop.create_session()
        return await loop.send(s.id, "spin")

    assert run(go()).stop_reason == "max_iterations" and len(prov.calls) == 2


def _spin(n):
    return [reply("", [ToolCall(id=f"c{i}", name="echo", arguments={})]) for i in range(n)]


def _capped(tmp_path, wrap, **kw):
    """Two tool rounds hit the cap; ``wrap`` is the wrap-up call's script step."""
    prov = Script(*_spin(2), wrap)
    store, loop, seen = make(tmp_path, prov, tool_specs=[SPEC], max_iterations=2, **{"run_tool": echo_tool, **kw})

    async def go():
        s = await loop.create_session()
        return s, await loop.send(s.id, "spin")

    s, res = run(go())
    return store, seen, s, res


def _bodies(seen, cls):
    return [e.body for e in seen if isinstance(e.body, cls)]


def test_round_cap_wrap_up_drops_a_stray_tool_call_and_keeps_the_route_meta(tmp_path):
    ran = []

    async def tool(call, ctx):
        ran.append(call.id)
        return ToolResult.of_text(call.id, "ok")

    wrap = reply("did A; next: B", [ToolCall(id="late", name="echo", arguments={})])
    wrap[-1].message.meta["model"] = "fake:wrap"  # what the router stamps on the reply
    store, seen, s, res = _capped(tmp_path, wrap, run_tool=tool)
    last = store.messages(s.id)[-1]
    assert res.stop_reason == "max_iterations" and ran == ["c0", "c1"]  # the stray call never ran
    assert last.role == "assistant" and last.text() == "did A; next: B" and last.tool_calls == []  # none dangles
    assert last.meta["wrap_up"] is True and last.meta["model"] == "fake:wrap"


def test_round_cap_wrap_up_with_only_a_tool_call_stores_nothing(tmp_path):
    ran = []

    async def tool(call, ctx):
        ran.append(call.id)
        return ToolResult.of_text(call.id, "ok")

    store, seen, s, res = _capped(tmp_path, reply("", [ToolCall(id="late", name="echo", arguments={})]), run_tool=tool)
    assert res.stop_reason == "max_iterations" and ran == ["c0", "c1"]  # the stray call never ran
    assert store.messages(s.id)[-1].role == "tool"  # no empty wrap-up message is stored
    assert not res.message.meta.get("wrap_up")


def test_round_cap_wrap_up_error_keeps_the_partial_and_reports_it(tmp_path):
    from exo.protocol import schema as ev

    wrap = [ProviderEvent(kind="text", text="I finished A; still open: "),
            ProviderEvent(kind="error", error="upstream reset", retryable=True)]
    store, seen, s, res = _capped(tmp_path, wrap)
    last = store.messages(s.id)[-1]
    assert res.stop_reason == "max_iterations" and res.error == "upstream reset"
    assert last.text() == "I finished A; still open: " and res.message.id == last.id  # what the user saw is kept
    assert last.meta["interrupted"] is True and last.meta["error"] == "upstream reset"
    assert [b.code for b in _bodies(seen, ev.Error)] == ["provider_error"]


def test_round_cap_wrap_up_spend_refusal_is_reported_with_its_code(tmp_path):
    from exo.protocol import schema as ev

    refused = ProviderEvent(kind="error", error="Model request refused: daily cap", retryable=False,
                            attempts=[{"route": "anthropic:m", "outcome": "spend_refused"}])
    store, seen, s, res = _capped(tmp_path, [refused])
    assert res.stop_reason == "max_iterations" and res.error == "Model request refused: daily cap"
    assert [b.code for b in _bodies(seen, ev.Error)] == ["model_spend_refused"]
    assert store.messages(s.id)[-1].role == "tool"  # nothing was said, nothing stored


def test_round_cap_wrap_up_exception_keeps_the_cap_and_is_recorded(tmp_path):
    from exo.protocol import schema as ev

    prov = Script(*_spin(2))
    store, loop, seen = make(tmp_path, prov, tool_specs=[SPEC], run_tool=echo_tool, max_iterations=2)
    build, builds = loop.context.build, []

    async def flaky(session, system):  # the third build is the wrap-up's
        builds.append(session)
        if len(builds) == 3:
            raise RuntimeError("tree broke")
        return await build(session, system)

    loop.context.build = flaky

    async def go():
        s = await loop.create_session()
        return await loop.send(s.id, "spin")

    res = run(go())
    assert res.stop_reason == "max_iterations" and res.error is None and len(prov.calls) == 2
    assert not _bodies(seen, ev.Error)  # no turn_failed
    assert [(b.where, b.message) for b in _bodies(seen, ev.HarnessError)] == [("wrap-up", "tree broke")]


def test_round_cap_wrap_up_interrupt_ends_the_turn_interrupted(tmp_path):
    prov = Script(*_spin(2), [ProviderEvent(kind="text", text="so far "), "WAIT"])
    store, loop, seen = make(tmp_path, prov, tool_specs=[SPEC], run_tool=echo_tool, max_iterations=2)

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "spin"))
        while not any(e.type == "message.delta" for e in seen):
            await asyncio.sleep(0.01)
        assert loop.interrupt(s.id)
        return s, await asyncio.wait_for(task, 2)

    s, res = run(go())
    last = store.messages(s.id)[-1]
    assert res.stop_reason == "interrupted"  # an interrupt wins over the cap, like in any other call
    assert last.text() == "so far " and last.meta["interrupted"] is True


def _sse(*events):
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()


ANTHROPIC_WRAP = _sse(
    {"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Summing up."}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Read it; next: summarize."}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 9}},
    {"type": "message_stop"})
OPENAI_WRAP = _sse(
    {"choices": [{"index": 0, "delta": {"content": "Read it; next: summarize."}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 9}}) + b"data: [DONE]\n\n"


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
def test_round_cap_wrap_up_request_keeps_the_tools_and_sets_tool_choice_none(tmp_path, kind):
    import httpx

    from exo.core.models.anthropic import AnthropicProvider
    from exo.core.models.openai_chat import OpenAIChatProvider

    fixtures = Path(__file__).parents[1] / "models" / "fixtures"
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        tools_round = (fixtures / f"{kind}_tool_stream.sse").read_bytes()
        wrap = ANTHROPIC_WRAP if kind == "anthropic" else OPENAI_WRAP
        return httpx.Response(200, content=tools_round if len(bodies) <= 2 else wrap,
                              headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    if kind == "anthropic":
        prov, names, none = AnthropicProvider(api_key="k", client=client), ["read_file"], {"type": "none"}
    else:
        prov, names, none = OpenAIChatProvider("https://example.invalid/v1", "k", client=client), ["weather", "time"], "none"
    specs = [ToolSpec(name=n, description=n) for n in names]
    store, loop, seen = make(tmp_path, prov, tool_specs=specs, run_tool=echo_tool, max_iterations=2,
                             model="claude-opus-5-5" if kind == "anthropic" else "m")

    async def go():
        s = await loop.create_session()
        return s, await loop.send(s.id, "read the readme")

    s, res = run(go())
    assert len(bodies) == 3 and bodies[2]["tools"] == bodies[1]["tools"] == bodies[0]["tools"]  # no tool_set edit
    assert [b.get("tool_choice") for b in bodies] == [None, None, none]
    assert "tool rounds" in json.dumps(bodies[2]["messages"][-1])
    last = store.messages(s.id)[-1]
    assert res.stop_reason == "max_iterations" and last.meta["wrap_up"] is True
    assert last.text() == "Read it; next: summarize." and last.tool_calls == []
    assert not any(p.signature for p in last.content)  # thinking signed against the unstored prompt is not kept


def test_transient_summary_outage_retries_after_minutes_with_capped_delay_and_one_report(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from exo.core import context, trace

    clock = [0.0]
    monkeypatch.setattr(context, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    reported = []
    monkeypatch.setattr(trace, "report", lambda where, error, session: reported.append((where, str(error))))

    class Outage:
        calls = 0

        async def summarize(self, content, previous=None):
            self.calls += 1
            if clock[0] < 600:
                raise SummaryError("service unavailable")
            return "short summary"

    store = SessionStore(tmp_path / "s.sqlite")
    comp = Outage()
    ctx = TreeContext(store, comp, leaf_size=2, tail_messages=2, min_compact_tokens=0)
    sid = store.create_session().id
    for i in range(4):
        store.append_message(sid, Message.user(f"q{i} " + "fact " * 50))

    async def go():
        for now in (0, 30, 90, 210, 450):
            clock[0] = now
            ctx.note(sid)
            await ctx.settle(sid)
            assert comp.calls == (0, 30, 90, 210, 450).index(now) + 1
            assert all(n.status == "failed" for n in store.summaries(sid))
            for _ in range(5):
                ctx.note(sid)
                await ctx.settle(sid)
            assert comp.calls == (0, 30, 90, 210, 450).index(now) + 1
        assert ctx._retry_at[next(iter(ctx._retry_at))] == 750  # bounded at 300 seconds
        clock[0] = 750
        ctx.note(sid)
        await ctx.settle(sid)
        assert comp.calls == 6 and all(n.status == "done" for n in store.summaries(sid))
        assert not ctx._retry_at
    run(go())
    assert len(reported) == 1 and "will retry" in reported[0][1]


def test_default_compressor_preserves_terminal_and_transient_retryability():
    async def go():
        for retryable in (False, True):
            comp = DefaultCompressor(Script([ProviderEvent(kind="error", error="offline", retryable=retryable)]),
                                     retries=0)
            try:
                await comp.summarize("source")
            except SummaryError as exc:
                assert exc.retryable is retryable
            else:
                raise AssertionError("expected a summary error")
    run(go())


def test_transient_summary_zero_backoff_still_has_a_retry_floor(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from exo.core import context, trace

    clock = [0.0]
    monkeypatch.setattr(context, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(trace, "report", lambda *args: None)
    store, ctx, comp, session, _ = _failing_tree(tmp_path, retry_backoff=0.0)

    async def offline(content, previous=None):
        comp.calls += 1
        raise SummaryError("offline")

    comp.summarize = offline

    async def go():
        for _ in range(5):
            ctx.note(session.id)
            await ctx.settle(session.id)
        assert comp.calls == 2
        clock[0] = 1.0
        ctx.note(session.id)
        await ctx.settle(session.id)
        assert comp.calls == 4
    run(go())


def test_steer_during_turn_completion_runs_a_new_turn(tmp_path):
    """Finalization still owns the lock, but it can no longer consume a steer."""
    prov = Script(reply("first answer"), reply("late answer"))
    store, loop, seen = make(tmp_path, prov)

    async def go():
        closing, release = asyncio.Event(), asyncio.Event()
        async def observer(session, turn_id):
            if not closing.is_set():
                closing.set()
                await release.wait()
        loop.on_turn_end = observer
        s = await loop.create_session()
        first = asyncio.create_task(loop.send(s.id, "start"))
        await asyncio.wait_for(closing.wait(), 2)
        second = asyncio.create_task(loop.send(s.id, "late note", mode="steer"))
        await asyncio.sleep(0)
        assert not second.done(), "the completed turn must queue a new turn, not accept an orphan steer"
        release.set()
        await first
        result = await asyncio.wait_for(second, 2)
        assert result.message.text() == "late answer"
        assert not loop.state(s.id).steer
        return s

    s = run(go())
    assert [(m.role, m.text()) for m in store.messages(s.id)][-2:] == [("user", "late note"), ("assistant", "late answer")]


def test_a_send_that_waited_for_a_session_locked_meanwhile_runs_no_turn(tmp_path):
    """A send checks the session's lock again once it holds the turn lock: a child that finished and was locked while
    the send waited behind its turn takes no new turn, and the text is not stored."""
    from exo.core.sessions import SessionLocked

    prov = Script(["WAIT", *reply("first answer")])
    prov.gate = asyncio.Event()
    store, loop, seen = make(tmp_path, prov)

    async def go():
        s = await loop.create_session()
        first = asyncio.create_task(loop.send(s.id, "start"))
        while not prov.calls:
            await asyncio.sleep(0.01)
        late = asyncio.create_task(loop.send(s.id, "queued behind the turn"))
        await asyncio.sleep(0.02)
        assert not late.done()  # waits on the session's lock
        store.lock(s.id, "done")  # the delegator locks a finished child
        prov.gate.set()
        await first
        with pytest.raises(SessionLocked):
            await asyncio.wait_for(late, 2)
        return s

    s = run(go())
    assert [e.type for e in seen].count("turn.started") == 1
    assert "queued behind the turn" not in [m.text() for m in store.messages(s.id)]


@pytest.mark.parametrize('case,expected_attempts,recovered', [
    ('terminal', ['error'], False),
    ('exhausted', ['fallback', 'error'], False),
    ('timeout', ['timeout'], False),
    ('recovered', ['fallback', 'ok'], True),
])
def test_failed_call_does_not_claim_successful_fallback_recovery(tmp_path, case, expected_attempts, recovered):
    from exo.core.models import FakeProvider, Router
    from exo.core.models.fake import rate_limited
    terminal = ProviderEvent(kind='error', error='fixture terminal refusal', status=400, retryable=False)
    first = ProviderEvent(kind='error', error='ReadTimeout fixture', retryable=True) if case == 'timeout' else (
        terminal if case == 'terminal' else rate_limited())
    chain = ['primary:x'] + (['backup:y'] if case in ('exhausted', 'recovered') else [])
    router = Router(roles={'main': chain}, catalog={'providers': {}, 'models': {}, 'roles': {}}, env={},
                    instances={'primary': FakeProvider([first]),
                               'backup': FakeProvider(['recovered' if recovered else terminal])})
    store = SessionStore(tmp_path / 'sessions.sqlite')
    loop = Loop(store, router.provider('main'), model='primary:x', max_retries=0)
    async def go():
        info = await loop.create_session()
        result = await loop.send(info.id, 'fixture request')
        return info.id, result
    sid, result = run(go())
    events = store.events_since(0, session=sid)
    call = next(e.body for e in events if e.type == 'model.call')
    timing = next(e.body.timing for e in events if e.type == 'turn.completed')
    assert [a['outcome'] for a in call.attempts] == expected_attempts
    assert bool(call.route) == recovered and call.retries == 0
    assert result.stop_reason == ('end' if recovered else 'error')
    assert timing['fallbacks'] == int(recovered)
    turn = trace.last_turn(store, sid)
    assert turn.fallbacks == int(recovered)
    flags = trace.detect(turn, trace.Thresholds())
    fallback = [f for f in flags if f.signature.startswith('model.fallback:')]
    assert bool(fallback) == recovered
    if not recovered:
        assert not any('fell back' in f.title for f in flags)
        assert any(f.signature.startswith('model.timeout:' if case == 'timeout' else 'model.error:') for f in flags)


def test_actual_router_same_route_retry_success_is_not_a_fallback(tmp_path):
    from exo.core.models import FakeProvider, Router
    from exo.core.models.fake import rate_limited
    now = [0.0]
    provider = FakeProvider([rate_limited(), 'retry recovered'])
    router = Router(roles={'main': ['primary:x']}, instances={'primary': provider},
                    catalog={'providers': {}, 'models': {}, 'roles': {}}, env={}, clock=lambda: now[0])
    store = SessionStore(tmp_path / 'sessions.sqlite')
    bus = EventBus(store)
    def retry_clock(event):
        if event.type == 'notice' and event.body.text.startswith('retrying model call'):
            now[0] = 1000.0  # expire the fake credential cooldown, without a real wait
    bus.subscribe(retry_clock)
    loop = Loop(store, router.provider('main'), bus=bus, model='primary:x', max_retries=1, retry_delay=0)
    async def go():
        session = await loop.create_session()
        result = await loop.send(session.id, 'fixture retry')
        return session.id, result
    sid, result = run(go())
    events = store.events_since(0, session=sid)
    call = next(e.body for e in events if e.type == 'model.call')
    assert len(provider.calls) == 2 and result.stop_reason == 'end'
    assert [a['outcome'] for a in call.attempts] == ['error', 'ok']
    assert call.retries == 1 and call.route == 'primary:x' and not call.error
    assert next(e.body.timing['fallbacks'] for e in events if e.type == 'turn.completed') == 0
    turn = trace.last_turn(store, sid)
    assert turn.fallbacks == 0
    assert not any(f.signature.startswith('model.fallback:') for f in trace.detect(turn, trace.Thresholds()))
