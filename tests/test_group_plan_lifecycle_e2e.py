"""Plan lifecycle on REAL CoreEngine members: pause between tasks, the turn-concurrency bound, interrupt.

Same seam as ``test_group_plan_real_engine_e2e``: ``group_launch.create`` -> ``GroupHost.start(plan=, grants=)``
with real engines, router, loop, runtime, plan executor and SQLite rows, an isolated ``EXO_HOME`` and the
offline ``RuleFake`` provider; the only patch is the provider TRANSPORT (``RuleFake.stream``), which records
each call by task id, counts overlapping calls and can hold a call open on a per-task ``asyncio.Event``.
One event loop per scenario, ``launch.aclose()`` always runs (also when a body assertion fails), and every
barrier is an observable fact (provider reached, durable row state, member status), never a timing sleep.
"""

import asyncio
import json
import re

import pytest

from exo.core.agents.route_config import RouteConfig
from exo.core.group_plan_execution import PlanExecutions
from exo.core.group_task_contract import SCHEMA_VERSION, HostGrants, validate_plan
from exo.core.models.fake import RuleFake
from exo.group_launch import create

BOUND = 10  # seconds; a deadline for a hang, never a pacing sleep
PROFILES = ["builder", "checker"]
TASK_IDS = ("b1", "b2", "c1", "c2")
MODELS = {
    "fake": {
        "rules": [
            {"match": f"MARK-{t.upper()}", "steps": [f"{t.upper()}-REPLY"]}
            for t in TASK_IDS
        ]
    },
    "roles": {"main": ["fake:x"]},
}


