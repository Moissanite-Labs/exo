import asyncio
import json
from types import SimpleNamespace

import pytest

from exo.core.group_conversation import (
    ConversationError,
    ConversationWake,
    GroupConversationAdapter,
    start_conversation,
)
from exo.core.group_messages import GroupMessages
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore


class FakeEngine:
    """Deterministic member engine: binds like CoreEngine, replays turns from a queue."""

    def __init__(self, profile, store):
        self.agent = profile
        self.store = store
        self.turns = []
        self.behavior = []
        self._turn_done: dict[int, asyncio.Event] = {}

    def turn_done(self, index) -> asyncio.Event:
        event = self._turn_done.get(index)
        if event is None:
            event = self._turn_done[index] = asyncio.Event()
        return event

    def push(self, behavior):
        self.behavior.append(behavior)

    async def run_turn(self, command):
        self.turns.append(command)
        self.turn_done(len(self.turns)).set()
        await asyncio.sleep(0)  # let concurrently sent messages land mid-turn
        behavior = self.behavior.pop(0) if self.behavior else {"kind": "end"}
        kind = behavior["kind"]
        if kind == "raise":
            raise RuntimeError("provider exploded")
        if kind == "interrupted":
            return SimpleNamespace(stop_reason="interrupted", error=None, message=None)
        if kind == "error":
            return SimpleNamespace(stop_reason="error", error="boom", message=None)
        reply = behavior.get("reply", f"reply-{len(self.turns)}")
        return SimpleNamespace(stop_reason="end", error=None,
                               message=SimpleNamespace(text=lambda: reply))


@pytest.fixture
def room():
    store = SessionStore()
    groups = GroupStore(store)
    group = groups.create(owner="host", mode="conductor", members=["lead", "one", "two"],
                          known_profiles=["lead", "one", "two"], conductor="lead")
    engines = {m.profile: FakeEngine(m.profile, store) for m in group.members}
    audiences = {"lead": ["one"], "one": ["lead"], "two": ["lead"]}
    wake = ConversationWake()
    yield SimpleNamespace(store=store, groups=groups, group=group, engines=engines,
                          audiences=audiences, wake=wake,
                          messages=GroupMessages(groups))
    store.close()


def make_adapter(room, **kw):
    return GroupConversationAdapter(room.group, room.engines,
                                    audiences=room.audiences, wake=room.wake, **kw)


def send_human(room, audience, text):
    return room.messages.send_human(room.group.id, owner="host", audience=audience, text=text)


def states_of(statuses):
    return {s.profile: s.state for s in statuses}


async def let_member_settle():
    # Scheduling yields only (no time-based waiting): let the member coroutine
    # finish the synchronous tail of its turn (publish, acknowledge, bookkeep).
    for _ in range(4):
        await asyncio.sleep(0)


async def stop_and_wait(runtime):
    # Lifecycle is event-driven (per-turn engine events, wake broadcasts,
    # checkpoint pauses). Reactive loops persist waiting for input, so the
    # host stops them explicitly; wait_for is solely a deadlock guard.
    runtime.stop()
    return await asyncio.wait_for(runtime.wait(), 30)


def test_binding_and_budget_validation(room):
    with pytest.raises(ConversationError):
        make_adapter(room, max_turns=0)
    with pytest.raises(ConversationError):
        make_adapter(room, max_turns=True)
    with pytest.raises(ConversationError):
        make_adapter(room, idle_timeout=0)
    with pytest.raises(ConversationError):
        GroupConversationAdapter(room.group, room.engines,
                                 audiences=room.audiences, wake=object())
    engines = dict(room.engines)
    engines["one"] = engines["lead"]  # shared engine
    with pytest.raises(ConversationError):
        GroupConversationAdapter(room.group, engines,
                                 audiences=room.audiences, wake=room.wake)
    audiences = dict(room.audiences)
    audiences["one"] = ["two"]  # worker must target conductor
    with pytest.raises(ConversationError):
        GroupConversationAdapter(room.group, room.engines,
                                 audiences=audiences, wake=room.wake)


