"""Plan execution through the EXISTING GroupHost/GroupRuntime path. Fakes only; one loop per scenario."""
import asyncio
import dataclasses
from types import SimpleNamespace

import pytest

from exo.core.agents.route_config import RouteConfig
from exo.core.group_host import GroupHost, HostError
from exo.core.group_plan_execution import (PLAN_PREFIX, TRUNCATED, PlanExecutionError, PlanExecutions,
                                          _bound, plan_digest)
from exo.core.group_task_contract import SCHEMA_VERSION, HostGrants, validate_plan
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore

OWNER = "host"
PROFILES = ["builder", "checker"]


def result(text="built", stop="end", error=None):
    return SimpleNamespace(stop_reason=stop, error=error, message=SimpleNamespace(text=lambda: text))


def make_grants(**over):
    base = dict(owner=OWNER, profiles=frozenset(PROFILES),
                permits={"builder": frozenset({"read_repo", "write_scope"})},
                write_roots={"builder": ("/srv/w",), "checker": ("/srv/w",)},
                routes=RouteConfig.of({"backend": "model_map", "routes": {"cheap": "fake:fast"}}),
                reviewer_profiles=frozenset({"checker"}))
    base.update(over)
    return HostGrants(**base)


RAW = {"schema": SCHEMA_VERSION, "review_of": {"build": "check"}, "tasks": [
    {"task_id": "build", "role": "builder", "goal": "write it", "profile": "builder",
     "acceptance": ["tests pass"]},
    {"task_id": "check", "role": "reviewer", "goal": "review it", "profile": "checker",
     "acceptance": ["findings"], "depends_on": ["build"],
     "outputs": [{"kind": "message", "ref": "m-1"}]}]}


class Room:
    def __init__(self):
        self.store = SessionStore()
        self.groups = GroupStore(self.store)
        self.calls = []       # (profile, prompt) per engine turn
        self.factory_calls = 0
        self.replies = {}
        self.block = {}       # profile -> asyncio.Event the engine turn waits on

        def factory(group):
            self.factory_calls += 1

            def engine(profile):
                async def run_turn(command):
                    self.calls.append((profile, command.text))
                    if profile in self.block:
                        await self.block[profile].wait()
                    r = self.replies.get(profile, result(f"{profile} done"))
                    return r() if callable(r) else r
                return SimpleNamespace(agent=profile, store=self.store, run_turn=run_turn)
            return {m.profile: engine(m.profile) for m in group.members}

        self.factory = factory
        self.host = GroupHost(self.groups, owner=OWNER, known_profiles=PROFILES, engine_factory=factory)
        self.group = self.host.create(mode="socratic", members=PROFILES)
        self.grants = make_grants()
        self.plan = validate_plan(RAW, self.grants, group=self.group)

    def rows(self, plan=None):
        plan = plan or self.plan
        return {r.task_id: r for r in PlanExecutions(self.groups).tasks(
            plan.plan_id, group_id=self.group.id, owner=OWNER)}


@pytest.fixture
def room():
    r = Room()
    yield r
    r.store.close()


def test_plan_runs_in_order_and_hands_results_as_quoted_data(room):
    async def go():
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        await room.host.wait(room.group.id)
    asyncio.run(go())
    assert [p for p, _ in room.calls] == ["builder", "checker"]
    assert all(text.startswith(PLAN_PREFIX) for _, text in room.calls)
    assert "builder done" in room.calls[1][1]          # reviewed result is carried as data
    rows = room.rows()
    assert {t: r.state for t, r in rows.items()} == {"build": "completed", "check": "completed"}
    assert PlanExecutions(room.groups).plan_state(
        room.plan.plan_id, group_id=room.group.id, owner=OWNER) == "settled"


def test_failed_task_fails_dependent_closed_without_a_turn(room):
    room.replies["builder"] = result(stop="error", error="boom")

    async def go():
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        await room.host.wait(room.group.id)
    asyncio.run(go())
    assert [p for p, _ in room.calls] == ["builder"]
    rows = room.rows()
    assert rows["build"].state == "failed" and "boom" not in (rows["build"].error or "")
    assert rows["check"].state == "failed" and rows["check"].error == "dependency build failed"


@pytest.mark.parametrize("case", ["owner", "digest", "profiles", "grants_only", "wrong_group", "conversation"])
def test_authority_refused_before_factory_and_claim(room, case):
    kw = dict(plan=room.plan, grants=room.grants)
    gid = room.group.id
    if case == "owner":
        kw["grants"] = make_grants(owner="mallory")
    elif case == "digest":
        kw["plan"] = dataclasses.replace(room.plan, plan_id="0" * 64)
    elif case == "profiles":
        kw["grants"] = make_grants(profiles=frozenset({*PROFILES, "extra"}))
    elif case == "grants_only":
        kw = dict(grants=room.grants)
    elif case == "wrong_group":
        kw["plan"] = dataclasses.replace(room.plan, group_id="other")
    elif case == "conversation":
        kw["audiences"] = {"builder": ["checker"], "checker": ["builder"]}
    with pytest.raises(HostError):
        room.host.start(gid, **kw)
    assert room.factory_calls == 0 and room.calls == []
    # no claim and no plan row were left: a correct start still works
    async def go():
        room.host.start(gid, plan=room.plan, grants=room.grants)
        await room.host.wait(gid)
    asyncio.run(go())
    assert room.factory_calls == 1 and len(room.calls) == 2


