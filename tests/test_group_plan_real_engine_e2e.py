"""Plan execution on REAL CoreEngine members: ``group_launch.create`` -> ``GroupHost.start(plan=, grants=)``.

Hermetic: isolated ``EXO_HOME`` (tmp_path), the offline ``RuleFake`` provider, no network, no keychain.
Engine, loop, router, host, runtime and plan executor are all real. The only patch is the provider
TRANSPORT (``RuleFake.stream``): it records the prompt each turn sends, and for two scenarios it
raises a non-retryable provider error or holds the call open on an ``asyncio.Event``.
One event loop per scenario; ``launch.aclose()`` always runs; every wait is bounded.
"""
import asyncio
import dataclasses
import json

import pytest

from exo.core.agents.route_config import RouteConfig
from exo.core.group_host import HostError
from exo.core.group_plan_execution import PLAN_PREFIX, PlanExecutions, plan_digest
from exo.core.group_task_contract import SCHEMA_VERSION, HostGrants, validate_plan
from exo.core.models.fake import RuleFake
from exo.core.types import ProviderEvent
from exo.group_launch import create

BOUND = 10  # seconds; a deadline, never a pacing sleep
PROFILES = ["builder", "checker"]
MODELS = {"fake": {"rules": [{"match": "MARK-BUILD", "steps": ["BUILD-REPLY-7"]},
                             {"match": "MARK-CHECK", "steps": ["CHECK-REPLY-9"]}]},
          "roles": {"main": ["fake:x"]}}
