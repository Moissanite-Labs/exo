"""A stalled event subscriber is ended, not buffered without bound, and resumes with since (no gap, no repeat)."""
import asyncio
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from exo.protocol import schema as s
from exo.protocol.client import Client
from exo.server import engine
from exo.server.app import Dispatcher, HttpServer, serve_lines
from exo.server.echo import EchoEngine
from exo.server.engine import Closed, Hub


@pytest.fixture
def sock_dir():
    # Unix socket paths are capped near 104 bytes on macOS; pytest tmp_path can exceed that.
    d = tempfile.mkdtemp(prefix="exo", dir="/tmp" if os.path.isdir("/tmp") else None)
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _run(coro, timeout=30):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def _depth(hub: Hub) -> int:
    return max((q.qsize() for q in hub._subs), default=0)


async def _drain(sub) -> list[int]:
    """Every event the subscription holds now, until it goes idle. Raises Closed if it has ended."""
    out = []
    while (ev := await sub.next(timeout=0.01)) is not None:
        out.append(ev.seq)
    return out


def test_stalled_subscriber_stays_bounded_and_its_stream_ends():
    async def main():
        hub, lag = Hub(), engine.MAX_LAG
        sub = hub.subscribe(since=0)
        for i in range(10):
            hub.publish(s.Notice(text=str(i)))
        read = [(await sub.next(timeout=0.1)).seq for _ in range(10)]
        deepest = 0
        for i in range(3 * lag):  # the reader has stopped; the publisher must not block or queue all of it
            hub.publish(s.Notice(text="x"))
            deepest = max(deepest, _depth(hub))
        assert deepest <= lag
        assert sub._queue.qsize() == 1  # what it had not read is released, only the end marker is left
        assert sub._queue not in hub._subs
        with pytest.raises(Closed):  # the stream ends right after the last event it read: no gap
            await sub.next(timeout=0.1)
        assert read == list(range(1, 11)) and sub.last == 10

    _run(main())


def test_subscriber_at_the_limit_is_kept_and_one_more_ends_it():
    async def main():
        hub, lag = Hub(), engine.MAX_LAG
        kept, stalled, iterated = (hub.subscribe(since=0) for _ in range(3))
        for _ in range(lag):
            hub.publish(s.Notice(text="x"))
        assert kept._queue.qsize() == stalled._queue.qsize() == lag
        assert await _drain(kept) == list(range(1, lag + 1))  # exactly lag behind is still a whole stream
        hub.publish(s.Notice(text="one more"))
        assert await _drain(kept) == [lag + 1]
        assert kept._queue in hub._subs and stalled._queue not in hub._subs
        with pytest.raises(Closed):
            await stalled.next(timeout=0.1)
        got = []
        async for ev in iterated:  # in-process iteration sees a normal end of stream
            got.append(ev.seq)
        assert got == [] and iterated._queue not in hub._subs

    _run(main())


def test_reader_that_keeps_up_sees_every_event_in_order_and_is_not_closed():
    async def main():
        hub, lag = Hub(), engine.MAX_LAG
        reader, stalled = hub.subscribe(since=0), hub.subscribe(since=0)
        got: list[int] = []
        ended = asyncio.Event()

        async def read():
            try:
                while True:
                    ev = await reader.next()
                    got.append(ev.seq)
            except Closed:
                ended.set()

        task = asyncio.create_task(read())
        await asyncio.sleep(0)
        total = 0
        for burst in (lag, 1, lag - 1, lag, 17):  # bursts up to the limit, the reader catching up in between
            for _ in range(burst):
                hub.publish(s.MessageDelta(message_id="m", text="t"))
            total += burst
            while len(got) < total:
                await asyncio.sleep(0)
        assert got == list(range(1, total + 1))
        assert not ended.is_set() and reader._queue in hub._subs
        with pytest.raises(Closed):  # the one that never read was ended alongside
            await stalled.next(timeout=0.1)
        task.cancel()

    _run(main())