def task(task_id, profile, **extra):
    return {
        "task_id": task_id,
        "role": "builder",
        "goal": f"MARK-{task_id.upper()} do it",
        "profile": profile,
        "acceptance": ["done"],
        **extra,
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    for name in (*PROFILES, "owner"):
        (home / "seats" / f"{name}.toml").write_text(
            f'name = "{name}"\n', encoding="utf-8"
        )
    (home / "config.json").write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    monkeypatch.setenv("EXO_HOME", str(home))
    return home


class Provider:
    """Records provider calls by task id; ``block`` holds named tasks' calls open until ``release``."""

    def __init__(self, monkeypatch, block=()):
        self.block = set(block)
        self.calls = []  # task id per provider call, in arrival order
        self.active = 0  # provider calls currently in flight
        self.max_active = 0
        self._reached, self._gates = {}, {}
        original = RuleFake.stream
        provider = self

        async def stream(this, messages, tools, **opts):
            text = next((m.text() for m in reversed(messages) if m.role == "user"), "")
            found = re.search(r'"task_id": "(\w+)"', text)
            task_id = found.group(1) if found else "?"
            provider.calls.append(task_id)
            provider.active += 1
            provider.max_active = max(provider.max_active, provider.active)
            try:
                provider.reached(task_id).set()
                if task_id in provider.block:
                    await provider.gate(task_id).wait()
                async for event in original(this, messages, tools, **opts):
                    yield event
            finally:
                provider.active -= 1

        monkeypatch.setattr(RuleFake, "stream", stream)

    def reached(self, task_id):
        return self._reached.setdefault(task_id, asyncio.Event())

    def gate(self, task_id):
        return self._gates.setdefault(task_id, asyncio.Event())

    def release(self, *task_ids):
        for task_id in task_ids:
            self.gate(task_id).set()


class Rig:
    def __init__(self, tmp_path, tasks, max_concurrent=None):
        self.launch = create(seat="owner", profiles=PROFILES, mode="socratic")
        self.gid, self.owner, self.host = (
            self.launch.group.id,
            self.launch.group.owner,
            self.launch.host,
        )
        root = str(tmp_path / "w")
        self.grants = HostGrants(
            owner=self.owner,
            profiles=frozenset(PROFILES),
            permits={"builder": frozenset({"read_repo", "write_scope"})},
            write_roots={"builder": (root,), "checker": (root,)},
            routes=RouteConfig.of(
                {"backend": "model_map", "routes": {"cheap": "fake:fast"}}
            ),
            reviewer_profiles=frozenset({"checker"}),
        )
        self.plan = validate_plan(
            {"schema": SCHEMA_VERSION, "tasks": tasks},
            self.grants,
            group=self.launch.group,
        )
        self.max_concurrent = max_concurrent

    def start(self):
        return self.host.start(
            self.gid,
            plan=self.plan,
            grants=self.grants,
            max_concurrent=self.max_concurrent,
        )

    def rows(self):
        return {
            r.task_id: r
            for r in PlanExecutions(self.host._groups).tasks(
                self.plan.plan_id, group_id=self.gid, owner=self.owner
            )
        }

    def states(self):
        return {t: r.state for t, r in self.rows().items()}

    def plan_state(self):
        return PlanExecutions(self.host._groups).plan_state(
            self.plan.plan_id, group_id=self.gid, owner=self.owner
        )

    def member(self, profile):
        return {m.profile: m.state for m in self.host.status(self.gid)["members"]}[
            profile
        ]

    def runtime(self):
        return self.host._runtimes[self.gid]


async def until(pred, what):
    """Yield to the loop (no timer) until ``pred`` holds; the deadline only bounds a hang."""

    async def spin():
        while not pred():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(spin(), BOUND)
    except asyncio.TimeoutError:
        raise AssertionError(f"never reached: {what}") from None


async def reach(provider, task_id):
    try:
        await asyncio.wait_for(provider.reached(task_id).wait(), BOUND)
    except asyncio.TimeoutError:
        raise AssertionError(f"provider never reached {task_id}") from None


async def settle_turns(count=100):
    for _ in range(count):
        await asyncio.sleep(0)


def run_scenario(
    home, tmp_path, monkeypatch, tasks, body, *, block=(), max_concurrent=None
):
    """One loop, guaranteed ``launch.aclose`` (releasing held provider calls first); ``body(rig, provider)``."""
    provider = Provider(monkeypatch, block)
    rig = Rig(tmp_path, tasks, max_concurrent)

    async def go():
        try:
            return await asyncio.wait_for(body(rig, provider), BOUND * 2)
        finally:
            provider.block.clear()
            provider.release(*TASK_IDS)
            await asyncio.wait_for(rig.launch.aclose(), BOUND)

    out = asyncio.run(go())
    return rig, provider, out


def test_pause_mid_run_holds_next_task_from_provider_until_resume(
    home, tmp_path, monkeypatch
):
    tasks = [task("b1", "builder"), task("b2", "builder")]

    async def body(rig, provider):
        rig.start()
        await reach(provider, "b1")  # builder is inside its first real provider call
        assert rig.states() == {"b1": "running", "b2": "pending"}
        rig.host.pause(
            rig.gid, profile="builder"
        )  # paused while the first task is running
        provider.release("b1")
        await until(lambda: rig.states()["b1"] == "completed", "b1 completed")
        await until(
            lambda: rig.member("builder") == "paused",
            "builder parked at its checkpoint",
        )
        await settle_turns()
        held = (list(provider.calls), rig.states(), rig.member("builder"))
        rig.host.resume(rig.gid, profile="builder")
        statuses = await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return held, statuses, rig.states(), rig.plan_state()

    rig, provider, (held, statuses, final, plan_state) = run_scenario(
        home, tmp_path, monkeypatch, tasks, body, block={"b1"}
    )
    calls, states, member = held
    assert calls == ["b1"]  # b2 never reached the provider while paused
    assert states == {"b1": "completed", "b2": "pending"}
    assert member == "paused"
    assert provider.calls == ["b1", "b2"] and provider.max_active == 1
    assert final == {"b1": "completed", "b2": "completed"} and plan_state == "settled"
    assert {s.profile: s.state for s in statuses}["builder"] == "completed"
    assert rig.launch.members == {} and rig.launch.closed


@pytest.mark.parametrize("bound, overlap", [(1, 1), (None, 2)])
def test_max_concurrent_bounds_provider_overlap_and_blocks_second_task(
    home, tmp_path, monkeypatch, bound, overlap
):
    tasks = [task("b1", "builder"), task("c1", "checker")]

    async def body(rig, provider):
        rig.start()
        if bound is None:  # control: with no bound both calls overlap
            await reach(provider, "b1")
            await reach(provider, "c1")
            return list(provider.calls), provider.max_active, rig.states(), None
        await until(lambda: provider.calls, "first provider call")
        first = provider.calls[0]
        other = "c1" if first == "b1" else "b1"
        other_profile = "checker" if other == "c1" else "builder"
        await until(
            lambda: rig.member(other_profile) == "queued",
            "second member queued for a turn slot",
        )
        await settle_turns()
        blocked = (
            list(provider.calls),
            provider.max_active,
            rig.states(),
            rig.member(other_profile),
        )
        provider.release(first)
        await reach(
            provider, other
        )  # the slot frees: only now may the second call start
        provider.release(other)
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return blocked, first, other

    rig, provider, out = run_scenario(
        home,
        tmp_path,
        monkeypatch,
        tasks,
        body,
        block={"b1", "c1"},
        max_concurrent=bound,
    )
    if bound is None:
        calls, peak, states, _ = out
        assert sorted(calls) == ["b1", "c1"] and peak == overlap
        return
    (calls, peak, states, queued), first, other = out
    assert calls == [first] and peak == 1  # second task is held back, not just late
    assert (
        states[first] == "running" and states[other] == "pending"
    )  # running only once a slot is granted
    assert queued == "queued"
    assert provider.calls == [first, other] and provider.max_active == overlap
    assert (
        rig.states() == {"b1": "completed", "c1": "completed"}
        and rig.plan_state() == "settled"
    )


def test_pause_while_queued_for_turn_slot_never_reaches_provider_until_resume(
    home, tmp_path, monkeypatch
):
    tasks = [task("b1", "builder"), task("c1", "checker")]

    async def body(rig, provider):
        rig.start()
        await until(lambda: provider.calls, "first provider call")
        first = provider.calls[0]
        other = "c1" if first == "b1" else "b1"
        profile = "checker" if other == "c1" else "builder"
        await until(
            lambda: rig.member(profile) == "queued",
            "second member waiting for the slot",
        )
        rig.host.pause(
            rig.gid, profile=profile
        )  # paused while queued: no permit is held yet
        provider.release(first)  # the slot frees; the paused waiter acquires it
        await until(lambda: rig.states()[first] == "completed", "first task completed")
        await until(
            lambda: rig.member(profile) == "paused",
            "queued member parked after the recheck",
        )
        await settle_turns()
        held = (
            list(provider.calls),
            rig.states(),
            rig.runtime().turn_denied(profile),
            rig.runtime()._turn_slots.locked(),
        )
        provider.release(other)
        rig.host.resume(rig.gid, profile=profile)
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return held, first, other

    rig, provider, (held, first, other) = run_scenario(
        home, tmp_path, monkeypatch, tasks, body, block={"b1", "c1"}, max_concurrent=1
    )
    calls, states, denied, slot_held = held
    assert calls == [first]  # the refused turn never reached the provider
    assert states == {first: "completed", other: "pending"}
    assert denied and not slot_held  # permit was acquired, refused, and handed back
    assert provider.calls == [first, other] and provider.max_active == 1
    assert (
        rig.states() == {"b1": "completed", "c1": "completed"}
        and rig.plan_state() == "settled"
    )


def test_loop_interrupt_settles_task_stopped_and_closes_dependents_without_leaking_slot(
    home, tmp_path, monkeypatch
):
    # b1 is interrupted; b2 is behind it on the same member; c1 depends on b1; c2 is independent.
    tasks = [
        task("b1", "builder"),
        task("b2", "builder"),
        task("c1", "checker", depends_on=["b1"]),
        task("c2", "checker"),
    ]

    async def body(rig, provider):
        rig.start()
        await reach(
            provider, "b1"
        )  # inside the real provider call, holding the only slot
        session = next(
            m.session for m in rig.launch.group.members if m.profile == "builder"
        )
        engine = rig.launch.members["builder"]
        assert rig.states()["b1"] == "running"
        assert (
            engine.loop.interrupt(session) is True
        )  # the real Loop.interrupt path (no gate release)
        statuses = await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return (
            statuses,
            rig.rows(),
            rig.plan_state(),
            rig.host.status(rig.gid)["receipt"],
            rig.runtime(),
        )

    rig, provider, (statuses, rows, plan_state, receipt, runtime) = run_scenario(
        home, tmp_path, monkeypatch, tasks, body, block={"b1"}, max_concurrent=1
    )
    assert rows["b1"].state == "stopped" and rows["b1"].result is None
    assert (
        rows["b2"].state == "stopped" and rows["b2"].started is None
    )  # same member: cancelled, no turn
    assert rows["c1"].state == "failed" and rows["c1"].error == "dependency b1 stopped"
    assert rows["c1"].started is None
    assert (
        rows["c2"].state == "completed" and rows["c2"].result == "C2-REPLY"
    )  # slot was released
    assert provider.calls == ["b1", "c2"] and provider.max_active == 1
    assert plan_state == "settled" and receipt.state == "settled"
    final = {s.profile: s.state for s in statuses}
    assert final["builder"] == "stopped"
    assert not runtime._turn_slots.locked()


def test_pause_while_waiting_on_dependency_holds_dependency_failure_until_resume(
    home, tmp_path, monkeypatch
):
    # c1 (checker) waits on b1 (builder). The checker is paused while parked on that dependency; b1 is then
    # interrupted. The paused checker must not begin/settle its next task (here: close it as failed on the
    # stopped dependency) until resumed: its row stays pending, the plan stays open, no provider turn starts.
    tasks = [task("b1", "builder"), task("c1", "checker", depends_on=["b1"])]

    async def body(rig, provider):
        rig.start()
        await reach(
            provider, "b1"
        )  # b1 in the provider; c1 is waiting on its dependency
        await until(
            lambda: rig.states() == {"b1": "running", "c1": "pending"},
            "b1 running, c1 pending",
        )
        rig.host.pause(
            rig.gid, profile="checker"
        )  # paused while parked on the dependency wait
        session = next(
            m.session for m in rig.launch.group.members if m.profile == "builder"
        )
        assert rig.launch.members["builder"].loop.interrupt(session) is True
        await until(lambda: rig.states()["b1"] == "stopped", "b1 stopped")
        await until(
            lambda: rig.member("checker") == "paused",
            "checker parked at its checkpoint",
        )
        await settle_turns()
        held = (
            rig.states(),
            rig.plan_state(),
            rig.member("checker"),
            list(provider.calls),
            rig.rows()["c1"].error,
        )
        rig.host.resume(rig.gid, profile="checker")
        statuses = await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return held, statuses, rig.rows(), rig.plan_state()

    rig, provider, (held, statuses, rows, plan_state) = run_scenario(
        home, tmp_path, monkeypatch, tasks, body, block={"b1"}
    )
    states, held_plan, member, calls, error = held
    assert states == {
        "b1": "stopped",
        "c1": "pending",
    }  # c1 not finalized while its member is paused
    assert error is None and member == "paused" and held_plan != "settled"
    assert calls == ["b1"]  # and no turn was begun for it
    assert rows["c1"].state == "failed" and rows["c1"].error == "dependency b1 stopped"
    assert rows["c1"].started is None and provider.calls == ["b1"]
    assert (
        plan_state == "settled"
        and {s.profile: s.state for s in statuses}["checker"] == "failed"
    )


def test_pause_between_tasks_reports_paused_while_next_dependency_unresolved(
    home, tmp_path, monkeypatch
):
    # builder: b1 then b2 (b2 depends on checker's c1). Builder is paused during b1; b1 is released while c1 is
    # still held. The builder finishes b1 and must park ("paused", not "pausing") at the checkpoint BEFORE it
    # waits on b2's unresolved dependency, and must not begin b2 after c1 completes until resumed.
    tasks = [
        task("b1", "builder"),
        task("c1", "checker"),
        task("b2", "builder", depends_on=["c1"]),
    ]

    async def body(rig, provider):
        rig.start()
        await reach(provider, "b1")
        await reach(
            provider, "c1"
        )  # both held open: c1 (and so b2's dependency) unresolved
        rig.host.pause(rig.gid, profile="builder")  # paused while b1 is running
        provider.release("b1")
        await until(lambda: rig.states()["b1"] == "completed", "b1 completed")
        await until(
            lambda: rig.member("builder") == "paused",
            "builder parked at its checkpoint while c1 is unresolved",
        )
        await settle_turns()
        held = (
            rig.states(),
            rig.member("builder"),
            list(provider.calls),
        )
        provider.release(
            "c1"
        )  # dependency resolves; the paused builder still must not start b2
        await until(lambda: rig.states()["c1"] == "completed", "c1 completed")
        await settle_turns()
        after_dep = (rig.states(), rig.member("builder"), list(provider.calls))
        rig.host.resume(rig.gid, profile="builder")
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return held, after_dep

    rig, provider, (held, after_dep) = run_scenario(
        home, tmp_path, monkeypatch, tasks, body, block={"b1", "c1"}
    )
    states, member, calls = held
    assert states == {"b1": "completed", "c1": "running", "b2": "pending"}
    assert member == "paused"
    assert sorted(calls) == ["b1", "c1"]
    states, member, calls = after_dep
    assert states == {"b1": "completed", "c1": "completed", "b2": "pending"}
    assert member == "paused" and "b2" not in calls
    assert provider.calls.count("b2") == 1
    assert (
        rig.states() == {"b1": "completed", "c1": "completed", "b2": "completed"}
        and rig.plan_state() == "settled"
    )
