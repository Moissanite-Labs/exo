"""GroupHost conversation opt-in tests. Fakes only; no CoreEngine, no live install.

Covers the explicit ``conversation=ConversationOptions(...)`` opt-in on
``GroupHost.start``: a human send wakes an idle member loop through the
per-member ``ConversationWake`` events, invalid bounds are rejected before
the engine factory is invoked or any durable claim exists, and stop/close
settle conversations without lingering waiters. One lifecycle per
``asyncio.run`` loop; members are released with asyncio.Event, never sleeps.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from exo.core.group_execution import GroupExecutions
from exo.core.group_host import (DEFAULT_IDLE_TIMEOUT, DEFAULT_MAX_TURNS,
                                 ConversationOptions, GroupHost, HostError)
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore

PROFILES = ["lead", "one", "two"]


class SpyEngine:
    """Deterministic engine that records prompts and signals each turn end."""

    def __init__(self, profile, store):
        self.agent = profile
        self.store = store
        self.turns = []
        self._done = {}

    def turn_done(self, index) -> asyncio.Event:
        return self._done.setdefault(index, asyncio.Event())

    async def run_turn(self, command):
        self.turns.append(command)
        self.turn_done(len(self.turns)).set()
        await asyncio.sleep(0)  # let concurrently sent messages land mid-turn
        return SimpleNamespace(stop_reason="end", error=None,
                               message=SimpleNamespace(text=lambda: "ack"))


@pytest.fixture
def room():
    store = SessionStore()
    groups = GroupStore(store)
    engines = {}

    def factory(group):
        # One distinct engine per member; the shared dict lets tests spy on turns.
        return {m.profile: engines.setdefault(m.profile, SpyEngine(m.profile, store))
                for m in group.members}

    host = GroupHost(groups, owner="host", known_profiles=PROFILES, engine_factory=factory)
    yield SimpleNamespace(store=store, groups=groups, host=host, engines=engines)
    store.close()


def create(host):
    return host.create(mode="conductor", members=PROFILES, conductor="lead")


async def let_member_settle():
    # Scheduling yields only: let the member coroutine finish the synchronous
    # tail of its turn (publish, acknowledge, bookkeeping).
    for _ in range(4):
        await asyncio.sleep(0)


async def wait_idle_wake_cleared(wake, profile):
    # Event synchronization, not a sleep: the member is parked in its idle
    # wake wait once its own event has been consumed (cleared) by its loop.
    event = wake._events[profile]
    for _ in range(1000):
        if not event.is_set():
            return
        await asyncio.sleep(0)
    raise AssertionError("member loop never reached its idle wake wait")


def test_opt_in_wakes_idle_member_on_human_send(room):
    """A human send after an opt-in conversation start persists, sets the
    idle member's wake event and drives exactly one engine turn; nothing
    activates a client or a protocol command."""
    host, engines, groups = room.host, room.engines, room.groups
    group = create(host)

    async def scenario():
        host.start(group.id, conversation=ConversationOptions(max_turns=3,
                                                              idle_timeout=60))
        wake = host._wakes[group.id]
        assert wake is not None  # stored only after a successful start
        await wait_idle_wake_cleared(wake, "two")
        assert all(len(e.turns) == 0 for e in engines.values())  # idle, no poll

        note = host.send(group.id, audience=["two"], text="hello")
        assert wake._events["two"].is_set()  # host send woke this member's event
        await asyncio.wait_for(engines["two"].turn_done(1).wait(), 30)
        await let_member_settle()

        prompt = engines["two"].turns[0].text
        assert json.loads(prompt.split("\n", 1)[1]) == [
            {"sender": "host", "sender_kind": "human", "text": "hello"}]
        # The input was consumed exactly once after the successful turn.
        assert host._messages.pending(group.id, owner="host", member="two") == []
        # Audiences derived from the stored conductor mode: the worker's reply
        # routes to the conductor only, never to the other worker.
        assert [m.text for m in host._messages.pending(group.id, owner="host",
                                                       member="lead")] == ["ack"]
        assert host._messages.pending(group.id, owner="host", member="one") == []
        assert note in host._messages.history(group.id, owner="host", member="two")

        host.stop(group.id)
        result = {s.profile: s.state
                  for s in await asyncio.wait_for(host.wait(group.id), 30)}
        assert set(result.values()) <= {"completed", "stopped"}
        receipt = GroupExecutions(groups).get(group.id, owner="host")
        assert receipt is not None and receipt.state == "settled"
        # One durable claim per group: a conversation restart is refused.
        with pytest.raises(HostError, match="already started"):
            host.start(group.id, conversation=ConversationOptions())

    asyncio.run(scenario())


def test_send_before_conversation_start_picked_up_by_initial_wake(room):
    """Input persisted before the opt-in start needs no idle poll: the
    post-start initial wake delivers it at the first checkpoint, so a large
    idle_timeout (60s here) is never the delivery path."""
    host, engines = room.host, room.engines
    group = create(host)

    async def scenario():
        host.send(group.id, audience=["two"], text="early")
        host.start(group.id, conversation=ConversationOptions(max_turns=1,
                                                              idle_timeout=60))
        await asyncio.wait_for(engines["two"].turn_done(1).wait(), 30)
        await let_member_settle()
        assert "early" in engines["two"].turns[0].text
        assert host._messages.pending(group.id, owner="host", member="two") == []
        host.stop(group.id)
        await asyncio.wait_for(host.wait(group.id), 30)

    asyncio.run(scenario())


def test_invalid_conversation_bounds_rejected_before_factory_or_claim(room):
    """A bad max_turns/idle_timeout, or audiences passed with the opt-in,
    leaves no engines, no runtime and no durable claim: the caller may fix
    the inputs and start once. The opt-in defaults mirror the module's."""
    assert ConversationOptions().max_turns == DEFAULT_MAX_TURNS
    assert ConversationOptions().idle_timeout == DEFAULT_IDLE_TIMEOUT
    groups, host = room.groups, room.host
    group = create(host)
    called = []

    def spying(g):
        called.append(True)
        return {}

    spy = GroupHost(groups, owner="host", known_profiles=PROFILES,
                    engine_factory=spying)
    rejected = [ConversationOptions(max_turns=0),
                ConversationOptions(max_turns=True),
                ConversationOptions(max_turns=1.5),
                ConversationOptions(idle_timeout=0),
                ConversationOptions(idle_timeout=-1),
                ConversationOptions(idle_timeout=True),
                ConversationOptions(idle_timeout="30")]
    for options in rejected:
        with pytest.raises(HostError):
            spy.start(group.id, conversation=options)
    with pytest.raises(HostError, match="do not pass them"):
        spy.start(group.id, audiences={"lead": ["one"], "one": ["lead"],
                                       "two": ["lead"]},
                  conversation=ConversationOptions())
    assert not called  # the factory was never invoked for any rejected start
    assert GroupExecutions(groups).get(group.id, owner="host") is None  # no claim
    snap = host.status(group.id)
    assert snap["receipt"] is None and snap["reconciliation_required"] is False
    assert {s.state for s in snap["members"]} == {"idle"}


