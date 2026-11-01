"""Issue #262: a build, a merge job and /compact read each summary tree once per stretch, not once per helper call."""
from __future__ import annotations

import asyncio

from exo.core.context import AgentContext, TreeContext
from exo.core.sessions import SessionStore
from exo.core.types import Message
from exo.turn_settle import verbatim_overflows


class Summ:
    """Leaves shrink; a merge never does, so it stays skipped and the view shows its two children."""

    async def summarize(self, content, previous=None, session=None, **kw):
        return "M" * (len(content) + len(previous or "") + 10) if previous is not None else "S" * 40


class Short:
    """Every summary is short, so every merge is done."""

    async def summarize(self, content, previous=None, session=None, **kw):
        return "S" * 40


def counted(store):
    reads: list[str] = []
    store._db.set_trace_callback(lambda sql: reads.append(sql) if sql.startswith("SELECT * FROM summaries") else None)
    return reads


async def grown(ctx, key, sid, store, pairs):
    """A tree with many leaves and skipped merges at several levels (each skipped merge used to read the tree)."""
    for i in range(pairs):
        store.append_message(sid, Message.user(f"question {i} " + "q" * 200))
        store.append_message(sid, Message.assistant(f"answer {i} " + "a" * 200))
        if i % 16 == 0:
            ctx.note(sid)
            await ctx.settle(key, 30, None)
    for _ in range(4):
        ctx.note(sid)
        await ctx.settle(key, 30, None)
    nodes = store.summaries(key)
    assert sum(1 for n in nodes if n.level and n.status == "skipped") >= 8
    assert not ctx.pending(key)


def test_an_agent_build_reads_its_tree_once_per_stretch():
    async def go():
        store = SessionStore()
        ctx = AgentContext(store, Summ(), leaf_size=8, tail_messages=12, min_compact_tokens=0, view_budget=200)
        sid = store.create_session(agent="probe").id
        await grown(ctx, ctx.key("probe"), sid, store, 160)
        before = await ctx.build(sid, "SYS")
        reads = counted(store)
        after = await ctx.build(sid, "SYS")
        store._db.set_trace_callback(None)
        assert len(reads) <= 2, len(reads)  # upkeep, then the view; before #262 one more per skipped merge
        assert [m.text() for m in after] == [m.text() for m in before]
        assert ctx._read_once is None  # nothing outlives the build

    asyncio.run(go())


def test_a_plain_session_build_reads_its_tree_once_per_stretch():
    async def go():
        store = SessionStore()
        ctx = TreeContext(store, Summ(), leaf_size=8, tail_messages=12, min_compact_tokens=0, view_budget=200)
        await grown(ctx, "s", "s", store, 160)
        before = await ctx.build("s", "SYS")
        reads = counted(store)
        after = await ctx.build("s", "SYS")
        store._db.set_trace_callback(None)
        assert len(reads) <= 2, len(reads)
        assert [m.text() for m in after] == [m.text() for m in before]

    asyncio.run(go())


def test_each_step_reads_its_tree_once_when_no_text_is_cached():
    """After a restart, or once a text is evicted, a skipped merge must not read the tree again for its children."""
    async def go():
        store = SessionStore()
        plain = TreeContext(store, Summ(), leaf_size=8, tail_messages=12, min_compact_tokens=0, view_budget=200)
        await grown(plain, "s", "s", store, 160)
        agent = AgentContext(store, Summ(), leaf_size=8, tail_messages=12, min_compact_tokens=0, view_budget=200)
        sid, key = store.create_session(agent="probe").id, agent.key("probe")
        await grown(agent, key, sid, store, 160)
        steps = {"plain view": lambda: plain._plain_view("s"), "plain merge": lambda: plain._maybe_merge("s"),
                 "plain note": lambda: plain.note("s"), "plain wait check": lambda: verbatim_overflows(plain, "s"),
                 "agent view": lambda: agent._view(key), "agent merge": lambda: agent._maybe_merge(key),
                 "agent note": lambda: agent.note(sid), "agent wait check": lambda: verbatim_overflows(agent, key),
                 "/context": lambda: agent.report(sid)}
        for name, step in steps.items():
            plain._verbatim_cache.clear()
            agent._verbatim_cache.clear()
            reads = counted(store)
            step()
            store._db.set_trace_callback(None)
            assert len(reads) == 1, (name, len(reads))
        assert not plain.pending("s") and not agent.pending(sid)  # the tree was full: no step scheduled a node

    asyncio.run(go())


def test_a_shared_read_sees_the_nodes_its_own_step_schedules():
    async def go():
        store = SessionStore()
        ctx = TreeContext(store, Summ(), leaf_size=8, tail_messages=12, min_compact_tokens=0, view_budget=10**6)
        for i in range(40):
            store.append_message("s", Message.user(f"m{i + 1} " + "x" * 100))
        seen, merge = [], ctx._maybe_merge

        def spy(session, force=False):
            seen.append([(n.level, n.index, n.status) for n in ctx._nodes(session)])
            return merge(session, force)

        ctx._maybe_merge = spy
        ctx.note("s")  # reads the empty tree, then makes three leaves, then looks for merges in the same step
        assert seen == [[(0, 0, "pending"), (0, 1, "pending"), (0, 2, "pending")]]
        await ctx.settle("s", 30, None)
        assert "## Messages 1-8\n" + "S" * 40 in (await ctx.build("s", "SYS"))[0].text()

    asyncio.run(go())


def test_a_merge_job_reads_its_tree_once_for_its_two_halves():
    """With no cached text, a merge above skipped merges read the tree again for each skipped merge below it."""
    async def go():
        store = SessionStore()
        ctx = AgentContext(store, Summ(), leaf_size=8, tail_messages=12, min_compact_tokens=0, view_budget=200)
        sid, key = store.create_session(agent="probe").id, ctx.key("probe")
        await grown(ctx, key, sid, store, 160)
        top = max((n for n in store.summaries(key) if n.status == "skipped"), key=lambda n: n.level)
        assert top.level >= 3
        ctx._verbatim_cache.clear()  # as after a restart
        reads, at, summarize = counted(store), [], ctx._summarize

        async def spy(session, content, previous=None, extra=""):
            at.append(len(reads))
            return await summarize(session, content, previous, extra)

        ctx._summarize = spy
        await ctx._run(top)  # the job a retry of this merge runs
        store._db.set_trace_callback(None)
        assert at == [1], at  # one read before its summary call; it was 2 ** level - 1

    asyncio.run(go())


def test_compact_sizes_its_new_summaries_with_one_read():
    async def go():
        store = SessionStore()
        ctx = AgentContext(store, Short(), leaf_size=8, tail_messages=12, min_compact_tokens=0, view_budget=10**6)
        sid = store.create_session(agent="probe").id
        for i in range(64):
            store.append_message(sid, Message.user(f"question {i} " + "q" * 200))
            store.append_message(sid, Message.assistant(f"answer {i} " + "a" * 200))
        reads, sizing, size = counted(store), [], ctx._source_tokens

        def spy(node):
            before = len(reads)
            out = size(node)
            sizing.append(len(reads) - before)
            return out

        ctx._source_tokens = spy
        res = await ctx.compact(sid, "SYS", timeout=30)
        store._db.set_trace_callback(None)
        assert sum(1 for f in res["folded"] if f["level"]) >= 8
        assert sum(sizing) <= 1, sizing  # one read for the whole report; it was one for each merge

    asyncio.run(go())
