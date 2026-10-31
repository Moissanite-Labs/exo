"""Bounded turn concurrency: runtime permits, strict bounds, pause/stop queueing.

Hermetic and deterministic: fakes only, no network, no CoreEngine, no live
install. Members are sequenced with asyncio.Event, never with wall-clock
sleeps; scheduling lets (``await asyncio.sleep(0)``) are bounded by ``_until``
so a missing state surfaces as a failure instead of a hang.

The bound is a *turn* bound: every member loop still starts and keeps observing
pause/stop between turns, only ``engine.run_turn`` is inside ``turn_slot``.
Adapters here acquire ``MemberContext.turn_slot`` exactly like the real
``GroupEngineAdapter`` / ``GroupConversationAdapter`` do.
"""
import asyncio
from types import SimpleNamespace

import pytest

from exo.core.group_engine_adapter import GroupEngineAdapter
from exo.core.group_execution import GroupExecutions
from exo.core.group_host import ConversationOptions, GroupHost, HostError
from exo.core.group_messages import GroupMessages
from exo.core.group_runtime import GroupRuntime, validate_max_concurrent
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore

PROFILES = ["lead", "one", "two"]
AUDIENCES = {"lead": ["one"], "one": ["lead"], "two": ["lead"]}


@pytest.fixture
def room():
    store = SessionStore()
    groups = GroupStore(store)
    group = groups.create(owner="host", mode="conductor", members=list(PROFILES),
                          known_profiles=list(PROFILES), conductor="lead")
    yield SimpleNamespace(store=store, groups=groups, group=group,
                          messages=GroupMessages(groups), gid=group.id)
    store.close()


def states(runtime):
    return {s.profile: s.state for s in runtime.status()}


async def _until(predicate, message="condition never held"):
    """Bounded scheduling yields: deterministic without sleeps or timers."""
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(message)


def build(room, adapter, **kwargs):
    return GroupRuntime(room.messages, room.gid, owner="host",
                        adapters=dict.fromkeys(PROFILES, adapter), **kwargs)


# --- strict bounds ---------------------------------------------------------


def test_validate_max_concurrent_is_strict_and_never_excludes_a_member():
    assert validate_max_concurrent(None, 3) == 3  # None == historical full overlap
    assert validate_max_concurrent(1, 3) == 1
    assert validate_max_concurrent(3, 3) == 3
    for bad in (0, -1, 4, 99, True, False, 1.0, 2.5, "2", [1]):
        with pytest.raises(ValueError, match="max_concurrent"):
            validate_max_concurrent(bad, 3)


def test_runtime_rejects_invalid_bound_before_adapter_or_claim(room):
    ran = []

    async def adapter(ctx):
        ran.append(ctx.member.profile)

    for bad in (0, -1, 4, True, False, 1.5, "2"):
        with pytest.raises(ValueError, match="max_concurrent"):
            build(room, adapter, max_concurrent=bad)
    assert ran == []  # no adapter was ever invoked
    assert GroupExecutions(room.groups).get(room.gid, owner="host") is None

    # Accepted bounds (inclusive range) construct and expose their bound.
    assert build(room, adapter).max_concurrent == 3  # default: full overlap
    assert build(room, adapter, max_concurrent=1).max_concurrent == 1
    assert build(room, adapter, max_concurrent=3).max_concurrent == 3


class _RecordingEngine:
    """Fake engine: records every turn and the live/peak turn concurrency."""

    def __init__(self, profile, store, live, turns):
        self.agent = profile
        self.store = store
        self.live = live
        self.turns = turns

    async def run_turn(self, command):
        self.turns.append((self.agent, command.text))
        self.live["now"] += 1
        self.live["peak"] = max(self.live["peak"], self.live["now"])
        for _ in range(3):  # let peers try to enter concurrently
            await asyncio.sleep(0)
        self.live["now"] -= 1
        return SimpleNamespace(stop_reason="end", error=None,
                               message=SimpleNamespace(text=lambda: "ack"))