def test_interleave_during_turn_and_idle_stop(room):
    # A message sent while member "two" executes its first turn is not
    # consumed by that turn; it arrives at the next checkpoint. Routing:
    # worker "two" only ever gets human messages (its peers route to the
    # conductor, never to it), while each of its replies wakes exactly one
    # conductor turn. Observations are event-driven (per-turn engine events);
    # the host stop afterwards is explicit, and wait_for is a deadlock guard.
    async def scenario():
        send_human(room, ["two"], "first")
        adapter = make_adapter(room, max_turns=2, idle_timeout=5)
        runtime = start_conversation(room.messages, room.group, owner="host",
                                     adapters=adapter.adapters(), wake=room.wake)
        await asyncio.wait_for(room.engines["two"].turn_done(1).wait(), 30)
        late = send_human(room, ["two"], "late")
        room.wake.set()  # host duty after every accepted send
        await asyncio.wait_for(room.engines["two"].turn_done(2).wait(), 30)
        await let_member_settle()
        first, second = room.engines["two"].turns[0], room.engines["two"].turns[1]
        assert "late" not in first.text
        assert json.loads(second.text.split("\n", 1)[1])[-1]["text"] == "late"
        # Both inputs consumed exactly once, by the turns that accepted them.
        assert room.messages.pending(room.group.id, owner="host", member="two") == []
        assert late in room.messages.history(room.group.id, owner="host", member="two")
        # Two's replies route to the conductor: lead turns once per reply and
        # replies only to worker one (its declared single audience).
        await asyncio.wait_for(room.engines["lead"].turn_done(2).wait(), 30)
        await let_member_settle()
        assert len(room.engines["lead"].turns) == 2
        for prompt in room.engines["lead"].turns:
            batch = json.loads(prompt.text.split("\n", 1)[1])
            assert {m["sender"] for m in batch} == {"two"}
        assert [m.sender for m in room.messages.history(
            room.group.id, owner="host", member="one")] == ["lead", "lead"]
        statuses = await stop_and_wait(runtime)
        outcomes = adapter.outcomes()
        # "two" and "lead" each exhausted their explicit budget of two turns;
        # nothing idle-exited and no budget was fabricated for a member that
        # did not earn it. Worker "one" received lead's replies but had not
        # started a turn when the host stopped: cancelled, zero turns charged.
        assert outcomes["two"].reason == "budget" and outcomes["two"].turns == 2
        assert outcomes["lead"].reason == "budget" and outcomes["lead"].turns == 2
        assert states_of(statuses)["two"] == "completed"
        assert states_of(statuses)["lead"] == "completed"
        assert states_of(statuses)["one"] == "stopped"
        assert outcomes["one"].reason == "cancelled" and outcomes["one"].turns == 0
    asyncio.run(scenario())


def test_initial_prompt_and_no_self_trigger(room):
    # The explicit host prompt reaches every member once; one turn per member,
    # then the loops persist but nothing self-triggers another turn, so the
    # runtime settles on its own once each member's budget is exhausted.
    async def scenario():
        adapter = make_adapter(room, max_turns=1, idle_timeout=5)
        runtime = start_conversation(room.messages, room.group, owner="host",
                                     adapters=adapter.adapters(), wake=room.wake,
                                     prompt="kickoff")
        statuses = await asyncio.wait_for(runtime.wait(), 30)
        assert states_of(statuses) == {"lead": "completed", "one": "completed",
                                       "two": "completed"}
        outcomes = adapter.outcomes()
        assert {p: o.turns for p, o in outcomes.items()} == {"lead": 1, "one": 1, "two": 1}
        assert all(o.reason == "budget" for o in outcomes.values())
        for engine in room.engines.values():
            assert len(engine.turns) == 1  # no self-triggered second turn
            assert "kickoff" in engine.turns[0].text
        with pytest.raises(ConversationError):
            start_conversation(room.messages, room.group, owner="host",
                               adapters=adapter.adapters(), wake=room.wake, prompt="  ")
    asyncio.run(scenario())


def test_pause_resume(room):
    # Pause is taken before the loop ever runs (host controls are synchronous
    # on the event loop), so the lead's turn cannot start while paused: the
    # checkpoint holds the pending input until resume() releases it.
    async def scenario():
        adapter = make_adapter(room, max_turns=1, idle_timeout=5)
        runtime = start_conversation(room.messages, room.group, owner="host",
                                     adapters=adapter.adapters(), wake=room.wake)
        runtime.pause("lead")
        assert room.engines["lead"].turns == []
        assert room.messages.pending(room.group.id, owner="host", member="lead") == []
        send_human(room, ["lead"], "hold")
        room.wake.set()  # host duty after every accepted send
        assert states_of(runtime.status())["lead"] == "pausing"
        assert room.messages.pending(room.group.id, owner="host", member="lead")
        runtime.resume("lead")
        await asyncio.wait_for(room.engines["lead"].turn_done(1).wait(), 30)
        await let_member_settle()
        outcomes = adapter.outcomes()
        assert outcomes["lead"].reason == "budget" and outcomes["lead"].turns == 1
        assert "hold" in room.engines["lead"].turns[0].text
        assert room.messages.pending(room.group.id, owner="host", member="lead") == []
        statuses = await stop_and_wait(runtime)
        assert states_of(statuses) == {"lead": "completed", "one": "stopped",
                                       "two": "stopped"}
    asyncio.run(scenario())


