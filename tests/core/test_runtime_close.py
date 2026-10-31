"""CoreRuntime.close() releases the databases it opened, and the spend journal closes its schema connection.

Without this, every engine a process opens and closes leaves sessions.sqlite (with its -wal and -shm files)
and schedule.sqlite open. The root suite then held about 900 handles by its end and failed at the 256-file
default of a macOS Terminal.
"""
import asyncio
import os
import sqlite3

import pytest

from exo.core.engine import CoreEngine
from exo.model_spend.journal import SpendJournal
from exo.scheduler import Scheduler
from exo.server.engine import CommandFailed

MODELS = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
needs_fd_dir = pytest.mark.skipif(not os.path.isdir("/dev/fd"), reason="counts handles through /dev/fd")


def _open_handles() -> int:
    return len(os.listdir("/dev/fd"))


def _engine(home, cwd):
    return CoreEngine(home, MODELS, cwd=str(cwd), env={}, attach_scheduler=Scheduler)


def test_close_releases_the_session_store_and_schedule_database(tmp_path):
    eng = _engine(tmp_path / "home", tmp_path)
    asyncio.run(eng.close())
    assert eng.store._conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        eng.scheduler.db.execute("SELECT 1")


def test_the_store_still_answers_after_close_and_releases_again(tmp_path):
    eng = _engine(tmp_path / "home", tmp_path)
    sid = asyncio.run(eng.loop.create_session(agent="tester")).id
    asyncio.run(eng.close())
    assert eng.store.get_session(sid) is not None  # a late reader reopens the file
    eng.store.close()
    assert eng.store._conn is None


def test_a_closed_memory_store_stays_closed():
    from exo.core.sessions import SessionStore
    store = SessionStore()
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store._db.execute("SELECT 1")


@needs_fd_dir
def test_engines_opened_and_closed_do_not_accumulate_handles(tmp_path):
    kept = []  # held like a long-lived owner would, so garbage collection cannot hide a missing close

    def cycle(i):
        eng = _engine(tmp_path / f"home{i}", tmp_path)
        asyncio.run(eng.close())
        kept.append(eng)

    cycle(0)
    before = _open_handles()
    for i in range(1, 11):
        cycle(i)
    assert _open_handles() - before < 5


@needs_fd_dir
def test_provisioning_a_spend_journal_leaves_no_connection_open(tmp_path):
    SpendJournal.provision(tmp_path / "warm.sqlite", "alice")
    before = _open_handles()
    for i in range(10):
        SpendJournal.provision(tmp_path / f"journal{i}.sqlite", "alice")
    assert _open_handles() - before < 3


def test_an_engine_nobody_closed_can_be_collected(tmp_path):
    import gc
    import weakref
    from exo.core import trace
    eng = _engine(tmp_path / "home", tmp_path)
    ref = weakref.ref(eng)
    del eng
    gc.collect()
    assert ref() is None  # the trace sink does not keep it, or its databases, alive
    trace.report("test", "after the engine is gone")  # a dead sink is skipped, not called


def test_a_plain_function_sink_is_kept_and_removable():
    from exo.core import trace
    seen = []
    sink = lambda body, session: seen.append(body.where)  # noqa: E731
    trace.add_sink(sink)
    try:
        trace.report("here", "boom")
    finally:
        trace.remove_sink(sink)
    trace.report("gone", "boom")
    assert seen == ["here"]


def test_reports_from_many_threads_prune_dead_sinks_without_raising():
    import sys
    import threading
    from exo.core import trace

    class Owner:
        def on(self, body, session):
            pass

    live = [Owner() for _ in range(3)]
    for owner in live:
        trace.add_sink(owner.on)
    for _ in range(400):
        trace.add_sink(Owner().on)  # dead at once: nothing else holds the owner
    errors = []
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-7)
    try:
        for _ in range(40):
            barrier = threading.Barrier(16)

            def go():
                barrier.wait()
                try:
                    trace.report("race", "boom")
                except Exception as exc:  # noqa: BLE001 - any raise is the failure
                    errors.append(exc)

            threads = [threading.Thread(target=go) for _ in range(16)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for _ in range(400):
                trace.add_sink(Owner().on)
    finally:
        sys.setswitchinterval(old)
        for owner in live:
            trace.remove_sink(owner.on)
    assert errors == []


def test_a_method_whose_owner_cannot_be_weakly_referenced_is_held_strongly():
    from exo.core import trace

    class Slotted:
        __slots__ = ("seen",)

        def __init__(self):
            self.seen = []

        def on(self, body, session):
            self.seen.append(body.where)

    owner = Slotted()
    trace.add_sink(owner.on)
    try:
        trace.report("slotted", "boom")
    finally:
        trace.remove_sink(owner.on)
    trace.report("after", "boom")
    assert owner.seen == ["slotted"]


def test_a_store_reopened_after_its_file_was_removed_is_owner_only(tmp_path):
    from exo.core.sessions import SessionStore
    path = tmp_path / "sessions.sqlite"
    store = SessionStore(path)
    store.close()
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / f"sessions.sqlite{suffix}").unlink(missing_ok=True)
    with pytest.raises(sqlite3.OperationalError):
        store._q("SELECT * FROM sessions")  # a removed store does not come back with its tables
    assert path.stat().st_mode & 0o777 == 0o600
    store.close()


