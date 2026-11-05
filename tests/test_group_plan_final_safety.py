"""Offline fault injection and cross-process pre-factory reservation regressions."""
import asyncio
import copy
import multiprocessing
import sqlite3

import pytest

from exo.core.group_host import GroupHost, HostError
from exo.core.group_runtime import GroupRuntime
from exo.core.group_plan_execution import PlanExecutions, PlanExecutionError, start_plan
from exo.core.group_task_contract import validate_plan
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore
from test_group_plan_execution import OWNER, PROFILES, RAW, Room, make_grants


@pytest.fixture
def room():
    r = Room()
    yield r
    r.store.close()


def _contender(path, group_id, barrier, release, events):
    store = SessionStore(path)
    groups = GroupStore(store)
    grants = make_grants()
    plan = validate_plan(RAW, grants, group=groups.get(group_id, owner=OWNER))

    def factory(group):
        events.put("factory")
        if not release.wait(15):
            raise AssertionError("release timed out")
        raise ValueError("injected allocation failure")

    host = GroupHost(groups, owner=OWNER, known_profiles=PROFILES, engine_factory=factory)
    try:
        barrier.wait(15)
        start_plan(host, group_id, plan=plan, grants=grants)
    except PlanExecutionError as exc:
        events.put("duplicate" if "already started" in str(exc) else repr(exc))
    except ValueError as exc:
        events.put("released" if str(exc) == "injected allocation failure" else repr(exc))
    finally:
        store.close()


def test_process_reservation_precedes_factory_and_failed_setup_releases(tmp_path):
    path = str(tmp_path / "shared.sqlite")
    store = SessionStore(path)
    groups = GroupStore(store)
    host = GroupHost(groups, owner=OWNER, known_profiles=PROFILES, engine_factory=lambda g: {})
    group = host.create(mode="socratic", members=PROFILES)
    plan = validate_plan(RAW, make_grants(), group=group)
    ctx = multiprocessing.get_context("spawn")
    barrier, release, events = ctx.Barrier(2), ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_contender, args=(path, group.id, barrier, release, events))
                 for _ in range(2)]
    try:
        for process in processes:
            process.start()
        # Winner holds its reservation while allocating, but holds no SQLite transaction.
        assert sorted([events.get(timeout=20), events.get(timeout=20)]) == ["duplicate", "factory"]
        executions = PlanExecutions(groups)
        assert executions.started(plan.plan_id, group_id=group.id, owner=OWNER)
        assert all(row.state == "pending" for row in executions.tasks(
            plan.plan_id, group_id=group.id, owner=OWNER))
        release.set()
        assert events.get(timeout=20) == "released"
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
        assert not executions.started(plan.plan_id, group_id=group.id, owner=OWNER)
    finally:
        release.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)
        events.close()
        store.close()


def test_reservation_cleanup_is_token_owned_and_never_removes_started_tasks(room):
    executions = PlanExecutions(room.groups)
    token = executions.begin(room.plan, owner=OWNER)
    kw = dict(group_id=room.group.id, owner=OWNER)
    executions.discard_unstarted(room.plan.plan_id, execution_id="not-the-owner", **kw)
    assert executions.started(room.plan.plan_id, **kw)
    executions.mark(plan_id=room.plan.plan_id, task_id="build", state="running", **kw)
    executions.discard_unstarted(room.plan.plan_id, execution_id=token, **kw)
    assert executions.started(room.plan.plan_id, **kw)


@pytest.mark.parametrize("failure", ["factory", "binding"])
def test_allocation_failure_allows_later_start(room, failure):
    factory = room.host._factory
    if failure == "factory":
        def broken(group):
            raise ValueError("injected allocation failure")
    else:
        def broken(group):
            return {}
    room.host._factory = broken
    with pytest.raises(Exception):
        start_plan(room.host, room.group.id, plan=room.plan, grants=room.grants)
    assert not PlanExecutions(room.groups).started(
        room.plan.plan_id, group_id=room.group.id, owner=OWNER)
    room.host._factory = factory

    async def run():
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        await room.host.wait(room.group.id)
    asyncio.run(run())
    assert {r.state for r in room.rows().values()} == {"completed"}


