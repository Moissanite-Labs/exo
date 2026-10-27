"""A close() that a synchronous tool outlives still stops jobs and transports (#500 reviews, B1).

``CoreRuntime.close()`` is bounded. When a sync tool outlives the bound, background jobs, channels, peers, the
scheduler and MCP are already stopped. Only what that tool can still touch is kept: plugin resources, the session
store and the writer claim. ``exo.application.CoreEngine.close()``, which exod and the terminal use, closes again
until the tool returns, so plugin cleanups run once and the process exits cleanly.
"""
import asyncio
import logging
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

import exo.runtime_shutdown as shutdown
from exo.core.engine import CoreEngine, CoreRuntime
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.plugins import Manifest, PluginManager
from exo.core.tools.registry import ToolError
from exo.plugin_lifecycle import Resources
from exo.runtime_shutdown import ShutdownTimeout
from exo.scheduler import Scheduler

MODELS = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def _engine(tmp_path, handler, **kw):
    """An engine with one plugin: a cleanup, a pre_tool_call hook and a sync ``hold`` tool."""
    order = []
    manager = PluginManager()

    def register(ctx):
        ctx.register_cleanup(lambda: order.append("cleanup"))
        ctx.register_hook("pre_tool_call", lambda **kw: None)
        ctx.register_tool("hold", "test", handler=handler)

    assert manager._run_register(Manifest("resource"), "test", register).ok
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={}, plugins=manager, **kw)
    engine.loop.provider = FakeProvider([tool_turn("hold", {}), "done"])
    return engine, order


def _held():
    """A plugin tool that blocks without polling cancellation, as reviewers' repro used ``time.sleep(8)``."""
    started, release = threading.Event(), threading.Event()

    def hold(args, **kwargs):
        started.set()
        assert release.wait(20)
        return "finished"

    return hold, started, release


class _Peers:
    autostart = False

    def __init__(self):
        self.stops = 0

    async def stop(self):
        self.stops += 1


def test_a_timed_out_close_stops_a_live_job_and_keeps_only_what_the_tool_can_touch(tmp_path, monkeypatch):
    hold, started, release = _held()
    engine, order = _engine(tmp_path, hold, attach_scheduler=Scheduler)
    engine.peers = _Peers()
    engine.claim_writer()
    writer = engine._writer
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.2)

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        job = engine.jobs.start("sleep 120", str(tmp_path), session.id)["id"]
        proc = engine.jobs.live[job].proc
        engine.start_turn(session.id, "hold")
        assert await asyncio.to_thread(started.wait, 5)
        try:
            with pytest.raises(ShutdownTimeout, match="tool workers"):
                await CoreRuntime.close(engine)  # one bounded attempt
            # Stopped before the raise: the job's process group, job wakes, new jobs, peers, scheduler, MCP.
            assert proc.poll() is not None and not engine.jobs.live and engine.jobs.target.closed
            assert engine.jobs.get(job)["state"] == "killed"
            with pytest.raises(ToolError, match="shutting down"):
                engine.jobs.start("sleep 1", str(tmp_path), session.id)
            assert engine.peers.stops == 1 and engine._started is None
            with pytest.raises(sqlite3.ProgrammingError):
                engine.scheduler.db.execute("SELECT 1")
            # Kept for the retry: what the running tool may still use.
            assert engine._writer is writer and engine.store._conn is not None
            assert "cleanup" not in order and engine.registry.workers
        finally:
            release.set()
        await asyncio.gather(*list(engine.registry.workers))
        await CoreRuntime.close(engine)  # the retry completes
        assert engine._writer is None and engine.store._conn is None
        assert order.count("cleanup") == 1 and not engine.registry.workers

    asyncio.run(run())