def test_a_closed_scheduler_refuses_to_start(tmp_path):
    eng = _engine(tmp_path / "home", tmp_path)
    asyncio.run(eng.close())
    with pytest.raises(RuntimeError, match="closed"):
        eng.scheduler.start()


def test_close_still_closes_the_scheduler_and_store_when_peers_or_jobs_fail(tmp_path):
    class BadPeers:
        async def stop(self):
            raise RuntimeError("peers boom")

    eng = _engine(tmp_path / "home", tmp_path)
    eng.peers = BadPeers()

    async def bad_jobs_close():
        raise ValueError("jobs boom")

    eng.jobs.close = bad_jobs_close
    with pytest.raises(RuntimeError, match="peers boom"):  # the first error wins
        asyncio.run(eng.close())
    assert eng.store._conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        eng.scheduler.db.execute("SELECT 1")


# ---- trace sinks: copy on write, safe under garbage-collector re-entry ----

_REENTRY = '''
import faulthandler, logging
logging.disable(logging.CRITICAL)
from exo.core import trace
faulthandler.dump_traceback_later(40, exit=True)
class Owner:
    def on(self, body, session): pass
class Reporter:
    def __init__(self): self.me = self
    def __del__(self): trace.report("finalizer", "late")
live = [Owner() for _ in range(3)] + [Owner() for _ in range(50)]
for o in live: trace.add_sink(o.on)
for i in range(20000):
    Reporter()
    trace.report("main", "x")
print("survived")
'''


def test_a_finalizer_that_reports_during_a_report_does_not_hang():
    import subprocess
    import sys

    env = dict(os.environ)
    try:
        out = subprocess.run([sys.executable, "-c", _REENTRY], capture_output=True, text=True, timeout=90, env=env)
    except subprocess.TimeoutExpired:
        pytest.fail("trace.report hung under finalizer re-entry")
    assert "survived" in out.stdout, out.stderr[-800:]


def test_a_sink_added_by_a_finalizer_during_pruning_is_not_lost():
    from exo.core import trace

    class Owner:
        def on(self, body, session):
            pass

    added = []

    class Late:
        def on(self, body, session):
            added.append(body.where)

    late = Late()
    dead = Owner()
    trace.add_sink(dead.on)
    del dead  # the entry is dead; the next report prunes it
    real_tuple = tuple
    fired = []

    def tuple_hook(*a):
        if not fired:  # the allocation inside pruning: a finalizer adds a sink on this same thread
            fired.append(1)
            trace.add_sink(late.on)
        return real_tuple(*a)

    import builtins
    trace_globals = trace.__dict__
    trace_globals["tuple"] = tuple_hook
    try:
        trace.report("one", "boom")
    finally:
        del trace_globals["tuple"]
    try:
        assert fired
        trace.report("two", "boom")
        assert added == ["two"]
    finally:
        trace.remove_sink(late.on)
    assert builtins.tuple is real_tuple


