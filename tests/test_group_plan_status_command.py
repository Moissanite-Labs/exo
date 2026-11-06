"""``/group plan_status``: read-only, owner-scoped view of durable plan task state.

Offline only: real CoreEngine members, the fake provider and a real PlanExecutions store (the rig of
``test_group_plan_real_engine_e2e``). No host is injected on the command engine, no network, no live provider.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_group_plan_real_engine_e2e import BOUND, MODELS, PROFILES, run_scenario

from exo import principal
from exo.core import group_commands
from exo.server.engine import CommandFailed


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


REFUSAL = "not_found: no such plan"


def engine(rig, agent=None):
    """The command's engine: seat name and sessions DB only. No ``_group_host`` is set."""
    return SimpleNamespace(agent=agent or rig.owner, store=rig.launch.store)


async def status(rig, args, who="principal", agent=None):
    token = principal.CALLER.set(principal.Caller(who))
    try:
        return await group_commands.command(
            engine(rig, agent), SimpleNamespace(args=f"plan_status {args}")
        )
    finally:
        principal.CALLER.reset(token)


def arg(rig, **over):
    body = {"id": rig.gid, "plan_id": rig.plan.plan_id, **over}
    return json.dumps({k: v for k, v in body.items() if v is not ...})


def by_task(out):
    return {t["task_id"]: t for t in out["tasks"]}


