"""Regression tests for opt-in durable dispatch (docs/SWARM-DURABLE-DISPATCH.md): QA B1-B3 and the flag.

Same offline seam as ``test_group_plan_lifecycle_e2e`` (real CoreEngine members, RuleFake transport,
isolated EXO_HOME). Barriers are observable facts; the only real sleeps are the bounded durable-pause
poll windows asserted by B3.
"""

import asyncio
import sqlite3

import pytest

import exo.core.group_commands as gc
import exo.core.group_plan_execution as gpe
from exo.core.group_attempt_ledger import (
    AdmissionDenied, AttemptLedger, IdentityConflict, InvalidTransition, OwnershipError,
    RequestConflict, SchemaConflict, Scope, StaleFence, TaskKey)
from exo.core.group_host import HostError
from exo.core.group_supervision import TaskIdentity
import test_group_plan_lifecycle_e2e as e2e
from test_group_plan_lifecycle_e2e import BOUND, Rig, reach, run_scenario, settle_turns, task, until  # noqa: F401

# Reuse the e2e `home` fixture under the same name. Binding it by assignment (not `import home`) keeps
# pytest discovery and avoids ruff F811 on every test that takes `home` as an argument.
home = e2e.home


def start(rig, **kw):
    return rig.host.start(rig.gid, plan=rig.plan, grants=rig.grants, durable_attempts=True, **kw)


def ledger_of(rig):
    return rig.runtime()._ledger


def snap(rig, task_id):
    t = next(t for t in rig.plan.tasks if t.task_id == task_id)
    return ledger_of(rig).read(rig.runtime()._plan_adapter._key(t))