@pytest.mark.parametrize("options", [
    pytest.param(ConversationOptions(max_turns=0), id="max_turns-zero"),
    pytest.param(ConversationOptions(max_turns=-1), id="max_turns-negative"),
    pytest.param(ConversationOptions(max_turns=10001),
                 id="max_turns-above-adapter-bound"),
    pytest.param(ConversationOptions(max_turns=1.5), id="max_turns-float"),
    pytest.param(ConversationOptions(max_turns=float("inf")), id="max_turns-inf"),
    pytest.param(ConversationOptions(max_turns=float("nan")), id="max_turns-nan"),
    pytest.param(ConversationOptions(max_turns=True), id="max_turns-bool-true"),
    pytest.param(ConversationOptions(max_turns=False), id="max_turns-bool-false"),
    pytest.param(ConversationOptions(max_turns="3"), id="max_turns-str"),
    pytest.param(ConversationOptions(idle_timeout=0), id="idle_timeout-zero"),
    pytest.param(ConversationOptions(idle_timeout=-1), id="idle_timeout-negative"),
    pytest.param(ConversationOptions(idle_timeout=86400.5),
                 id="idle_timeout-above-adapter-bound"),
    pytest.param(ConversationOptions(idle_timeout=float("inf")),
                 id="idle_timeout-inf"),
    pytest.param(ConversationOptions(idle_timeout=float("nan")),
                 id="idle_timeout-nan"),
    pytest.param(ConversationOptions(idle_timeout=True), id="idle_timeout-bool-true"),
    pytest.param(ConversationOptions(idle_timeout=False),
                 id="idle_timeout-bool-false"),
    pytest.param(ConversationOptions(idle_timeout="30"), id="idle_timeout-str"),
])
def test_invalid_bound_parametrized_rejected_before_factory_or_claim(room, options):
    """Each invalid bound mirrors the adapter's full check (range, exact
    type, bool, finiteness) and is rejected by the host before the factory
    is invoked or any durable claim exists."""
    groups = room.groups
    group = create(room.host)
    called = []

    def spying(g):
        called.append(True)
        return {}

    spy = GroupHost(groups, owner="host", known_profiles=PROFILES,
                    engine_factory=spying)
    with pytest.raises(HostError):
        spy.start(group.id, conversation=options)
    assert not called  # the factory was never invoked for the rejected start
    assert GroupExecutions(groups).get(group.id, owner="host") is None  # no claim
    snap = spy.status(group.id)
    assert snap["receipt"] is None and snap["reconciliation_required"] is False
    assert {s.state for s in snap["members"]} == {"idle"}