def test_stop_unentered_attempts_all_cleanup_and_preserves_first_error(room, monkeypatch):
    raw = copy.deepcopy(RAW)
    raw["tasks"].append({"task_id": "after", "role": "builder", "profile": "builder",
                         "goal": "follow up", "acceptance": ["done"], "depends_on": ["build"]})
    room.plan = validate_plan(raw, room.grants, group=room.group)
    original = PlanExecutions.mark
    attempted = []
    failure = RuntimeError("injected first mark failure")

    def mark(self, **kw):
        attempted.append(kw["task_id"])
        if kw["task_id"] == "build":
            raise failure
        return original(self, **kw)

    monkeypatch.setattr(PlanExecutions, "mark", mark)

    async def run():
        runtime = start_plan(room.host, room.group.id, plan=room.plan, grants=room.grants)
        runtime.stop()  # cancellation succeeded; the cleanup error is reported by wait()
        adapter = runtime._plan_adapter
        assert adapter._profiles_done == set(PROFILES)
        assert all(adapter._gate.state(t.task_id) == "stopped" for t in room.plan.tasks)
        with pytest.raises(RuntimeError) as caught:
            await runtime.wait()
        assert caught.value is failure

    asyncio.run(run())
    assert set(attempted) == {"build", "check", "after"}
    assert {k: r.state for k, r in room.rows().items()} == {
        "build": "pending", "check": "stopped", "after": "stopped"}
    assert not room.calls
    assert PlanExecutions(room.groups).plan_state(
        room.plan.plan_id, group_id=room.group.id, owner=OWNER) == "running"


def test_stop_unentered_reports_finish_failure(room, monkeypatch):
    def fail(self, **kw):
        raise RuntimeError("injected finish failure")
    monkeypatch.setattr(PlanExecutions, "finish", fail)

    async def run():
        runtime = start_plan(room.host, room.group.id, plan=room.plan, grants=room.grants)
        runtime.stop()
        with pytest.raises(PlanExecutionError, match="injected finish failure"):
            await runtime.wait()
    asyncio.run(run())
    assert {r.state for r in room.rows().values()} == {"stopped"}
    assert PlanExecutions(room.groups).plan_state(
        room.plan.plan_id, group_id=room.group.id, owner=OWNER) == "running"


def test_begin_sqlite_error_is_a_refusal_before_the_factory(room, monkeypatch):
    def locked(self, plan, *, owner):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(PlanExecutions, "begin", locked)
    with pytest.raises(PlanExecutionError, match="plan reservation failed: database is locked"):
        start_plan(room.host, room.group.id, plan=room.plan, grants=room.grants)

    async def through_host():
        with pytest.raises(HostError, match="database is locked"):
            room.host.start(room.group.id, plan=room.plan, grants=room.grants)
    asyncio.run(through_host())
    assert room.factory_calls == 0 and room.group.id not in room.host._runtimes


def test_cleanup_failure_does_not_mask_the_setup_error(room, monkeypatch):
    def broken(group):
        raise ValueError("injected allocation failure")

    def cleanup_fails(self, *args, **kw):
        raise sqlite3.OperationalError("cleanup locked")
    room.host._factory = broken
    monkeypatch.setattr(PlanExecutions, "discard_unstarted", cleanup_fails)
    with pytest.raises(ValueError, match="injected allocation failure"):
        start_plan(room.host, room.group.id, plan=room.plan, grants=room.grants)


def test_runtime_start_failure_after_runner_start_keeps_reservation_and_stops_members(room, monkeypatch):
    def start_then_fail(self):
        self._runner.start()
        raise RuntimeError("injected settler failure")
    monkeypatch.setattr(GroupRuntime, "start", start_then_fail)

    async def run():
        with pytest.raises(RuntimeError, match="injected settler failure"):
            start_plan(room.host, room.group.id, plan=room.plan, grants=room.grants)
        executions = PlanExecutions(room.groups)
        assert executions.started(room.plan.plan_id, group_id=room.group.id, owner=OWNER)
        for _ in range(5):
            await asyncio.sleep(0)
    asyncio.run(run())
    assert not room.calls  # members were cancelled before any engine turn
    assert {r.state for r in room.rows().values()} == {"stopped"}
    with pytest.raises(PlanExecutionError, match="already started"):
        PlanExecutions(room.groups).begin(room.plan, owner=OWNER)


def test_stop_returns_after_cancellation_even_when_cleanup_fails(room, monkeypatch):
    def fail(self, **kw):
        raise sqlite3.OperationalError("mark locked")
    monkeypatch.setattr(PlanExecutions, "mark", fail)

    async def run():
        runtime = start_plan(room.host, room.group.id, plan=room.plan, grants=room.grants)
        runtime.stop()  # must not raise: cancellation was requested
        runtime.stop()  # and stays idempotent
        with pytest.raises(sqlite3.OperationalError, match="mark locked"):
            await runtime.wait()
    asyncio.run(run())
    assert not room.calls