def test_a_job_that_ends_while_close_waits_files_no_bug_finding(tmp_path):
    hold, started, release = _held()
    engine, order = _engine(tmp_path, hold)

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        job = engine.jobs.start("sleep 0.3", str(tmp_path), session.id)["id"]
        engine.start_turn(session.id, "hold")
        assert await asyncio.to_thread(started.wait, 5)
        closing = asyncio.create_task(engine.close())
        try:
            deadline = time.monotonic() + 10
            while engine.jobs.get(job)["state"] == "running" and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)  # a wake from that job would have reached _spawn_turn by now
        finally:
            release.set()
        await closing
        assert engine.jobs.get(job)["state"] in ("done", "killed")
        assert [f["signature"] for f in engine.findings.list()] == []  # no harness.error:jobs.notice bug
        engine.store.close()

    asyncio.run(run())


def test_the_owner_close_retries_until_the_tool_returns(tmp_path, monkeypatch, caplog):
    def hold(args, **kwargs):
        time.sleep(0.6)
        return "finished"

    engine, order = _engine(tmp_path, hold)
    engine.claim_writer()
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.1)

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        engine.start_turn(session.id, "hold")
        deadline = time.monotonic() + 5
        while not engine.registry.workers and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        with caplog.at_level(logging.WARNING, logger="exo.application"):
            await engine.close()  # no ShutdownTimeout reaches the owner
        assert engine._writer is None and engine.store._conn is None
        assert order == ["cleanup"] and not engine.registry.workers
        assert "closing again" in caplog.text

    asyncio.run(run())


def test_serve_closes_once_after_a_sync_tool_outlives_the_bound(tmp_path, monkeypatch):
    """exod's own path: cancelling serve() (SIGTERM) ends it cancelled, not with a shutdown error, and cleans up."""
    from exo.server.app import serve

    def hold(args, **kwargs):
        time.sleep(0.6)
        return "finished"

    engine, order = _engine(tmp_path, hold)
    engine.claim_writer()
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.1)
    sock_dir = Path(tempfile.mkdtemp(prefix="exo-close-", dir="/tmp"))

    async def run():
        up = asyncio.Event()
        task = asyncio.create_task(serve(engine, socket=str(sock_dir / "core.sock"), ready=lambda _: up.set()))
        await asyncio.wait_for(up.wait(), 10)
        session = await engine.loop.create_session(cwd=str(tmp_path))
        engine.start_turn(session.id, "hold")
        deadline = time.monotonic() + 5
        while not engine.registry.workers and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert order == ["cleanup"] and engine._writer is None and engine.store._conn is None

    try:
        asyncio.run(run())
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)


def test_close_cancels_the_scheduler_loop_it_took_over(tmp_path):
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={}, attach_scheduler=Scheduler)

    async def run():
        engine.scheduler.start()
        main = engine.scheduler._main
        await asyncio.sleep(0)
        await engine.close()
        assert main.done() and main.cancelled()

    asyncio.run(run())


def test_close_during_start_stops_the_peer_service_start_was_opening(tmp_path):
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})

    class Peers(_Peers):
        autostart = True

        def __init__(self, opened):
            super().__init__()
            self.opened, self.running = opened, False

        async def start(self, *args):
            await self.opened.wait()
            self.running = True

        async def stop(self):
            await super().stop()
            self.running = False

    async def run():
        engine.peers = Peers(asyncio.Event())
        starting = asyncio.create_task(engine.start())
        await asyncio.sleep(0.01)
        await engine.close()
        engine.peers.opened.set()
        with pytest.raises(RuntimeError, match="closing"):
            await asyncio.wait_for(starting, 5)
        assert not engine.peers.running and engine._started is None

    asyncio.run(run())


def test_close_ends_learning_reviews_then_closes_the_learner_before_the_store(tmp_path):
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})
    events = []

    class Learner:
        def __init__(self):
            self.tasks = {}

        def close(self):
            events.append(("learner closed", engine.store._conn is not None))

    async def review():
        try:
            await asyncio.Event().wait()
        finally:
            events.append("review ended")

    async def run():
        engine._learn = Learner()
        engine._learn.tasks["s"] = asyncio.create_task(review())
        await asyncio.sleep(0)
        await engine.close()
        assert events == ["review ended", ("learner closed", True)]

    asyncio.run(run())


