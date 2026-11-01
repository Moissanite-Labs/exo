"""#254 W2-4: event reads with no bound go page by page, and a replay from seq 0 still gives every event once."""

from __future__ import annotations

import asyncio
import json

import pytest

import exo.core.sessions as sessions
from exo.core import trace
from exo.core.engine import CoreEngine, SqliteLog
from exo.core.sessions import SessionStore
from exo.protocol import schema as s
from exo.server.engine import Hub

PAGE = 1000


@pytest.fixture
def decoded(monkeypatch):
    """Counts the events and messages the store decodes from JSON."""
    seen: list[int] = []
    decode = sessions.from_dict

    def record(*args, **kwargs):
        seen.append(1)
        return decode(*args, **kwargs)

    monkeypatch.setattr(sessions, "from_dict", record)
    return seen


@pytest.fixture
def limits(monkeypatch):
    """The ``limit`` of every events_since call."""
    seen: list[int] = []
    since = SessionStore.events_since

    def record(self, *args, **kwargs):
        seen.append(kwargs.get("limit", args[2] if len(args) > 2 else 10000))
        return since(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "events_since", record)
    return seen


def notice(i: int) -> s.Notice:
    return s.Notice(level="info", text=f"n{i}")


def fill(store: SessionStore, n: int, sid: str, other: str) -> None:
    """Events of ``sid``, of another session and of no session, interleaved."""
    for i in range(n):
        store.append_event(notice(i), (sid, other, None)[i % 3])


def test_iter_events_matches_one_unbounded_read(tmp_path, limits):
    store = SessionStore(tmp_path / "s.sqlite")
    try:
        fill(store, 2 * PAGE + 517, "s1", "s2")
        for args in [(0, None, False), (0, "s1", False), (0, "s1", True), (700, "s2", True), (5000, None, False)]:
            whole = [e.seq for e in store.events_since(args[0], args[1], 1 << 30, args[2])]
            assert [e.seq for e in store.iter_events(*args)] == whole
        skip = ("notice",)
        assert list(store.iter_events(0, "s1", True, skip)) == []
        limits.clear()
        events = store.iter_events(0, None)
        store.append_event(notice(-1), "s1")  # after the call: not in this read, as with one read at the call
        assert [e.seq for e in events] == list(range(1, 2 * PAGE + 518)) and max(limits) <= PAGE
    finally:
        store.close()


def test_a_page_with_global_events_does_not_sort_the_rest_of_the_log():
    """A page that also keeps events of no session costs the same at 2,000 events and at 20,000. The OR form
    collected and sorted every later match on each call, so paging a long log took time that grew with its square."""
    store = SessionStore()

    def add(start: int, n: int) -> None:
        for i in range(start, start + n):
            store.append_event(notice(i), ("s1", "s2", None)[i % 3])

    def steps() -> int:
        count = [0]

        def tick() -> int:
            count[0] += 1
            return 0

        store._db.set_progress_handler(tick, 10)
        try:
            assert len(store.events_since(0, "s1", 10, with_global=True)) == 10
        finally:
            store._db.set_progress_handler(None, 10)
        return count[0]

    try:
        add(0, 2000)
        small = steps()
        add(2000, 18_000)
        assert steps() <= 2 * small + 50  # the same either way; the OR form grew tenfold
    finally:
        store.close()


def test_replay_from_zero_across_pages_while_live_events_publish(tmp_path):
    """OPTIMIZATION.md W2-4 acceptance: every seq arrives once, in order, with live events published mid replay."""
    store = SessionStore(tmp_path / "s.sqlite")
    hub = Hub(SqliteLog(store))
    for i in range(2 * PAGE + 517):
        hub.publish(notice(i), ("s1", "s2", None)[i % 3])

    async def replay(session):
        sub = hub.subscribe(0, session)
        got, live = [], 0
        try:
            while True:
                ev = await sub.next(timeout=0.2)  # the backlog and queued live events come at once
                if ev is None:
                    return got, live
                got.append(ev.seq)
                if len(got) % 250 == 0 and live < 12:
                    for target in ("s1", "s2", None):
                        hub.publish(notice(10_000 + live), target)
                        live += 1
        finally:
            sub.close()

    try:
        for session in (None, "s1"):
            before = hub.head()
            got, live = asyncio.run(replay(session))
            assert got == sorted(set(got)), "a repeat or a step back"
            want = [e.seq for e in store.events_since(0, session, 1 << 30, with_global=True)]
            assert got == want and got[-1] > before
    finally:
        store.close()


def test_subscribe_from_zero_holds_one_page(tmp_path, decoded):
    store = SessionStore(tmp_path / "s.sqlite")
    hub = Hub(SqliteLog(store))
    for i in range(3 * PAGE):
        hub.publish(notice(i), "s1")

    async def first():
        sub = hub.subscribe(0, "s1")
        try:
            return (await sub.next(timeout=1)).seq
        finally:
            sub.close()

    try:
        decoded.clear()
        assert asyncio.run(first()) == 1
        assert len(decoded) <= PAGE  # main decoded all 3,000 before the first one went out
    finally:
        store.close()


