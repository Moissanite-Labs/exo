"""``GroupLaunch.start(knowledge=)``: exact-bool opt-in over ``GroupHost.start(knowledge=)``.

Offline and hermetic: temp ``EXO_HOME``, ``EXO_NO_KEYCHAIN=1``, deterministic ``RuleFake`` only
(transport wrapper records prompts). Forwarding/validation tests patch ``host.start``; the real
CoreEngine proofs publish knowledge, start through ``launch.start`` and read member prompts.
Waits are bounded deadlines; every launch is closed in ``finally``.
"""
import asyncio

import pytest
from test_group_command_knowledge_e2e import (BOUND, TEXT_V1, assert_default_caps_snapshot, cmd,  # noqa: F401
                                              consumed, marks, publish_args, run, seat_home, seen,
                                              snapshot_of, until)
from test_group_plan_real_engine_e2e import PROFILES, home, run_scenario  # noqa: F401

from exo.core.group_host import ConversationOptions
from exo.core.group_knowledge import KNOWLEDGE_PREFIX
from exo.group_launch import LaunchError, create

BAD = [1, 0, "yes", "true", None, 1.0, [], {}]


def record(rig_or_launch, monkeypatch):
    host = getattr(rig_or_launch, "host")
    calls = []

    def fake_start(*args, **kwargs):
        calls.append((args, kwargs))
        return ("started",)

    monkeypatch.setattr(host, "start", fake_start)
    return calls


def scenario(home, tmp_path, monkeypatch, body):  # noqa: F811
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    return run_scenario(home, tmp_path, monkeypatch, "ok", body)


def test_default_and_false_host_calls_are_identical_to_legacy(home, tmp_path, monkeypatch):  # noqa: F811
    async def body(rig, provider):
        calls = record(rig, monkeypatch)
        options = ConversationOptions(max_turns=3, idle_timeout=1.0)
        for extra in ({}, {"knowledge": False}):
            rig.launch.start(**extra)
            rig.launch.start(conversation=options, max_concurrent=1, **extra)
            rig.launch.start(plan=rig.plan, grants=rig.grants, **extra)
            rig.launch.start(plan=rig.plan, grants=rig.grants, durable_attempts=True, **extra)
        return calls, options

    rig, _, (calls, options) = scenario(home, tmp_path, monkeypatch, body)
    assert len(calls) == 8
    for i in range(4):
        assert calls[i] == calls[i + 4]                       # false == omitted
        assert all("knowledge" not in kw for _, kw in calls[i:i + 1])
    assert calls[1][1] == {"conversation": options, "max_concurrent": 1}
    assert calls[2][1] == {"plan": rig.plan, "grants": rig.grants, "max_concurrent": None}


def test_true_is_forwarded_for_every_start_path(home, tmp_path, monkeypatch):  # noqa: F811
    async def body(rig, provider):
        calls = record(rig, monkeypatch)
        options = ConversationOptions(max_turns=3, idle_timeout=1.0)
        rig.launch.start(knowledge=True, max_concurrent=2)
        rig.launch.start(conversation=options, knowledge=True)
        rig.launch.start(plan=rig.plan, grants=rig.grants, knowledge=True)
        rig.launch.start(plan=rig.plan, grants=rig.grants, knowledge=True, durable_attempts=True)
        return calls, options

    rig, _, (calls, options) = scenario(home, tmp_path, monkeypatch, body)
    kws = [kw for _, kw in calls]
    assert kws[0] == {"audiences": {"builder": ["checker"], "checker": ["builder"]},
                      "max_concurrent": 2, "knowledge": True}
    assert kws[1] == {"conversation": options, "max_concurrent": None, "knowledge": True}
    assert kws[2] == {"plan": rig.plan, "grants": rig.grants, "max_concurrent": None,
                      "knowledge": True}
    assert kws[3] == {**kws[2], "durable_attempts": True}
    assert all(args == (rig.gid,) for args, _ in calls)
    assert kws[2]["plan"] is rig.plan and kws[2]["grants"] is rig.grants


@pytest.mark.parametrize("bad", BAD)
def test_non_bool_knowledge_rejected_before_host_allocation_or_claim(home, tmp_path, monkeypatch, bad):  # noqa: F811
    async def body(rig, provider):
        calls = record(rig, monkeypatch)
        options = ConversationOptions(max_turns=2, idle_timeout=1.0)
        for kw in ({}, {"conversation": options}, {"plan": rig.plan, "grants": rig.grants}):
            with pytest.raises(LaunchError, match="knowledge must be a bool"):
                rig.launch.start(knowledge=bad, **kw)
        return calls, dict(rig.launch.members), rig.plan_tables(), rig.host.status(rig.gid)["receipt"]

    rig, provider, (calls, members, tables, receipt) = scenario(home, tmp_path, monkeypatch, body)
    assert calls == [] and members == {} and tables == set() and receipt is None
    assert provider.seen == []


