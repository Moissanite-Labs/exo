"""AgentContext (D-028): one tree per agent, thread tails, context notes, recall placement, concurrency."""

from __future__ import annotations

import asyncio
import re

from exo.core.context import NOTE_HEAD, PRIVATE_HEAD, AgentContext, TreeContext
from exo.core.loop import Loop
from exo.core.sessions import SessionStore
from exo.core.types import Message, ProviderEvent, Usage


def run(coro):
    return asyncio.run(coro)


class Echo:
    """Fake chat model: answers "ok <n>" and records each call's messages; optional gates per call."""

    name = "fake"

    def __init__(self):
        self.calls: list[list[Message]] = []
        self.hold: dict[int, asyncio.Event] = {}

    async def stream(self, messages, tools, **opts):
        n = len(self.calls)
        self.calls.append(list(messages))
        if n in self.hold:
            await self.hold[n].wait()
        yield ProviderEvent(kind="usage", usage=Usage(input_tokens=10, output_tokens=2))
        yield ProviderEvent(kind="done", stop_reason="end", message=Message.assistant(f"ok {n}"))


class Summ:
    def __init__(self):
        self.prompts: list[str] = []
        self.keys: list[str | None] = []  # the tree each prompt was for (an agent key or a session id)

    async def summarize(self, content, previous=None, session=None, **kw):
        self.prompts.append(content)
        self.keys.append(session)
        return f"S{len(self.prompts)}"


class Memory:
    def __init__(self, recall=""):
        self.recall = recall

    def system_prompt_block(self):
        return "PACK"

    def prefetch(self, query, session_id=""):
        return self.recall


def setup(memory=None, **kw):
    store = SessionStore()
    summ = Summ()
    ctx = AgentContext(store, summ, **{"leaf_size": 8, "tail_messages": 12, "min_compact_tokens": 0, **kw})
    prov = Echo()
    loop = Loop(store, prov, context=ctx, memory=memory, system_prompt="SYS", retry_delay=0)
    return store, ctx, prov, loop, summ


def test_a_new_thread_knows_what_another_thread_just_did():
    store, ctx, prov, loop, _ = setup()

    async def go():
        a = await loop.create_session(agent="razi", title="window A")
        await loop.send(a.id, "my locker code is 4417")
        b = await loop.create_session(agent="razi", title="window B")
        await loop.send(b.id, "what is my locker code?")
        return a, b
    a, b = run(go())
    call = prov.calls[-1]
    notes = [m for m in call if m.role == "user" and NOTE_HEAD in m.text()]
    assert len(notes) == 1 and "4417" in notes[0].text() and '"window A"' in notes[0].text()
    assert call[-1].text() == "what is my locker code?" or call[-2].text() == "what is my locker code?"
    stored = store.messages(b.id)
    assert any(m.meta.get("note") for m in stored)  # stored in the thread, so the next call replays it
    assert store.last_pos("razi") == 4  # the note is not in the log
    assert "4417" not in call[0].text()  # not summarized yet: it came through the note


def test_notes_are_not_repeated_and_skip_private_threads():
    store, ctx, prov, loop, _ = setup()

    async def go():
        a = await loop.create_session(agent="razi")
        p = await loop.create_session(agent="razi", kind="private")
        b = await loop.create_session(agent="razi")
        await loop.send(a.id, "public fact")
        await loop.send(p.id, "private matter")
        await loop.send(b.id, "first")
        await loop.send(b.id, "second")
        return b
    _b = run(go())
    first, second = prov.calls[-2], prov.calls[-1]
    text = "\n".join(m.text() for m in first)
    assert "public fact" in text and "private matter" not in text
    assert sum(NOTE_HEAD in m.text() for m in second) == 1  # the same stored note, not a new one


def test_system_message_is_shared_by_threads_and_holds_the_agent_tree():
    store, ctx, prov, loop, summ = setup(leaf_size=2, tail_messages=2)

    async def go():
        a = await loop.create_session(agent="razi", title="alpha")
        b = await loop.create_session(agent="razi", title="beta")
        for i in range(3):
            await loop.send(a.id, f"a{i}")
            await loop.send(b.id, f"b{i}")
        ma = await ctx.build(a.id, "SYS")
        mb = await ctx.build(b.id, "SYS")
        assert ma[0].text() == mb[0].text()  # byte-identical prefix across threads
        return a, b
    a, b = run(go())
    assert "Your memory" in prov.calls[-1][0].text() and "S1" in prov.calls[-1][0].text()
    assert "in thread" in summ.prompts[0] and ("alpha" in summ.prompts[0] or "beta" in summ.prompts[0])
    assert store.summaries("agent:razi") and not store.summaries(a.id)


