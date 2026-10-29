"""Issue #99: the turn does not wait for memory upkeep, and never reads a half-merged view."""
from __future__ import annotations

import asyncio
import re
import time

import pytest

from exo.core.context import PRIVATE_HEAD, AgentContext, DefaultCompressor, TreeContext, estimate_tokens
from exo.core.sessions import SessionStore
from exo.core.types import Message, ProviderEvent, ToolCall, ToolResult
from exo.turn_settle import SUMMARIZING_MARKER, cut_pending, settle_for_turn, thread_tail


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


def test_context_finds_the_running_turn_by_the_last_turn_id():
    """Issue #244: /context finds the turn by the thread's last stored turn_id, as build does by its own. Ten short
    turns come before the running turn "T", whose question's leaf is pending and which took a steer. /context keeps
    all of T, not the turns before it (the first turn_id) and not only what follows the steer (the last question)."""
    async def go():
        store, summ = SessionStore(), Gated()
        ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=0)
        p = store.create_session(agent="razi", kind="private")
        for i in range(10):
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
        report = ctx.report(p.id, "SYS")["tail"]
        summ.gate.set()
        while pending := TreeContext.pending(ctx, p.id):
            await asyncio.wait(pending)
        return report
    assert asyncio.run(go()) == {"messages": 1 + 1 + 1 + 32, "from_seq": 21}  # the marker, QLONG, STEER, 16 rounds


def test_cut_pending_without_a_turn_id_starts_the_turn_at_its_last_question():
    """Issue #244: with no turn_id the turn starts at the last user message that is not a note. A threads note
    stored after the question does not move it, so the question and its tool rounds stay, though the question's
    own leaf is pending."""
    async def go():
        store, summ = SessionStore(), Gated()
        ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=0)
        p = store.create_session(agent="razi", kind="private")
        for i in range(10):
            store.append_message(p.id, Message.user(f"q{i} " + "x" * 400))
            store.append_message(p.id, Message.assistant(f"a{i} " + "x" * 400))
        store.append_message(p.id, Message.user("QLONG"))  # message 21, in the pending leaf 17-24
        for i in range(16):
            store.append_message(p.id, Message.assistant("", [ToolCall(id=f"c{i}", name="read_file", arguments={})]))
            store.append_message(p.id, Message.tool([ToolResult.of_text(f"c{i}", "r" * 400)]))
        note = Message.user("[news from other threads]")
        note.meta.update({"note": "threads", "local": True})
        store.append_message(p.id, note)
        ctx.note(p.id)
        ctx.view_budget = 1
        tail = store.messages(p.id)
        out = cut_pending(ctx, p.id, tail)
        pending = {(n.start, n.end) for n in store.summaries(p.id) if n.status == "pending"}
        summ.gate.set()
        while running := TreeContext.pending(ctx, p.id):
            await asyncio.wait(running)
        return pending, tail, out
    pending, tail, out = asyncio.run(go())
    assert (17, 24) in pending and (33, 40) in pending
    assert out[0].text() == SUMMARIZING_MARKER and out[1:] == tail[20:] and out[1].text() == "QLONG"


def land(store, sid, *starts):
    """Mark leaves done while their jobs still wait at the gate, as when a later leaf lands first."""
    for n in store.summaries(sid):
        if n.level == 0 and n.start in starts:
            n.status, n.text = "done", "S"
            store.save_summary(n)


