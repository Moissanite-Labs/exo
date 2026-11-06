"""``GroupLaunch.start(plan=, grants=)``: trusted-Python wrapper over ``GroupHost.start`` (docs/SWARM-LAUNCH.md).

Hermetic: isolated ``EXO_HOME`` (tmp_path), offline ``RuleFake`` provider, no network. Fixtures are
reused from ``test_group_plan_real_engine_e2e`` (unmodified). Every scenario runs in one loop with a
bounded wait and ``launch.aclose()`` in ``finally``.
"""
import dataclasses

import pytest
from test_group_plan_real_engine_e2e import (BOUND, PROFILES, Rig, home, make_grants,  # noqa: F401
                                             run_scenario)

from exo.core.group_host import ConversationOptions, HostError
from exo.group_launch import LaunchError


def scenario(home, tmp_path, monkeypatch, body):  # noqa: F811
    return run_scenario(home, tmp_path, monkeypatch, "ok", body)


def record_host_start(rig, monkeypatch):
    calls = []

    def fake_start(*args, **kwargs):
        calls.append((args, kwargs))
        return ("started",)

    monkeypatch.setattr(rig.host, "start", fake_start)
    return calls


def test_legacy_start_calls_are_unchanged(home, tmp_path, monkeypatch):  # noqa: F811
    async def body(rig, provider):
        calls = record_host_start(rig, monkeypatch)
        rig.launch.start()
        rig.launch.start(max_concurrent=2)
        options = ConversationOptions(max_turns=3, idle_timeout=1.0)
        rig.launch.start(conversation=options, max_concurrent=1)
        return calls, options

    rig, _, (calls, options) = scenario(home, tmp_path, monkeypatch, body)
    gid = rig.gid
    assert [c[0] for c in calls] == [(gid,)] * 3
    assert set(calls[0][1]) == {"audiences", "max_concurrent"}
    assert calls[0][1]["max_concurrent"] is None
    assert calls[1][1] == {**calls[0][1], "max_concurrent": 2}
    assert calls[2][1] == {"conversation": options, "max_concurrent": 1}   # no audiences, no plan, no grants


@pytest.mark.parametrize("max_concurrent", [None, 1])
def test_plan_start_forwards_without_audiences(home, tmp_path, monkeypatch, max_concurrent):  # noqa: F811
    async def body(rig, provider):
        calls = record_host_start(rig, monkeypatch)
        out = rig.launch.start(plan=rig.plan, grants=rig.grants, max_concurrent=max_concurrent)
        return calls, out

    rig, _, (calls, out) = scenario(home, tmp_path, monkeypatch, body)
    assert out == ("started",)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (rig.gid,)
    assert kwargs == {"plan": rig.plan, "grants": rig.grants, "max_concurrent": max_concurrent}
    assert kwargs["plan"] is rig.plan and kwargs["grants"] is rig.grants   # same objects, not rebuilt


@pytest.mark.parametrize("case", ["plan_only", "grants_only", "plan_wrong_type", "grants_wrong_type",
                                  "grants_is_plan", "conversation"])
def test_wrapper_refusal_happens_before_host_or_allocation(home, tmp_path, monkeypatch, case):  # noqa: F811
    async def body(rig, provider):
        calls = record_host_start(rig, monkeypatch)
        kw = {
            "plan_only": dict(plan=rig.plan),
            "grants_only": dict(grants=rig.grants),
            "plan_wrong_type": dict(plan={"tasks": []}, grants=rig.grants),
            "grants_wrong_type": dict(plan=rig.plan, grants=object()),
            "grants_is_plan": dict(plan=rig.plan, grants=rig.plan),
            "conversation": dict(plan=rig.plan, grants=rig.grants,
                                 conversation=ConversationOptions(max_turns=2, idle_timeout=1.0)),
        }[case]
        with pytest.raises(LaunchError):
            rig.launch.start(**kw)
        return calls, dict(rig.launch.members), rig.plan_tables(), rig.host.status(rig.gid)["receipt"]

    rig, provider, (calls, members, tables, receipt) = scenario(home, tmp_path, monkeypatch, body)
    assert calls == [] and members == {} and tables == set() and receipt is None
    assert provider.seen == []


