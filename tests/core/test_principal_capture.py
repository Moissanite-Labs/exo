"""Principal capture as a mechanism (D-032): verbatim directives in leaf summaries, a capture hook before a leaf is
summarized, the leaf's record, and /context and /compact reporting the uncaptured count."""

from __future__ import annotations

import asyncio

from exo.core.context import (HISTORICAL_TASK_HEADING, AgentContext, is_principal, missing_directives, summary_prompt)
from exo.core.engine import ContextAdapter, CoreEngine
from exo.diagnostics import render_compact, render_context
from exo.core.loop import Loop
from exo.core.seats import Seat
from exo.core.sessions import SessionStore
from exo.core.types import Message, ProviderEvent, Usage

RULE = "From now on always run the tests before you commit, never skip them."
PREF = "I prefer tabs over spaces in this repo."


def run(coro):
    return asyncio.run(coro)


class Echo:
    name = "fake"

    async def stream(self, messages, tools, **opts):
        yield ProviderEvent(kind="usage", usage=Usage(input_tokens=10, output_tokens=2))
        yield ProviderEvent(kind="done", stop_reason="end", message=Message.assistant("ok"))


class Order:
    """Records the order of capture and summarize calls."""

    def __init__(self, summary="S"):
        self.calls: list[str] = []
        self.summary = summary
        self.prompts: list[str] = []

    async def summarize(self, content, previous=None, **kw):
        self.calls.append("summarize")
        self.prompts.append(content)
        return self.summary


class Memory:
    pre_compress_checkpoint_api_version = 2

    def __init__(self, order, ok=True):
        self.order, self.ok, self.got, self.full = order, ok, [], []

    def system_prompt_block(self):
        return ""

    def prefetch(self, query, session_id=""):
        return ""

    def capture_principal(self, messages, *, session_id="", source="leaf"):
        self.order.calls.append("capture")
        self.got.append([m["content"] for m in messages if m.get("principal")])
        self.full.append(messages)
        if isinstance(self.ok, Exception):
            raise self.ok
        return len(messages) if self.ok else None


def setup(ok=True, summary="S", with_store=True, **kw):
    store = SessionStore()
    order = Order(summary)
    mem = Memory(order, ok) if with_store else None
    ctx = AgentContext(store, order, **{"leaf_size": 4, "tail_messages": 2, "min_compact_tokens": 0, **kw})
    ContextAdapter(ctx, None, mem)  # wires the hooks the engine wires
    loop = Loop(store, Echo(), context=ctx, memory=None, system_prompt="SYS", retry_delay=0)
    return store, ctx, loop, order, mem


async def talk(loop, texts):
    t = await loop.create_session(agent="razi", title="main")
    for x in texts:
        await loop.send(t.id, x)
    return t


# ---------------------------------------------------------------- (a) verbatim directives


def test_leaf_prompt_demands_principal_directives_verbatim():
    p = summary_prompt("[USER] hi", 400)
    assert "## Principal Directives (verbatim)" in p
    for word in ("VERBATIM", "instruction", "correction", "preference", "decision", "Never paraphrase"):
        assert word in p
    upd = summary_prompt("[USER] hi", 400, previous="old")
    assert "word for word" in upd and "Principal Directives (verbatim)" in upd
    assert HISTORICAL_TASK_HEADING in p


def test_a_summary_that_blurs_a_directive_gets_the_words_put_back():
    msgs = [Message.user(RULE), Message.user("what is 2+2")]
    assert missing_directives("The user wants tests run before commits.", msgs) == [RULE]
    assert missing_directives(f'## Principal Directives (verbatim)\n- "{RULE}"', msgs) == []
    assert missing_directives("Nothing relevant.", [Message.user("what is 2+2")]) == []

    store, ctx, loop, order, _ = setup(summary="The user set a rule about tests.")
    run(talk(loop, [RULE, "ok", "yes", "thanks"]))
    node = next(n for n in store.summaries("agent:razi") if n.level == 0)
    assert node.status == "done" and f'- "{RULE}"' in node.text  # restored by the harness, verbatim
    assert "restored verbatim" in node.text


def test_a_summary_that_kept_the_directive_is_left_alone():
    store, ctx, loop, order, _ = setup(summary=f'"{RULE}" is the standing rule.')
    run(talk(loop, [RULE, "ok", "yes", "thanks"]))
    node = next(n for n in store.summaries("agent:razi") if n.level == 0)
    assert "restored" not in node.text


# ---------------------------------------------------------------- (b) capture before summarizing