def test_conductor_privacy_and_audience_enforcement(room):
    # Only the conductor had human input, so only the conductor turns; its
    # reply routes to its declared audience (worker one) and never reaches
    # worker two, who only ever gets human messages addressed to it.
    async def scenario():
        send_human(room, ["lead"], "go")
        adapter = make_adapter(room, max_turns=1, idle_timeout=5)
        runtime = start_conversation(room.messages, room.group, owner="host",
                                     adapters=adapter.adapters(), wake=room.wake)
        await asyncio.wait_for(room.engines["lead"].turn_done(1).wait(), 30)
        await let_member_settle()
        assert [m.sender for m in room.messages.pending(room.group.id, owner="host",
                                                        member="one")] == ["lead"]
        assert room.messages.pending(room.group.id, owner="host", member="two") == []
        prompt = room.engines["lead"].turns[0].text
        senders = [json.loads(prompt.split("\n", 1)[1])]
        assert {m["sender"] for batch in senders for m in batch} <= {"lead", "host"}
        assert room.engines["one"].turns == [] and room.engines["two"].turns == []
        statuses = await stop_and_wait(runtime)
        assert states_of(statuses) == {"lead": "completed", "one": "stopped",
                                       "two": "stopped"}
        outcomes = adapter.outcomes()
        assert outcomes["lead"].reason == "budget" and outcomes["lead"].turns == 1
        for profile in ("one", "two"):
            assert outcomes[profile].reason == "cancelled"
            assert outcomes[profile].turns == 0
    asyncio.run(scenario())


def test_no_retry_on_error_and_failure_isolation(room):
    # Every member gets the prompt; member "two"'s engine fails on it once and
    # is never retried. Only a successful turn counts against the budget, so
    # the failed attempt records reason "error" with zero charged turns while
    # the peers complete by budget. Nothing is acked on failure.
    async def scenario():
        room.engines["two"].push({"kind": "error"})
        send_human(room, ["lead", "one", "two"], "kickoff")
        adapter = make_adapter(room, max_turns=1, idle_timeout=5)
        runtime = start_conversation(room.messages, room.group, owner="host",
                                     adapters=adapter.adapters(), wake=room.wake)
        statuses = await asyncio.wait_for(runtime.wait(), 30)
        assert states_of(statuses) == {"lead": "completed", "one": "completed",
                                       "two": "failed"}
        assert len(room.engines["two"].turns) == 1  # no automatic retry
        outcomes = adapter.outcomes()
        assert outcomes["two"].reason == "error"
        # Corrected turns semantics: only successful turns are charged; the
        # one failed engine attempt charged zero.
        assert outcomes["two"].turns == 0
        assert outcomes["lead"].reason == outcomes["one"].reason == "budget"
        assert outcomes["lead"].turns == outcomes["one"].turns == 1
        pending = room.messages.pending(room.group.id, owner="host", member="two")
        assert [m.text for m in pending] == ["kickoff"]
        assert runtime.receipt().members["two"] == "failed"
    asyncio.run(scenario())


def test_consume_failure_partial_ack_leaves_error_and_no_rerun(room):
    # Fault injection: the engine turn and the reply publication succeed, but
    # acknowledging the second of two inputs fails. The member records an
    # "error" outcome with zero charged turns, the durable receipt shows
    # "failed", the reply is published exactly once, the first input stays
    # consumed and the second stays pending (per-message acknowledgement is
    # not atomic), and nothing re-runs automatically. The host reconciles.
    async def scenario():
        first, second = (send_human(room, ["two"], text) for text in ("first", "second"))
        original = room.messages.consume

        def flaky_consume(group_id, *, owner, member, message_id):
            if message_id == second.id:
                raise RuntimeError("injected ack failure")
            return original(group_id, owner=owner, member=member, message_id=message_id)
        room.messages.consume = flaky_consume
        adapter = make_adapter(room, max_turns=2, idle_timeout=5)
        runtime = start_conversation(room.messages, room.group, owner="host",
                                     adapters=adapter.adapters(), wake=room.wake)
        statuses = await asyncio.wait_for(runtime.wait(), 30)
        assert states_of(statuses)["two"] == "failed"
        assert runtime.receipt().members["two"] == "failed"
        assert len(room.engines["two"].turns) == 1  # no automatic retry
        outcomes = adapter.outcomes()
        assert outcomes["two"].reason == "error" and outcomes["two"].turns == 0
        # Precise persisted state: reply published exactly once to the
        # conductor; the first input is consumed, the failed one is still
        # pending.
        replies = [m for m in room.messages.observe(room.group.id, owner="host")
                   if m.sender == "two"]
        assert [m.text for m in replies] == ["reply-1"]
        assert [m.text for m in room.messages.pending(
            room.group.id, owner="host", member="two")] == ["second"]
        observed = [m.text for m in room.messages.observe(room.group.id, owner="host")]
        assert observed[:3] == ["first", "second", "reply-1"]
        assert [m.text for m in room.messages.observe(room.group.id, owner="host")
                if m.sender == "two"] == ["reply-1"]  # exactly one reply from "two"
    asyncio.run(scenario())