def test_recall_is_placed_before_the_turn_not_in_the_system_message():
    store, ctx, prov, loop, _ = setup(memory=Memory("belief: likes tea"))

    async def go():
        a = await loop.create_session(agent="razi")
        await loop.send(a.id, "hello")
        await loop.send(a.id, "again")
    run(go())
    first, second = prov.calls
    assert first[0].text() == second[0].text() == "SYS\n\nPACK"
    texts = [m.text() for m in second]
    i = texts.index("again")
    assert texts[i - 1].startswith("# Recalled memory") and "likes tea" in texts[i - 1]
    assert sum("likes tea" in t for t in texts) == 1  # last turn's recall is not kept
    assert not any("Recalled" in m.text() for m in store.messages(store.list_sessions()[0].id))  # never stored


def test_subagent_and_plain_sessions_keep_their_own_tree():
    store, ctx, prov, loop, _ = setup(memory=Memory("R"))

    async def go():
        sub = await loop.create_session(agent="razi", kind="subagent")
        plain = await loop.create_session()
        await loop.send(sub.id, "child work")
        await loop.send(plain.id, "legacy")
    run(go())
    assert store.last_pos("razi") == 0
    assert all("Recalled memory" in c[0].text() for c in prov.calls)  # old placement for per-session trees


async def settle_own(ctx, sid):
    """Wait for a private thread's own tree (AgentContext.settle waits for the agent tree)."""
    while pending := TreeContext.pending(ctx, sid):
        await asyncio.wait(pending)


def test_a_private_thread_keeps_its_own_history_and_shares_none_of_it():
    """D-028: private entries stay out of the agent tree, so the agent frontier must not cut a private thread's tail.
    It keeps a tree of its own: its opening fact survives a dozen turns while another thread's leaves are summarized,
    and no other thread, agent summary or plugin hook sees it."""
    store, ctx, prov, loop, summ = setup()
    hooked = []
    ctx.on_pre_compress = lambda s, msgs: hooked.append(("pre", s, " ".join(m.text() for m in msgs)))
    ctx.on_capture = lambda s, msgs: hooked.append(("capture", s, " ".join(m.text() for m in msgs)))
    ctx.on_compacted = lambda s, node: hooked.append(("compacted", s, node.text))

    async def go():
        a = await loop.create_session(agent="razi", title="public")
        p = await loop.create_session(agent="razi", kind="private", title="private")
        await loop.send(p.id, "my locker code is 4417")
        for i in range(12):
            await loop.send(a.id, f"public work {i} " + "y" * 3000)
            await loop.send(p.id, f"private chat {i}")
            await ctx.settle(AgentContext.key("razi"), 5, None)
            await settle_own(ctx, p.id)
        await loop.send(p.id, "what is my locker code?")
        asked = prov.calls[-1]
        await loop.send(a.id, "public last")
        both = [(await ctx.build(t.id, "SYS", settle=False))[0].text() for t in (p, a)]
        return a, p, asked, both
    a, p, asked, both = run(go())
    first = [n for n in store.summaries(p.id) if n.level == 0 and n.start == 1]
    assert first and first[0].status == "done", "the private thread's opening is in no tree of its own"
    n = summ.prompts.index(next(t for t, k in zip(summ.prompts, summ.keys) if k == p.id and "4417" in t))
    assert first[0].text == f"S{n + 1}" and f"## Messages 1-8\n{first[0].text}" in asked[1].text()
    assert asked[1].text().startswith(PRIVATE_HEAD) and "4417" not in asked[0].text()  # its view leads the tail
    assert both[0] == both[1]  # the system message (cached prefix) is the same for both threads
    public = [c for c in prov.calls if c[-1].text().startswith("public")]
    assert len(public) == 13 and not any("4417" in m.text() for c in public for m in c)
    assert not any("4417" in t for t, k in zip(summ.prompts, summ.keys) if k != p.id)
    assert {h[0] for h in hooked} == {"pre", "capture", "compacted"}  # the agent tree's hooks still run
    mine = {f"S{i + 1}" for i, k in enumerate(summ.keys) if k == p.id}  # the private tree's summaries
    assert mine and not any("4417" in h[2] or "private chat" in h[2] or h[2] in mine for h in hooked)  # none of its own