def test_since_and_a_subscription_both_replay_through_the_hub(tmp_path):
    """A rule on what the hub replays, put in ``Hub.replay``, reaches ``since`` and a subscription's backlog alike.
    A backlog read from the log directly would skip it."""
    class Owned(Hub):
        def replay(self, seq, session=None):
            return (e for e in super().replay(seq, session) if e.session != "s2")

    store = SessionStore(tmp_path / "s.sqlite")
    hub = Owned(SqliteLog(store))
    for i in range(PAGE + 30):
        hub.publish(notice(i), ("s1", "s2", None)[i % 3])

    async def backlog():
        sub, got = hub.subscribe(0), []
        try:
            while (ev := await sub.next(timeout=0.05)) is not None:
                got.append(ev.seq)
            return got
        finally:
            sub.close()

    try:
        want = [e.seq for e in store.events_since(0, None, 1 << 30) if e.session != "s2"]
        assert [e.seq for e in hub.since(0)] == want
        assert asyncio.run(backlog()) == want
    finally:
        store.close()


def test_a_stdio_subscribe_reads_one_page_before_its_first_event(tmp_path, decoded):
    """The JSON-lines transport first built the one-shot events.subscribe reply, with every event decoded, and then
    dropped it to stream from a Subscription."""
    from exo.server.app import Dispatcher, serve_lines
    from exo.server.echo import EchoEngine

    store = SessionStore(tmp_path / "s.sqlite")
    hub = Hub(SqliteLog(store))
    for i in range(3 * PAGE):
        hub.publish(notice(i), "s1")
    lines = [s.Command.of(s.EventsSubscribe(since=0, session="s1"), id="c0").to_json()]
    out: list[tuple[dict, int]] = []  # each line written, with the count decoded when it went out

    async def read():
        if lines:
            return lines.pop(0)
        while sum("type" in o for o, _n in out) < 3 * PAGE:
            await asyncio.sleep(0.01)
        return None

    try:
        decoded.clear()
        asyncio.run(asyncio.wait_for(serve_lines(Dispatcher(EchoEngine(), hub=hub), read,
                                                 lambda text: out.append((json.loads(text), len(decoded)))), 30))
    finally:
        store.close()
    assert [o["result"]["subscribed"] for o, _n in out if "type" not in o] == [True]
    events = [(o["seq"], n) for o, n in out if "type" in o]
    assert [seq for seq, _n in events] == list(range(1, 3 * PAGE + 1))
    assert events[0][1] <= PAGE  # main decoded all 3,000 for the reply it dropped, then the first page again


def test_a_stdio_subscribe_the_log_cannot_read_is_a_typed_reply_and_leaves_no_queue():
    from exo.server.app import Dispatcher, serve_lines
    from exo.server.echo import EchoEngine

    store = SessionStore()
    hub = Hub(SqliteLog(store))
    lines = [s.Command.of(s.EventsSubscribe(since=2**63), id="c0").to_json(),
             s.Command.of(s.SessionList(), id="c1").to_json()]
    out: list[dict] = []

    async def read():
        return lines.pop(0) if lines else None

    try:
        asyncio.run(asyncio.wait_for(serve_lines(Dispatcher(EchoEngine(), hub=hub), read,
                                                 lambda text: out.append(json.loads(text))), 10))
    finally:
        store.close()
    assert [(o["id"], o["ok"]) for o in out] == [("c0", False), ("c1", True)]
    assert out[0]["error"]["code"] == "internal" and "OverflowError" in out[0]["error"]["message"]
    assert not hub._subs  # no queue is left behind to fill with every later event


def test_trace_reads_its_events_in_pages(tmp_path, limits):
    store = SessionStore(tmp_path / "s.sqlite")
    try:
        sid = store.create_session().id
        store.append_event(s.TurnStarted(turn_id="t1", model="m"), sid)
        for i in range(PAGE + 300):
            store.append_event(s.MessageDelta(message_id="m1", text=f"x{i}"), sid)
        store.append_event(s.TurnCompleted(turn_id="t1", stop_reason="end"), sid)
        turns = trace.build(store, sid)
        assert [t.turn_id for t in turns] == ["t1"] and turns[0].complete
        assert limits and max(limits) <= PAGE
    finally:
        store.close()


def test_handoff_reads_task_events_in_pages(tmp_path, limits):
    models = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"rules": []}}
    eng = CoreEngine(tmp_path / "home", models, env={}, cwd=str(tmp_path))

    async def go():
        info = await eng.loop.create_session(title="w")
        for i in range(PAGE + 200):
            status = ("queued", "running", "done")[i % 3]
            eng.store.append_event(s.TaskUpdated(task_id=f"k{i % 7}", title=f"task {i % 7}", status=status), info.id)
        limits.clear()
        return await eng.handoff_facts(info.id)

    try:
        facts = asyncio.run(go())
    finally:
        eng.store.close()
    last = {}
    for i in range(PAGE + 200):
        last[f"k{i % 7}"] = ("queued", "running", "done")[i % 3]
    assert sorted(t["id"] for t in facts["tasks"]) == sorted(k for k, v in last.items() if v != "done")
    assert limits and max(limits) <= PAGE
