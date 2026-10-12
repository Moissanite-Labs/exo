"""Turn loop, sessions and context model (W1-C). No network; local fakes only."""

from __future__ import annotations

import asyncio

from exo.core.context import DefaultCompressor, TreeContext, summary_prompt
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
    assert store.lineage(grand.id) == [grand.id, child.id, s.id]
    assert [m.text() for m in store.messages(child.id)] == ["one", "r1", "branch", "child"]
    assert store.messages(child.id)[0].meta["forked_from"] == store.messages(s.id)[0].id
    assert store.get_session(s.id).message_count == 4  # parent untouched
    assert store.undo(s.id) == 2 and [m.text() for m in store.messages(s.id)] == ["one", "r1"]


def _tree_loop(tmp_path, summ, **kw):
    store = SessionStore(tmp_path / "s.sqlite")
    ctx = TreeContext(store, DefaultCompressor(summ, retry_delay=0), leaf_size=2, tail_messages=2, **kw)
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

    s = run(go())
    last = prov.calls[-1]
    assert last[0].role == "system" and "SUMMARY" in last[0].text()
    assert len(last) <= 1 + 2 + 2  # system, maybe a bridge, tail of at most leaf+tail
    assert last[-1].text() == "q3"
    assert "q0" not in [m.text() for m in last[1:]]
    assert any(e.type == "context.compacted" for e in seen)
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


def test_turn_waits_for_pending_summary(tmp_path):
    summ = Summ()
    store, ctx, prov, loop, seen = _tree_loop(tmp_path, summ)

    async def go():
        summ.gate = asyncio.Event()
        s = await loop.create_session()
        for i in range(2):
            store.append_message(s.id, Message.user(f"old{i}"))
            store.append_message(s.id, Message.assistant(f"ans{i}"))
        task = asyncio.create_task(loop.send(s.id, "new"))
        await asyncio.sleep(0.05)
        assert not task.done() and prov.calls == []  # blocked on the summary
        summ.gate.set()
        await asyncio.wait_for(task, 2)
        return s

    run(go())
    assert "SUMMARY1" in prov.calls[0][0].text()


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


def test_summary_prompt_is_lifted_text():
    p = summary_prompt("X", 100, previous="P")
    assert "You are a summarization agent creating a context checkpoint" in p
    assert "PREVIOUS SUMMARY:\nP" in p and chr(0x2014) not in p