@pytest.mark.parametrize("options", [
    pytest.param(ConversationOptions(max_turns=1, idle_timeout=60),
                 id="lower-max_turns-bound"),
    pytest.param(ConversationOptions(max_turns=10000, idle_timeout=60),
                 id="upper-max_turns-bound"),
    pytest.param(ConversationOptions(max_turns=2, idle_timeout=86400),
                 id="upper-idle_timeout-bound"),
])
def test_bounds_at_adapter_limits_are_accepted(room, options):
    """The exact adapter bounds (1..10000 turns, (0, 86400] seconds) pass
    host validation and start, stop and settle normally."""
    host = room.host
    group = create(host)

    async def scenario():
        host.start(group.id, conversation=options)
        host.stop(group.id)
        result = {s.profile: s.state
                  for s in await asyncio.wait_for(host.wait(group.id), 30)}
        assert set(result.values()) <= {"completed", "stopped"}

    asyncio.run(scenario())


def test_conversation_stop_and_aclose_settle_and_clear_wakes(room):
    """stop cancels the conversation member loops; aclose then settles the
    runtime, closes the engine factory only after settlement, and closes and
    drops the stored wake so no idle member lingers in a long idle wait."""
    store, groups, engines = room.store, room.groups, room.engines
    order = []

    class Factory:
        def __call__(self, g):
            return {m.profile: engines.setdefault(m.profile, SpyEngine(m.profile, store))
                    for m in g.members}

        async def aclose(self):
            order.append("factory_close")

    host2 = GroupHost(groups, owner="host", known_profiles=PROFILES,
                      engine_factory=Factory())
    group = host2.create(mode="conductor", members=PROFILES, conductor="lead")

    async def scenario():
        host2.start(group.id, conversation=ConversationOptions(max_turns=2,
                                                               idle_timeout=60))
        host2.send(group.id, audience=["two"], text="kick")
        await asyncio.wait_for(engines["two"].turn_done(1).wait(), 30)
        host2.stop(group.id)
        result = {s.profile: s.state
                  for s in await asyncio.wait_for(host2.wait(group.id), 30)}
        assert set(result.values()) <= {"completed", "stopped"}
        assert group.id in host2._wakes  # the wake survives stop, not close
        await host2.aclose()
        assert host2._closed is True
        assert host2._runtimes == {}
        assert host2._wakes == {}  # closed and dropped: no lingering waiter
        with pytest.raises(HostError, match="closing or closed"):
            host2.start(group.id, conversation=ConversationOptions())
        # send still persists after close; no wake exists to set any more.
        note = host2.send(group.id, audience=["two"], text="after close")
        assert note.text == "after close"
        assert order == ["factory_close"]  # factory closed after settlement

    asyncio.run(scenario())
