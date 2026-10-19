"""Two windows of one seat share one core (D-028): the second joins the first's socket; when the first closes,
the second takes over and keeps the same memory."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from exo.protocol import schema as s
from exo_tui.conn import LocalClient, SharedClient, connect

CONFIG = {"models": {"fake": {"rules": [{"match": "hello", "steps": ["hi there"]}]},
                     "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
          "core_plugins": [], "learn": {"enabled": False}, "schedule": False, "peers": {"enabled": False}}


@pytest.fixture
def home(monkeypatch):
    d = Path(tempfile.mkdtemp(dir="/tmp", prefix="exo-"))  # unix socket paths must stay short
    (d / "config.json").write_text(json.dumps(CONFIG))
    monkeypatch.setenv("EXO_HOME", str(d))
    monkeypatch.delenv("EXO_SEAT", raising=False)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_second_window_joins_then_takes_over(home):
    async def go():
        one = connect()
        assert isinstance(one, LocalClient)
        a = (await one.call(s.SessionCreate(title="A")))["session"]  # starts serving the agent socket
        two = connect()
        assert isinstance(two, SharedClient)
        b = (await two.call(s.SessionCreate(title="B")))["session"]
        assert b["agent"] == "default"
        listed = {r["id"] for r in (await two.call(s.SessionList()))["sessions"]}
        assert {a["id"], b["id"]} <= listed  # one core: both windows see both threads
        await one.aclose()  # the window holding the core closes
        again = (await two.call(s.SessionList()))["sessions"]  # the second window takes over
        assert {a["id"], b["id"]} <= {r["id"] for r in again}
        assert isinstance(two.inner, LocalClient)
        await two.aclose()
    asyncio.run(go())


def test_joined_window_answers_an_approval_as_principal_and_sees_it_close(home):
    """D-045: the approval card of a window that joined the core works end to end over the shared socket with the
    principal credential, and the resolved event reaches that window's own stream."""
    async def go():
        one = connect()
        sid = (await one.call(s.SessionCreate(title="A")))["session"]["id"]
        two = connect()
        assert isinstance(two, SharedClient)
        seen: list[str] = []

        async def watch():
            async for ev in two.events(since=0):
                seen.append(ev.type)
                if ev.type == "approval.resolved":
                    return

        w = asyncio.ensure_future(watch())
        ask = asyncio.ensure_future(one.engine.approvals.ask(sid, "run it?", None))
        for _ in range(100):
            rows = (await two.call(s.ApprovalsList()))["approvals"]
            if rows:
                break
            await asyncio.sleep(0.02)
        assert rows, "the joined window sees the open approval"
        await two.call(s.ApprovalRespond(request_id=rows[0]["request_id"], decision="allow", client="tui"))
        assert await asyncio.wait_for(ask, 5) == "allow"
        await asyncio.wait_for(w, 5)
        assert "approval.requested" in seen and "approval.resolved" in seen
        assert (await two.call(s.ApprovalsList()))["approvals"] == []
        await one.aclose()
        await two.aclose()
    asyncio.run(go())


def test_joined_window_resumes_its_stream_after_the_core_ends_it_for_falling_behind(home):
    """A joined window more than MAX_LAG events behind has its stream ended by the core; it reconnects and resumes
    with since, so it sees every event once and in order."""
    from exo.server.engine import MAX_LAG

    async def go():
        one = connect()
        await one.call(s.SessionCreate(title="A"))
        two = connect()
        assert isinstance(two, SharedClient)
        first, hub = two.inner, one.dispatcher.hub
        start = hub.seq
        total = start + MAX_LAG + 20
        seen: list[int] = []

        async def watch():
            async for ev in two.events(since=start, reconnect=True, backoff=0.01):
                seen.append(ev.seq)
                if ev.seq >= total:
                    return

        w = asyncio.ensure_future(watch())
        hub.publish(s.Notice(text="first"))
        while not seen:
            await asyncio.sleep(0.01)
        for _ in range(MAX_LAG + 1):  # one burst the window's stream cannot write in time
            hub.publish(s.Notice(text="burst"))
        while hub.seq < total:
            hub.publish(s.Notice(text="after"))
            await asyncio.sleep(0)
        await asyncio.wait_for(w, 20)
        assert seen == list(range(start + 1, seen[-1] + 1)) and seen[-1] >= total  # no gap, no repeat
        assert two.inner is not first  # the stream ended and the window reconnected
        await two.aclose()
        await one.aclose()
    asyncio.run(go())


def test_first_window_feed_rides_through_a_lag_close_and_ends_with_the_core():
    """The first window runs the core in process (LocalClient). A burst it cannot read in one loop turn ends its
    subscription (D-079); the feed resumes from the last event seen, so it misses nothing and stays open until the
    core shuts down."""
    from exo.server.echo import EchoEngine
    from exo.server.engine import MAX_LAG

    async def go():
        client = LocalClient(EchoEngine())
        hub = client.dispatcher.hub
        feed = client.events(since=0, reconnect=True)
        hub.publish(s.Notice(text="first"))
        seen = [(await feed.__anext__()).seq]
        for _ in range(MAX_LAG + 50):  # one loop turn's burst: the window cannot read in between
            hub.publish(s.Notice(text="burst"))
        assert max((q.qsize() for q in hub._subs), default=0) <= MAX_LAG  # the core's memory stays bounded
        while len(seen) < MAX_LAG + 51:
            seen.append((await asyncio.wait_for(feed.__anext__(), 5)).seq)
        hub.publish(s.Notice(text="later"))  # the next turn's deltas, a turn.completed, an approval
        seen.append((await asyncio.wait_for(feed.__anext__(), 5)).seq)
        assert seen == list(range(1, MAX_LAG + 53))  # no gap, no repeat
        await client.aclose()  # the core shuts down: only now does the feed end
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(feed.__anext__(), 5)
    asyncio.run(go())


def test_window_that_took_over_keeps_its_core_through_a_lag_close(home):
    """A joined window that took over runs the core in process. Falling behind ends its subscription, not its core:
    no failover, so running turns, open approvals and other joined windows are untouched."""
    from exo.server.engine import MAX_LAG

    async def go():
        one = connect()
        await one.call(s.SessionCreate(title="A"))
        two = connect()
        assert isinstance(two, SharedClient)
        await one.aclose()
        await two.call(s.SessionList())  # takes over: this window now runs the core
        core = two.inner
        assert isinstance(core, LocalClient)
        hub = core.dispatcher.hub
        start = hub.seq
        total = start + MAX_LAG + 50
        seen: list[int] = []

        async def watch():
            async for ev in two.events(since=start, reconnect=True, backoff=0.01):
                seen.append(ev.seq)
                if ev.seq >= total:
                    return

        w = asyncio.ensure_future(watch())
        hub.publish(s.Notice(text="first"))
        while not seen:
            await asyncio.sleep(0.01)
        for _ in range(MAX_LAG + 49):  # one loop turn's burst the window cannot read in time
            hub.publish(s.Notice(text="burst"))
        assert max((q.qsize() for q in hub._subs), default=0) <= MAX_LAG
        await asyncio.wait_for(w, 20)
        assert seen == list(range(start + 1, total + 1))  # no gap, no repeat
        assert two.inner is core and not hub.closed  # the same core still runs
        await two.aclose()
    asyncio.run(go())