def test_concurrent_add_remove_report_and_gc_have_no_errors():
    import gc
    import sys
    import threading
    import time
    from exo.core import trace

    class Persistent:
        def __init__(self):
            self.n = 0
            self.lk = threading.Lock()

        def on(self, body, session):
            with self.lk:
                self.n += 1

    class Cyclic:
        def __init__(self):
            self.me = self

        def on(self, body, session):
            pass

    gc.collect()  # free sinks whose owners earlier tests dropped, so the baseline counts only live ones
    baseline = len(trace._live_sinks())  # other tests may leave sinks of their own
    keep = [Persistent() for _ in range(3)]
    for k in keep:
        trace.add_sink(k.on)
    errors, reports = [], [0]
    stop = threading.Event()
    start = threading.Barrier(9)

    def reporter():
        start.wait()
        n = 0
        while not stop.is_set():
            try:
                trace.report("stress", "boom")
                n += 1
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
        reports[0] += n

    def churn():
        start.wait()
        while not stop.is_set():
            try:
                o = Persistent()
                trace.add_sink(o.on)
                trace.remove_sink(o.on)
                for _ in range(20):
                    trace.add_sink(Cyclic().on)
                gc.collect(0)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            time.sleep(0)

    threads = [threading.Thread(target=reporter) for _ in range(5)] + [threading.Thread(target=churn) for _ in range(4)]
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for t in threads:
            t.start()
        time.sleep(1.5)
        stop.set()
        for t in threads:
            t.join(30)
        assert not any(t.is_alive() for t in threads), "deadlock"
    finally:
        sys.setswitchinterval(old)
        for k in keep:
            trace.remove_sink(k.on)
    gc.collect()
    trace.report("drain", "prune")
    assert errors == []
    assert all(k.n >= reports[0] for k in keep)
    assert len(trace._live_sinks()) == baseline
    assert len(trace._sinks) == baseline  # the drain pruned every dead entry


def test_a_sink_added_by_a_finalizer_while_remove_sink_builds_is_not_lost():
    import subprocess
    import sys

    code = '''
import logging
logging.disable(logging.CRITICAL)
from exo.core import trace
class Owner:
    def on(self, b, s): pass
a, late = Owner(), Owner()
trace.add_sink(a.on)
real = tuple
fired = []
def hook(*args):
    if not fired:
        fired.append(1); trace.add_sink(late.on)
    return real(*args)
trace.__dict__["tuple"] = hook
try:
    trace.remove_sink(a.on)
finally:
    del trace.__dict__["tuple"]
print("PRESENT" if any(r() == late.on for r in trace._sinks) else "LOST")
'''
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail("remove_sink hung")
    assert out.stdout.strip() == "PRESENT", out.stderr[-500:]


def test_close_closes_the_scheduler_and_store_when_scheduler_stop_raises(tmp_path):
    eng = _engine(tmp_path / "home", tmp_path)

    async def bad_stop():
        raise RuntimeError("stop boom")

    eng.scheduler.stop = bad_stop
    with pytest.raises(RuntimeError, match="stop boom"):
        asyncio.run(eng.close())
    assert eng.store._conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        eng.scheduler.db.execute("SELECT 1")


def _resource_engine(tmp_path, handler=None):
    from exo.core.plugins import Manifest, PluginManager
    order = []
    manager = PluginManager()

    def register(ctx):
        ctx.register_cleanup(lambda: order.append("cleanup"))
        ctx.register_hook("pre_tool_call", lambda **kw: None)
        ctx.register_hook("on_turn_end", lambda **kw: order.append("turn end"))
        if handler is not None:
            ctx.register_tool("hold", "test", handler=handler)

    assert manager._run_register(Manifest("resource"), "test", register).ok
    engine = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={}, plugins=manager)
    return engine, order


def test_close_drains_accepted_turn_before_plugin_cleanup(tmp_path):
    from exo.core.models.fake import FakeProvider
    from exo.protocol import schema as s
    started = asyncio.Event()
    exited = asyncio.Event()

    class WaitingProvider:
        name = "fake"

        async def stream(self, messages, tools, **opts):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()
            async for event in FakeProvider(["unreachable"]).stream(messages, tools):
                yield event

    engine, order = _resource_engine(tmp_path)
    engine.loop.provider = WaitingProvider()

    async def run():
        session = await engine.loop.create_session()
        engine.start_turn(session.id, "wait")
        await asyncio.wait_for(started.wait(), 1)
        await engine.close()
        assert exited.is_set() and not engine._tasks
        assert order == ["turn end", "cleanup"]
        assert engine.store._conn is None
        with pytest.raises(CommandFailed, match="closing"):
            engine.start_turn(session.id, "late")
        with pytest.raises(CommandFailed, match="closing"):  # refused before its turn hooks fire
            await engine.run_turn(s.TurnSend(session=session.id, text="late"))
        assert order == ["turn end", "cleanup"]

    asyncio.run(run())