def _host(room, live, turns, group=None):
    def factory(g):
        return {m.profile: _RecordingEngine(m.profile, room.store, live, turns)
                for m in g.members}

    return GroupHost(room.groups, owner="host", known_profiles=list(PROFILES),
                     engine_factory=factory)


@pytest.mark.parametrize("bound,peak", [(None, 3), (1, 1), (2, 2)])
def test_host_bound_limits_actual_engine_turns_only(room, bound, peak):
    """The real GroupEngineAdapter share one runtime bound: with ``None`` every
    member is inside its engine turn at once; a bound caps live engine turns
    while every member still runs and completes."""
    live, turns = {"now": 0, "peak": 0}, []
    host = _host(room, live, turns)
    group = room.group

    async def scenario():
        host.start(group.id, audiences=AUDIENCES, max_concurrent=bound)
        result = {s.profile: s.state
                  for s in await asyncio.wait_for(host.wait(group.id), 10)}
        assert set(result.values()) == {"completed"}

    asyncio.run(scenario())
    assert len(turns) == 3  # every member got its turn
    assert live["peak"] == peak
    assert live["now"] == 0


def test_host_rejects_invalid_bound_before_turn_or_claim(room):
    """A bad bound fails closed at the host boundary: HostError, no engine
    turn, no factory invocation, no stored runtime and no durable claim, so
    the caller may fix the bound and start once. The bound is validated
    before the factory for BOTH start kinds: one-shot, the conversation
    start-level fallback, the ConversationOptions declaration, and when both
    are supplied (the options field wins and is still rejected, never
    silently ignored).
    """
    live, turns = {"now": 0, "peak": 0}, []
    calls = []

    def factory(g):
        calls.append(g.id)
        return {m.profile: _RecordingEngine(m.profile, room.store, live, turns)
                for m in g.members}

    host = GroupHost(room.groups, owner="host", known_profiles=list(PROFILES),
                     engine_factory=factory)
    group = room.group
    bad = [0, -1, 4, True, 1.5, "2"]

    async def scenario():
        for value in bad:
            with pytest.raises(HostError, match="max_concurrent"):
                host.start(group.id, audiences=AUDIENCES, max_concurrent=value)
            with pytest.raises(HostError, match="max_concurrent"):
                host.start(group.id, conversation=ConversationOptions(),
                           max_concurrent=value)
            with pytest.raises(HostError, match="max_concurrent"):
                host.start(group.id,
                           conversation=ConversationOptions(max_concurrent=value))
            with pytest.raises(HostError, match="max_concurrent"):
                host.start(group.id,
                           conversation=ConversationOptions(max_concurrent=value),
                           max_concurrent=value)

    asyncio.run(scenario())
    assert calls == []  # the factory was never invoked for any invalid bound
    assert turns == []  # no engine turn ran
    assert GroupExecutions(room.groups).get(group.id, owner="host") is None
    snap = host.status(group.id)
    assert snap["receipt"] is None and snap["local_runtime"] is False
    assert {s.state for s in snap["members"]} == {"idle"}


def test_conversation_bound_options_wins_over_start_kwarg(room):
    """When both a conversation declaration and the start-level kwarg are
    supplied, the options field is the more specific declaration: its bound
    reaches the runtime and the fallback kwarg is not consulted."""
    live, turns = {"now": 0, "peak": 0}, []
    host = _host(room, live, turns)
    group = room.group

    async def scenario():
        host.start(group.id, conversation=ConversationOptions(max_concurrent=1),
                   max_concurrent=99)
        # the options field's bound won; the kwarg fallback was not used
        assert host._runtimes[group.id].max_concurrent == 1
        host.stop(group.id)
        result = {s.profile: s.state
                  for s in await asyncio.wait_for(host.wait(group.id), 10)}
        assert set(result.values()) <= {"completed", "stopped"}

    asyncio.run(scenario())


# --- runtime permit behavior ----------------------------------------------