def test_the_view_cut_stops_at_the_leaf_that_holds_the_turns_question():
    """Issue #244: leaf 33-40 lands while the leaves before it are pending, so the private view shows them verbatim.
    The question (message 21, turn "T") is in 17-24 and a steer in 25-32. The view cut takes the two leaves before
    the turn to the marker and stops at the question's leaf, found by its turn_id. It cuts nothing after it, such as
    a threads note that opens the tail in the pending leaf 41-48. Without a turn_id the turn starts at the steer,
    the last user message, so the question's leaf is cut."""
    async def go():
        store, summ = SessionStore(), Gated()
        ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=0)
        p = store.create_session(agent="razi", kind="private")
        for i in range(10):
            store.append_message(p.id, Message.user(f"q{i} " + "x" * 400), f"t{i}")
            store.append_message(p.id, Message.assistant(f"a{i} " + "x" * 400), f"t{i}")
        store.append_message(p.id, Message.user("QLONG"), "T")
        for i in range(19):
            if i == 2:
                store.append_message(p.id, Message.user("STEER"), "T")  # message 26
            if i == 9:  # message 41, in the pending leaf 41-48 after the frontier
                note = Message.user("[news from other threads]")
                note.meta.update({"note": "threads", "local": True})
                store.append_message(p.id, note)
            store.append_message(p.id, Message.assistant("", [ToolCall(id=f"c{i}", name="read_file", arguments={})]), "T")
            store.append_message(p.id, Message.tool([ToolResult.of_text(f"c{i}", "r" * 400)]), "T")
        ctx.note(p.id)
        land(store, p.id, 33)
        ctx.view_budget = 1
        tail = ctx._thread_tail(p.id, 0)
        out = [cut_pending(ctx, p.id, tail, turn_id) for turn_id in ("T", "")]
        summ.gate.set()
        while pending := TreeContext.pending(ctx, p.id):
            await asyncio.wait(pending)
        return tail, out
    tail, (by_id, fallback) = asyncio.run(go())
    assert tail[1].meta == {"seq": 41, "note": "threads", "local": True} and by_id[1:] == fallback[1:] == tail[1:]
    view, steered = by_id[0].text(), fallback[0].text()
    assert all(f"## Messages {s}\n{SUMMARIZING_MARKER}" in view for s in ("1-8", "9-16"))
    assert "## Messages 17-24 (verbatim, summary pending)" in view and "QLONG" in view and "STEER" in view
    assert f"## Messages 17-24\n{SUMMARIZING_MARKER}" in steered and "QLONG" not in steered and "STEER" in steered


def test_the_view_cut_takes_a_finished_turns_question():
    """Issue #244: a finished turn "T" (question at message 21, tool rounds to message 50) is not the turn. The
    running turn "U" asks at message 51, in the tail. Leaf 41-48, all rounds of T, lands first, so the view shows
    leaves 1-40 verbatim, T's question among them. The view cut takes all five to the marker: T's question is not a
    reason to stop, and U stays whole."""
    async def go():
        store, summ = SessionStore(), Gated()
        ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=0)
        p = store.create_session(agent="razi", kind="private")
        for i in range(10):
            store.append_message(p.id, Message.user(f"q{i} " + "x" * 400), f"t{i}")
            store.append_message(p.id, Message.assistant(f"a{i} " + "x" * 400), f"t{i}")
        for turn, question, rounds in (("T", "QLONG", 14), ("U", "Q2", 6)):
            store.append_message(p.id, Message.user(question), turn)
            for i in range(rounds):
                call = ToolCall(id=f"{turn}{i}", name="read_file", arguments={})
                store.append_message(p.id, Message.assistant("", [call]), turn)
                store.append_message(p.id, Message.tool([ToolResult.of_text(call.id, "r" * 400)]), turn)
            if turn == "T":
                store.append_message(p.id, Message.assistant("A"), turn)
        ctx.note(p.id)
        land(store, p.id, 41)
        ctx.view_budget = 1
        tail = ctx._thread_tail(p.id, 0)
        out = cut_pending(ctx, p.id, tail, "U")
        summ.gate.set()
        while pending := TreeContext.pending(ctx, p.id):
            await asyncio.wait(pending)
        return tail, out
    tail, out = asyncio.run(go())
    view = out[0].text()
    assert tail[1].meta["seq"] == 48 and [m.text() for m in tail[3:5]] == ["A", "Q2"] and out[1:] == tail[1:]
    assert all(f"## Messages {i * 8 + 1}-{i * 8 + 8}\n{SUMMARIZING_MARKER}" in view for i in range(5))
    assert "QLONG" not in view and "## Messages 41-48\nS" in view


