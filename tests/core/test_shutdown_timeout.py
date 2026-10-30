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

import exo.core.shutdown as shutdown
from exo.core.engine import CoreEngine, CoreRuntime
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.plugins import Manifest, PluginManager
from exo.core.tools.registry import ToolError
from exo.core.plugin_lifecycle import Resources
from exo.core.shutdown import ShutdownTimeout
from exo.scheduler import Scheduler
from exo.server.engine import CommandFailed

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


def test_close_cancels_a_running_scheduled_job_before_transports_stop(tmp_path):
    """Scheduler.tasks lists the running jobs too, so close cancels them at admission, not in its scheduler step."""
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={}, attach_scheduler=Scheduler)
    stop_channels, running, seen = engine.stop_channels, [], []

    async def check_then_stop():
        await asyncio.sleep(0)  # one turn of the loop delivers the cancel sent at admission
        seen.append(running[0].cancelled())
        await stop_channels()

    engine.stop_channels = check_then_stop

    async def run():
        running.append(asyncio.ensure_future(asyncio.sleep(30)))
        engine.scheduler.running["job"] = running[0]
        assert engine.scheduler.tasks() == tuple(running)
        await engine.close()
        assert seen == [True]

    asyncio.run(run())


def test_a_job_that_comes_due_while_close_stops_transports_is_left_for_the_next_start(tmp_path):
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={}, attach_scheduler=Scheduler)
    scheduler, now, launched = engine.scheduler, [1_000_000.0], []
    scheduler.clock = lambda: now[0]
    job = scheduler.add("every 1h", "report")
    scheduler.launch = lambda due: launched.append(due["id"])
    stop_channels, loop = engine.stop_channels, []

    def claimed():
        return scheduler.get(job["id"])["next_run"] != job["next_run"]

    async def due_while_channels_stop():
        now[0] = job["next_run"] + 1  # stop_admission has run: the scheduler's loop must not claim this slot
        scheduler._wake.set()  # in the same pass as the cancel: Python 3.11's wait_for then drops the cancel
        deadline = time.monotonic() + 10
        while not loop[0].done() and not claimed():  # the loop ends, or it claims the slot
            assert time.monotonic() < deadline, "the scheduler's loop neither ended nor claimed the slot"
            await asyncio.sleep(0.01)
        assert loop[0].cancelled() and not claimed()
        await stop_channels()

    engine.stop_channels = due_while_channels_stop

    async def run():
        scheduler.start()
        loop.extend(scheduler.tasks())
        await asyncio.sleep(0)
        await engine.close()
        assert launched == []

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
        with pytest.raises(CommandFailed, match="closing"):
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


def test_a_worker_whose_caller_close_does_not_own_still_gets_the_cancel_signal(tmp_path):
    from exo.core.tools.registry import ToolContext
    from exo.core.types import ToolCall, ToolSpec
    started = threading.Event()

    def cooperative(args, ctx):
        started.set()
        return "stopped" if ctx.cancel.wait(10) else "never signalled"

    engine, order = _engine(tmp_path, lambda args, **kw: "unused")
    engine.registry.register(ToolSpec(name="cooperative", description="test"), cooperative)

    async def run():
        call = ToolCall("direct", "cooperative", {})  # straight to the registry: no turn and no run_tool owns it
        caller = asyncio.create_task(engine.registry.call(call, ToolContext(cwd=str(tmp_path))))
        assert await asyncio.to_thread(started.wait, 5)
        before = time.monotonic()
        await CoreRuntime.close(engine)
        assert time.monotonic() - before < shutdown.SHUTDOWN_TIMEOUT and order == ["cleanup"]
        assert "stopped" in (await caller).text()

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