def test_settled_two_task_plan_in_topological_order(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return await status(rig, arg(rig))

    rig, _, out = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    assert (
        out["state"] == "settled"
        and out["id"] == rig.gid
        and out["plan_id"] == rig.plan.plan_id
    )
    assert [t["task_id"] for t in out["tasks"]] == ["build", "check"]
    assert [t["state"] for t in out["tasks"]] == ["completed", "completed"]
    assert by_task(out)["build"]["result"] == "BUILD-REPLY-7"
    assert by_task(out)["check"]["result"] == "CHECK-REPLY-9"
    assert (
        by_task(out)["build"]["profile"] == "builder"
        and by_task(out)["build"]["attempt"] == 1
    )
    assert not any(t["result_truncated"] or t["error_truncated"] for t in out["tasks"])
    assert (
        out["task_count"] == 2
        and out["tasks_returned"] == 2
        and out["tasks_truncated"] is False
    )
    assert "untrusted" in out["untrusted"]
    for forbidden in ("goal", "prompt", "grants", "write_scopes", "permits"):
        assert forbidden not in json.dumps(out)


def test_running_plan_shows_pending_and_running_not_complete(
    home, tmp_path, monkeypatch
):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(provider.reached.wait(), BOUND)
        out = await status(rig, arg(rig))
        rig.host.stop(rig.gid)
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return out

    _, _, out = run_scenario(home, tmp_path, monkeypatch, "block", body)
    assert out["state"] == "running"
    tasks = by_task(out)
    assert tasks["build"]["state"] == "running" and tasks["build"]["result"] is None
    assert tasks["check"]["state"] == "pending"
    assert all(t["state"] != "completed" for t in out["tasks"])


def test_provider_failure_reports_failed_and_dependency_failed(
    home, tmp_path, monkeypatch
):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return await status(rig, arg(rig))

    _, _, out = run_scenario(home, tmp_path, monkeypatch, "fail", body)
    tasks = by_task(out)
    assert out["state"] == "settled"
    assert (
        tasks["build"]["state"] == "failed"
        and tasks["build"]["error"] == "engine turn did not complete"
    )
    assert (
        tasks["check"]["state"] == "failed"
        and tasks["check"]["error"] == "dependency build failed"
    )
    assert "PROVIDER-SECRET-BOOM" not in json.dumps(out)


def test_stopped_group_reports_stopped_with_error_text(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(provider.reached.wait(), BOUND)
        rig.host.stop(rig.gid, profile="builder")
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return await status(rig, arg(rig))

    _, _, out = run_scenario(home, tmp_path, monkeypatch, "block", body)
    tasks = by_task(out)
    assert tasks["build"]["state"] == "stopped"
    assert (
        tasks["check"]["state"] == "failed"
        and tasks["check"]["error"] == "dependency build stopped"
    )


def test_unknown_foreign_owner_and_foreign_group_are_indistinguishable(
    home, tmp_path, monkeypatch
):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        other = rig.host.create(mode="socratic", members=PROFILES)
        refusals = {}
        cases = {
            "unknown_plan": (arg(rig, plan_id="0" * 64), None),
            "unknown_group": (arg(rig, id="g-nope"), None),
            "other_group_real_plan": (arg(rig, id=other.id), None),
            "foreign_owner": (arg(rig), "mallory"),
        }
        for name, (args, agent) in cases.items():
            with pytest.raises(CommandFailed) as err:
                await status(rig, args, agent=agent)
            refusals[name] = (
                err.value.code,
                err.value.message,
                str(err.value),
                err.value.retryable,
            )
        return refusals

    _, _, refusals = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    assert len(set(refusals.values())) == 1
    assert str(CommandFailed(*next(iter(refusals.values()))[:2])) == REFUSAL


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        "not-json",
        "{}",
        '{"id": "g"}',
        '{"plan_id": "%s"}' % ("a" * 64),
        '{"id": "g", "plan_id": "%s", "extra": 1}' % ("a" * 64),
        '{"id": 5, "plan_id": "%s"}' % ("a" * 64),
        '{"id": " ", "plan_id": "%s"}' % ("a" * 64),
        '{"id": "%s", "plan_id": "%s"}' % ("g" * 129, "a" * 64),
        '{"id": "g", "plan_id": 7}',
        '{"id": "g", "plan_id": null}',
        '{"id": "g", "plan_id": "%s"}' % ("a" * 63),
        '{"id": "g", "plan_id": "%s"}' % ("a" * 65),
        '{"id": "g", "plan_id": "%s"}' % ("A" * 64),
        '{"id": "g", "plan_id": "%s"}' % ("g" * 64),
        '{"id": "g", "plan_id": "%s"}' % ("a" * 10000),
        '{"id": "g", "plan_id": "%s", "limit": 0}' % ("a" * 64),
        '{"id": "g", "plan_id": "%s", "limit": 201}' % ("a" * 64),
        '{"id": "g", "plan_id": "%s", "limit": true}' % ("a" * 64),
        '{"id": "g", "plan_id": "%s", "limit": "5"}' % ("a" * 64),
    ],
)
def test_bad_arguments_are_bad_request_before_any_store_access(raw):
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("store touched before validation")

    eng = SimpleNamespace(agent="one", store=Boom())
    token = principal.CALLER.set(principal.Caller("principal"))
    try:
        with pytest.raises(CommandFailed) as err:
            asyncio.run(
                group_commands.command(eng, SimpleNamespace(args=f"plan_status {raw}"))
            )
    finally:
        principal.CALLER.reset(token)
    assert err.value.code == "bad_request"


def test_non_principal_refused_before_any_access():
    eng = SimpleNamespace(
        agent="one"
    )  # no store at all: any access would raise AttributeError
    token = principal.CALLER.set(principal.Caller("local"))
    try:
        with pytest.raises(CommandFailed) as err:
            asyncio.run(
                group_commands.command(eng, SimpleNamespace(args="plan_status {}"))
            )
    finally:
        principal.CALLER.reset(token)
    assert err.value.code == "forbidden"


def test_output_is_bounded_and_truncation_is_flagged(home, tmp_path, monkeypatch):
    big = "x" * 20_000

    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        rig.launch.store._q(
            "UPDATE swarm_plan_tasks SET result=?, error=?",
            (json.dumps(big), "e" * 1000),
        )
        full = await status(rig, arg(rig))
        limited = await status(rig, arg(rig, limit=1))
        return full, limited

    _, _, (full, limited) = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    for t in full["tasks"]:
        assert len(t["result"]) == 4000 and t["result_truncated"] is True
        assert len(t["error"]) == 256 and t["error_truncated"] is True
    assert len(json.dumps(full)) < 40_000
    assert limited["tasks_returned"] == 1 and limited["task_count"] == 2
    assert limited["tasks_truncated"] is True and [
        t["task_id"] for t in limited["tasks"]
    ] == ["build"]