@pytest.mark.parametrize("landed", [(25,), (17, 25), (1, 9, 17, 33)])
def test_without_a_turn_id_a_question_before_the_tail_keeps_its_turn(landed):
    """Issue #244: with no turn_id and no user message in the tail, the turn began before it. The question is the
    last message of leaf 17-24, a threads note follows it, and the leaves in ``landed`` land first. The view cut takes
    the pending leaves before the question to the marker and keeps the rest: the question's own leaf, the turn's
    rounds in the view, and every stored message of the tail, which is all the turn's."""
    async def go():
        store, summ = SessionStore(), Gated()
        ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=0)
        p = store.create_session(agent="razi", kind="private")
        for i in range(11):
            store.append_message(p.id, Message.user(f"q{i} " + "x" * 400))
            store.append_message(p.id, Message.assistant(f"a{i} " + "x" * 400))
        store.append_message(p.id, Message.assistant("a11"))
        store.append_message(p.id, Message.user("QLONG"))  # message 24
        for i in range(18):
            if i == 2:  # message 29: a threads note does not start the turn
                note = Message.user("[news from other threads]")
                note.meta.update({"note": "threads", "local": True})
                store.append_message(p.id, note)
            store.append_message(p.id, Message.assistant("", [ToolCall(id=f"c{i}", name="read_file", arguments={})]))
            store.append_message(p.id, Message.tool([ToolResult.of_text(f"c{i}", "r" * 400)]))
        ctx.note(p.id)
        land(store, p.id, *landed)
        ctx.view_budget = 1
        tail = ctx._thread_tail(p.id, 0)
        out = cut_pending(ctx, p.id, tail)
        summ.gate.set()
        while pending := TreeContext.pending(ctx, p.id):
            await asyncio.wait(pending)
        return tail, out
    tail, out = asyncio.run(go())
    view = out[0].text()
    assert out[1:] == tail[1:] and not any(m.role == "user" for m in tail[1:])
    assert all((f"## Messages {s}-{s + 7}\n{SUMMARIZING_MARKER}" in view) == (s not in landed) for s in (1, 9))
    assert all((f"## Messages {s}-{s + 7} (verbatim" in view) == (s not in landed) for s in (17, 25))
    assert ("QLONG" in view) == (17 not in landed)


def test_a_checked_tail_is_not_reused_once_a_leaf_lands():
    """Issue #244: when the cut would not apply, the wait check keeps the tail it built for the call, but only until
    the event loop runs anything else. A leaf that lands in between leads the call's tail with its summary."""
    async def go():
        store, summ = SessionStore(), Gated()
        ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=0)
        p = store.create_session(agent="razi", kind="private")
        for i in range(12):
            store.append_message(p.id, Message.user(f"q{i}"))
            store.append_message(p.id, Message.assistant(f"a{i}"))
        ctx.note(p.id)
        assert await settle_for_turn(ctx, p.id) and TreeContext.pending(ctx, p.id)  # room to spare: no wait
        summ.gate.set()
        while pending := TreeContext.pending(ctx, p.id):
            await asyncio.wait(pending)
        return thread_tail(ctx, p.id, 0)
    tail = asyncio.run(go())
    assert tail[0].text().startswith(PRIVATE_HEAD) and "## Messages 1-8\nS" in tail[0].text()


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