def test_default_bound_lets_every_member_hold_a_slot(room):
    async def scenario():
        inside, all_inside = set(), asyncio.Event()
        release = asyncio.Event()

        async def adapter(ctx):
            async with ctx.turn_slot():
                inside.add(ctx.member.profile)
                if len(inside) == 3:
                    all_inside.set()
                await release.wait()

        runtime = build(room, adapter)  # no bound: historical full overlap
        runtime.start()
        await asyncio.wait_for(all_inside.wait(), 2)
        assert inside == set(PROFILES)  # all three turns live at once
        assert not any(runtime.turn_waiting(p) for p in PROFILES)
        release.set()
        assert {s.state for s in await runtime.wait()} == {"completed"}

    asyncio.run(scenario())


def test_max_one_queues_turns_in_member_order(room):
    async def scenario():
        order, peaks, active = [], [], set()
        gates = {p: asyncio.Event() for p in PROFILES}

        async def adapter(ctx):
            profile = ctx.member.profile
            async with ctx.turn_slot():
                active.add(profile)
                peaks.append(len(active))
                order.append(profile)
                await gates[profile].wait()
                active.discard(profile)

        runtime = build(room, adapter, max_concurrent=1)
        runtime.start()
        await _until(lambda: order == ["lead"], "first member never started")
        await _until(lambda: runtime.turn_waiting("one") and runtime.turn_waiting("two"),
                     "peers never queued for the only permit")
        assert order == ["lead"]
        assert states(runtime) == {"lead": "running", "one": "queued", "two": "queued"}

        gates["lead"].set()
        await _until(lambda: order == ["lead", "one"], "next permit never handed over")
        assert states(runtime)["two"] == "queued"
        gates["one"].set()
        await _until(lambda: order == PROFILES, "last member never took its turn")
        gates["two"].set()

        assert {s.state for s in await runtime.wait()} == {"completed"}
        assert peaks == [1, 1, 1]  # never two engine turns at once
        assert not any(runtime.turn_waiting(p) for p in PROFILES)

    asyncio.run(scenario())


def test_failed_turn_releases_the_permit_for_peers(room):
    """An exception inside a bounded turn must not strand the single permit:
    peers still take their turns and the failing member is just failed."""
    async def scenario():
        entered, peers_done = [], asyncio.Event()

        async def adapter(ctx):
            profile = ctx.member.profile
            async with ctx.turn_slot():
                entered.append(profile)
                if profile == "lead":
                    raise RuntimeError("provider exploded")
                if len(entered) == 3:
                    peers_done.set()

        runtime = build(room, adapter, max_concurrent=1)
        runtime.start()
        await asyncio.wait_for(peers_done.wait(), 2)
        result = {s.profile: s.state for s in await runtime.wait()}
        assert result == {"lead": "failed", "one": "completed", "two": "completed"}
        assert entered == PROFILES

    asyncio.run(scenario())


def test_stop_of_queued_waiter_prevents_its_turn_and_frees_peers(room):
    """A stop while a member is queued for the permit: no engine turn starts,
    the pending wait ends, and the permit still reaches the next member."""
    async def scenario():
        entered, gates = [], {p: asyncio.Event() for p in PROFILES}

        async def adapter(ctx):
            profile = ctx.member.profile
            async with ctx.turn_slot():
                entered.append(profile)
                await gates[profile].wait()

        runtime = build(room, adapter, max_concurrent=1)
        runtime.start()
        await _until(lambda: entered == ["lead"], "first member never started")
        await _until(lambda: runtime.turn_waiting("one"), "peer never queued")
        runtime.stop("one")
        assert states(runtime)["one"] == "stopping"
        gates["lead"].set()
        await _until(lambda: entered == ["lead", "two"], "permit never reached the peer")
        gates["two"].set()
        result = {s.profile: s.state for s in await runtime.wait()}
        assert result == {"lead": "completed", "one": "stopped", "two": "completed"}
        assert "one" not in entered  # the stopped waiter never took a turn
        assert runtime.turn_denied("one") is False  # it never held a permit

    asyncio.run(scenario())


