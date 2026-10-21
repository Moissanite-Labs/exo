"""Issue #99: the turn does not wait for memory upkeep, and never reads a half-merged view."""
from __future__ import annotations

import asyncio
import re
import time

from exo.core.context import DefaultCompressor, TreeContext
from exo.core.sessions import SessionStore
from exo.core.types import Message, ProviderEvent


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


def test_fit_tail_keeps_the_newest_that_fit_and_always_the_last():
    from exo.core.context import estimate_tokens
    from exo.turn_settle import fit_tail

    msgs = [Message.user(f"m{i} " + "x" * 400) for i in range(10)]  # about 100 tokens each
    kept, cut = fit_tail(msgs, 250)
    assert kept == msgs[len(msgs) - len(kept):] and 0 < len(kept) < len(msgs) and cut == len(msgs) - len(kept)
    assert sum(estimate_tokens(m) for m in kept) <= 250 < sum(estimate_tokens(m) for m in msgs[len(msgs) - len(kept) - 1:])
    kept, cut = fit_tail(msgs, 10 ** 9)
    assert kept == msgs and cut == 0
    kept, cut = fit_tail(msgs, 1)  # even a budget under one message keeps the last: it is the turn being answered
    assert kept == msgs[-1:] and cut == 9


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
