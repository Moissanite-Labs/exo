"""Issue #99: the turn does not wait for memory upkeep, and never reads a half-merged view."""
from __future__ import annotations

import asyncio
import re
import time

from exo.core.context import AgentContext, DefaultCompressor, TreeContext, estimate_tokens
from exo.core.sessions import SessionStore
from exo.core.types import Message, ProviderEvent, ToolCall, ToolResult
from exo.turn_settle import SUMMARIZING_MARKER, cut_pending


class Summ:
    """Summarizer fake. ``gate`` holds every call; ``merge_gate`` holds only merges (prompts with a previous summary)."""

    name = "summ"

    def __init__(self):
        self.gate: asyncio.Event | None = None
        self.merge_gate: asyncio.Event | None = None
        self.n = 0

    async def stream(self, messages, tools, **opts):
        text = messages[0].text()
        self.n += 1
        n = self.n
        gate = self.merge_gate if "PREVIOUS SUMMARY" in text and self.merge_gate else self.gate
        if gate is not None:
            await gate.wait()
        yield ProviderEvent(kind="done", stop_reason="end", message=Message.assistant(f"SUMMARY{n}"))


def make(tmp_path, summ, **kw):
    store = SessionStore(tmp_path / "s.sqlite")
    ctx = TreeContext(store, DefaultCompressor(summ, retry_delay=0), leaf_size=2, tail_messages=2,
                      **{"min_compact_tokens": 0, **kw})
    return store, ctx


def fill(store, sid, n, size=1):
    for i in range(n):
        store.append_message(sid, (Message.user if i % 2 == 0 else Message.assistant)(f"m{i + 1} " + "x" * size))


def ranges(text):
    return [(int(a), int(b)) for a, b in re.findall(r"## Messages (\d+)-(\d+)", text)]


def test_build_uses_ready_summaries_only():
    async def go(tmp_path):
        summ = Summ()
        store, ctx = make(tmp_path, summ, settle_timeout=60)
        summ.gate = asyncio.Event()
        fill(store, "s", 6)
        t = time.monotonic()
        first = (await asyncio.wait_for(ctx.build("s", "sys"), 2))[0].text()
        assert time.monotonic() - t < 1 and "summary pending" in first and "m1 " in first
        assert ctx.pending("s")  # the work keeps running in the background
        summ.gate.set()
        await asyncio.wait_for(ctx.settle("s"), 2)
        second = (await ctx.build("s", "sys"))[0].text()
        assert "SUMMARY" in second and "m1 " not in second and "summary pending" not in second

    import pathlib
    import tempfile
    asyncio.run(go(pathlib.Path(tempfile.mkdtemp())))


class Gated:
    """Summarizer fake for an AgentContext: finishes nothing until ``gate`` opens."""

    def __init__(self):
        self.gate = asyncio.Event()

    async def summarize(self, content, previous=None, **kw):
        await self.gate.wait()
        return "S"


def test_cut_pending_cuts_whole_pending_leaves_and_keeps_a_call_with_its_results():
    """Issue #244: cut_pending cuts the spans of pending leaves, oldest first and a whole leaf at a time, until what
    comes before the turn fits view_budget, and a marker stands in the gap. A leaf edge between a call and its
    results keeps both. With room to spare, or with no leaf pending, the tail passes through."""
    async def go():
        store, summ = SessionStore(), Gated()
        ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=0)
        p = store.create_session(agent="razi", kind="private")
        for i in range(30):  # rounds of three, so the edge after leaf 4 (message 32) falls after call c10
            store.append_message(p.id, Message.user(f"u{i} " + "x" * 400))
            store.append_message(p.id, Message.assistant("", [ToolCall(id=f"c{i}", name="read_file", arguments={})]))
            store.append_message(p.id, Message.tool([ToolResult.of_text(f"c{i}", "r" * 400)]))
        ctx.note(p.id)
        tail = store.messages(p.id)
        assert {(n.start, n.end, n.status) for n in store.summaries(p.id)} == {(i * 8 + 1, i * 8 + 8, "pending")
                                                                               for i in range(9)}
        # the turn is u29 (message 88); what is left of the leaves before it after cutting leaves 1-4 just fits
        ctx.view_budget = sum(estimate_tokens(m) for m in tail if 33 <= m.meta["seq"] < 88)
        out = cut_pending(ctx, p.id, tail)
        ctx.view_budget = 10 ** 9
        roomy = cut_pending(ctx, p.id, tail)
        summ.gate.set()
        while pending := TreeContext.pending(ctx, p.id):
            await asyncio.wait(pending)
        ctx.view_budget = 1
        return tail, out, roomy, cut_pending(ctx, p.id, tail)
    tail, out, roomy, done = asyncio.run(go())
    assert out[0].text() == SUMMARIZING_MARKER and out[0].meta == {"note": "private", "local": True}
    assert out[1].meta["seq"] == 32 and out[1].tool_calls[0].id == "c10"  # the call stays with its results
    assert out[2].role == "tool" and out[2].tool_results[0].call_id == "c10"
    assert out[1:] == tail[31:] and roomy == tail and done == tail