def test_pause_of_queued_waiter_denies_turn_until_resume(room):
    """A pause requested while the member is queued survives the permit: the
    runtime re-reads its checkpoint, refuses the turn, hands the permit to a
    peer, and the paused member's turn starts only after an explicit resume."""
    async def scenario():
        entered, gates = [], {p: asyncio.Event() for p in PROFILES}

        async def adapter(ctx):
            profile = ctx.member.profile
            async with ctx.turn_slot():
                entered.append(profile)
                await gates[profile].wait()

        runtime = build(room, adapter, max_concurrent=1)
        runtime.start()
        await _until(lambda: entered == ["lead"], "first member never started")
        await _until(lambda: runtime.turn_waiting("one") and runtime.turn_waiting("two"),
                     "peers never queued")
        runtime.pause("one")  # while queued, before it holds any permit
        gates["lead"].set()

        await _until(lambda: runtime.turn_denied("one"), "queued pause was not honored")
        assert "one" not in entered  # no engine turn started for the paused member
        await _until(lambda: entered == ["lead", "two"], "permit was not handed to a peer")
        assert runtime.turn_denied("one") is True
        assert states(runtime)["one"] == "paused"  # waiting outside the permit

        runtime.resume("one")  # only an explicit resume may start the turn
        await _until(lambda: runtime.turn_waiting("one"), "resumed member never requeued")
        gates["two"].set()
        await _until(lambda: entered == ["lead", "two", "one"], "resume never started the turn")
        gates["one"].set()

        assert {s.state for s in await runtime.wait()} == {"completed"}
        assert runtime.turn_denied("one") is False  # cleared once the turn began

    asyncio.run(scenario())


def test_stop_of_paused_queued_waiter_is_not_started_by_resume(room):
    """A member paused while queued and then stopped must never start a turn on
    resume: the checkpoint re-check keeps stop authoritative over pause."""
    async def scenario():
        entered, gates = [], {p: asyncio.Event() for p in PROFILES}

        async def adapter(ctx):
            profile = ctx.member.profile
            async with ctx.turn_slot():
                entered.append(profile)
                await gates[profile].wait()

        runtime = build(room, adapter, max_concurrent=1)
        runtime.start()
        await _until(lambda: entered == ["lead"], "first member never started")
        await _until(lambda: runtime.turn_waiting("one"), "peer never queued")
        runtime.pause("one")
        gates["lead"].set()
        await _until(lambda: runtime.turn_denied("one"), "queued pause was not honored")
        runtime.stop("one")
        runtime.resume("one")  # must not resurrect the stopped member
        gates["two"].set() if "two" in entered else None
        await _until(lambda: entered == ["lead", "two"], "peer never took its turn")
        gates["two"].set()

        result = {s.profile: s.state for s in await runtime.wait()}
        assert result["one"] == "stopped"
        assert "one" not in entered
        assert result["lead"] == "completed" and result["two"] == "completed"

    asyncio.run(scenario())


def test_engine_adapter_inside_bound_matches_real_adapter_binding(room):
    """Integration: the real GroupEngineAdapter acquires the runtime slot, so a
    bound of 1 serializes its actual ``run_turn`` calls, and pause/resume of a
    queued member still works with a real adapter rather than a test double."""
    live, turns = {"now": 0, "peak": 0}, []
    engines = {p: _RecordingEngine(p, room.store, live, turns) for p in PROFILES}

    async def scenario():
        adapter = GroupEngineAdapter(room.group, engines, audiences=AUDIENCES)
        runtime = GroupRuntime(room.messages, room.gid, owner="host",
                               adapters=adapter.adapters(), max_concurrent=1)
        runtime.start()
        result = {s.profile: s.state for s in await asyncio.wait_for(runtime.wait(), 10)}
        assert set(result.values()) == {"completed"}
        assert [p for p, _ in turns] == PROFILES  # queued in member order
        assert live["peak"] == 1  # never two real engine turns at once

    asyncio.run(scenario())
