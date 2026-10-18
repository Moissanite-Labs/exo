"""Two seats in one EXO_HOME: two cores share sessions.sqlite (docs/SEATS.md, "Several seats in one home")."""

from __future__ import annotations

import asyncio
import threading

import pytest

from exo.core import agentlog
from exo.core.engine import CoreEngine, SqliteLog
from exo.core.models.fake import FakeProvider
from exo.core.seats import Seat
from exo.core.sessions import SessionStore
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo.server.engine import Hub

MODELS = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def seat_engine(tmp_path, name: str) -> CoreEngine:
    eng = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={}, seat=Seat(name=name))
    eng.loop.provider = FakeProvider([f"hello from {name}"], repeat=True)
    return eng


def notice(i: int) -> s.Notice:
    return s.Notice(text=f"n{i}")


def test_two_hubs_on_one_store_never_reuse_a_seq(tmp_path):
    db = tmp_path / "sessions.sqlite"
    a, b = Hub(SqliteLog(SessionStore(db))), Hub(SqliteLog(SessionStore(db)))
    got: dict[str, list[int]] = {"a": [a.publish(notice(0)).seq, a.publish(notice(1)).seq], "b": []}
    assert got["a"] == [1, 2]  # a core writing alone keeps a dense stream
    for i in range(3):  # alternate: each Hub's own counter is behind once the other one writes
        got["b"].append(b.publish(notice(i)).seq)
        got["a"].append(a.publish(notice(i)).seq)
    every = got["a"] + got["b"]
    assert len(set(every)) == 8
    for name, hub in (("a", a), ("b", b)):
        assert got[name] == sorted(got[name])  # one core's stream still rises, with gaps where the other wrote
        assert hub.seq == got[name][-1]
    rows = SessionStore(db).events_since(0)
    assert [e.seq for e in rows] == sorted(every)


def test_hubs_publishing_at_once_from_two_connections_never_collide(tmp_path):
    db, n = tmp_path / "sessions.sqlite", 150
    hubs = [Hub(SqliteLog(SessionStore(db))) for _ in range(2)]
    seqs: list[list[int]] = [[], []]
    errors: list[BaseException] = []
    go = threading.Barrier(2)

    def run(k: int) -> None:
        go.wait()
        try:
            for i in range(n):
                seqs[k].append(hubs[k].publish(notice(i)).seq)
        except BaseException as exc:  # pragma: no cover - the failure being tested
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(k,)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    assert all(len(x) == n and x == sorted(x) for x in seqs)
    assert len(set(seqs[0]) | set(seqs[1])) == 2 * n
    assert SessionStore(db).last_event_seq() == 2 * n


def test_a_client_joining_one_seat_starts_after_the_other_seats_events(tmp_path):
    """hello, events.subscribe and /health report the log's highest seq, not this core's last publish. A window that
    joins alpha's core starts after beta's job cards and notices, so it never replays them as new notes."""
    db = tmp_path / "sessions.sqlite"
    alpha, beta = Hub(SqliteLog(SessionStore(db))), Hub(SqliteLog(SessionStore(db)))
    alpha.publish(notice(0), "s_alpha")
    for i in range(3):  # session-less, like a scheduled job's card and its result
        beta.publish(s.TaskUpdated(task_id=f"k{i}", status="done"))
        beta.publish(notice(i))
    assert alpha.seq == 1  # alpha's own last publish
    d = Dispatcher(None, alpha)  # type: ignore[arg-type]  # hello and events.subscribe never reach the engine

    async def main():
        hello = await d.run(s.Command.of(s.Hello()))
        once = await d.run(s.Command.of(s.EventsSubscribe(since=1, session="s_alpha")))
        return hello.result["seq"], once.result

    seq, once = asyncio.run(main())
    assert seq == once["seq"] == 7
    assert [e["seq"] for e in once["events"]] == [2, 3, 4, 5, 6, 7]  # an explicit replay still crosses seats
    assert alpha.since(seq, "s_alpha") == []
    assert alpha.publish(notice(9), "s_alpha").seq == 8