def test_a_private_thread_keeps_all_of_itself_until_its_own_tree_starts():
    """Before its first leaf, a private thread's tail is all of it, even once the agent frontier has passed it."""
    store, ctx, prov, loop, _ = setup(tail_messages=4)

    async def go():
        a = await loop.create_session(agent="razi")
        p = await loop.create_session(agent="razi", kind="private")
        await loop.send(p.id, "my locker code is 4417")
        for i in range(6):
            await loop.send(a.id, f"public work {i}")
        await ctx.settle(AgentContext.key("razi"), 5, None)
        await loop.send(p.id, "anything new?")
        await loop.send(p.id, "what is my locker code?")
        return p
    p = run(go())
    assert ctx._view(AgentContext.key("razi"))[1] >= 2 and store.summaries(p.id) == []
    assert any("my locker code is 4417" == m.text() for m in prov.calls[-1])


def test_a_private_threads_small_spans_stay_whole_in_its_tail():
    """As in the agent view, the private view ends at its last summary: spans too small to summarize stay whole
    in the tail, not cut in the view. Alone, a 4.6k-char paste survives 15 turns and each call extends the one
    before (the cached prefix holds); while another thread's leaves are summarized, the paste stays whole too."""
    store, ctx, prov, loop, _ = setup(min_compact_tokens=1500)
    paste = "PASTE-START " + "x" * 4600 + " PASTE-END"

    async def go():
        a = await loop.create_session(agent="razi", title="public")
        p = await loop.create_session(agent="razi", kind="private", title="private")
        await loop.send(p.id, "please keep this config: " + paste)
        calls = []
        for i in range(30):
            if i >= 15:
                await loop.send(a.id, f"public work {i} " + "y" * 3000)
                await ctx.settle(AgentContext.key("razi"), 5, None)
            await loop.send(p.id, f"short private turn {i}")
            await settle_own(ctx, p.id)
            calls.append([(m.role, m.text()) for m in prov.calls[-1]])
        return p, calls
    p, calls = run(go())
    assert all(any(paste in text for _, text in call) for call in calls)
    assert all(nxt[:len(prev)] == prev for prev, nxt in zip(calls[:14], calls[1:15]))
    assert ctx._view(AgentContext.key("razi"))[1] > 31  # the agent frontier is past the private turns before i=15
    assert len(store.summaries(p.id)) > 2 and {n.status for n in store.summaries(p.id)} == {"skipped"}


class Keep(Summ):
    """A faithful fake: its summary keeps every marker (mk<n>, ok <n>) it is shown."""

    async def summarize(self, content, previous=None, session=None, **kw):
        await super().summarize(content, previous, session)
        return "kept " + ",".join(re.findall(r"mk\d+|ok \d+", f"{previous or ''}\n{content}"))


def test_a_private_thread_and_its_fork_after_undo_keep_every_message():
    """Undo hides a private thread's rows (its seqs stay used) and a fork renumbers its copies, so a summary past
    the gap would cover other messages in the fork: it is not copied. The thread and its fork both carry every
    message, verbatim or in a summary, and neither carries the undone one."""
    store, summ = SessionStore(), Keep()
    ctx = AgentContext(store, summ, leaf_size=8, tail_messages=12, min_compact_tokens=0)
    prov = Echo()
    loop = Loop(store, prov, context=ctx, system_prompt="SYS", retry_delay=0)

    async def go():
        a = await loop.create_session(agent="razi", title="public")
        p = await loop.create_session(agent="razi", kind="private", title="private")
        await loop.send(p.id, "mk0 opening")
        await loop.send(p.id, "mk1 to undo")
        assert store.undo(p.id) == 2
        for i in range(2, 21):
            await loop.send(p.id, f"mk{i} chat")
            await loop.send(a.id, f"public {i} " + "y" * 200)
            await ctx.settle(AgentContext.key("razi"), 5, None)
            await settle_own(ctx, p.id)
        want = {w for m in store.messages(p.id) for w in re.findall(r"mk\d+|ok \d+", m.text())}
        child = await loop.fork(p.id)
        await loop.send(child.id, "mk99 fork asks")
        fork_call = prov.calls[-1]
        await loop.send(p.id, "mk98 parent asks")
        return p, want, fork_call, prov.calls[-1]
    p, want, fork_call, p_call = run(go())
    for call in (p_call, fork_call):
        text = "\n".join(m.text() for m in call[1:])
        assert {w for w in want if not re.search(rf"\b{w}\b", text)} == set()
        assert not re.search(r"\bmk1\b", text)
    assert any(n.status == "done" and n.start > 4 for n in store.summaries(p.id))  # leaves past the gap