def test_an_interrupted_cleanup_still_runs_the_rest_and_is_raised_last():
    ran = []
    resources = Resources("test")
    resources.add(lambda: ran.append("first"))

    def interrupt():
        ran.append("interrupt")
        raise KeyboardInterrupt

    resources.add(interrupt)
    resources.add(lambda: ran.append("last"))
    with pytest.raises(KeyboardInterrupt):
        resources.close()
    assert ran == ["last", "interrupt", "first"]
    resources.close()  # once only
    assert ran == ["last", "interrupt", "first"]


def test_an_interrupt_in_register_leaves_its_cleanup_with_the_manager():
    ran = []
    manager = PluginManager()

    def register(ctx):
        ctx.register_cleanup(lambda: ran.append("cleanup"))
        raise SystemExit(3)

    with pytest.raises(SystemExit):
        manager._run_register(Manifest("interrupted"), "test", register)
    manager.close()
    assert ran == ["cleanup"]


def test_a_cleanup_that_raises_systemexit_still_releases_the_writer_and_store(tmp_path):
    def stop():
        raise SystemExit(4)

    manager = PluginManager()
    assert manager._run_register(Manifest("exits"), "test", lambda ctx: ctx.register_cleanup(stop)).ok
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={}, plugins=manager)
    engine.claim_writer()
    with pytest.raises(SystemExit):
        asyncio.run(engine.close())
    assert engine._writer is None and engine.store._conn is None


# ---- each shutdown step is pinned: deleting one fails a test (review 5634152980 item 10) ----

class _Blocking:
    """A provider whose stream waits until released. With ``stubborn`` it also ignores cancellation and
    interrupts, as a provider stuck in a blocking call would."""
    name = "fake"

    def __init__(self, stubborn=False):
        self.stubborn, self.entered, self.release = stubborn, asyncio.Event(), asyncio.Event()

    async def stream(self, messages, tools, **opts):
        self.entered.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.stubborn:
                    raise
        async for event in FakeProvider(["done"]).stream(messages, tools):
            yield event


def _stubborn_task(release):
    async def body():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    return asyncio.create_task(body())


def test_a_job_that_ends_while_channels_stop_files_no_bug_finding(tmp_path):
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})

    class SlowChannel:
        async def stop(self):  # the job ends while close() waits here, before jobs.close() runs
            deadline = time.monotonic() + 10
            while engine.jobs.get(job)["state"] == "running" and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.3)  # a wake from that job would have reached _spawn_turn by now

    async def run():
        nonlocal job
        session = await engine.loop.create_session(cwd=str(tmp_path))
        job = engine.jobs.start("sleep 0.2", str(tmp_path), session.id)["id"]
        engine.channels["slow"] = SlowChannel()
        await engine.close()
        assert engine.jobs.get(job)["state"] == "done"
        assert [f["signature"] for f in engine.findings.list()] == []
        engine.store.close()

    job = None
    asyncio.run(run())


def test_close_interrupts_a_direct_loop_send_and_completes(tmp_path):
    engine, order = _engine(tmp_path, lambda args, **kw: "unused")

    async def run():
        engine.loop.provider = provider = _Blocking()
        session = await engine.loop.create_session(cwd=str(tmp_path))
        direct = asyncio.create_task(engine.loop.send(session.id, "embedded caller"))  # not owned by the engine
        await asyncio.wait_for(provider.entered.wait(), 5)
        started = time.monotonic()
        await CoreRuntime.close(engine)  # one bounded attempt: the interrupt ends the turn well inside it
        assert time.monotonic() - started < shutdown.SHUTDOWN_TIMEOUT and direct.done()
        assert order == ["cleanup"] and engine.store._conn is None

    asyncio.run(run())


