"""Issue #1013: an agent tree and a private thread's own tree size a leaf as the tail carries it.

``render_message`` cuts each tool result at 2000 characters, so a leaf of two rounds with 20k ``read_file`` results
measured about 1,050 tokens and was skipped as too small. Those trees end their view at the last summary, so a
skipped span after it rode the tail in full. A plain tree shows a skipped leaf as its preview, so it keeps that size.
"""
from __future__ import annotations

import asyncio

import pytest

from exo.core.context import AgentContext, TreeContext
from exo.core.loop import Loop
from exo.core.sessions import SessionStore
from exo.core.types import Message, ProviderEvent, ToolCall, ToolResult, ToolSpec, Usage
from exo.turn_settle import SUMMARIZING_MARKER

ROUNDS, SIZE = 30, 20000
QUESTION = "what did the files say?"


class Reader:
    """Fake chat model: a turn that asks to read gets one ``read_file`` call, then a short reply."""

    name = "fake"

    def __init__(self):
        self.calls: list[list[Message]] = []

    async def stream(self, messages, tools, **opts):
        n = len(self.calls)
        self.calls.append(list(messages))
        if messages[-1].role == "user" and messages[-1].text().startswith("read "):
            call = ToolCall(id=f"c{n}", name="read_file", arguments={"path": f"f{n}"})
            yield ProviderEvent(kind="tool_call", tool_call=call)
            yield ProviderEvent(kind="done", stop_reason="tool_use", message=Message.assistant("", tool_calls=[call]))
            return
        yield ProviderEvent(kind="usage", usage=Usage(input_tokens=10, output_tokens=2))
        yield ProviderEvent(kind="done", stop_reason="end", message=Message.assistant(f"ok {n}"))


class Summ:
    """Answers at once, or, with a ``gate``, once the test opens it."""

    def __init__(self):
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def summarize(self, content, previous=None, session=None, **kw):
        if self.gate is not None:
            await self.gate.wait()
        self.calls += 1
        return f"S{self.calls}"


def results(call):
    """How many tool results a call carries in full."""
    return sum(1 for m in call for r in m.tool_results if len(r.text()) > SIZE)


async def read_rounds(kind, slow):
    """30 turns that each read a 20k file, then the question. A fast summarizer lands each job before the next turn.
    A slow one lands nothing until the rounds are done. Returns the store, the tree's key, the last round's call
    and the question's call."""
    store, summ = SessionStore(), Summ()
    ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=1500)
    ctx.overflow_settle = 0  # a turn never waits: what the call carries comes from the leaves alone
    prov = Reader()

    async def read(call, c):
        return ToolResult.of_text(call.id, f"FILE-{call.id} " + "f" * SIZE)
    loop = Loop(store, prov, context=ctx, system_prompt="SYS", retry_delay=0,
                tool_specs=[ToolSpec(name="read_file", description="read a file")], run_tool=read)
    if kind == "plain":
        thread = await loop.create_session()
    else:
        thread = await loop.create_session(agent="razi", kind="private" if kind == "private" else "thread")
    tree = AgentContext.key("razi") if kind == "agent" else thread.id

    async def land():
        while pending := TreeContext.pending(ctx, tree):
            await asyncio.wait(pending)
    if slow:
        summ.gate = asyncio.Event()
    for i in range(ROUNDS):
        await loop.send(thread.id, f"read file {i}")
        if not slow:
            await land()
    last_round = prov.calls[-1]
    if slow:
        summ.gate.set()
        await land()
    await loop.send(thread.id, QUESTION)
    store_leaves = [n for n in store.summaries(tree) if n.level == 0]
    store.close()
    return store_leaves, last_round, prov.calls[-1]


@pytest.mark.parametrize("slow", [False, True], ids=["fast", "slow"])
@pytest.mark.parametrize("kind", ["private", "agent"])
def test_large_results_are_summarized_and_the_call_is_bounded_once_the_leaves_land(kind, slow):
    """30 rounds of 20k results make 13 leaves of about 10k tokens each. Before #1013 each measured about 1,050
    tokens, under the floor of 1500, so all 13 were skipped and the next call carried 22 of the results in full
    with a fast summarizer, 6 with a slow one. Now each leaf is summarized, and the call carries only the 4 rounds
    in no leaf yet. With a slow summarizer the leaves wait for it. In a private thread their spans are then cut
    behind the marker (#244), where before they were skipped at once and the last round carried all 30 results."""
    leaves, last_round, call = asyncio.run(read_rounds(kind, slow))
    assert len(leaves) == 13 and {n.status for n in leaves} == {"done"}
    assert results(call) == 4 and call[-1].text() == QUESTION
    if slow and kind == "private":
        assert any(m.text() == SUMMARIZING_MARKER for m in last_round) and results(last_round) <= 4