def test_resume_with_since_after_the_close_replays_the_rest_with_no_gap():
    async def main():
        hub, lag = Hub(), engine.MAX_LAG
        sid = "s1"
        sub = hub.subscribe(since=0, session=sid)
        seen = []
        for i in range(5):
            hub.publish(s.Notice(text=str(i)), sid if i % 2 else None)
            seen.append((await sub.next(timeout=0.1)).seq)
        for i in range(2 * lag):
            hub.publish(s.MessageDelta(message_id="m", text=str(i)), sid if i % 3 else "other")
        with pytest.raises(Closed):
            await sub.next(timeout=0.1)
        resumed = hub.subscribe(since=sub.last, session=sid)
        hub.publish(s.Notice(text="live"), sid)  # a live event after the resume follows the replay
        rest = await _drain(resumed)
        want = [e.seq for e in hub.since(0, sid)]
        assert seen + rest == want  # in order, nothing missing, nothing twice

    _run(main())


def test_http_stalled_client_stream_ends_and_resumes_with_since(sock_dir):
    async def main():
        hub, lag = Hub(), engine.MAX_LAG
        srv = HttpServer(Dispatcher(EchoEngine(), hub), heartbeat=30)
        path = sock_dir / "exod.sock"
        await srv.start_unix(path)
        try:
            r, w = await asyncio.open_unix_connection(str(path))
            w.write(b"GET /events?since=0 HTTP/1.1\r\nHost: x\r\n\r\n")  # subscribe, then stop reading
            await w.drain()
            while not hub._subs:
                await asyncio.sleep(0.01)
            deepest, total = 0, 3 * lag
            for i in range(total):
                hub.publish(s.Notice(text="x" * 2000))
                deepest = max(deepest, _depth(hub))
                if i % 100 == 0:
                    await asyncio.sleep(0)  # let the stream write until the socket is full
            assert deepest <= lag and not hub._subs
            raw = await asyncio.wait_for(r.read(), 10)  # the reader wakes up: what was sent, then end of stream
            w.close()
            ids = [int(line[4:]) for line in raw.decode().splitlines() if line.startswith("id: ")]
            assert ids and ids == list(range(1, len(ids) + 1)) and len(ids) < total
            rest = []
            async with Client(socket=str(path)) as c:
                async for ev in c.events(since=ids[-1]):
                    rest.append(ev.seq)
                    if ev.seq == total:
                        break
            assert ids + rest == list(range(1, total + 1))
        finally:
            await srv.close()

    _run(main())


def test_client_with_reconnect_rides_through_the_close(sock_dir, monkeypatch):
    opened: list[int] = []
    stream = Client._stream

    def counting(self, since, session):
        opened.append(since)
        return stream(self, since, session)

    monkeypatch.setattr(Client, "_stream", counting)

    async def main():
        hub, lag = Hub(), engine.MAX_LAG
        srv = HttpServer(Dispatcher(EchoEngine(), hub), heartbeat=30)
        path = sock_dir / "exod.sock"
        await srv.start_unix(path)
        got: list[int] = []
        total = lag + 50

        async def follow(c):
            async for ev in c.events(since=0, reconnect=True, backoff=0.01):
                got.append(ev.seq)
                if ev.seq == total:
                    return

        try:
            async with Client(socket=str(path)) as c:
                task = asyncio.create_task(follow(c))
                hub.publish(s.Notice(text="first"))
                while not got:
                    await asyncio.sleep(0.01)
                for _ in range(lag + 1):  # one burst the stream cannot write in time: its subscriber is ended
                    hub.publish(s.MessageDelta(message_id="m", text="t"))
                for _ in range(total - lag - 2):
                    hub.publish(s.MessageDelta(message_id="m", text="t"))
                    await asyncio.sleep(0)
                await asyncio.wait_for(task, 10)
        finally:
            await srv.close()
        return got

    got = _run(main())
    assert got == list(range(1, len(got) + 1)) and got[-1] == engine.MAX_LAG + 50  # no gap, no repeat
    assert opened == [0, 1]  # the stream was closed after seq 1 and resumed with since=1