@pytest.mark.parametrize("case", ["owner", "wrong_group", "profiles", "digest"])
def test_host_refusal_propagates_and_leaves_no_claim(home, tmp_path, monkeypatch, case):  # noqa: F811
    async def body(rig, provider):
        plan, grants = rig.plan, rig.grants
        if case == "owner":
            grants = make_grants(rig.launch, tmp_path, owner="mallory")
        elif case == "wrong_group":
            plan = dataclasses.replace(plan, group_id="other")
        elif case == "profiles":
            grants = make_grants(rig.launch, tmp_path, profiles=frozenset({*PROFILES, "extra"}))
        elif case == "digest":
            plan = dataclasses.replace(plan, plan_id="0" * 64)
        with pytest.raises(HostError) as info:
            rig.launch.start(plan=plan, grants=grants)
        assert not isinstance(info.value, LaunchError)                     # propagated, not rewrapped
        return dict(rig.launch.members), rig.plan_tables(), rig.host.status(rig.gid)["receipt"]

    rig, provider, (members, tables, receipt) = scenario(home, tmp_path, monkeypatch, body)
    assert members == {}                      # the engine factory never ran
    assert tables == set() and receipt is None and provider.seen == []


def test_valid_plan_through_launch_start_completes_on_real_engines(home, tmp_path, monkeypatch):  # noqa: F811
    async def body(rig, provider):
        import asyncio
        rig.launch.start(plan=rig.plan, grants=rig.grants)
        engines = dict(rig.launch.members)
        statuses = await asyncio.wait_for(rig.launch.wait(), BOUND)
        return engines, statuses, rig.states(), rig.plan_state()

    rig, provider, (engines, statuses, states, plan_state) = scenario(home, tmp_path, monkeypatch, body)
    assert set(engines) == set(PROFILES)
    assert states == {"build": "completed", "check": "completed"}
    assert plan_state == "settled"
    assert provider.marks == ["MARK-BUILD", "MARK-CHECK"]
    assert {st.profile: st.state for st in statuses} == {"builder": "completed", "checker": "completed"}
    assert rig.launch.closed


# -- durable_attempts: exact-bool opt-in, forwarded only when true ------------------------------------
def test_durable_false_keeps_plan_call_kwargs_exact(home, tmp_path, monkeypatch):  # noqa: F811
    async def body(rig, provider):
        calls = record_host_start(rig, monkeypatch)
        rig.launch.start(plan=rig.plan, grants=rig.grants, durable_attempts=False)
        rig.launch.start(plan=rig.plan, grants=rig.grants)
        rig.launch.start(durable_attempts=False)
        return calls

    rig, _, calls = scenario(home, tmp_path, monkeypatch, body)
    assert set(calls[0][1]) == {"plan", "grants", "max_concurrent"} == set(calls[1][1])   # no durable key
    assert set(calls[2][1]) == {"audiences", "max_concurrent"}


@pytest.mark.parametrize("max_concurrent", [None, 1])
def test_durable_true_forwards_flag_and_same_objects(home, tmp_path, monkeypatch, max_concurrent):  # noqa: F811
    async def body(rig, provider):
        calls = record_host_start(rig, monkeypatch)
        out = rig.launch.start(plan=rig.plan, grants=rig.grants, max_concurrent=max_concurrent,
                               durable_attempts=True)
        return calls, out

    rig, _, (calls, out) = scenario(home, tmp_path, monkeypatch, body)
    assert out == ("started",) and len(calls) == 1
    args, kwargs = calls[0]
    assert args == (rig.gid,)
    assert kwargs == {"plan": rig.plan, "grants": rig.grants, "max_concurrent": max_concurrent,
                      "durable_attempts": True}                 # no audiences
    assert kwargs["plan"] is rig.plan and kwargs["grants"] is rig.grants
    assert kwargs["durable_attempts"] is True


