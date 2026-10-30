"""GroupHost knowledge opt-in context tests (M02-M04). Fakes only.

Covers the explicit ``knowledge=True`` opt-in on both host start paths: the
one-shot ``GroupEngineAdapter`` default and the reactive
``GroupConversationAdapter``. Every member turn receives the group's
owner-published knowledge as a *bounded* snapshot of labelled, quoted,
untrusted reference data. With ``knowledge=False`` (the default) prompts are
byte-identical to the knowledge-free prompt. Bad ``knowledge`` flags and bad
snapshot bounds are rejected before the factory is invoked or any durable
claim exists, and a snapshot that cannot fit its bounds fails closed rather
than being sliced or half-rendered. One lifecycle per ``asyncio.run`` loop;
members are released with asyncio.Event, never sleeps.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from exo.core.group_execution import GroupExecutions
from exo.core.group_host import (DEFAULT_IDLE_TIMEOUT, DEFAULT_MAX_TURNS,
                                 ConversationOptions, GroupHost, HostError)
from exo.core.group_knowledge import (DEFAULT_MAX_CHARS, DEFAULT_MAX_ITEMS,
                                      KNOWLEDGE_PREFIX, MIN_SNAPSHOT_CHARS,
                                      PROMPT_PREFIX, SNAPSHOT_NOTE,
                                      GroupKnowledge)
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore

PROFILES = ["lead", "one", "two"]
AUDIENCES = {"lead": ["one"], "one": ["lead"], "two": ["lead"]}


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
        return {m.profile: engines.setdefault(m.profile, SpyEngine(m.profile, store))
                for m in group.members}

    host = GroupHost(groups, owner="host", known_profiles=PROFILES, engine_factory=factory)
    yield SimpleNamespace(store=store, groups=groups, host=host, engines=engines)
    store.close()


@pytest.fixture
def kb():
    """Knowledge store plus one host group and one foreign group (same members)."""
    sessions = SessionStore()
    groups = GroupStore(sessions)
    knowledge = GroupKnowledge(groups)
    group = groups.create(owner="host", mode="socratic",
                          members=["alpha", "beta"],
                          known_profiles=["alpha", "beta"])
    other = groups.create(owner="other", mode="socratic",
                          members=["alpha", "beta"],
                          known_profiles=["alpha", "beta"])
    yield knowledge, group, other
    sessions.close()


def create(host):
    return host.create(mode="conductor", members=PROFILES, conductor="lead")


def publish(knowledge, group, **kw):
    args = dict(owner="host", key="plan", text="Ship M02 on Friday",
                sources=["doc:roadmap.md"])
    args.update(kw)
    return knowledge.publish(group.id, **args)


async def let_member_settle():
    for _ in range(4):
        await asyncio.sleep(0)


async def wait_idle_wake_cleared(wake, profile):
    event = wake._events[profile]
    for _ in range(1000):
        if not event.is_set():
            return
        await asyncio.sleep(0)
    raise AssertionError("member loop never reached its idle wake wait")


def assert_snapshot_prompt(prompt, group_id, member, *, keys, items, max_items,
                           max_chars, returned, truncated):
    """The prompt is PROMPT_PREFIX + messages JSON + KNOWLEDGE_PREFIX + snapshot JSON."""
    msgs = json.dumps([{"sender": "host", "sender_kind": "human", "text": "hello"}])
    assert prompt.startswith(PROMPT_PREFIX)
    assert prompt[len(PROMPT_PREFIX):len(PROMPT_PREFIX) + len(msgs)] == msgs
    assert KNOWLEDGE_PREFIX in prompt
    snapshot = json.loads(prompt.split(KNOWLEDGE_PREFIX, 1)[1])
    assert snapshot["kind"] == "group_knowledge"
    assert snapshot["group_id"] == group_id
    assert snapshot["member"] == member
    assert snapshot["max_items"] == max_items
    assert snapshot["max_chars"] == max_chars
    assert snapshot["returned"] == returned
    assert snapshot["truncated"] is truncated
    assert [i["key"] for i in snapshot["items"]] == keys
    assert snapshot["items"] == items


def test_oneshot_knowledge_optin_injects_bounded_snapshot(room):
    """The default one-shot host path passes the reader to the engine adapter:
    a member turn carries the bounded snapshot right after the participant
    messages, and never before them."""
    host, engines, groups = room.host, room.engines, room.groups
    group = create(host)
    knowledge = GroupKnowledge(groups)
    plan = publish(knowledge, group)
    ops = publish(knowledge, group, key="ops", text="Stand up staging",
                  sources=["doc:ops.md"])

    async def scenario():
        host.send(group.id, audience=["two"], text="hello")
        host.start(group.id, audiences=AUDIENCES, knowledge=True)
        await asyncio.wait_for(host.wait(group.id), 30)

        prompt = engines["two"].turns[0].text
        items = [
            {"key": "ops", "revision": 1, "text": "Stand up staging",
             "sources": ["doc:ops.md"], "publisher": "host",
             "updated": ops.updated, "truncated": False},
            {"key": "plan", "revision": 1, "text": "Ship M02 on Friday",
             "sources": ["doc:roadmap.md"], "publisher": "host",
             "updated": plan.updated, "truncated": False},
        ]
        assert_snapshot_prompt(
            prompt, group.id, "two", keys=["ops", "plan"], items=items,
            max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS,
            returned=2, truncated=False)
        # A member with no pending input still gets the same labelled snapshot.
        lead_snapshot = GroupKnowledge.render_prompt_context(
            knowledge.snapshot(group.id, owner="host", member="lead"))
        assert engines["lead"].turns[0].text == (
            PROMPT_PREFIX + json.dumps([]) + KNOWLEDGE_PREFIX + lead_snapshot)

    asyncio.run(scenario())


def test_conversation_knowledge_optin_injects_bounded_snapshot(room):
    """The reactive conversation host path injects the same bounded reference
    data on a human send that wakes an idle member."""
    host, engines, groups = room.host, room.engines, room.groups
    group = create(host)
    knowledge = GroupKnowledge(groups)
    item = publish(knowledge, group)

    async def scenario():
        host.start(group.id, conversation=ConversationOptions(max_turns=3,
                                                              idle_timeout=60),
                   knowledge=True, max_items=1, max_chars=4000)
        wake = host._wakes[group.id]
        await wait_idle_wake_cleared(wake, "two")
        host.send(group.id, audience=["two"], text="hello")
        await asyncio.wait_for(engines["two"].turn_done(1).wait(), 30)
        await let_member_settle()

        prompt = engines["two"].turns[0].text
        items = [
            {"key": "plan", "revision": 1, "text": "Ship M02 on Friday",
             "sources": ["doc:roadmap.md"], "publisher": "host",
             "updated": item.updated, "truncated": False},
        ]
        assert_snapshot_prompt(
            prompt, group.id, "two", keys=["plan"], items=items,
            max_items=1, max_chars=4000, returned=1, truncated=False)

        host.stop(group.id)
        await asyncio.wait_for(host.wait(group.id), 30)
        receipt = GroupExecutions(groups).get(group.id, owner="host")
        assert receipt is not None and receipt.state == "settled"

    asyncio.run(scenario())


def test_oneshot_knowledge_default_no_injection_byte_identical(room):
    """Default `knowledge=False` keeps the one-shot prompt byte-identical."""
    host, engines = room.host, room.engines
    group = create(host)

    async def scenario():
        host.send(group.id, audience=["two"], text="hello")
        host.start(group.id, audiences=AUDIENCES)
        await asyncio.wait_for(host.wait(group.id), 30)
        expected = PROMPT_PREFIX + json.dumps(
            [{"sender": "host", "sender_kind": "human", "text": "hello"}])
        assert engines["two"].turns[0].text == expected

    asyncio.run(scenario())


def test_conversation_knowledge_default_no_injection_byte_identical(room):
    """Default `knowledge=False` keeps the conversation prompt byte-identical."""
    host, engines = room.host, room.engines
    group = create(host)

    async def scenario():
        host.start(group.id, conversation=ConversationOptions(max_turns=3,
                                                              idle_timeout=60))
        host.send(group.id, audience=["two"], text="hello")
        await asyncio.wait_for(engines["two"].turn_done(1).wait(), 30)
        await let_member_settle()
        expected = PROMPT_PREFIX + json.dumps(
            [{"sender": "host", "sender_kind": "human", "text": "hello"}])
        assert engines["two"].turns[0].text == expected
        host.stop(group.id)
        await asyncio.wait_for(host.wait(group.id), 30)

    asyncio.run(scenario())


def test_knowledge_optin_flag_and_bounds_rejected_before_factory_or_claim(room):
    """A non-bool `knowledge` flag or an invalid snapshot bound is rejected by
    the host before the factory is invoked or any durable claim exists, on
    either start path. The conversation opt-in defaults mirror the module's."""
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
    bad = [
        dict(knowledge=0),
        dict(knowledge=1),
        dict(knowledge="yes"),
        dict(knowledge=None),
        dict(knowledge=True, max_items=0),
        dict(knowledge=True, max_items=-1),
        dict(knowledge=True, max_items=21),
        dict(knowledge=True, max_items=1.5),
        dict(knowledge=True, max_items=float("inf")),
        dict(knowledge=True, max_items=float("nan")),
        dict(knowledge=True, max_items=True),
        dict(knowledge=True, max_items="20"),
        dict(knowledge=True, max_chars=0),
        dict(knowledge=True, max_chars=MIN_SNAPSHOT_CHARS - 1),
        dict(knowledge=True, max_chars=DEFAULT_MAX_CHARS + 1),
        dict(knowledge=True, max_chars=1.5),
        dict(knowledge=True, max_chars=float("inf")),
        dict(knowledge=True, max_chars=float("nan")),
        dict(knowledge=True, max_chars=True),
        dict(knowledge=True, max_chars="16000"),
    ]
    for kw in bad:
        with pytest.raises(HostError):
            spy.start(group.id, audiences=AUDIENCES, **kw)
        with pytest.raises(HostError):
            spy.start(group.id, conversation=ConversationOptions(), **kw)
    assert not called  # the factory was never invoked for any rejected start
    assert GroupExecutions(groups).get(group.id, owner="host") is None  # no claim
    snap = spy.status(group.id)
    assert snap["receipt"] is None and snap["reconciliation_required"] is False
    assert {s.state for s in snap["members"]} == {"idle"}


