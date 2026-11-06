"""D03 proof: an authorized group plan runs each task on its OWN profile's seat-configured model.

Hermetic: temp ``EXO_HOME``, ``EXO_NO_KEYCHAIN=1``, offline ``RuleFake`` only, no network. ``GroupHost``,
``group_launch.create``, the plan executor and both profile ``CoreEngine`` objects are real. The seat TOMLs
are synthetic. The host default model (``fake:host-default``) differs from both seat models, so an
implementation that ignored ``Seat.models`` would fail the assertions. The model is read from the
``model`` option the router passes to the provider on each actual call, not from ``router.chain``.
Reuses the RuleFake ``Provider``, ``make_grants`` and ``until`` helpers of test_group_plan_real_engine_e2e.
"""
import asyncio
import json

import pytest

from exo.core.group_plan_execution import PlanExecutions
from exo.core.group_task_contract import validate_plan
from exo.core.models.fake import RuleFake
from exo.group_launch import create
from test_group_plan_real_engine_e2e import BOUND, PROFILES, RAW, Provider, make_grants

HOST_DEFAULT = "fake:host-default"
SEAT_MODELS = {"builder": "fake:build-model", "checker": "fake:review-model"}
MODELS = {"fake": {"rules": [{"match": "MARK-BUILD", "steps": ["BUILD-REPLY-7"]},
                             {"match": "MARK-CHECK", "steps": ["CHECK-REPLY-9"]}]},
          "roles": {"main": [HOST_DEFAULT]}}
BUILDER_SEAT = 'name = "builder"\n[models.roles]\nmain = ["fake:build-model"]\n'
# checker selects its model through the seat tier (tier -> engine "main" role), builder through roles.main
CHECKER_SEAT = 'name = "checker"\n[models]\ntier = "review"\n[models.roles]\nreview = ["fake:review-model"]\n'


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    (home / "seats" / "owner.toml").write_text('name = "owner"\n', encoding="utf-8")
    (home / "seats" / "builder.toml").write_text(BUILDER_SEAT, encoding="utf-8")
    (home / "seats" / "checker.toml").write_text(CHECKER_SEAT, encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    return home


def with_route(raw, task_id, route):
    return {**raw, "tasks": [{**t, "route": route} if t["task_id"] == task_id else t for t in raw["tasks"]]}


def run_plan(tmp_path, monkeypatch, raw):
    """Run the authorized plan once. Return what the provider actually saw on every call."""
    provider = Provider(monkeypatch, "ok")
    calls = []                                    # one entry per actual provider call, in call order
    inner = RuleFake.stream

    async def capture(this, messages, tools, **opts):
        text = next((m.text() for m in reversed(messages) if m.role == "user"), "")
        calls.append({"mark": next((m for m in ("MARK-BUILD", "MARK-CHECK") if m in text), None),
                      "model": opts.get("model"), "instance": this})
        async for event in inner(this, messages, tools, **opts):
            yield event

    monkeypatch.setattr(RuleFake, "stream", capture)   # outermost: wraps Provider's recorder
    launch = create(seat="owner", profiles=PROFILES, mode="socratic")

    async def go():
        provider.arm()
        try:
            grants = make_grants(launch, tmp_path)
            plan = validate_plan(raw, grants, group=launch.group)
            launch.host.start(launch.group.id, plan=plan, grants=grants)
            engines = dict(launch.members)
            await asyncio.wait_for(launch.host.wait(launch.group.id), BOUND)
            rows = {r.task_id: r for r in PlanExecutions(launch.host._groups).tasks(
                plan.plan_id, group_id=launch.group.id, owner=launch.group.owner)}
            instances = {p: e.router.instances["fake"] for p, e in engines.items()}
            chains = {p: [r.ref for r in e.router.chain("main")] for p, e in engines.items()}
            return engines, rows, instances, chains
        finally:
            await asyncio.wait_for(launch.aclose(), BOUND)

    engines, rows, instances, chains = asyncio.run(asyncio.wait_for(go(), BOUND * 2))
    return calls, provider, engines, rows, instances, chains, launch


def check_seat_models(calls, engines, rows, instances, chains, launch):
    assert SEAT_MODELS["builder"] != SEAT_MODELS["checker"]
    assert HOST_DEFAULT not in SEAT_MODELS.values()
    assert [c["mark"] for c in calls] == ["MARK-BUILD", "MARK-CHECK"]      # one real call per task, in order
    by_mark = {c["mark"]: c for c in calls}
    # actual provider-stream model per task: the profile's seat model, never the host default
    assert by_mark["MARK-BUILD"]["model"] == "build-model"
    assert by_mark["MARK-CHECK"]["model"] == "review-model"
    assert {c["model"] for c in calls}.isdisjoint({"host-default", "fast"})
    # each call ran on its own profile's engine (provider instance is per-engine router state)
    assert by_mark["MARK-BUILD"]["instance"] is instances["builder"]
    assert by_mark["MARK-CHECK"]["instance"] is instances["checker"]
    assert instances["builder"] is not instances["checker"]
    assert set(engines) == set(PROFILES) and engines["builder"] is not engines["checker"]
    assert engines["builder"].agent == "builder" and engines["checker"].agent == "checker"
    # consistency with configuration (secondary: the calls above are the proof)
    assert chains["builder"][0] == SEAT_MODELS["builder"] and chains["checker"][0] == SEAT_MODELS["checker"]
    assert {r.state for r in rows.values()} == {"completed"}
    assert rows["build"].result == "BUILD-REPLY-7" and rows["check"].result == "CHECK-REPLY-9"
    assert launch.members == {} and launch.closed
    assert all(e._writer is None for e in engines.values())


def test_each_task_runs_on_its_profiles_seat_model_not_the_host_default(home, tmp_path, monkeypatch):
    calls, provider, engines, rows, instances, chains, launch = run_plan(tmp_path, monkeypatch, RAW)
    check_seat_models(calls, engines, rows, instances, chains, launch)
    assert provider.marks == ["MARK-BUILD", "MARK-CHECK"]


def test_task_route_is_data_only_today_and_does_not_select_the_model(home, tmp_path, monkeypatch):
    """Documents EXISTING behavior (group_plan_execution header): route "cheap" -> fake:fast is validated and
    carried in the prompt, but the profile seat's model still runs. This is not a promised route selection."""
    raw = with_route(RAW, "build", "cheap")
    calls, provider, engines, rows, instances, chains, launch = run_plan(tmp_path, monkeypatch, raw)
    check_seat_models(calls, engines, rows, instances, chains, launch)
    assert '"cheap"' in provider.seen[0]                                   # route reached the prompt as data