def test_consume_failure_on_first_input_leaves_reply_published(room):
    # Same fault on the single input: publication is durable, nothing is
    # consumed, the outcome is "error" and no turn is charged or re-run.
    async def scenario():
        send_human(room, ["two"], "only")

        def always_failing_consume(group_id, *, owner, member, message_id):
            raise RuntimeError("injected ack failure")
        room.messages.consume = always_failing_consume
        adapter = make_adapter(room, max_turns=2, idle_timeout=5)
        runtime = start_conversation(room.messages, room.group, owner="host",
                                     adapters=adapter.adapters(), wake=room.wake)
        statuses = await asyncio.wait_for(runtime.wait(), 30)
        assert states_of(statuses)["two"] == "failed"
        assert runtime.receipt().members["two"] == "failed"
        assert len(room.engines["two"].turns) == 1
        outcomes = adapter.outcomes()
        assert outcomes["two"].reason == "error" and outcomes["two"].turns == 0
        assert [m.text for m in room.messages.pending(
            room.group.id, owner="host", member="two")] == ["only"]
        # The reply is durable and visible even though nothing was consumed.
        observed = room.messages.observe(room.group.id, owner="host")
        assert [m.text for m in observed if m.sender == "two"] == ["reply-1"]
    asyncio.run(scenario())


def test_stop_during_turn_swallowed_by_engine_classifies_cancelled(room):
    # A stop lands while the engine is mid-turn; the engine catches
    # CancelledError and returns an ordinary successful result. The adapter
    # must still classify the member cancelled before publishing or
    # consuming: zero charged turns, no reply, every input still pending.
    class SwallowingEngine(FakeEngine):
        async def run_turn(self, command):
            self.turns.append(command)
            self.turn_done(len(self.turns)).set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return SimpleNamespace(stop_reason="end", error=None,
                                       message=SimpleNamespace(text=lambda: "late reply"))
            return SimpleNamespace(stop_reason="end", error=None,
                                   message=SimpleNamespace(text=lambda: "reply"))
    room.engines["two"] = SwallowingEngine("two", room.store)

    async def scenario():
        send_human(room, ["two"], "kick")
        adapter = make_adapter(room, max_turns=10, idle_timeout=30)
        runtime = start_conversation(room.messages, room.group, owner="host",
                                     adapters=adapter.adapters(), wake=room.wake)
        await asyncio.wait_for(room.engines["two"].turn_done(1).wait(), 30)
        statuses = await stop_and_wait(runtime)
        assert states_of(statuses) == {"lead": "stopped", "one": "stopped",
                                       "two": "stopped"}
        outcomes = adapter.outcomes()
        assert outcomes["two"].reason == "cancelled" and outcomes["two"].turns == 0
        assert outcomes["lead"].reason == "cancelled"
        # Nothing was published or consumed after the swallowed cancellation.
        assert [m.text for m in room.messages.pending(
            room.group.id, owner="host", member="two")] == ["kick"]
        assert [m for m in room.messages.observe(room.group.id, owner="host")
                if m.sender == "two"] == []
    asyncio.run(scenario())


def test_stop_cancels_and_records_receipt(room):
    # A member mid-turn is cancelled with it; peers waiting for input are
    # cancelled too. Outcomes distinguish this host stop from budget or error,
    # and the in-flight turn is not charged.
    async def scenario():
        send_human(room, ["two"], "kick")
        adapter = make_adapter(room, max_turns=10, idle_timeout=30)
        runtime = start_conversation(room.messages, room.group, owner="host",
                                     adapters=adapter.adapters(), wake=room.wake)
        await asyncio.wait_for(room.engines["two"].turn_done(1).wait(), 30)
        statuses = await stop_and_wait(runtime)
        assert states_of(statuses) == {"lead": "stopped", "one": "stopped",
                                       "two": "stopped"}
        outcomes = adapter.outcomes()
        assert set(outcomes) == {"lead", "one", "two"}
        assert all(o.reason == "cancelled" for o in outcomes.values())
        assert outcomes["two"].turns == 0
        # The in-flight turn acked nothing.
        assert room.messages.pending(room.group.id, owner="host", member="two")
        assert all(state == "stopped" for state in runtime.receipt().members.values())
    asyncio.run(scenario())