RAW = {"schema": SCHEMA_VERSION, "review_of": {"build": "check"}, "tasks": [
    {"task_id": "build", "role": "builder", "goal": "MARK-BUILD write it", "profile": "builder",
     "acceptance": ["tests pass"]},
    {"task_id": "check", "role": "reviewer", "goal": "MARK-CHECK review it", "profile": "checker",
     "acceptance": ["findings"], "depends_on": ["build"],
     "outputs": [{"kind": "message", "ref": "m-1"}]}]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    for name in (*PROFILES, "owner"):
        (home / "seats" / f"{name}.toml").write_text(f'name = "{name}"\n', encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    monkeypatch.setenv("EXO_HOME", str(home))
    return home


class Provider:
    """Records each provider call's last user prompt; optionally fails or blocks the builder's call."""

    def __init__(self, monkeypatch, scenario="ok"):
        self.scenario = scenario
        self.seen = []             # full last-user text per provider call, in call order
        self.reached = None        # asyncio events are created by arm() inside the scenario's loop
        self.gate = None
        original = RuleFake.stream
        provider = self

        async def stream(this, messages, tools, **opts):
            text = next((m.text() for m in reversed(messages) if m.role == "user"), "")
            provider.seen.append(text)
            if "MARK-BUILD" in text and provider.scenario == "fail":
                yield ProviderEvent(kind="error", error="PROVIDER-SECRET-BOOM", retryable=False)
                return
            if "MARK-BUILD" in text and provider.scenario == "block":
                provider.reached.set()
                await provider.gate.wait()
            async for event in original(this, messages, tools, **opts):
                yield event

        monkeypatch.setattr(RuleFake, "stream", stream)

    def arm(self):
        """Create the events inside the scenario's own loop."""
        self.reached = asyncio.Event()
        self.gate = asyncio.Event()

    @property
    def marks(self):
        return [m for text in self.seen for m in ("MARK-BUILD", "MARK-CHECK") if m in text]


def make_grants(launch, tmp_path, **over):
    root = str(tmp_path / "w")
    base = dict(owner=launch.group.owner, profiles=frozenset(PROFILES),
                permits={"builder": frozenset({"read_repo", "write_scope"})},
                write_roots={"builder": (root,), "checker": (root,)},
                routes=RouteConfig.of({"backend": "model_map", "routes": {"cheap": "fake:fast"}}),
                reviewer_profiles=frozenset({"checker"}))
    base.update(over)
    return HostGrants(**base)


class Rig:
    def __init__(self, tmp_path):
        self.launch = create(seat="owner", profiles=PROFILES, mode="socratic")
        self.gid = self.launch.group.id
        self.owner = self.launch.group.owner
        self.grants = make_grants(self.launch, tmp_path)
        self.plan = validate_plan(RAW, self.grants, group=self.launch.group)
        self.host = self.launch.host

    def start(self):
        return self.host.start(self.gid, plan=self.plan, grants=self.grants)

    def rows(self):
        return {r.task_id: r for r in PlanExecutions(self.host._groups).tasks(
            self.plan.plan_id, group_id=self.gid, owner=self.owner)}

    def states(self):
        return {t: r.state for t, r in self.rows().items()}

    def plan_state(self):
        return PlanExecutions(self.host._groups).plan_state(
            self.plan.plan_id, group_id=self.gid, owner=self.owner)

    def plan_tables(self):
        names = {r[0] for r in self.launch.store._q("SELECT name FROM sqlite_master WHERE type='table'")}
        return {"swarm_plans", "swarm_plan_tasks"} & names


async def until(pred, what):
    """Yield to the loop (no timer) until ``pred`` holds; the deadline only bounds a hang."""
    async def spin():
        while not pred():
            await asyncio.sleep(0)
    try:
        await asyncio.wait_for(spin(), BOUND)
    except asyncio.TimeoutError:
        raise AssertionError(f"never reached: {what}") from None


async def settle_turns(count=100):
    for _ in range(count):
        await asyncio.sleep(0)


def run_scenario(home, tmp_path, monkeypatch, scenario, body):
    """One loop, guaranteed ``launch.aclose``; ``body(rig, provider)`` is an async function."""
    provider = Provider(monkeypatch, scenario)
    rig = Rig(tmp_path)

    async def go():
        provider.arm()
        try:
            return await asyncio.wait_for(body(rig, provider), BOUND * 2)
        finally:
            await asyncio.wait_for(rig.launch.aclose(), BOUND)

    out = asyncio.run(go())
    return rig, provider, out


def test_authorized_plan_runs_in_dependency_order_on_real_engines(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        rig.start()
        engines = dict(rig.launch.members)          # real factory ran: engines exist now
        statuses = await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        status = rig.host.status(rig.gid)
        with pytest.raises(HostError):              # reservation survives the real run
            rig.start()
        return engines, statuses, status, rig.states(), rig.rows(), rig.plan_state()

    rig, provider, (engines, statuses, status, states, rows, plan_state) = run_scenario(
        home, tmp_path, monkeypatch, "ok", body)
    assert provider.marks == ["MARK-BUILD", "MARK-CHECK"]            # dependency order, one turn each
    assert set(engines) == set(PROFILES)
    assert len({id(e) for e in engines.values()}) == 2
    for profile, eng in engines.items():
        assert type(eng).__name__ == "CoreEngine" and eng.agent == profile and eng.home == home
    assert states == {"build": "completed", "check": "completed"}
    assert rows["build"].result == "BUILD-REPLY-7" and rows["check"].result == "CHECK-REPLY-9"
    assert rows["build"].error is None and rows["check"].error is None
    assert rows["build"].finished <= rows["check"].started   # dependency settled before the dependent began
    builder_prompt, checker_prompt = provider.seen
    assert builder_prompt.startswith(PLAN_PREFIX) and checker_prompt.startswith(PLAN_PREFIX)
    assert "BUILD-REPLY-7" not in builder_prompt
    assert "BUILD-REPLY-7" in checker_prompt                         # dependency result carried as data
    assert {st.profile: st.state for st in statuses} == {"builder": "completed", "checker": "completed"}
    assert plan_state == "settled"
    assert status["receipt"].state == "settled"
    assert rig.launch.members == {} and rig.launch.closed
    assert all(e._writer is None for e in engines.values())          # engines released by aclose


@pytest.mark.parametrize("case", ["owner", "digest", "profiles", "wrong_group", "grants_only"])
def test_unauthorized_plan_refused_before_any_engine_is_allocated(home, tmp_path, monkeypatch, case):
    async def body(rig, provider):
        kw = dict(plan=rig.plan, grants=rig.grants)
        if case == "owner":
            kw["grants"] = make_grants(rig.launch, tmp_path, owner="mallory")
        elif case == "digest":
            kw["plan"] = dataclasses.replace(rig.plan, plan_id="0" * 64)
        elif case == "profiles":
            kw["grants"] = make_grants(rig.launch, tmp_path, profiles=frozenset({*PROFILES, "extra"}))
        elif case == "wrong_group":
            kw["plan"] = dataclasses.replace(rig.plan, group_id="other")
        elif case == "grants_only":
            kw = dict(grants=rig.grants)
        with pytest.raises(HostError):
            rig.host.start(rig.gid, **kw)
        refused = (dict(rig.launch.members), list(provider.seen), rig.plan_tables(),
                   rig.host.status(rig.gid)["receipt"])
        rig.start()                                  # nothing leaked: the correct start still runs
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return refused, rig.states()

    rig, provider, ((members, seen, tables, receipt), states) = run_scenario(
        home, tmp_path, monkeypatch, "ok", body)
    assert members == {}                    # the real engine factory was never reached
    assert seen == [] and tables == set() and receipt is None
    assert states == {"build": "completed", "check": "completed"}
    assert provider.marks == ["MARK-BUILD", "MARK-CHECK"]


def test_tampered_task_beyond_grants_is_refused_before_engines(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        wide = dataclasses.replace(rig.plan.tasks[0], write_scopes=("/etc",))
        tampered = dataclasses.replace(rig.plan, tasks=(wide, rig.plan.tasks[1]))
        tampered = dataclasses.replace(tampered, plan_id=plan_digest(tampered))  # identity is not authority
        with pytest.raises(HostError, match="write scope"):
            rig.host.start(rig.gid, plan=tampered, grants=rig.grants)
        return dict(rig.launch.members), rig.plan_tables()

    rig, provider, (members, tables) = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    assert members == {} and tables == set() and provider.seen == []


@pytest.mark.parametrize("paused", ["checker", "builder"])
def test_pause_holds_member_before_its_task_until_resume(home, tmp_path, monkeypatch, paused):
    async def body(rig, provider):
        rig.start()
        rig.host.pause(rig.gid, profile=paused)      # synchronously: no turn has begun yet
        if paused == "checker":
            await until(lambda: rig.states().get("build") == "completed", "build completed")
        await settle_turns()
        held = (list(provider.marks), rig.states(),
                {m.profile: m.state for m in rig.host.status(rig.gid)["members"]})
        rig.host.resume(rig.gid, profile=paused)
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return held, rig.states()

    rig, provider, (held, final) = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    held_marks, held_states, held_members = held
    assert held_members[paused] == "paused"
    if paused == "checker":
        assert held_marks == ["MARK-BUILD"]
        assert held_states == {"build": "completed", "check": "pending"}
    else:
        assert held_marks == []                       # not even the first turn began
        assert held_states == {"build": "pending", "check": "pending"}
    assert provider.marks == ["MARK-BUILD", "MARK-CHECK"]
    assert final == {"build": "completed", "check": "completed"}


def test_provider_failure_fails_task_and_closes_dependent(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        rig.start()
        statuses = await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        with pytest.raises(HostError):
            rig.start()
        return statuses, rig.rows(), rig.plan_state(), rig.host.status(rig.gid)["receipt"]

    rig, provider, (statuses, rows, plan_state, receipt) = run_scenario(
        home, tmp_path, monkeypatch, "fail", body)
    assert provider.marks == ["MARK-BUILD"]           # no checker turn, no retry of the failed call
    assert len(provider.seen) == 1
    assert rows["build"].state == "failed" and rows["build"].error == "engine turn did not complete"
    assert rows["build"].result is None
    assert "PROVIDER-SECRET-BOOM" not in (rows["build"].error or "")
    assert rows["check"].state == "failed" and rows["check"].error == "dependency build failed"
    assert rows["check"].started is None              # never began a turn
    assert plan_state == "settled"
    assert {st.profile: st.state for st in statuses} == {"builder": "failed", "checker": "failed"}
    assert receipt.state == "settled"


@pytest.mark.parametrize("target", ["builder", None])
def test_stop_during_blocked_real_turn_settles_stopped_and_dependent(home, tmp_path, monkeypatch, target):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(provider.reached.wait(), BOUND)   # builder is inside the real provider call
        assert rig.states()["build"] == "running"
        rig.host.stop(rig.gid, profile=target)
        statuses = await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return statuses, rig.rows(), rig.plan_state(), rig.host.status(rig.gid)["receipt"]

    rig, provider, (statuses, rows, plan_state, receipt) = run_scenario(
        home, tmp_path, monkeypatch, "block", body)
    assert provider.marks == ["MARK-BUILD"]           # the dependent never ran a turn
    assert rows["build"].state == "stopped" and rows["build"].result is None
    if target == "builder":
        assert rows["check"].state == "failed" and rows["check"].error == "dependency build stopped"
    else:
        assert rows["check"].state == "stopped" and rows["check"].started is None
    assert plan_state == "settled"
    assert {st.profile: st.state for st in statuses}["builder"] == "stopped"
    assert receipt.state == "settled"