def supervision_tables(rig):
    return {r[0] for r in rig.launch.store._q(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'swarm_supervision%'")}


@pytest.mark.parametrize("bad", [1, 0, "yes", None, 1.0, ()])
@pytest.mark.parametrize("with_plan", [True, False])
def test_durable_nonbool_refused_before_host(home, tmp_path, monkeypatch, bad, with_plan):  # noqa: F811
    async def body(rig, provider):
        calls = record_host_start(rig, monkeypatch)
        kw = dict(plan=rig.plan, grants=rig.grants) if with_plan else {}
        with pytest.raises(LaunchError, match="durable_attempts must be a bool"):
            rig.launch.start(durable_attempts=bad, **kw)
        return calls, dict(rig.launch.members), supervision_tables(rig)

    rig, provider, (calls, members, tables) = scenario(home, tmp_path, monkeypatch, body)
    assert calls == [] and members == {} and tables == set() and provider.seen == []


@pytest.mark.parametrize("case", ["no_plan", "conversation", "grants_only"])
def test_durable_true_without_plan_refused_before_host(home, tmp_path, monkeypatch, case):  # noqa: F811
    async def body(rig, provider):
        calls = record_host_start(rig, monkeypatch)
        kw = {"no_plan": {},
              "conversation": dict(conversation=ConversationOptions(max_turns=2, idle_timeout=1.0)),
              "grants_only": dict(grants=rig.grants)}[case]
        with pytest.raises(LaunchError, match="durable_attempts requires a plan"):
            rig.launch.start(durable_attempts=True, **kw)
        return calls, dict(rig.launch.members), supervision_tables(rig)

    rig, provider, (calls, members, tables) = scenario(home, tmp_path, monkeypatch, body)
    assert calls == [] and members == {} and tables == set() and provider.seen == []


def test_durable_true_still_runs_existing_plan_checks(home, tmp_path, monkeypatch):  # noqa: F811
    async def body(rig, provider):
        calls = record_host_start(rig, monkeypatch)
        for kw in (dict(plan=rig.plan), dict(plan=rig.plan, grants=object()),
                   dict(plan=rig.plan, grants=rig.grants,
                        conversation=ConversationOptions(max_turns=2, idle_timeout=1.0))):
            with pytest.raises(LaunchError):
                rig.launch.start(durable_attempts=True, **kw)
        return calls

    _, _, calls = scenario(home, tmp_path, monkeypatch, body)
    assert calls == []


def test_durable_host_refusal_propagates_and_leaves_no_claim(home, tmp_path, monkeypatch):  # noqa: F811
    async def body(rig, provider):
        grants = make_grants(rig.launch, tmp_path, owner="mallory")
        with pytest.raises(HostError) as info:
            rig.launch.start(plan=rig.plan, grants=grants, durable_attempts=True)
        assert not isinstance(info.value, LaunchError)
        return dict(rig.launch.members), rig.plan_tables(), supervision_tables(rig), \
            rig.host.status(rig.gid)["receipt"]

    rig, provider, (members, tables, sup, receipt) = scenario(home, tmp_path, monkeypatch, body)
    assert members == {} and tables == set() and sup == set() and receipt is None and provider.seen == []


@pytest.mark.parametrize("durable", [True, False])
def test_real_engine_durable_flag_controls_ledger_tables(home, tmp_path, monkeypatch, durable):  # noqa: F811
    async def body(rig, provider):
        import asyncio
        rig.launch.start(plan=rig.plan, grants=rig.grants, durable_attempts=durable)
        await asyncio.wait_for(rig.launch.wait(), BOUND)
        return rig.states(), rig.plan_state(), supervision_tables(rig)

    rig, provider, (states, plan_state, tables) = scenario(home, tmp_path, monkeypatch, body)
    assert states == {"build": "completed", "check": "completed"} and plan_state == "settled"
    assert provider.marks == ["MARK-BUILD", "MARK-CHECK"]
    assert (len(tables) > 0) is durable
    assert rig.launch.closed