def test_two_seats_on_one_home_both_run_turns(tmp_path):
    alpha, beta = seat_engine(tmp_path, "alpha"), seat_engine(tmp_path, "beta")
    alpha.claim_writer()
    beta.claim_writer()  # different agents: both get their writer lock

    async def main():
        try:
            sa = await alpha.loop.create_session(agent="alpha")
            sb = await beta.loop.create_session(agent="beta")
            ra = await alpha.loop.send(sa.id, "hi")
            rb = await beta.loop.send(sb.id, "hi")
            return sa.id, sb.id, ra, rb
        finally:
            await alpha.close()
            await beta.close()

    sa, sb, ra, rb = asyncio.run(main())
    assert (ra.stop_reason, rb.stop_reason) == ("end", "end")
    assert ra.message.text() == "hello from alpha" and rb.message.text() == "hello from beta"
    store = SessionStore(tmp_path / "home" / "sessions.sqlite")
    for sid in (sa, sb):
        types = [e.type for e in store.events_since(0, session=sid)]
        assert "turn.started" in types and "turn.completed" in types
    seqs = [e.seq for e in store.events_since(0)]
    assert len(seqs) == len(set(seqs))
    assert alpha.hub.seq != beta.hub.seq


def _task(eng: CoreEngine, parent: str | None, status: str, tid: str = "k_alive", child: str | None = None) -> None:
    eng.store._q("INSERT INTO tasks (id, parent, child, depth, title, status, brief, created, updated) VALUES "
                 "(?,?,?,?,?,?,?,?,?)", (tid, parent, child, 1, "work", status, "brief", s.now_ts(), s.now_ts()))


def _status(tmp_path, tid: str = "k_alive") -> str:
    return SessionStore(tmp_path / "home" / "sessions.sqlite")._q("SELECT status FROM tasks WHERE id=?",
                                                                    (tid,))[0]["status"]


def test_another_seats_core_leaves_live_tasks_alone(tmp_path):
    alpha, beta = seat_engine(tmp_path, "alpha"), None
    try:
        alpha.claim_writer()
        parent = alpha.store.create_session(title="work", agent="alpha")
        _task(alpha, parent.id, "running")
        beta = seat_engine(tmp_path, "beta")  # another seat's core starts on this home
        beta.claim_writer()
        assert _status(tmp_path) == "running"
    finally:
        for e in (alpha, beta):
            if e is not None:
                asyncio.run(e.close())


def test_a_core_that_fails_the_writer_lock_leaves_live_tasks_alone(tmp_path):
    alpha = seat_engine(tmp_path, "alpha")
    alpha.claim_writer()
    parent = alpha.store.create_session(title="work", agent="alpha")
    _task(alpha, parent.id, "running")
    child = alpha.store.create_session(title="sub", agent="beta", kind="subagent")  # a beta task, no parent row
    _task(alpha, None, "running", "k_beta", child=child.id)
    second = seat_engine(tmp_path, "alpha")  # a second core of the live seat, as a second `exo serve` builds it
    try:
        with pytest.raises(agentlog.WriterBusy):
            second.claim_writer()
        assert _status(tmp_path) == "running"
    finally:
        asyncio.run(second.close())
        asyncio.run(alpha.close())  # the live core ends: its lock is released
    again = seat_engine(tmp_path, "alpha")
    try:
        again.claim_writer()
        assert _status(tmp_path) == "failed"  # the next writer of alpha calls the dead core's task failed
        assert _status(tmp_path, "k_beta") == "running"  # beta's own task is beta's to sweep
    finally:
        asyncio.run(again.close())


def test_stale_queued_task_is_swept_through_its_parent(tmp_path):
    alpha = seat_engine(tmp_path, "alpha")
    parent = alpha.store.create_session(title="work", agent="alpha")
    _task(alpha, parent.id, "queued", "k_q")  # not started yet: no child session, so its parent names the agent
    _task(alpha, parent.id, "queued", "k_bus", child="bus:scout")
    try:
        alpha.claim_writer()
        assert _status(tmp_path, "k_q") == "failed"
        assert _status(tmp_path, "k_bus") == "queued"  # a bus task's receipt arrives later, from the other seat
    finally:
        asyncio.run(alpha.close())