def test_a_start_that_close_cancelled_is_not_retried(tmp_path):
    """Main clears a failed or cancelled start, so the next command starts again (#726). close() cancels a start in
    progress. The command that waited on it fails with closed, not a dropped request. A command after that is
    refused too, and neither peers nor MCP start again: start() checks the closing flag before it looks at the
    cleared start. tests/integration/test_engine_start_retry.py keeps the retry."""
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher

    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})
    events = []

    class Mcp:
        def __init__(self):
            self.entered = asyncio.Event()

        async def start(self, registry):
            events.append("mcp start")
            self.entered.set()
            await asyncio.Event().wait()

        async def close(self):
            events.append("mcp close")

    class Peers(_Peers):
        autostart = True

        async def start(self, *args):
            events.append("peers start")

    async def run():
        engine.mcp, engine.peers = Mcp(), Peers()
        dispatcher = Dispatcher(engine)
        listing = s.Command.of(s.SessionList())
        waiting = asyncio.ensure_future(dispatcher.run(listing))
        await asyncio.wait_for(engine.mcp.entered.wait(), 5)
        await asyncio.wait_for(engine.close(), 5)
        first = await waiting
        assert not first.ok and first.error.code == "closed"
        assert engine._started is None  # cleared, as main clears any cancelled start
        again = await dispatcher.run(listing)
        assert not again.ok and again.error.code == "closed"
        with pytest.raises(CommandFailed, match="closing"):
            await engine.start()
        assert events == ["peers start", "mcp start", "mcp close"] and engine.peers.stops == 1

    asyncio.run(run())


def test_a_command_its_caller_cancelled_stays_cancelled_when_close_cancels_the_start(tmp_path):
    """start() turns close()'s cancel of the start into closed only for a caller that was not cancelled itself. A
    caller that cancelled its own command while close began still gets CancelledError, so no cancel is lost."""
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})

    class Mcp:
        def __init__(self):
            self.entered = asyncio.Event()

        async def start(self, registry):
            self.entered.set()
            await asyncio.Event().wait()

        async def close(self):
            pass

    async def run():
        engine.mcp = Mcp()
        waiting = asyncio.ensure_future(engine.start())
        await asyncio.wait_for(engine.mcp.entered.wait(), 5)
        closing = asyncio.ensure_future(engine.close())
        await asyncio.sleep(0)  # close() has begun: the flag is set and the start is being cancelled
        assert engine._closing and not waiting.done()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await asyncio.wait_for(closing, 5)

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


# ---- a failing or interrupted step still ends jobs and runs the final steps (checker round on #500) ----

def _ended(proc, within=10.0):
    deadline = time.monotonic() + within
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    return proc.poll() is not None


def test_a_learner_that_fails_to_close_still_lets_close_release_everything(tmp_path):
    engine, order = _engine(tmp_path, lambda args, **kw: "unused")
    engine.claim_writer()

    class Learner:
        tasks = {}

        def close(self):
            raise RuntimeError("learner close failed")

    async def run():
        engine._learn = Learner()
        session = await engine.loop.create_session(cwd=str(tmp_path))
        proc = engine.jobs.live[engine.jobs.start("sleep 120", str(tmp_path), session.id)["id"]].proc
        with pytest.raises(RuntimeError, match="learner close failed"):
            await CoreRuntime.close(engine)
        assert _ended(proc)
        assert order == ["cleanup"] and engine._writer is None and engine.store._conn is None

    asyncio.run(run())


def test_a_step_that_fails_before_the_bound_is_named_by_the_timeout(tmp_path, monkeypatch, caplog):
    hold, started, release = _held()
    engine, order = _engine(tmp_path, hold)
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.2)
    close_jobs, failed = engine.jobs.close, OSError("could not stop a job")

    async def fails_once():
        engine.jobs.close = close_jobs  # the retry's step passes, so only the timeout can carry this error
        raise failed

    engine.jobs.close = fails_once

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        engine.start_turn(session.id, "hold")
        assert await asyncio.to_thread(started.wait, 5)
        try:
            with pytest.raises(ShutdownTimeout, match="could not stop a job") as timed_out:
                await CoreRuntime.close(engine)
            assert timed_out.value.errors == [failed] and timed_out.value.__cause__ is failed
        finally:
            release.set()
        with caplog.at_level(logging.WARNING, logger="exo.application"):
            await engine.close()  # the owner's retry completes
        assert order == ["cleanup"] and engine.store._conn is None

    asyncio.run(run())