def test_snapshot_auth_fails_closed(kb):
    """A snapshot is member-only: the group's owner must match and the member
    must belong to the group. The same members of another group can only read
    that other group's knowledge."""
    knowledge, group, other = kb
    publish(knowledge, group)
    publish(knowledge, other, owner="other", key="ops", text="foreign",
            sources=["doc:foreign.md"])

    snap = knowledge.snapshot(group.id, owner="host", member="alpha")
    assert snap["kind"] == "group_knowledge"
    assert [i["key"] for i in snap["items"]] == ["plan"]

    with pytest.raises(ValueError):
        knowledge.snapshot(group.id, owner="other", member="alpha")  # wrong owner
    with pytest.raises(ValueError):
        knowledge.snapshot(group.id, owner="host", member="gamma")  # non-member
    with pytest.raises(ValueError):
        knowledge.snapshot(group.id, owner="host", member="host")
    with pytest.raises(ValueError):
        knowledge.snapshot(other.id, owner="host", member="alpha")  # foreign group
    # Members read only their own group's knowledge.
    foreign = knowledge.snapshot(other.id, owner="other", member="alpha")
    assert foreign["group_id"] == other.id
    assert [i["key"] for i in foreign["items"]] == ["ops"]


def test_snapshot_oversized_item_omitted_whole_not_sliced(kb):
    """An item that cannot fit `max_chars` is omitted whole and reported as
    truncated; the emitted document stays complete valid JSON. A render whose
    envelope truly cannot fit raises instead of being sliced."""
    knowledge, group, _ = kb
    publish(knowledge, group, text="S" * 3000)
    snap = knowledge.snapshot(group.id, owner="host", member="alpha",
                               max_items=20, max_chars=2000)
    assert snap["returned"] == 0
    assert snap["truncated"] is True
    assert snap["items"] == []
    rendered = GroupKnowledge.render_prompt_context(snap)
    assert json.loads(rendered)["items"] == []
    huge = {
        "kind": "group_knowledge",
        "note": SNAPSHOT_NOTE,
        "group_id": "g_" + "x" * 400,
        "member": "alpha",
        "max_items": 20,
        "max_chars": MIN_SNAPSHOT_CHARS,
        "max_text_chars": 100000,
        "returned": 0,
        "truncated": False,
        "items": [],
    }
    with pytest.raises(ValueError, match="exceeds max_chars"):
        GroupKnowledge.render_prompt_context(
            huge, max_chars=MIN_SNAPSHOT_CHARS)