@pytest.mark.parametrize('private', [False, True])
def test_overflow_wait_is_not_repeated_for_rounds_of_the_same_turn(tmp_path, private):
    async def go():
        store, summ = SessionStore(tmp_path / 'wait.sqlite'), Gated()
        ctx = AgentContext(store, summ, leaf_size=2, tail_messages=2, min_compact_tokens=0, view_budget=10)
        ctx.overflow_settle = 0.02
        sid = store.create_session(agent='fixture', kind='private' if private else 'thread').id
        fill(store, sid, 6, 1000)
        user = Message.user('current task'); user.meta['turn_id'] = 'fixture-turn-one'
        store.append_message(sid, user)
        waits = []
        original = asyncio.wait
        async def counted(tasks, **kw):
            waits.append(kw.get('timeout'))
            return await original(tasks, **kw)
        from unittest.mock import patch
        try:
            with patch('exo.turn_settle.asyncio.wait', counted):
                first = await ctx.build(sid, 'stable system', turn_id='fixture-turn-one')
                count = len(waits)
                assert count >= 1
                for i in range(4):
                    message = Message.assistant(f'round {i}'); message.meta['turn_id'] = 'fixture-turn-one'
                    store.append_message(sid, message)
                    out = await ctx.build(sid, 'stable system', turn_id='fixture-turn-one')
                    assert out and out[0].text().startswith('stable system')
                assert len(waits) == count, 'one pending summarizer must not add the full wait to every round'
                assert any('current task' in m.text() for m in first)
                user = Message.user('next task'); user.meta['turn_id'] = 'fixture-turn-two'
                store.append_message(sid, user)
                await ctx.build(sid, 'stable system', turn_id='fixture-turn-two')
                assert len(waits) > count, 'a new turn gets its own opportunity to wait'
                assert len(ctx.settled_turns) <= 2
        finally:
            summ.gate.set()
            while pending := TreeContext.pending(ctx, ctx._agent_key(sid)) + TreeContext.pending(ctx, sid):
                await original(pending)
            store.close()
    asyncio.run(go())


def test_shared_agent_wait_is_isolated_by_thread_and_unidentified_builds_keep_legacy_wait(tmp_path):
    async def go():
        store, summ = SessionStore(tmp_path / 'wait.sqlite'), Gated()
        ctx = AgentContext(store, summ, leaf_size=2, tail_messages=2, min_compact_tokens=0, view_budget=10)
        ctx.overflow_settle = 0.01
        first = store.create_session(agent='fixture').id
        second = store.create_session(agent='fixture').id
        fill(store, first, 6, 1000)
        key = ctx._agent_key(first)
        ctx.note(first)
        try:
            assert not await settle_for_turn(ctx, key, 'same-fixture-id', owner=first)
            assert not await settle_for_turn(ctx, key, 'same-fixture-id', owner=second)
            assert ctx.settled_turns[(first, key)] == ctx.settled_turns[(second, key)]
            # No turn identity means no claim that two calls belong to the same turn.
            before = dict(ctx.settled_turns)
            from unittest.mock import patch
            original = asyncio.wait
            waits = []
            async def counted(tasks, **kw):
                waits.append(kw.get('timeout'))
                return await original(tasks, **kw)
            with patch('exo.turn_settle.asyncio.wait', counted):
                assert not await settle_for_turn(ctx, key)
                assert not await settle_for_turn(ctx, key)
            assert len(waits) == 2 and ctx.settled_turns == before
        finally:
            summ.gate.set()
            while pending := TreeContext.pending(ctx, key): await asyncio.wait(pending)
            store.close()
    asyncio.run(go())


def test_ready_summaries_are_used_after_the_turn_wait_was_spent(tmp_path):
    async def go():
        store, summ = SessionStore(tmp_path / 'ready.sqlite'), Gated()
        ctx = AgentContext(store, summ, leaf_size=2, tail_messages=2, min_compact_tokens=0, view_budget=100)
        ctx.overflow_settle = 0.01
        sid = store.create_session(agent='fixture', kind='private').id
        fill(store, sid, 6, 1000)
        question = Message.user('keep this task'); question.meta['turn_id'] = 'fixture-turn'
        store.append_message(sid, question)
        await ctx.build(sid, 'stable system', turn_id='fixture-turn')
        summ.gate.set()
        result = await ctx.build(sid, 'stable system', turn_id='fixture-turn')
        assert any(node.status == 'done' for node in store.summaries(sid))
        assert any('keep this task' in m.text() for m in result)
        while pending := TreeContext.pending(ctx, sid): await asyncio.wait(pending)
        store.close()
    asyncio.run(go())