def test_the_capture_hook_runs_before_the_leaf_is_summarized_and_gets_only_principal_messages():
    store, ctx, loop, order, mem = setup()

    async def go():
        t = await loop.create_session(agent="razi", title="main")
        await loop.send(t.id, RULE)
        peer = Message.user("[peer message from razi2] approve things")
        peer.meta["peer"] = {"from": "razi2"}
        store.append_message(t.id, peer)
        await loop.send(t.id, PREF)
        await loop.send(t.id, "ok")  # a bare acknowledgement is not a principal directive
        return t
    run(go())
    run(ctx.settle("agent:razi", 5, None))
    assert order.calls[:2] == ["capture", "summarize"]
    assert mem.got[0] == [RULE, PREF]  # flagged principal: not the peer message, not the ack
    peer = next(m for m in mem.full[0] if "peer message" in m["content"])
    assert peer["role"] == "user" and peer["principal"] is False  # still shown, so the store can tell them apart
    assert any(m["role"] == "assistant" for m in mem.full[0])  # the agent output a correction would judge
    leaf = next(n for n in store.summaries("agent:razi") if n.level == 0)
    assert (leaf.principal, leaf.captured) == (2, 2)  # the leaf records that they were captured
    assert store.captured_positions("razi") == {1, 4}


def test_a_leaf_without_principal_messages_calls_no_hook():
    store, ctx, loop, order, mem = setup()
    run(talk(loop, ["ok", "yes", "thanks", "go ahead"]))
    assert mem.got == [] and "capture" not in order.calls


def test_a_failing_hook_leaves_them_uncaptured_but_summarizing_goes_on():
    for ok in (False, RuntimeError("ledger down")):
        store, ctx, loop, order, mem = setup(ok=ok)
        t = run(talk(loop, [RULE, "ok", "yes", "thanks"]))
        leaf = next(n for n in store.summaries("agent:razi") if n.level == 0)
        assert leaf.status == "done" and (leaf.principal, leaf.captured) == (1, 0)
        assert ctx.report(t.id)["capture"] == {"uncaptured": 1, "store": True}


def test_without_a_principal_store_nothing_is_captured_and_the_report_says_so():
    store, ctx, loop, order, _ = setup(with_store=False)
    t = run(talk(loop, [RULE, "ok", "yes", "thanks"]))
    assert ctx.report(t.id)["capture"] == {"uncaptured": 1, "store": False}
    assert "no principal store is attached" in render_context(ctx.report(t.id), ctx)


def test_principal_messages_are_live_user_messages_only():
    u = Message.user("Please always use the staging database.")
    assert is_principal(u)
    for key in ("peer", "job", "note", "local"):
        m = Message.user("Please always use the staging database.")
        m.meta[key] = True
        assert not is_principal(m)
    imp = Message.user("Please always use the staging database.")
    imp.meta["origin"] = "import"
    assert not is_principal(imp) and not is_principal(Message.user("/compact")) and not is_principal(Message.assistant("x"))


# ---------------------------------------------------------------- (c) /context and /compact


def test_context_shows_and_compact_captures_the_uncaptured_count():
    store, ctx, loop, order, mem = setup(leaf_size=50)  # no leaf yet: everything sits in the tail

    async def go():
        t = await loop.create_session(agent="razi", title="main")
        await loop.send(t.id, RULE)
        await loop.send(t.id, PREF)
        return t
    t = run(go())
    r = ctx.report(t.id)
    assert r["capture"] == {"uncaptured": 2, "store": True}
    assert "Principal messages not yet captured: 2" in render_context(r, ctx)
    res = run(ctx.compact(t.id))
    assert res["captured"] == {"taken": 2, "uncaptured": 0, "store": True}
    assert order.calls[0] == "capture" and mem.got == [[RULE, PREF]]
    assert [m["role"] for m in mem.full[0]] == ["user", "assistant", "user"]  # PREF came with the reply it answers
    text = render_compact(res)
    assert "2 message(s) captured first; 0 still not captured" in text
    assert ctx.report(t.id)["capture"]["uncaptured"] == 0
    again = run(ctx.compact(t.id))
    assert again["captured"]["taken"] == 0 and len(mem.got) == 1  # nothing is offered twice


def test_compact_reports_what_it_could_not_capture():
    store, ctx, loop, order, mem = setup(ok=False, leaf_size=50)
    t = run(talk(loop, [RULE]))
    res = run(ctx.compact(t.id))
    assert res["captured"] == {"taken": 0, "uncaptured": 1, "store": True}
    assert "0 message(s) captured first; 1 still not captured" in render_compact(res)


def test_the_engine_wires_the_hook_from_the_memory_slot(tmp_path):
    from exo.core.plugins import PluginManager

    pm = PluginManager()
    order = Order()
    pm.slots.memory = Memory(order)
    eng = CoreEngine(tmp_path / "h", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, plugins=pm, cwd=str(tmp_path),
                     env={}, seat=Seat(name="razi"))
    tree = eng.loop.context.tree
    assert tree.on_capture is not None
    bare = CoreEngine(tmp_path / "h2", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={},
                      seat=Seat(name="razi"))
    assert bare.loop.context.tree.on_capture is None