def test_close_waits_for_actual_sync_plugin_worker_after_turn_cancellation(tmp_path):
    import threading
    from exo.core.models.fake import FakeProvider, tool_turn
    started, release, exited = threading.Event(), threading.Event(), threading.Event()

    def hold(args, **kwargs):
        started.set()
        try:
            assert release.wait(2)
            assert "cleanup" not in order
            return "finished"
        finally:
            exited.set()

    engine, order = _resource_engine(tmp_path, hold)
    engine.loop.provider = FakeProvider([tool_turn("hold", {}), "done"])

    async def run():
        session = await engine.loop.create_session()
        engine.start_turn(session.id, "use resource")
        assert await asyncio.to_thread(started.wait, 1)
        closing = asyncio.create_task(engine.close())
        try:
            await asyncio.sleep(0.02)
            assert not closing.done() and "cleanup" not in order
        finally:
            release.set()
        await closing
        assert exited.is_set() and order[-1] == "cleanup"
        assert not engine.registry.workers and not engine._tasks

    asyncio.run(run())


def test_shutdown_timeout_retains_resources_and_writer_until_retry(tmp_path, monkeypatch):
    import threading
    from exo.core.models.fake import FakeProvider, tool_turn
    import exo.core.shutdown as shutdown
    started, release = threading.Event(), threading.Event()

    def hold(args, **kwargs):
        started.set()
        assert release.wait(2)
        return "finished"

    engine, order = _resource_engine(tmp_path, hold)
    engine.loop.provider = FakeProvider([tool_turn("hold", {}), "done"])
    engine.claim_writer()
    writer = engine._writer
    monkeypatch.setattr(shutdown, "SHUTDOWN_TIMEOUT", 0.02)

    async def run():
        session = await engine.loop.create_session()
        engine.start_turn(session.id, "hold resource")
        assert await asyncio.to_thread(started.wait, 1)
        from exo.core.engine import CoreRuntime
        try:
            with pytest.raises(shutdown.ShutdownTimeout, match="tool workers; jobs and transports are stopped"):
                await CoreRuntime.close(engine)  # one bounded attempt; CoreEngine.close retries it
            assert engine._writer is writer and engine.store._conn is not None
            assert "cleanup" not in order and engine.registry.workers
        finally:
            release.set()
        await asyncio.gather(*list(engine.registry.workers))
        await CoreRuntime.close(engine)
        assert engine._writer is None and engine.store._conn is None
        assert order.count("cleanup") == 1

    asyncio.run(run())


def test_cancelled_sync_tool_gets_cancel_event_and_finishes_before_cleanup(tmp_path):
    import threading
    from exo.core.models.fake import FakeProvider, tool_turn
    from exo.core.types import ToolSpec
    started, stopped = threading.Event(), threading.Event()
    engine, order = _resource_engine(tmp_path)

    def cooperative(args, ctx):
        started.set()
        assert ctx.cancel.wait(2)
        assert "cleanup" not in order
        stopped.set()
        return "cancelled cooperatively"

    engine.registry.register(ToolSpec(name="cooperative", description="test"), cooperative)
    engine.loop.provider = FakeProvider([tool_turn("cooperative", {}), "done"])

    async def run():
        session = await engine.loop.create_session()
        engine.start_turn(session.id, "use worker")
        assert await asyncio.to_thread(started.wait, 1)
        await engine.close()
        assert stopped.is_set() and order[-1] == "cleanup"

    asyncio.run(run())


def test_async_plugin_handler_keeps_event_loop_and_direct_queued_turns_stop(tmp_path):
    from exo.core.models.fake import FakeProvider, tool_turn
    started = asyncio.Event()
    handler_loops = []

    async def hold(args, **kwargs):
        handler_loops.append(asyncio.get_running_loop())
        started.set()
        await asyncio.Event().wait()

    engine, order = _resource_engine(tmp_path, hold)
    engine.loop.provider = FakeProvider([tool_turn("hold", {}), "done"])

    async def run():
        session = await engine.loop.create_session()
        first = asyncio.create_task(engine.loop.send(session.id, "first"))
        await asyncio.wait_for(started.wait(), 1)
        queued = asyncio.create_task(engine.loop.send(session.id, "queued"))
        await asyncio.sleep(0)
        await engine.close()
        [result] = await asyncio.gather(first, return_exceptions=True)
        assert isinstance(result, asyncio.CancelledError) or result.stop_reason == "interrupted"
        with pytest.raises(RuntimeError, match="closing"):
            await queued
        assert handler_loops == [asyncio.get_running_loop()] and order == ["cleanup"]
        assert not engine.registry.workers

    asyncio.run(run())