def test_conversation_knowledge_tightest_bound_fails_closed(room):
    """At the smallest accepted bound a failed-to-fit item degrades to a
    complete, valid, bounded snapshot (returned 0 with the truncation flag)
    rather than a sliced or malformed reference, and the member turn still
    succeeds with the engine invoked exactly once."""
    host, engines, groups = room.host, room.engines, room.groups
    group = create(host)
    knowledge = GroupKnowledge(groups)
    publish(knowledge, group, text="S" * 3000)

    async def scenario():
        host.start(group.id, conversation=ConversationOptions(max_turns=3,
                                                              idle_timeout=60),
                   knowledge=True, max_chars=MIN_SNAPSHOT_CHARS)
        wake = host._wakes[group.id]
        await wait_idle_wake_cleared(wake, "two")
        host.send(group.id, audience=["two"], text="hello")
        await asyncio.wait_for(engines["two"].turn_done(1).wait(), 30)
        await let_member_settle()
        prompt = engines["two"].turns[0].text
        assert_snapshot_prompt(
            prompt, group.id, "two", keys=[], items=[],
            max_items=DEFAULT_MAX_ITEMS, max_chars=MIN_SNAPSHOT_CHARS,
            returned=0, truncated=True)
        host.stop(group.id)
        result = {s.profile: s.state
                  for s in await asyncio.wait_for(host.wait(group.id), 30)}
        assert set(result.values()) <= {"completed", "stopped"}
        receipt = GroupExecutions(groups).get(group.id, owner="host")
        assert receipt is not None and receipt.state == "settled"

    asyncio.run(scenario())
