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


def test_a_plain_tree_keeps_the_preview_size():
    """The control: a plain session's view shows a skipped leaf as its preview, so a leaf there keeps the preview
    size. The same 13 leaves stay under the floor and are skipped, and the call carries their previews, results
    cut short, and only the 4 rounds in no leaf yet in full."""
    leaves, _, call = asyncio.run(read_rounds("plain", False))
    assert len(leaves) == 13 and {n.status for n in leaves} == {"skipped"}
    assert results(call) == 4 and "chars cut]" in call[0].text() and call[-1].text() == QUESTION