def test_knowledge_does_not_widen_plan_wrapper_checks(home, tmp_path, monkeypatch):  # noqa: F811
    async def body(rig, provider):
        calls = record(rig, monkeypatch)
        with pytest.raises(LaunchError, match="both plan and grants"):
            rig.launch.start(plan=rig.plan, knowledge=True)
        with pytest.raises(LaunchError, match="not a conversation start"):
            rig.launch.start(plan=rig.plan, grants=rig.grants, knowledge=True,
                             conversation=ConversationOptions(max_turns=2, idle_timeout=1.0))
        with pytest.raises(LaunchError, match="durable_attempts requires a plan"):
            rig.launch.start(knowledge=True, durable_attempts=True)
        return calls

    _, _, calls = scenario(home, tmp_path, monkeypatch, body)
    assert calls == []


def test_plan_start_with_knowledge_reaches_member_prompts(home, tmp_path, monkeypatch):  # noqa: F811
    async def body(rig, provider):
        await cmd(rig.launch, publish_args(rig.gid, TEXT_V1, 0))
        rig.launch.start(plan=rig.plan, grants=rig.grants, knowledge=True)
        await asyncio.wait_for(rig.launch.wait(), BOUND)
        return rig.states()

    rig, provider, states = scenario(home, tmp_path, monkeypatch, body)
    assert states == {"build": "completed", "check": "completed"}
    assert len(provider.seen) == 2
    for p in provider.seen:
        assert KNOWLEDGE_PREFIX in p
        snap = snapshot_of(p)
        assert snap["group_id"] == rig.gid and snap["member"] in PROFILES
        assert snap["items"][0]["text"] == TEXT_V1 and snap["items"][0]["revision"] == 1


@pytest.mark.parametrize("knowledge", ["omitted", False, True])
@pytest.mark.parametrize("form", ["oneshot", "conversation"])
def test_real_engines_launch_start_knowledge_reaches_member_prompts(tmp_path, monkeypatch, seen,  # noqa: F811
                                                                   form, knowledge):
    seat_home(tmp_path, monkeypatch, ["builder", "checker", "owner"])
    launch = create(seat="owner", profiles=["builder", "checker"], mode="socratic")
    gid = launch.group.id

    async def body():
        await cmd(launch, publish_args(gid, TEXT_V1, 0))
        await cmd(launch, f'send {{"id": "{gid}", "text": "MARK-ALPHA kickoff"}}')
        kw = {} if knowledge == "omitted" else {"knowledge": knowledge}
        if form == "conversation":
            kw["conversation"] = ConversationOptions(max_turns=4, idle_timeout=600)
        launch.start(**kw)
        await until(lambda: len(marks(seen, "MARK-ALPHA")) == 2
                    and consumed(launch, gid, "MARK-ALPHA") == {"builder": True, "checker": True},
                    "both members took their turn")
        if form == "conversation":
            await cmd(launch, f'stop {{"id": "{gid}"}}')
        await asyncio.wait_for(launch.wait(), BOUND)

    run(launch, body)
    prompts = marks(seen, "MARK-ALPHA")
    assert len(prompts) == 2
    if knowledge is True:
        assert {snapshot_of(p)["member"] for p in prompts} == {"builder", "checker"}
        for p in prompts:
            assert_default_caps_snapshot(p, gid, snapshot_of(p)["member"], TEXT_V1, 1)
    else:
        for p in seen:
            assert KNOWLEDGE_PREFIX not in p and "Ship on Friday" not in p


def test_invalid_knowledge_on_real_launch_allocates_nothing(tmp_path, monkeypatch, seen):  # noqa: F811
    seat_home(tmp_path, monkeypatch, ["builder", "checker"] + ["owner"])
    launch = create(seat="owner", profiles=["builder", "checker"], mode="socratic")

    async def body():
        with pytest.raises(LaunchError):
            launch.start(knowledge=1)
        assert launch.members == {} and seen == []
        assert launch.store._q("SELECT 1 FROM swarm_messages") == []
        # the host never saw the bad start, so a valid start is still possible
        launch.start(knowledge=True)
        await asyncio.wait_for(launch.wait(), BOUND)

    run(launch, body)
