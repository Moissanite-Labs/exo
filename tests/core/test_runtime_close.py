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