def test_a_direct_turn_that_ignores_its_interrupt_times_out_then_the_retry_completes(tmp_path, monkeypatch):
    engine, order = _engine(tmp_path, lambda args, **kw: "unused")
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.3)

    async def run():
        engine.loop.provider = provider = _Blocking(stubborn=True)
        session = await engine.loop.create_session(cwd=str(tmp_path))
        direct = asyncio.create_task(engine.loop.send(session.id, "embedded caller"))
        await asyncio.wait_for(provider.entered.wait(), 5)
        try:
            with pytest.raises(ShutdownTimeout, match="turns"):
                await asyncio.wait_for(CoreRuntime.close(engine), 5)  # bounded: never waits for the turn itself
            assert order == [] and engine.store._conn is not None
        finally:
            provider.release.set()  # a failure here must not leave a turn that ignores cancellation
        await asyncio.wait_for(direct, 5)
        await CoreRuntime.close(engine)
        assert order == ["cleanup"] and engine.store._conn is None

    asyncio.run(run())


def test_an_owned_tool_that_ignores_cancellation_keeps_resources_until_retry(tmp_path, monkeypatch):
    from exo.core.types import ToolCall
    release, entered = asyncio.Event(), asyncio.Event()

    async def hold(args, **kwargs):
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return "late"

    engine, order = _engine(tmp_path, hold)
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.3)

    async def run():
        direct = asyncio.create_task(engine.run_tool(ToolCall("direct", "hold", {}), {}))  # owned while it runs
        await asyncio.wait_for(entered.wait(), 5)
        try:
            with pytest.raises(ShutdownTimeout, match="turns"):
                await CoreRuntime.close(engine)
            assert order == [] and engine.store._conn is not None
        finally:
            release.set()
        await asyncio.gather(direct, return_exceptions=True)
        await CoreRuntime.close(engine)
        assert order == ["cleanup"] and engine.store._conn is None

    asyncio.run(run())


def test_close_cancels_an_mcp_start_in_progress(tmp_path):
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})
    events = []

    class Mcp:
        def __init__(self):
            self.entered = asyncio.Event()

        async def start(self, registry):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("start cancelled")
                raise

        async def close(self):
            events.append("closed")

    async def run():
        engine.mcp = Mcp()
        starting = asyncio.create_task(engine.start())
        await asyncio.wait_for(engine.mcp.entered.wait(), 5)
        await asyncio.wait_for(engine.close(), 5)
        assert sorted(events) == ["closed", "start cancelled"]
        await asyncio.gather(starting, return_exceptions=True)

    asyncio.run(run())


def test_close_retrieves_the_error_of_a_turn_that_failed_while_it_was_cancelled(tmp_path):
    import gc
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})
    unretrieved = []

    async def turn():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise ValueError("its cleanup failed") from None

    async def run():
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: unretrieved.append(ctx.get("message")))
        task = asyncio.create_task(turn())  # owned the way _spawn_turn owns a turn
        engine._tasks.add(task)
        task.add_done_callback(engine._tasks.discard)
        await asyncio.sleep(0)
        await engine.close()
        del task
        gc.collect()
        assert not [m for m in unretrieved if m and "never retrieved" in m]

    asyncio.run(run())


def test_a_learning_review_that_ignores_cancellation_keeps_the_store_until_retry(tmp_path, monkeypatch):
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.3)
    closed = []

    class Learner:
        tasks = {}

        def close(self):
            closed.append(engine.store._conn is not None)

    async def run():
        release = asyncio.Event()
        engine._learn = Learner()
        engine._learn.tasks["s"] = review = _stubborn_task(release)
        await asyncio.sleep(0)
        try:
            with pytest.raises(ShutdownTimeout, match="learning reviews"):
                await CoreRuntime.close(engine)
            assert closed == [] and engine.store._conn is not None
        finally:
            release.set()
        await review
        await CoreRuntime.close(engine)
        assert closed == [True] and engine.store._conn is None

    asyncio.run(run())