def test_cut_pending_keeps_the_whole_turn_by_its_turn_id():
    """Issue #244: the turn is found by its turn_id, so it stays whole even when a summary already holds its question
    and a steer message came in mid-turn. Only the pending spans before its first message are cut."""
    async def go():
        store, summ = SessionStore(), Gated()
        ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=0)
        p = store.create_session(agent="razi", kind="private")
        for i in range(10):  # ten short turns, then one long tool loop "T" from message 21
            store.append_message(p.id, Message.user(f"q{i} " + "x" * 400), f"t{i}")
            store.append_message(p.id, Message.assistant(f"a{i} " + "x" * 400), f"t{i}")
        store.append_message(p.id, Message.user("QLONG"), "T")
        for i in range(16):
            if i == 12:
                store.append_message(p.id, Message.user("STEER"), "T")
            store.append_message(p.id, Message.assistant("", [ToolCall(id=f"c{i}", name="read_file", arguments={})]), "T")
            store.append_message(p.id, Message.tool([ToolResult.of_text(f"c{i}", "r" * 400)]), "T")
        ctx.note(p.id)
        ctx.view_budget = 1
        tail = [m for m in store.messages(p.id) if m.text() != "QLONG"]  # as if a summary held the question
        out = cut_pending(ctx, p.id, tail, "T")
        summ.gate.set()
        while pending := TreeContext.pending(ctx, p.id):
            await asyncio.wait(pending)
        return store, p, tail, out
    store, p, tail, out = asyncio.run(go())
    assert [(n.start, n.end) for n in store.summaries(p.id) if n.level == 0] == [(i * 8 + 1, i * 8 + 8) for i in range(5)]
    assert out[0].text() == SUMMARIZING_MARKER and out[1:] == [m for m in tail if m.meta.get("turn_id") == "T"]


def test_overflow_settles_briefly_not_for_settle_timeout():
    async def go(tmp_path):
        summ = Summ()
        store, ctx = make(tmp_path, summ, settle_timeout=60, view_budget=100)
        summ.gate = asyncio.Event()  # never released
        fill(store, "s", 6, size=2000)  # the verbatim tail alone is far over the budget
        t = time.monotonic()
        text = (await asyncio.wait_for(ctx.build("s", "sys"), 10))[0].text()
        waited = time.monotonic() - t
        assert 0.05 < waited < 8, waited  # it did wait, but for the short timeout
        assert "summary pending" in text
        summ.gate.set()

    import pathlib
    import tempfile
    asyncio.run(go(pathlib.Path(tempfile.mkdtemp())))


def test_overflow_uses_summary_that_lands_within_the_short_wait():
    async def go(tmp_path):
        summ = Summ()
        store, ctx = make(tmp_path, summ, settle_timeout=60, view_budget=100)
        summ.gate = asyncio.Event()
        fill(store, "s", 6, size=2000)
        task = asyncio.create_task(ctx.build("s", "sys"))
        await asyncio.sleep(0.1)
        assert not task.done()
        summ.gate.set()
        text = (await asyncio.wait_for(task, 10))[0].text()
        assert "SUMMARY" in text and "summary pending" not in text

    import pathlib
    import tempfile
    asyncio.run(go(pathlib.Path(tempfile.mkdtemp())))


def test_turn_during_merge_sees_a_consistent_view_then_the_merged_one():
    async def go(tmp_path):
        summ = Summ()
        store, ctx = make(tmp_path, summ, settle_timeout=60, view_budget=1)
        fill(store, "s", 10, size=40)
        ctx.overflow_settle = 0.1  # the tiny budget makes every build "overflow": keep the short wait short
        summ.merge_gate = asyncio.Event()  # leaves finish; the merge of the first two is held
        ctx.note("s")
        await asyncio.sleep(0.2)
        assert ctx.pending("s"), "a merge must be in flight"
        during = (await asyncio.wait_for(ctx.build("s", "sys"), 2))
        assert ctx.pending("s"), "the merge was still running while the turn was built"
        text = during[0].text()
        span = ranges(text)
        # the view tiles 1..frontier with no gap or overlap, and the tail starts right after it
        assert span and span[0][0] == 1 and all(b[0] == a[1] + 1 for a, b in zip(span, span[1:]))
        assert all(hi - lo + 1 == 2 for lo, hi in span)  # old leaves only: the merge is not partly applied
        frontier = span[-1][1]
        tail = [m.text() for m in during[1:] if m.text().startswith("m")]
        everything = text + "\n".join(m.text() for m in during[1:])
        for i in range(1, 11):  # each message is in the view's range or in the tail, exactly once
            in_view = any(lo <= i <= hi for lo, hi in span)
            in_tail = any(t.startswith(f"m{i} ") for t in tail)
            assert in_view != in_tail, (i, everything)
        assert tail and tail[0].startswith(f"m{frontier + 1} ")
        summ.merge_gate.set()
        await asyncio.wait_for(ctx.settle("s"), 2)
        after = await ctx.build("s", "sys")
        merged = ranges(after[0].text())
        assert merged[0][0] == 1 and merged[0][1] >= 4  # the merge now stands in for the leaves
        rest = [m.text() for m in after[1:] if m.text().startswith("m")]
        assert rest and rest[0].startswith(f"m{merged[-1][1] + 1} ")
        assert all(f"m{i} " not in after[0].text() for i in range(1, 11))  # summarized text is not also verbatim

    import pathlib
    import tempfile
    asyncio.run(go(pathlib.Path(tempfile.mkdtemp())))