class Long:
    """A summary of 1,500 tokens: longer than the preview of two 20k rounds, far shorter than the rounds in full."""

    async def summarize(self, content, previous=None, session=None, **kw):
        return "L" * 6000


@pytest.mark.parametrize("kind", ["private", "agent"])
def test_the_shrink_test_and_the_reported_sizes_measure_the_leaf_in_full(kind):
    """The shrink test measures a leaf as the floor does. Two rounds of 20k results preview at 1,060 tokens,
    so a summary of 1,500 is kept only when the leaf counts in full, 10,080. The summarized event and the
    /compact report give that same size, so a summary they report is smaller than its source."""
    async def go():
        store = SessionStore()
        ctx = AgentContext(store, Long(), leaf_size=8, tail_messages=12, min_compact_tokens=1500)
        events = []

        async def on_event(event, session):
            events.append(event)
        ctx.on_event = on_event
        thread = store.create_session(agent="razi", kind="private" if kind == "private" else "thread").id
        tree = thread if kind == "private" else AgentContext.key("razi")
        for i in range(8):
            store.append_message(thread, Message.user(f"read file {i}"))
            store.append_message(thread, Message.assistant("", [ToolCall(id=f"c{i}", name="read_file",
                                                                         arguments={"path": f"f{i}"})]))
            store.append_message(thread, Message.tool([ToolResult.of_text(f"c{i}", "f" * SIZE)]))
            store.append_message(thread, Message.assistant(f"ok {i}"))
        folded = []
        if kind == "agent":
            folded = (await ctx.compact(thread, "SYS", timeout=30))["folded"]
        else:
            ctx.note(thread)
            while pending := TreeContext.pending(ctx, tree):
                await asyncio.wait(pending)
        leaves = [n for n in store.summaries(tree) if n.level == 0]
        store.close()
        return leaves, [(e.before_tokens, e.after_tokens) for e in events if e.level == 0], \
            [(f["before_tokens"], f["after_tokens"]) for f in folded if f["level"] == 0]
    leaves, reported, folded = asyncio.run(go())
    assert leaves and {n.status for n in leaves} == {"done"}
    assert len(reported) == len(leaves) and all(before > 2 * SIZE // 4 and after == 1500 for before, after in reported)
    assert folded == (reported if kind == "agent" else [])


def test_a_plain_tree_keeps_the_preview_size():
    """The control: a plain session's view shows a skipped leaf as its preview, so a leaf there keeps the preview
    size. The same 13 leaves stay under the floor and are skipped, and the call carries their previews, results
    cut short, and only the 4 rounds in no leaf yet in full."""
    leaves, _, call = asyncio.run(read_rounds("plain", False))
    assert len(leaves) == 13 and {n.status for n in leaves} == {"skipped"}
    assert results(call) == 4 and "chars cut]" in call[0].text() and call[-1].text() == QUESTION


@pytest.mark.parametrize("kind", ["thread", "private"])
def test_a_short_turn_in_another_thread_of_the_agent_waits_for_these_leaves(kind):
    """Any thread of the agent can wait for these leaves. Thread B reads ten 20k files while the summarizer is
    held, so its leaves stay pending in the agent tree. Thread A then asks a short question. Its overflow check
    counts the whole agent log after the frontier, B's results in full, so A waits for B's leaves (D-068), though
    its call carries none of B's results. Before #1013 those leaves were skipped at once, so A had nothing to wait
    for. The gate opens just before A's build, which suspends only to wait: B's leaves land only if A waits."""
    async def go():
        store, summ = SessionStore(), Summ()
        ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=1500)
        ctx.overflow_settle = 0  # B's own turns do not wait

        async def read(call, c):
            return ToolResult.of_text(call.id, f"FILE-{call.id} " + "f" * SIZE)
        loop = Loop(store, Reader(), context=ctx, system_prompt="SYS", retry_delay=0,
                    tool_specs=[ToolSpec(name="read_file", description="read a file")], run_tool=read)
        a = await loop.create_session(agent="razi", kind=kind)
        await loop.send(a.id, "hello")
        b = await loop.create_session(agent="razi", kind="thread")
        summ.gate = asyncio.Event()
        for i in range(10):
            await loop.send(b.id, f"read file {i}")
        tree = AgentContext.key("razi")
        held = {n.status for n in store.summaries(tree) if n.level == 0}
        ctx.overflow_settle = 60  # longer than the test runs: A's build returns once B's leaves land
        store.append_message(a.id, Message.user("how are you?"))
        summ.gate.set()
        call = await ctx.build(a.id, "SYS")
        landed = {n.status for n in store.summaries(tree) if n.level == 0}
        store.close()
        return held, landed, call
    held, landed, call = asyncio.run(go())
    assert held == {"pending"} and landed == {"done"}
    assert any(m.text() == "how are you?" for m in call)
    assert results(call) == 0 and sum(len(m.text()) for m in call) < 2000  # B's turns come as a short note