def test_a_tool_threads_output_burst_is_one_event_per_loop_turn(tmp_path):
    """The shell tool's drain thread hands chunks to the loop through run_tool's emit. Thousands queued while the
    loop is busy go out as one tool.output, so a reader that keeps up never falls MAX_LAG behind or is ended."""
    from exo.core.engine import CoreEngine
    from exo.core.types import ToolCall, ToolSpec

    lines = [f"PASSED tests/test_{i}.py::test_case\n" for i in range(2 * engine.MAX_LAG)]
    started, go, emitted = threading.Event(), threading.Event(), threading.Event()

    def chatty(args, ctx):  # a sync tool runs in a worker thread, like the shell tool's drain
        started.set()
        go.wait(10)
        for line in lines:
            ctx.emit(line)
        emitted.set()
        return "done"

    async def main():
        models = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"rules": []}}
        eng = CoreEngine(tmp_path / "home", models, env={}, cwd=str(tmp_path))
        try:
            eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
            eng.registry.register(ToolSpec(name="chatty", description="prints a lot", toolset="core"), chatty)
            sid = (await eng.loop.create_session(title="t")).id
            start = eng.hub.seq
            sub = eng.hub.subscribe(since=start)
            got: list[int] = []
            ended = asyncio.Event()

            async def read():
                async for ev in sub:
                    got.append(ev.seq)
                ended.set()

            reader = asyncio.create_task(read())
            tool = asyncio.create_task(eng.run_tool(ToolCall("c1", "chatty", {}), {"session": sid}))
            while not started.is_set():
                await asyncio.sleep(0.01)
            go.set()
            assert emitted.wait(10)  # the loop is held while the thread queues every chunk, as when it is busy
            assert (await tool).text() == "done"
            while len(got) < eng.hub.seq - start and not ended.is_set():
                await asyncio.sleep(0.01)
            outs = [e.body.text for e in eng.hub.since(start) if isinstance(e.body, s.ToolOutput)]
            assert "".join(outs) == "".join(lines)  # every chunk, in order
            assert len(outs) == 1  # one publish for the loop turn, not one per chunk
            assert got == list(range(start + 1, eng.hub.seq + 1)) and not ended.is_set()
            reader.cancel()
        finally:
            await eng.close()

    _run(main())


def test_stdio_feed_rides_through_a_lag_close_and_ends_with_the_hub():
    async def main():
        hub, lag = Hub(), engine.MAX_LAG
        lines: asyncio.Queue = asyncio.Queue()
        out: list[dict] = []
        serve = asyncio.create_task(serve_lines(Dispatcher(EchoEngine(), hub), lines.get,
                                                lambda text: out.append(json.loads(text))))

        def seqs() -> list[int]:
            return [o["seq"] for o in out if "type" in o]

        async def until(n: int) -> None:
            while len(seqs()) < n:
                await asyncio.sleep(0.01)

        lines.put_nowait(s.Command.of(s.EventsSubscribe(since=0)).to_json())
        hub.publish(s.Notice(text="first"))
        await asyncio.wait_for(until(1), 5)
        for _ in range(lag + 50):  # one loop turn's burst: the feed cannot write in between
            hub.publish(s.Notice(text="burst"))
        assert _depth(hub) <= lag  # the core's memory stays bounded
        await asyncio.wait_for(until(lag + 51), 5)  # the feed resumed after the last event it wrote
        hub.publish(s.Notice(text="later"))  # the next turn's events still reach the client
        await asyncio.wait_for(until(lag + 52), 5)
        assert seqs() == list(range(1, lag + 53))  # no gap, no repeat
        hub.close()
        await asyncio.sleep(0.05)
        assert not hub._subs  # the feed ends with the hub instead of subscribing again
        lines.put_nowait(None)
        await asyncio.wait_for(serve, 5)

    _run(main())