def test_tampered_task_beyond_grants_refused_before_factory(room):
    wide = dataclasses.replace(room.plan.tasks[0], write_scopes=("/etc",))
    tampered = dataclasses.replace(room.plan, tasks=(wide, room.plan.tasks[1]))
    tampered = dataclasses.replace(tampered, plan_id=plan_digest(tampered))  # identity is not authority
    with pytest.raises(HostError, match="write scope"):
        room.host.start(room.group.id, plan=tampered, grants=room.grants)
    assert room.factory_calls == 0


def test_second_start_of_same_plan_is_refused(room):
    async def go():
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        with pytest.raises(HostError):
            room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        await room.host.wait(room.group.id)
    asyncio.run(go())
    assert len(room.calls) == 2


def test_pause_holds_a_member_before_its_next_task_until_resumed(room):
    """Pausing a member stops it from BEGINNING a task (no engine turn); resume lets it proceed."""
    async def go():
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        room.host.pause(room.group.id, profile="checker")
        for _ in range(50):
            await asyncio.sleep(0)
        held = (list(room.calls), {t: r.state for t, r in room.rows().items()})
        room.host.resume(room.group.id, profile="checker")
        await asyncio.wait_for(room.host.wait(room.group.id), 5)
        return held
    held_calls, held_rows = asyncio.run(go())
    assert [p for p, _ in held_calls] == ["builder"]
    assert held_rows == {"build": "completed", "check": "pending"}
    assert [p for p, _ in room.calls] == ["builder", "checker"]
    assert {t: r.state for t, r in room.rows().items()} == {"build": "completed", "check": "completed"}


def test_duplicate_plan_refused_before_factory_by_a_fresh_host(room):
    async def go():
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        await room.host.wait(room.group.id)
    asyncio.run(go())
    assert room.factory_calls == 1
    other = GroupHost(room.groups, owner=OWNER, known_profiles=PROFILES, engine_factory=room.factory)

    async def again():
        with pytest.raises(HostError, match="already started"):
            other.start(room.group.id, plan=room.plan, grants=room.grants)
    asyncio.run(again())
    assert room.factory_calls == 1 and len(room.calls) == 2   # no engines built for a duplicate
    # the transactional race guard inside begin() still refuses on its own
    with pytest.raises(PlanExecutionError, match="already started"):
        PlanExecutions(room.groups).begin(room.plan, owner=OWNER)


def test_invalid_plan_refusal_creates_no_plan_schema(room):
    with pytest.raises(HostError):
        room.host.start(room.group.id, plan=room.plan, grants=make_grants(owner="mallory"))
    tables = {r[0] for r in room.store._q("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {"swarm_plans", "swarm_plan_tasks"} & tables
    assert PlanExecutions(room.groups).started(room.plan.plan_id, group_id=room.group.id, owner=OWNER) is None


async def _until(cond):
    for _ in range(200):
        if cond():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never held")


def test_stop_while_waiting_on_dependency_settles_stopped_without_a_turn(room):
    """Real GroupRuntime.stop cancels the member task blocked in the dependency wait."""
    async def go():
        room.block["builder"] = asyncio.Event()
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        await _until(lambda: room.calls)                 # builder's turn is in flight, checker waits
        room.host.stop(room.group.id, profile="checker")
        room.block["builder"].set()                      # dependency becomes ready after the stop
        return await asyncio.wait_for(room.host.wait(room.group.id), 5)
    statuses = asyncio.run(go())
    assert [p for p, _ in room.calls] == ["builder"]
    assert {t: r.state for t, r in room.rows().items()} == {"build": "completed", "check": "stopped"}
    assert {st.profile: st.state for st in statuses} == {"builder": "completed", "checker": "stopped"}
    assert PlanExecutions(room.groups).plan_state(
        room.plan.plan_id, group_id=room.group.id, owner=OWNER) == "settled"


def test_stopped_builder_fails_reviewer_dependency_closed(room):
    async def go():
        room.block["builder"] = asyncio.Event()
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        await _until(lambda: room.calls)
        room.host.stop(room.group.id, profile="builder")
        await asyncio.wait_for(room.host.wait(room.group.id), 5)
    asyncio.run(go())
    assert [p for p, _ in room.calls] == ["builder"]
    rows = room.rows()
    assert rows["build"].state == "stopped"
    assert rows["check"].state == "failed" and rows["check"].error == "dependency build stopped"


def test_bound_enforces_total_length_marker_included():
    assert _bound("short", 10) == "short"
    out = _bound("x" * 100, 20)
    assert len(out) == 20 and out.endswith(TRUNCATED)
    assert len(_bound("x" * 100, 3)) == 3
    assert len(_bound("x" * 100, len(TRUNCATED))) == len(TRUNCATED)


def test_failed_plan_settlement_is_not_hidden(room, monkeypatch):
    def boom(self, **kw):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(PlanExecutions, "finish", boom)

    async def go():
        room.host.start(room.group.id, plan=room.plan, grants=room.grants)
        return await asyncio.wait_for(room.host.wait(room.group.id), 5)
    statuses = asyncio.run(go())
    assert "failed" in {st.state for st in statuses}     # surfaced as a failed member, not swallowed
    assert {t: r.state for t, r in room.rows().items()} == {"build": "completed", "check": "completed"}
    assert PlanExecutions(room.groups).plan_state(
        room.plan.plan_id, group_id=room.group.id, owner=OWNER) == "running"