def test_close_drains_direct_run_turn_final_hook_before_cleanup(tmp_path):
    from exo.core.models.fake import FakeProvider
    from exo.protocol import schema as s
    finishing, exited = asyncio.Event(), asyncio.Event()
    engine, order = _resource_engine(tmp_path)
    engine.loop.provider = FakeProvider(["done"])

    async def finish(**kwargs):
        finishing.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    engine.plugins.hooks.register("on_turn_end", finish, "test")

    async def run():
        session = await engine.loop.create_session()
        task = asyncio.create_task(engine.run_turn(s.TurnSend(session=session.id, text="direct")))
        await asyncio.wait_for(finishing.wait(), 1)
        assert not engine.loop.state(session.id).running
        await engine.close()
        assert task.cancelled() and exited.is_set()
        assert order[-1] == "cleanup" and not engine._tasks

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["delegated", "scheduled"])
def test_close_drains_child_and_scheduled_tools_before_cleanup(tmp_path, mode):
    from exo.core.models.fake import FakeProvider, tool_turn
    started, exited = asyncio.Event(), asyncio.Event()

    async def hold(args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    engine, order = _resource_engine(tmp_path, hold)

    class DelegateProvider:
        name = "fake"

        async def stream(self, messages, tools, **opts):
            text = next(message.text() for message in reversed(messages) if message.role == "user")
            spec = tool_turn("hold", {}) if "CHILD_HOLD" in text else tool_turn("delegate", {"goal": "CHILD_HOLD"})
            async for event in FakeProvider([spec]).stream(messages, tools):
                yield event

    engine.loop.provider = DelegateProvider()

    async def run():
        if mode == "delegated":
            session = await engine.loop.create_session(cwd=str(tmp_path))
            engine.start_turn(session.id, "parent")
        else:
            engine.scheduler = Scheduler(engine)
            job = engine.scheduler.add("every 1h", "CHILD_HOLD")
            engine.scheduler.launch(job)
        await asyncio.wait_for(started.wait(), 1)
        await engine.close()
        assert exited.is_set() and order[-1] == "cleanup"
        assert not engine._tasks and not engine.agents.loops and not engine.registry.workers
        if engine.scheduler:
            assert not engine.scheduler.running and not engine.scheduler.loops
            with pytest.raises(sqlite3.ProgrammingError):
                engine.scheduler.db.execute("SELECT 1")

    asyncio.run(run())


def test_direct_tool_dispatch_drains_and_late_tools_or_start_cannot_reopen_resources(tmp_path):
    from exo.core.types import ToolCall
    started, exited = asyncio.Event(), asyncio.Event()
    invocations = []

    async def hold(args, **kwargs):
        invocations.append("called")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    engine, order = _resource_engine(tmp_path, hold)

    async def run():
        call = ToolCall("direct", "hold", {})
        task = asyncio.create_task(engine.run_tool(call, {}))
        await asyncio.wait_for(started.wait(), 1)
        await engine.close()
        assert task.cancelled() and exited.is_set() and order == ["cleanup"]
        late = await asyncio.wait_for(engine.run_tool(call, {}), 2)  # a lost closing guard fails, never hangs
        assert late.is_error and "closing" in late.text()
        with pytest.raises(CommandFailed, match="closing"):
            await engine.start()
        assert invocations == ["called"] and engine.store._conn is None

    asyncio.run(run())


def test_plugin_callback_cannot_close_its_own_live_resources(tmp_path):
    from exo.protocol import schema as s
    engine, order = _resource_engine(tmp_path, lambda args, **kwargs: "done")
    refusals = []

    async def finish(**kwargs):
        try:
            await engine.close()
        except RuntimeError as exc:
            refusals.append(str(exc))

    engine.plugins.hooks.register("on_turn_end", finish, "test")

    async def run():
        session = await engine.loop.create_session(cwd=str(tmp_path))
        await engine.run_turn(s.TurnSend(session=session.id, text="hello"))
        assert refusals and "engine owner" in refusals[0]
        assert order == ["turn end"] and not engine.jobs.target.closed
        await engine.close()
        assert order == ["turn end", "cleanup"]

    asyncio.run(run())