def tables(rig):
    return rig.host._groups.sessions._tx(lambda db: [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'swarm_supervision%'").fetchall()])


# -- flag: exact bool, plan only, off by default ---------------------------------------------------
def test_flag_requires_exact_bool_and_a_plan_and_writes_nothing(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        for bad in (1, "yes", None, 0):
            with pytest.raises(HostError, match="must be a bool"):
                rig.host.start(rig.gid, plan=rig.plan, grants=rig.grants, durable_attempts=bad)
        with pytest.raises(HostError, match="requires a plan"):
            rig.host.start(rig.gid, durable_attempts=True)
        assert rig.gid not in rig.host._runtimes
        assert tables(rig) == []
        with pytest.raises(Exception, match="no such plan"):
            rig.states()  # a refused start persisted no plan row

    run_scenario(home, tmp_path, monkeypatch, [task("b1", "builder")], body)


def test_flag_off_builds_no_ledger_and_no_tables(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        rig.start()
        assert rig.runtime()._ledger is None
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return tables(rig), rig.states()

    _, _, (tbl, states) = run_scenario(home, tmp_path, monkeypatch, [task("b1", "builder")], body)
    assert tbl == [] and states == {"b1": "completed"}


def test_durable_run_completes_in_order_with_one_dispatch_and_no_uncertain(home, tmp_path, monkeypatch):
    events = []

    async def body(rig, provider):
        start(rig)
        led = ledger_of(rig)
        for name in ("reserve_initial", "mark_dispatch", "mark_uncertain"):
            real = getattr(led, name)
            monkeypatch.setattr(led, name, lambda *a, _r=real, _n=name, **k: (events.append(_n), _r(*a, **k))[1])
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return snap(rig, "b1"), rig.states(), rig.plan_state()

    _, provider, (s, states, plan_state) = run_scenario(
        home, tmp_path, monkeypatch, [task("b1", "builder")], body)
    assert events == ["reserve_initial", "mark_dispatch"]  # mark_uncertain: 0 calls on a normal run
    assert provider.calls == ["b1"]
    assert states == {"b1": "completed"} and plan_state == "settled"
    assert s.latest_attempt == "dispatch_intent" and not s.reconciliation_required


# -- B1: control ids of the plan namespace never collide with a task literally named "plan" --------
def test_b1_task_named_plan_plan_and_member_controls_both_land(home, tmp_path, monkeypatch):
    tasks = [task("plan", "builder", goal="MARK-B1 go")]

    async def body(rig, provider):
        start(rig)
        await reach(provider, "plan")
        rt = rig.runtime()
        seen = []
        led = ledger_of(rig)
        real = led.set_control
        monkeypatch.setattr(led, "set_control", lambda *a, **k: (seen.append((k.get("task_id"), k["request_id"],
                            k["expected_revision"])), real(*a, **k))[1])
        for op in (rig.host.pause, rig.host.stop):  # both at expected revision 0, then 1
            op(rig.gid)  # plan level
            op(rig.gid, profile="builder")  # member level -> task "plan"
        assert rt.control_error is None
        s = snap(rig, "plan")
        provider.release("plan")
        return seen, s

    _, _, (seen, s) = run_scenario(home, tmp_path, monkeypatch, tasks, body, block={"plan"})
    seen = seen[:4]  # later entries are the harness teardown stop
    ids = [rid for _, rid, _ in seen]
    assert len(ids) == len(set(ids)) == 4  # four distinct request ids, no replay or conflict
    assert [r for t, _, r in seen if t is None] == [0, 1] and [r for t, _, r in seen if t == "plan"] == [0, 1]
    assert s.stop_requested and s.paused and s.plan_revision == 2 and s.task_revision == 2  # snap taken before teardown
    assert all(len(i) <= 512 for i in ids)


# -- B2: durable control failures are visible, local safe action always runs, retry clears -----------
FAILURES = {
    "stale_x8": lambda: StaleFence("control revision changed"),
    "sqlite": lambda: sqlite3.OperationalError("database is locked"),
    "schema": lambda: SchemaConflict("x"),
    "request": lambda: RequestConflict("x"),
    "ownership": lambda: OwnershipError("x"),
    "identity": lambda: IdentityConflict("x"),
    "transition": lambda: InvalidTransition("x"),
}


def failing(rig, monkeypatch, make, order=None):
    led = ledger_of(rig)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        if order is not None:
            order.append("set_control")
        raise make()

    monkeypatch.setattr(led, "set_control", boom)
    return led, calls


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_b2_stop_failure_is_visible_runner_still_cancelled_retry_clears(home, tmp_path, monkeypatch, name):
    async def body(rig, provider):
        start(rig)
        await reach(provider, "b1")
        led, calls = failing(rig, monkeypatch, FAILURES[name])
        with pytest.raises(HostError, match="NOT written"):
            rig.host.stop(rig.gid)
        if name == "stale_x8":
            assert len(calls) == gpe.CONTROL_MAX_LAPS
        assert rig.runtime().control_error is not None
        await until(lambda: rig.states()["b1"] == "stopped", "runner cancelled despite durable failure")
        # The real command must not report plain success.
        monkeypatch.setattr(gc, "_host", lambda eng: rig.host)
        with pytest.raises(gc.CommandFailed):
            gc._op_stop(None, {"id": rig.gid})
        del led.set_control  # healthy ledger again
        rig.host.stop(rig.gid)
        assert rig.runtime().control_error is None
        return snap(rig, "b1").stop_requested

    _, _, sticky = run_scenario(home, tmp_path, monkeypatch, [task("b1", "builder")], body, block={"b1"})
    assert sticky is True


@pytest.mark.parametrize("name", ["stale_x8", "sqlite", "request"])
def test_b2_pause_failure_visible_gate_still_cleared_and_resume_failure_stays_paused(
        home, tmp_path, monkeypatch, name):
    tasks = [task("b1", "builder"), task("b2", "builder")]

    async def body(rig, provider):
        start(rig)
        await reach(provider, "b1")
        led, _ = failing(rig, monkeypatch, FAILURES[name])
        with pytest.raises(HostError, match="NOT written"):
            rig.host.pause(rig.gid, profile="builder")
        provider.release("b1")
        await until(lambda: rig.states()["b1"] == "completed", "b1 done")
        await until(lambda: rig.member("builder") == "paused", "local pause applied despite failure")
        with pytest.raises(HostError, match="still paused"):  # durable unpause fails: stays paused
            rig.host.resume(rig.gid, profile="builder")
        await settle_turns()
        held = (list(provider.calls), rig.member("builder"))
        del led.set_control
        rig.host.resume(rig.gid, profile="builder")  # healthy retry of the failed op clears its error
        assert "resume" not in rig.runtime()._control_errors
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return held, rig.states()

    _, _, (held, states) = run_scenario(home, tmp_path, monkeypatch, tasks, body, block={"b1"})
    assert held == (["b1"], "paused") and states == {"b1": "completed", "b2": "completed"}


def test_b2_ordering_set_control_precedes_local_action(home, tmp_path, monkeypatch):
    order = []

    async def body(rig, provider):
        start(rig)
        await reach(provider, "b1")
        led = ledger_of(rig)
        real = led.set_control
        monkeypatch.setattr(led, "set_control", lambda *a, **k: (order.append("set_control"), real(*a, **k))[1])
        for name in ("pause", "resume", "stop"):
            base = getattr(gpe.GroupRuntime, name)
            monkeypatch.setattr(gpe.GroupRuntime, name,
                                lambda self, *a, _b=base, _n=name, **k: (order.append("local_" + _n), _b(self, *a, **k))[1])
        rig.host.pause(rig.gid)
        rig.host.resume(rig.gid)
        rig.host.stop(rig.gid)
        provider.release("b1")

    run_scenario(home, tmp_path, monkeypatch, [task("b1", "builder")], body, block={"b1"})
    assert order[:6] == ["set_control", "local_pause", "set_control", "local_resume", "set_control", "local_stop"]


def test_unknown_member_control_refused_before_any_ledger_write(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        start(rig)
        await reach(provider, "b1")
        led, calls = failing(rig, monkeypatch, FAILURES["sqlite"])
        for op in (rig.host.pause, rig.host.resume, rig.host.stop):
            with pytest.raises(HostError, match="unknown member"):
                op(rig.gid, profile="ghost")
        assert calls == []
        del led.set_control
        provider.release("b1")

    run_scenario(home, tmp_path, monkeypatch, [task("b1", "builder")], body, block={"b1"})


# -- B3: a task durably paused after reserve waits (awaited, bounded), keeps its fence --------------
def external_pause_after_reserve(rig, monkeypatch, task_id):
    led = ledger_of(rig)
    counts = {"reserve": 0, "dispatch": 0}
    real_reserve, real_dispatch = led.reserve_initial, led.mark_dispatch

    def reserve(key, **k):
        out = real_reserve(key, **k)
        if key.identity.task_id == task_id and counts["reserve"] == 0:
            s = led.read(key)
            led.set_control(rig.runtime()._plan_adapter._scope(), task_id=task_id,
                            expected_revision=s.task_revision, paused=True, request_id="ext-pause")
        counts["reserve"] += key.identity.task_id == task_id
        return out

    def dispatch(fence, **k):
        counts["dispatch"] += fence.key.identity.task_id == task_id
        return real_dispatch(fence, **k)

    monkeypatch.setattr(led, "reserve_initial", reserve)
    monkeypatch.setattr(led, "mark_dispatch", dispatch)
    return led, counts


def test_b3_paused_after_reserve_is_bounded_other_member_progresses_resume_same_fence(
        home, tmp_path, monkeypatch):
    tasks = [task("b1", "builder"), task("c1", "checker")]

    async def body(rig, provider):
        start(rig)
        led, counts = external_pause_after_reserve(rig, monkeypatch, "b1")
        await until(lambda: rig.states().get("c1") == "completed", "other member progressed on the loop")
        await asyncio.sleep(1.0)
        held = (dict(counts), list(provider.calls), rig.states()["b1"])
        s = snap(rig, "b1")
        led.set_control(rig.runtime()._plan_adapter._scope(), task_id="b1", expected_revision=s.task_revision,
                        paused=False, request_id="ext-resume")
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return held, counts, rig.states()

    _, provider, (held, counts, states) = run_scenario(home, tmp_path, monkeypatch, tasks, body)
    assert held[0]["reserve"] == 1 and 1 <= held[0]["dispatch"] <= 30  # bounded poll, no spin
    assert held[1] == ["c1"] and held[2] == "pending"  # b1 never reached the provider while paused
    assert counts["reserve"] == 1  # the same fence was reused after resume
    assert states == {"b1": "completed", "c1": "completed"}


def test_b3_stop_during_durable_pause_wait_exits_stopped_without_a_turn(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        start(rig)
        led, counts = external_pause_after_reserve(rig, monkeypatch, "b1")
        await until(lambda: counts["dispatch"] >= 1, "b1 parked in the durable pause wait")
        rig.host.stop(rig.gid, profile="builder")
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return rig.states()

    _, provider, states = run_scenario(home, tmp_path, monkeypatch, [task("b1", "builder")], body)
    assert provider.calls == [] and states == {"b1": "stopped"}


# -- failure guarantees promoted from independent QA probes P1, P2, P4, P5, P6 ---------------------
def boom_start(self):
    raise RuntimeError("boom-start")


def test_startup_failure_after_ledger_init_fails_rows_and_burns_plan_id(home, tmp_path, monkeypatch):
    tasks = [task("b1", "builder"), task("c1", "checker")]

    async def body(rig, provider):
        with monkeypatch.context() as m:
            m.setattr(gpe._PlanRuntime, "start", boom_start)
            with pytest.raises(RuntimeError, match="boom-start"):
                start(rig)
        first = (rig.states(), rig.plan_state())
        t = rig.plan.tasks[0]
        exec_id = gpe.PlanExecutions(rig.host._groups).execution_id(
            rig.plan.plan_id, group_id=rig.gid, owner=rig.owner)
        scope = Scope(rig.gid, rig.owner, rig.plan.plan_id, exec_id)
        row = AttemptLedger(rig.host._groups).read(
            TaskKey(scope, TaskIdentity(rig.plan.plan_id, t.task_id, t.profile)))
        refused = []
        for durable in (True, False):
            with pytest.raises(HostError, match="plan already started") as ei:
                start(rig) if durable else rig.start()
            refused.append(str(ei.value))
        return first, row.latest_attempt, tables(rig), refused

    _, _, (first, latest, tbl, refused) = run_scenario(home, tmp_path, monkeypatch, tasks, body)
    assert first == ({"b1": "failed", "c1": "failed"}, "settled")
    assert latest is None  # reserved but never dispatched
    assert "swarm_supervision_attempts" in tbl and len(refused) == 2


def test_startup_cleanup_tries_every_unstarted_task_and_keeps_primary_error(home, tmp_path, monkeypatch):
    tasks = [task("b1", "builder"), task("b2", "builder"), task("c1", "checker")]

    async def body(rig, provider):
        real, failed_marks = gpe.PlanExecutions.mark, []

        def mark(self, **k):
            if k["state"] == "failed":
                failed_marks.append(k["task_id"])
                if k["task_id"] == "b2":
                    raise sqlite3.OperationalError("locked")
            return real(self, **k)

        with monkeypatch.context() as m:
            m.setattr(gpe.PlanExecutions, "mark", mark)
            m.setattr(gpe._PlanRuntime, "start", boom_start)
            with pytest.raises(RuntimeError, match="boom-start") as ei:
                start(rig)
        return rig.states(), rig.plan_state(), failed_marks, getattr(ei.value, "__notes__", [])

    _, _, (states, plan, failed_marks, notes) = run_scenario(home, tmp_path, monkeypatch, tasks, body)
    assert failed_marks == ["b1", "b2", "c1"]  # b2's failure did not stop c1
    assert states == {"b1": "failed", "b2": "pending", "c1": "failed"} and plan == "running"
    assert any("durable startup cleanup also failed (plan id stays blocked)" in n for n in notes)


def test_failed_terminal_commit_is_never_announced_as_completed(home, tmp_path, monkeypatch):
    tasks = [task("b1", "builder"), task("c1", "checker", depends_on=["b1"])]

    async def body(rig, provider):
        events, real_mark, real_settle = [], gpe.PlanExecutions.mark, gpe.PlanGate.settle

        def mark(self, **k):
            events.append(("mark", k["task_id"], k["state"]))
            if k["task_id"] == "b1" and k["state"] == "completed":
                raise sqlite3.OperationalError("locked-mark")
            return real_mark(self, **k)

        def settle(self, tid, st):
            events.append(("settle", tid, st))
            return real_settle(self, tid, st)

        monkeypatch.setattr(gpe.PlanExecutions, "mark", mark)
        monkeypatch.setattr(gpe.PlanGate, "settle", settle)
        start(rig)
        try:
            await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        except Exception:  # wait semantics are not under test; the commit order is
            pass
        return events, rig.states(), rig.plan_state()

    _, _, (events, states, plan) = run_scenario(home, tmp_path, monkeypatch, tasks, body)
    assert ("settle", "b1", "completed") not in events
    assert events.index(("mark", "b1", "completed")) < events.index(("settle", "b1", "failed"))
    assert ("mark", "c1", "failed") in events
    # the failed commit leaves the row running: operator-visible, not silently completed
    assert states == {"b1": "running", "c1": "failed"} and plan == "running"


def run_hooked(home, tmp_path, monkeypatch, tasks, hook):
    """Run a plan with every ledger dispatch call and engine turn routed through ``hook(name, real, ...)``."""
    async def body(rig, provider):
        events, engine_calls, real_mark = [], [], gpe.PlanExecutions.mark

        def mark(self, **k):
            events.append(("mark", k["task_id"], k["state"]))
            return real_mark(self, **k)

        monkeypatch.setattr(gpe.PlanExecutions, "mark", mark)
        for name in ("mark_uncertain", "mark_dispatch", "reserve_initial"):
            def wrap(name=name, real=getattr(AttemptLedger, name)):
                def f(self, *a, **k):
                    events.append((name,))
                    return hook(name, real, self, *a, **k)
                return f
            monkeypatch.setattr(AttemptLedger, name, wrap())
        start(rig)
        for profile, eng in rig.runtime()._plan_adapter.binding.engines.items():
            def wrap_turn(orig=eng.run_turn, profile=profile):
                async def run_turn(send):
                    engine_calls.append(profile)
                    return await hook("run_turn", orig, None, send)
                return run_turn
            eng.run_turn = wrap_turn()
        try:
            await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        except Exception:  # wait semantics are not under test; the call sequence is
            pass
        return events, engine_calls, rig.states(), rig.plan_state()

    return run_scenario(home, tmp_path, monkeypatch, tasks, body)[2]


def test_turn_exception_marks_uncertain_once_and_a_failing_mark_does_not_mask_failed(
        home, tmp_path, monkeypatch):
    def hook(name, real, self, *a, **k):
        if name == "run_turn":
            async def raising():
                raise RuntimeError("engine boom")
            return raising()
        if name == "mark_uncertain":
            raise StaleFence("fence moved")
        return real(self, *a, **k)

    events, calls, states, plan = run_hooked(home, tmp_path, monkeypatch, [task("b1", "builder")], hook)
    assert calls == ["builder"] and states == {"b1": "failed"} and plan == "settled"
    assert events.count(("mark_uncertain",)) == 1
    assert events.index(("mark_uncertain",)) < events.index(("mark", "b1", "failed"))


@pytest.mark.parametrize("mode", ["false", "denied", "running_mark_raises"])
def test_no_engine_turn_when_dispatch_intent_is_not_recorded(home, tmp_path, monkeypatch, mode):
    def hook(name, real, self, *a, **k):
        if name == "run_turn":
            raise AssertionError("engine must not run")
        if name == "mark_dispatch" and mode == "false":
            real(self, *a, **k)
            return False
        if name == "mark_dispatch" and mode == "denied":
            raise AdmissionDenied("legacy dispatcher touched task")
        return real(self, *a, **k)

    if mode == "running_mark_raises":
        real_mark = gpe.PlanExecutions.mark

        def mark(self, **k):
            if k["state"] == "running":
                raise sqlite3.OperationalError("locked-running")
            return real_mark(self, **k)

        monkeypatch.setattr(gpe.PlanExecutions, "mark", mark)
    events, calls, states, plan = run_hooked(home, tmp_path, monkeypatch, [task("b1", "builder")], hook)
    assert calls == [] and ("mark_uncertain",) not in events
    assert states == {"b1": "failed"} and plan == "settled"