@pytest.mark.parametrize(
    ("total", "error", "result_cap", "error_cap"),
    [
        (
            40,
            "e" * 1000,
            600,
            200,
        ),  # long errors: error cap is a quarter of the 800-character share
        (
            40,
            "dependency build failed",
            777,
            23,
        ),  # short errors leave the rest of the share to the result
        (200, "e" * 1000, 120, 40),  # the row limit: 160-character share per task
    ],
)
def test_aggregate_budget_is_shared_by_result_and_error(
    home, tmp_path, monkeypatch, total, error, result_cap, error_cap
):
    seeded = 2  # the two real plan rows, build and check

    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        for i in range(total - seeded):
            rig.launch.store._q(
                "INSERT INTO swarm_plan_tasks VALUES (?, ?, 1, ?, ?, 'completed', ?, ?, NULL, NULL)",
                (
                    rig.plan.plan_id,
                    f"t{i:03d}" + "k" * 59,
                    rig.gid,
                    "p" * 64,
                    "2026-01-01T00:00:00.000Z",
                    "2026-01-01T00:00:01.000Z",
                ),
            )
        rig.launch.store._q(
            "UPDATE swarm_plan_tasks SET result=?, error=?",
            (json.dumps("r" * 5000), error),
        )
        return await status(rig, arg(rig, limit=200))

    _, _, out = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    tasks = out["tasks"]
    assert (
        out["task_count"] == out["tasks_returned"] == total
        and out["tasks_truncated"] is False
    )
    assert {(len(t["result"]), len(t["error"])) for t in tasks} == {
        (result_cap, min(len(error), error_cap))
    }
    assert all(
        t["result_truncated"] and t["error_truncated"] == (len(error) > error_cap)
        for t in tasks
    )
    assert sum(len(t["result"]) + len(t["error"]) for t in tasks) <= 32_000
    # Identifiers are finite by contract and are never clipped. JSON overhead is on top of the text budget.
    assert all(len(t["task_id"]) <= 64 and len(t["profile"]) <= 64 for t in tasks)
    text = sum(len(t["result"]) + len(t["error"]) for t in tasks)
    assert len(json.dumps(out)) - text <= 450 * total + 600


def test_no_host_needed_and_start_stays_forbidden(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        eng = engine(rig)
        assert getattr(eng, "_group_host", None) is None
        token = principal.CALLER.set(principal.Caller("principal"))
        try:
            out = await group_commands.command(
                eng, SimpleNamespace(args=f"plan_status {arg(rig)}")
            )
            with pytest.raises(CommandFailed) as err:
                await group_commands.command(
                    eng,
                    SimpleNamespace(
                        args=f'start {{"id": "{rig.gid}", "audiences": {{"builder": ["checker"]}}}}'
                    ),
                )
        finally:
            principal.CALLER.reset(token)
        return out, err.value.code

    _, _, (out, code) = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    assert out["state"] == "settled" and code == "forbidden"


def test_never_planned_database_refuses_and_creates_no_plan_tables(
    home, tmp_path, monkeypatch
):
    async def body(rig, provider):
        assert rig.plan_tables() == set()
        with pytest.raises(CommandFailed) as err:
            await status(rig, arg(rig))
        with pytest.raises(CommandFailed) as err2:
            await status(rig, arg(rig, id="g-nope"))
        return rig.plan_tables(), str(err.value), str(err2.value)

    _, _, (tables, first, second) = run_scenario(
        home, tmp_path, monkeypatch, "ok", body
    )
    assert tables == set()
    assert first == second == REFUSAL