def test_a_hard_stop_while_channels_stop_still_ends_jobs_and_releases_everything(tmp_path):
    """A second SIGTERM cancels close() while a channel stops: jobs still end, and so does the rest of close."""
    engine, order = _engine(tmp_path, lambda args, **kw: "unused")
    engine.claim_writer()
    stopping = asyncio.Event()

    class SlowChannel:
        async def stop(self):
            stopping.set()
            await asyncio.sleep(5)

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        proc = engine.jobs.live[engine.jobs.start("sleep 120", str(tmp_path), session.id)["id"]].proc
        engine.channels["slow"] = SlowChannel()
        closing = asyncio.create_task(CoreRuntime.close(engine))
        await asyncio.wait_for(stopping.wait(), 5)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert _ended(proc) and engine.jobs.target.closed and not engine.jobs.live
        assert order == ["cleanup"] and engine._writer is None and engine.store._conn is None

    asyncio.run(run())


def test_a_hard_stop_while_jobs_are_killed_still_ends_every_job(tmp_path, monkeypatch):
    import exo.core.jobs as jobs_mod

    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})
    killing = threading.Event()

    def slow_kill(proc, grace=1.0):  # a job that ignores SIGTERM keeps its kill in the grace wait
        killing.set()
        time.sleep(0.3)
        kill_process_group(proc, grace)

    from exo.core.tools.builtin.shell import kill_process_group
    monkeypatch.setattr(jobs_mod, "kill_process_group", slow_kill)

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        procs = [engine.jobs.live[engine.jobs.start("sleep 120", str(tmp_path), session.id)["id"]].proc
                 for _ in range(3)]
        closing = asyncio.create_task(engine.close())
        assert await asyncio.to_thread(killing.wait, 5)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert all(await asyncio.to_thread(lambda: [_ended(p) for p in procs]))
        assert not engine.jobs.live and engine.store._conn is None

    asyncio.run(run())


# ---- fresh review of 2026-10-27 (#500): a hard stop ends close, and close refuses new work ----

def test_a_hard_stop_while_peers_stop_is_raised_after_the_bounded_attempt(tmp_path, monkeypatch):
    """The review's probe: a second SIGTERM lands while close() stops peers, and a sync tool outlives the bound.
    The owner's close raises the CancelledError after its one bounded attempt, instead of closing again until the
    tool returns. Jobs are stopped, and the rest stays as after a timeout."""
    hold, started, release = _held()
    engine, order = _engine(tmp_path, hold)
    engine.claim_writer()
    stopping = asyncio.Event()

    class SlowPeers(_Peers):
        async def stop(self):
            self.stops += 1
            stopping.set()
            await asyncio.sleep(0.5)

    engine.peers = SlowPeers()
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.3)

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        proc = engine.jobs.live[engine.jobs.start("sleep 120", str(tmp_path), session.id)["id"]].proc
        engine.start_turn(session.id, "hold")
        assert await asyncio.to_thread(started.wait, 5)
        try:
            closing = asyncio.create_task(engine.close())
            await asyncio.wait_for(stopping.wait(), 5)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(closing, 5)  # a retry would hold close() until the tool returns
            assert _ended(proc) and engine.jobs.target.closed and not engine.jobs.live
            assert "cleanup" not in order and engine._writer is not None and engine.store._conn is not None
        finally:
            release.set()
        await asyncio.gather(*list(engine.registry.workers))
        await engine.close()  # a later close completes
        assert order == ["cleanup"] and engine._writer is None and engine.store._conn is None

    asyncio.run(run())