def test_two_threads_run_at_once_and_see_each_other_next_call():
    store, ctx, prov, loop, _ = setup()

    async def go():
        a = await loop.create_session(agent="razi", title="A")
        b = await loop.create_session(agent="razi", title="B")
        prov.hold[0] = asyncio.Event()
        ta = asyncio.create_task(loop.send(a.id, "A says: deploy is blue"))
        while not prov.calls:
            await asyncio.sleep(0.001)
        tb = asyncio.create_task(loop.send(b.id, "B says: the cat is named Pip"))
        while len(prov.calls) < 2:
            await asyncio.sleep(0.001)
        assert "deploy is blue" in "\n".join(m.text() for m in prov.calls[1])  # B saw A's committed entry
        prov.hold[0].set()
        await asyncio.gather(ta, tb)
        await loop.send(a.id, "and now?")
        return a
    run(go())
    assert "named Pip" in "\n".join(m.text() for m in prov.calls[-1])  # A sees B at its next call
    assert store.verify_log("razi")["ok"]


def test_skipped_spans_merge_once_the_view_is_over_budget():
    """Small spans stay verbatim (min_compact_tokens), but they still count toward the view budget and merge,
    so a long chat of short messages cannot grow the view without bound."""
    from exo.core.context import TreeContext

    store = SessionStore()
    s = store.create_session()
    for i in range(14):
        store.append_message(s.id, Message.user(f"short message number {i} " + "x" * 200))
    summ = Summ()
    ctx = TreeContext(store, summ, leaf_size=2, tail_messages=2, min_compact_tokens=200, view_budget=150)

    async def go():
        for _ in range(6):
            ctx.note(s.id)
            await ctx.settle(s.id)
        return store.summaries(s.id)
    nodes = run(go())
    assert any(n.status == "skipped" and n.level == 0 for n in nodes)
    assert any(n.status == "done" and n.level >= 1 for n in nodes)  # merged spans got big enough to summarize


def test_threads_of_one_agent_send_one_conversation_id():
    store, ctx, prov, loop, _ = setup()
    seen = []
    orig = prov.stream

    def stream(messages, tools, **opts):
        seen.append(opts.get("session_id"))
        return orig(messages, tools, **opts)
    prov.stream = stream

    async def go():
        a = await loop.create_session(agent="razi")
        b = await loop.create_session(agent="razi")
        plain = await loop.create_session()
        await loop.send(a.id, "one")
        await loop.send(b.id, "two")
        await loop.send(plain.id, "three")
        return plain
    plain = run(go())
    assert seen[0] == seen[1] and seen[0].startswith("a_")  # providers key their prompt cache on it
    assert seen[2] == plain.id


def test_small_recent_spans_stay_out_of_the_cached_prefix():
    """Spans too small to summarize stay in the recent part (thread tail and notes), so the system message,
    the prompt-cache prefix, changes only when a real summary lands."""
    store, ctx, prov, loop, _ = setup(leaf_size=2, tail_messages=2, min_compact_tokens=10**6)

    async def go():
        a = await loop.create_session(agent="razi", title="A")
        b = await loop.create_session(agent="razi", title="B")
        for i in range(4):
            await loop.send(a.id, f"fact a{i}")
            await loop.send(b.id, f"fact b{i}")
        return a, b
    a, b = run(go())
    assert any(n.status == "skipped" for n in store.summaries("agent:razi"))
    systems = {c[0].text() for c in prov.calls}
    assert systems == {"SYS"}  # never moved, though leaves were cut
    last = "\n".join(m.text() for m in prov.calls[-1])
    assert "fact a0" in last and "fact a3" in last  # B still sees all of A's recent work


