"""A session-meta update changes only its own keys, in one transaction (exo-desktop #52).

Each writer of a session's meta read the whole object, changed a key and wrote the whole object back, in two
separate store calls. A key another writer set in between was lost: a context note's ``seen_pos`` write could undo
``lock`` on a finished child, so it took new turns again, and ``compact`` could drop it the same way.
``SessionStore.update_meta`` now reads and writes under one lock and one write transaction.
"""
from __future__ import annotations

import asyncio
import sys
import threading

from exo.core.context import AgentContext
from exo.core.loop import Loop
from exo.core.sessions import SessionStore
from exo.core.types import Message, ProviderEvent, Usage


class Echo:
    name = "fake"

    async def stream(self, messages, tools, **opts):
        yield ProviderEvent(kind="usage", usage=Usage(input_tokens=10, output_tokens=2))
        yield ProviderEvent(kind="done", stop_reason="end", message=Message.assistant("ok"))


class Summ:
    async def summarize(self, content, previous=None, session=None, **kw):
        return "S"


def _setup(**kw):
    store = SessionStore()
    ctx = AgentContext(store, Summ(), **{"leaf_size": 2, "tail_messages": 4, "min_compact_tokens": 0, **kw})
    return store, ctx, Loop(store, Echo(), context=ctx, system_prompt="SYS", retry_delay=0)


def test_a_context_note_keeps_a_lock_set_while_it_reads(monkeypatch):
    store, ctx, _ = _setup()
    a = store.create_session(agent="razi", kind="thread", title="a")
    b = store.create_session(agent="razi", kind="thread", title="b")
    store.append_message(b.id, Message.user("news from b"))  # an entry of another thread that a has not seen
    real = store.last_pos

    def last_pos(agent):  # runs after sync_thread read a's meta and before it writes seen_pos
        store.lock(a.id, "done")
        return real(agent)

    monkeypatch.setattr(store, "last_pos", last_pos)
    assert ctx.sync_thread(a.id, 0) is not None
    assert store.status(a.id) == "locked:done"
    assert store.get_meta(a.id)["seen_pos"] == real("razi")


def test_compact_keeps_a_lock_set_while_it_writes_its_seq(monkeypatch):
    store, ctx, loop = _setup()

    async def talk():
        a = await loop.create_session(agent="razi", title="A")
        for i in range(6):
            await loop.send(a.id, f"fact number {i} " + "word " * 40)
        return a

    a = asyncio.run(talk())
    real = store.last_logged_seq

    def last_logged_seq(session, frontier):
        if sys._getframe(1).f_code.co_name == "compact":  # the seq compact writes, read after it read a's meta
            store.lock(a.id, "done")
        return real(session, frontier)

    monkeypatch.setattr(store, "last_logged_seq", last_logged_seq)
    res = asyncio.run(ctx.compact(a.id, "SYS"))
    assert res["folded"]
    assert store.status(a.id) == "locked:done"
    assert store.get_meta(a.id)["compact_seq"] > 0


def test_update_meta_keeps_every_key_written_from_many_threads():
    store = SessionStore()
    sid = store.create_session(title="t").id
    store.set_meta(sid, {"keep": 1})

    def write(n):
        for i in range(50):
            store.update_meta(sid, {f"k{n}": i})

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.get_meta(sid) == {"keep": 1, "k0": 49, "k1": 49, "k2": 49, "k3": 49}
    store.lock(sid, "done")
    assert store.get_meta(sid) == {"keep": 1, "k0": 49, "k1": 49, "k2": 49, "k3": 49, "locked": "done"}