def test_an_ordinary_step_error_does_not_stop_the_owner_close_from_closing_again(tmp_path, monkeypatch, caplog):
    """Only a hard stop ends the retries. A job that could not be stopped is named in the warning, and the owner's
    close still finishes once the tool returns, with the cleanup run once."""
    hold, started, release = _held()
    engine, order = _engine(tmp_path, hold)
    engine.claim_writer()
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.2)
    close_jobs, failed = engine.jobs.close, OSError("could not stop a job")

    async def fails_once():
        engine.jobs.close = close_jobs
        await close_jobs()
        raise failed

    engine.jobs.close = fails_once

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        engine.start_turn(session.id, "hold")
        assert await asyncio.to_thread(started.wait, 5)
        with caplog.at_level(logging.WARNING, logger="exo.application"):
            closing = asyncio.create_task(engine.close())
            try:
                deadline = time.monotonic() + 5
                while "closing again" not in caplog.text and not closing.done() and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                assert not closing.done() and "could not stop a job" in caplog.text
            finally:
                release.set()
            await asyncio.wait_for(closing, 10)  # it returns: the error was named, and closing again completed
        assert order == ["cleanup"] and engine._writer is None and engine.store._conn is None

    asyncio.run(run())


def test_a_steer_that_arrives_after_close_began_is_refused_not_queued(tmp_path):
    """A turn still takes steers until its cancellation runs. A steer sent in that window is refused, so it is not
    accepted and then lost with the turn."""
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        state = engine.loop.state(session.id)
        state.accepting_steers = True  # as while a turn runs its tools
        engine.loop.closing = True  # close() sets it first
        with pytest.raises(RuntimeError, match="loop is closing"):
            await engine.loop.send(session.id, "late", mode="steer")
        assert not state.steer
        state.accepting_steers = False
        await engine.close()

    asyncio.run(run())


def test_a_peers_command_while_close_kills_jobs_is_refused_and_starts_nothing(tmp_path, monkeypatch):
    """The review's probe: a command handled after close() stopped peers restarted the peer service, and close()
    returned with it running. Once close() has begun, every command is refused."""
    import exo.core.jobs as jobs_mod
    from exo.core.tools.builtin.shell import kill_process_group
    from exo.protocol import schema as s

    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})
    killing = threading.Event()

    def slow_kill(proc, grace=1.0):  # a job that ignores SIGTERM keeps close in its kill step a while
        killing.set()
        time.sleep(0.3)
        kill_process_group(proc, grace)

    class Peers(_Peers):
        autostart = True

        def __init__(self):
            super().__init__()
            self.node, self.starts = None, 0

        async def start(self, *args):
            self.starts += 1
            self.node = "running"

        async def stop(self):
            await super().stop()
            self.node = None

        async def ready(self):  # as PeerService.ready: started on demand while autostart is on
            if self.node is None and self.autostart:
                await self.start()
            return self.node is not None

        async def command(self, body, via):
            return {"peers": []}

    monkeypatch.setattr(jobs_mod, "kill_process_group", slow_kill)

    async def run():
        engine.peers = Peers()
        session = await engine.loop.create_session(cwd=str(tmp_path))
        peers_list = s.Command.of(s.Peers(action="list", session=session.id))
        assert await engine.handle(peers_list, engine.hub) == {"peers": []}
        engine.jobs.start("sleep 120", str(tmp_path), session.id)
        closing = asyncio.create_task(engine.close())
        assert await asyncio.to_thread(killing.wait, 5)
        assert engine.peers.node is None  # close() has stopped peers and is killing the job
        with pytest.raises(CommandFailed, match="closing") as refused:
            await engine.handle(peers_list, engine.hub)
        assert refused.value.code == "closed"
        await closing
        assert engine.peers.node is None and engine.peers.starts == 1 and engine.peers.stops == 1

    asyncio.run(run())