def test_transient_cleanup_error_does_not_make_the_host_unclosable(room, monkeypatch):
    original = PlanExecutions.mark
    state = {"locked": True, "calls": 0}

    def mark(self, **kw):
        state["calls"] += 1
        if state["locked"]:
            raise sqlite3.OperationalError("transient lock")
        return original(self, **kw)
    monkeypatch.setattr(PlanExecutions, "mark", mark)

    async def run():
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        room.host.stop(room.group.id)  # cancellation requested; durable cleanup failed
        with pytest.raises(HostError, match="transient lock"):
            await room.host.aclose()  # still locked: retried and still reported, not silent
        assert not room.host._closed
        locked_calls = state["calls"]
        assert locked_calls > 2  # the retry really re-attempted the writes
        state["locked"] = False  # the lock clears
        await room.host.aclose()  # the retry settles the rows, so the host closes
        assert room.host._closed
        await room.host.aclose()  # idempotent
    asyncio.run(run())
    assert {r.state for r in room.rows().values()} == {"stopped"}
    assert PlanExecutions(room.groups).plan_state(
        room.plan.plan_id, group_id=room.group.id, owner=OWNER) == "settled"
    assert not room.calls  # no engine turn ever ran


def test_cleanup_retry_never_rewrites_terminal_rows_or_other_plans(room, monkeypatch):
    original = PlanExecutions.mark
    state = {"locked": True}
    marks = []

    def mark(self, **kw):
        marks.append((kw["task_id"], kw["state"]))
        if state["locked"]:
            raise sqlite3.OperationalError("transient lock")
        return original(self, **kw)
    monkeypatch.setattr(PlanExecutions, "mark", mark)

    async def run():
        runtime = start_plan(room.host, room.group.id, plan=room.plan, grants=room.grants)
        runtime.stop()
        # Another writer settles one row while the retry is queued: the retry must leave it alone.
        original(PlanExecutions(room.groups), plan_id=room.plan.plan_id, task_id="build",
                 group_id=room.group.id, owner=OWNER, state="stopped")
        state["locked"] = False
        marks.clear()
        await runtime.wait()
        assert runtime.cleanup_error is None
        await runtime.wait()  # repeat is a no-op
    asyncio.run(run())
    assert marks == [("check", "stopped")]
    assert {r.state for r in room.rows().values()} == {"stopped"}


def test_start_failure_after_runner_start_keeps_cleanup_error_as_a_note(room, monkeypatch):
    def start_then_fail(self):
        self._runner.start()
        raise RuntimeError("injected settler failure")
    monkeypatch.setattr(GroupRuntime, "start", start_then_fail)

    def mark(self, **kw):
        raise sqlite3.OperationalError("mark locked")
    monkeypatch.setattr(PlanExecutions, "mark", mark)

    async def run():
        with pytest.raises(RuntimeError, match="injected settler failure") as caught:
            start_plan(room.host, room.group.id, plan=room.plan, grants=room.grants)
        for _ in range(5):
            await asyncio.sleep(0)
        return caught.value
    exc = asyncio.run(run())
    assert any("runtime cleanup also failed" in n and "mark locked" in n
               for n in getattr(exc, "__notes__", ()))
    # the claim is kept: the plan id cannot be restarted
    with pytest.raises(PlanExecutionError, match="already started"):
        PlanExecutions(room.groups).begin(room.plan, owner=OWNER)
    assert not room.calls


def test_host_start_failure_after_runner_start_keeps_the_note(room, monkeypatch):
    def start_then_fail(self):
        self._runner.start()
        raise RuntimeError("injected settler failure")
    monkeypatch.setattr(GroupRuntime, "start", start_then_fail)
    monkeypatch.setattr(PlanExecutions, "mark",
                        lambda self, **kw: (_ for _ in ()).throw(sqlite3.OperationalError("mark locked")))

    async def run():
        with pytest.raises(RuntimeError, match="injected settler failure") as caught:
            room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        for _ in range(5):
            await asyncio.sleep(0)
        assert room.group.id not in room.host._runtimes
        return caught.value
    exc = asyncio.run(run())
    assert any("mark locked" in n for n in exc.__notes__)


def test_host_wraps_setup_failure_notes(room, monkeypatch):
    def broken(group):
        raise ValueError("injected allocation failure")
    room.host._factory = broken
    monkeypatch.setattr(PlanExecutions, "discard_unstarted",
                        lambda self, *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError("cleanup locked")))

    async def run():
        with pytest.raises(HostError, match="injected allocation failure") as caught:
            room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        assert any("cleanup locked" in n for n in caught.value.__notes__)
    asyncio.run(run())