# ---- /context and /compact (the report is read-only; compact folds what is eligible now)


def _talk(loop, n=6, agent="razi"):
    async def go():
        a = await loop.create_session(agent=agent, title="A")
        for i in range(n):
            await loop.send(a.id, f"fact number {i} " + "word " * 40)
        return a
    return run(go())


def test_report_lists_each_part_and_changes_nothing():
    store, ctx, prov, loop, _ = setup(leaf_size=2, tail_messages=4)
    a = _talk(loop)
    other = run(loop.create_session(agent="razi", title="B"))
    run(loop.send(other.id, "news from B"))
    before = (store.last_seq(a.id), store.last_pos("razi"), store.get_meta(a.id), len(store.summaries("agent:razi")))
    r = ctx.report(a.id, "SYS")
    assert before == (store.last_seq(a.id), store.last_pos("razi"), store.get_meta(a.id), len(store.summaries("agent:razi")))
    t = r["tokens"]
    assert t["system"] == 1 and t["tree"] > 0 and t["tail"] > 0
    assert t["total"] == t["system"] + t["tree"] + t["notes"] + t["tail"]
    assert r["tree"]["summaries"] >= 1 and r["tree"]["levels"] and r["nodes"]["done"] >= 1
    assert r["notes"]["unseen"] and t["notes"] > 0  # B's news is counted before it is stored
    assert r["eligible"]["leaves"] >= r["eligible"]["leaves_after_tail_wait"]
    assert ctx.report(a.id, "SYS") == r  # same answer twice


def test_report_refuses_a_plain_session():
    store, ctx, prov, loop, _ = setup()
    plain = run(loop.create_session())
    try:
        ctx.report(plain.id)
    except ValueError as exc:
        assert "agent" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_compact_folds_leaves_inside_the_tail_and_shrinks_the_thread_tail():
    store, ctx, prov, loop, summ = setup(leaf_size=2, tail_messages=4)
    a = _talk(loop)
    before = ctx.report(a.id, "SYS")
    assert before["eligible"]["leaves"] > before["eligible"]["leaves_after_tail_wait"]
    res = run(ctx.compact(a.id, "SYS"))
    after = res["after"]
    assert res["complete"] and res["folded"]
    assert after["frontier"] > before["frontier"] and after["frontier"] == store.last_pos("razi")
    assert after["eligible"]["leaves"] == 0
    assert after["tail"]["messages"] < before["tail"]["messages"]
    assert after["tokens"]["total"] < before["tokens"]["total"]
    assert all(n["after_tokens"] < n["before_tokens"] for n in res["folded"])
    assert ctx.report(a.id, "SYS")["eligible"]["leaves"] == 0
    run(loop.send(a.id, "and after"))  # the next turn still works and the tail regrows
    assert "and after" in prov.calls[-1][-2].text() + prov.calls[-1][-1].text()


def test_compact_keeps_the_size_rules():
    # spans under min_compact_tokens stay verbatim
    store, ctx, prov, loop, summ = setup(leaf_size=2, tail_messages=4, min_compact_tokens=10**6)
    a = _talk(loop)
    res = run(ctx.compact(a.id, "SYS"))
    assert res["folded"] == [] and summ.prompts == []
    assert res["skipped"] >= 1
    # a summary that is not shorter than its source is dropped
    store, ctx, prov, loop, summ = setup(leaf_size=2, tail_messages=4)

    async def long(content, previous=None, **kw):
        return "x" * 10_000
    ctx.compressor.summarize = long
    a = _talk(loop)
    res = run(ctx.compact(a.id, "SYS"))
    assert res["folded"] == [] and res["skipped"] >= 1
    assert not any(n.status == "done" for n in store.summaries("agent:razi"))


def test_compact_with_nothing_to_do_is_quiet():
    store, ctx, prov, loop, summ = setup(leaf_size=8, tail_messages=12)
    a = _talk(loop, n=1)
    res = run(ctx.compact(a.id, "SYS"))
    assert res["folded"] == [] and res["skipped"] == 0 and res["failed"] == []
