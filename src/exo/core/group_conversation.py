"""Trusted-host reactive conversation adapters for saved groups (docs/SWARM-CONVERSATION.md).

Where ``GroupEngineAdapter`` performs exactly one engine turn per invocation,
this module keeps each member reactively alive: on every wake it reaches a
checkpoint, drains that checkpoint's pending mailbox through one real engine
turn, publishes the reply under the host's enforced audience rules, then waits
for the next wake without busy polling. ``GroupRuntime`` still owns claims,
concurrency, pause gates and stop receipts; this module changes no permission,
credential or model-selection decision.

Optionally the host may pass a ``knowledge`` reader so that each member turn
also receives a bounded snapshot of group knowledge as quoted, untrusted
reference data in the member's own user prompt. It is off by default: with no
reader the prompt is byte-identical to the knowledge-free prompt. The snapshot
is read before the engine turn, so a snapshot failure leaves every input
pending and charges no turn. Nothing here publishes knowledge or speaks for a
member.

The runtime exposes no wake notification, so the bounded seam lives here:
``ConversationWake`` fans a host ``set()`` out to one ``asyncio.Event`` per
registered member. The trusted host must call ``set()`` after every accepted
send (``GroupMessages.send``/``send_human`` return synchronously, so setting
right after the call cannot be missed by the sender). Each member loop
consumes only its own event: it clears the event before checking its mailbox
and re-waits after it, so a send that lands between check and wait re-sets
the member's own event and is observed; no other member's consumption can
clear this member's notification, which a single shared cleared event would
lose. Waits are additionally bounded (``idle_timeout``) so a forgotten host
``set()`` degrades into a slow poll, never a lost message.

This is an internal adapter, not an authentication boundary or OS sandbox.
Never expose these Python objects (or MemberContext) to models; message
bodies reach engines only as quoted JSON participant input.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from exo.core.group_engine_adapter import AdapterError
from exo.core.group_knowledge import (DEFAULT_MAX_CHARS, DEFAULT_MAX_ITEMS,
                                      PROMPT_PREFIX, GroupKnowledge)
from exo.core.group_runtime import Adapter, MemberContext
from exo.core.groups import SavedGroup
from exo.protocol import schema as s

DEFAULT_MAX_TURNS = 8
DEFAULT_IDLE_TIMEOUT = 300.0
StopReason = Literal["budget", "cancelled", "error"]


class ConversationError(AdapterError):
    """Invalid binding, budget or wake configuration. No automatic retry."""


@dataclass(frozen=True)
class ConversationOutcome:
    """Host-visible receipt for one member's conversation loop.

    ``reason`` distinguishes an exhausted host turn budget (``"budget"``)
    from host stop/cancellation (``"cancelled"``) and provider/engine failure
    (``"error"``). It is never an engine provider's raw stop_reason.
    """

    profile: str
    turns: int
    reason: StopReason


class ConversationWake:
    """Bounded per-member wake notification the trusted host sets after each send.

    ``set()`` is synchronous and returns immediately, so the host calls it on
    the event loop right after every accepted ``GroupMessages.send`` /
    ``send_human`` for the group. Broadcast fans out to one event per
    registered member, and only that member's loop consumes its event, so a
    notification is never lost by another member clearing it first. Member
    loops clear their own event before checking their mailbox, so a send
    between check and wait re-sets the event and is observed; waits are
    additionally bounded by the adapter's ``idle_timeout``. ``close()``
    releases any member still waiting once its adapter loop has ended, so an
    idle member cannot linger inside a long ``idle_timeout`` after every peer
    has stopped.
    """

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._closed = False

    def set(self) -> None:
        """Wake every member loop. Cheap and idempotent; safe to call per send."""
        for event in self._events.values():
            event.set()

    def _register(self, profile: str) -> None:
        if profile in self._events:
            raise ConversationError(f"duplicate conversation member {profile!r}")
        self._events[profile] = asyncio.Event()

    def close(self) -> None:
        """Set every member event one last time so no waiter lingers."""
        self._closed = True
        self.set()

    async def _wait(self, profile: str, timeout: float) -> None:
        # Timeout is a fallback poll, not an error: a forgotten set() degrades
        # to a bounded idle poll and the loop re-checks its mailbox.
        try:
            await asyncio.wait_for(self._events[profile].wait(), timeout)
        except TimeoutError:
            pass


class GroupConversationAdapter:
    """Reactive multi-turn factory: one looped ``Adapter`` per group member.

    Mirrors ``GroupEngineAdapter`` binding validation (one distinct authorized
    engine per member, private session binding, explicit non-empty audiences,
    conductor-mode routing) and adds the reactive loop. ``max_turns`` is the
    explicit host-supplied per-member budget of engine turns for one process
    run: a positive finite int with no secret model choice. ``idle_timeout``
    bounds each no-input wait; it is a fallback for a missed host wake, never
    the primary delivery path.
    """

    def __init__(self, group: SavedGroup, engines: Mapping[str, Any], *,
                 audiences: Mapping[str, list[str]], wake: ConversationWake,
                 max_turns: int = DEFAULT_MAX_TURNS,
                 idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
                 knowledge: GroupKnowledge | None = None,
                 max_items: int = DEFAULT_MAX_ITEMS,
                 max_chars: int = DEFAULT_MAX_CHARS) -> None:
        if not isinstance(wake, ConversationWake):
            raise ConversationError("a shared ConversationWake is required")
        if wake._closed:
            raise ConversationError("a closed ConversationWake cannot be reused")
        if type(max_turns) is not int or not 1 <= max_turns <= 10000:
            raise ConversationError("max_turns must be an explicit positive finite budget")
        if type(idle_timeout) not in (int, float) or not 0 < idle_timeout <= 86400:
            raise ConversationError("idle_timeout must bound the idle wait")
        profiles = {m.profile for m in group.members}
        if set(engines) != profiles or set(audiences) != profiles:
            raise ConversationError("one engine and audience required per member")
        if len({id(e) for e in engines.values()}) != len(profiles):
            raise ConversationError("members must not share an engine")
        if len({m.session for m in group.members}) != len(profiles):
            raise ConversationError("members must not share a session")
        self.group = group
        self.engines = dict(engines)
        self.audiences = {p: list(a) for p, a in audiences.items()}
        self.wake = wake
        self.max_turns = max_turns
        self.idle_timeout = float(idle_timeout)
        # Opt-in knowledge context: bounds are validated up front, before any
        # claim or turn, so a bad bound cannot reach an engine. With no reader
        # every prompt stays byte-identical to the knowledge-free prompt.
        self.knowledge = knowledge
        self.kbounds = (GroupKnowledge.bounds(max_items, max_chars)
                        if knowledge is not None else None)
        self._outcomes: dict[str, ConversationOutcome] = {}
        self._turns: dict[str, int] = {}
        self._active: set[str] = set()
        for member in group.members:
            self._validate(member)
            audience = self.audiences[member.profile]
            if not audience or len(set(audience)) != len(audience) or set(audience) - (profiles - {member.profile}):
                raise ConversationError("invalid audience")
            if group.mode == "conductor":
                if member.profile == group.conductor:
                    if len(audience) != 1:
                        raise ConversationError("conductor must target one worker")
                elif audience != [group.conductor]:
                    raise ConversationError("worker must target conductor")
            self._turns[member.profile] = 0
            self.wake._register(member.profile)

    def _validate(self, member) -> None:
        engine = self.engines[member.profile]
        info = engine.store.get_session(member.session)
        if engine.agent != member.profile or info is None or info.agent != member.profile or info.kind != "private":
            raise ConversationError("engine/session binding does not match private member")

    def adapters(self) -> dict[str, Adapter]:
        return {m.profile: self._loop for m in self.group.members}

    def outcomes(self) -> dict[str, ConversationOutcome]:
        """Per-member receipts recorded so far; combine with runtime status."""
        return dict(self._outcomes)

    async def _loop(self, context: MemberContext) -> None:
        member = context.member
        if member not in self.group.members:
            raise ConversationError("context is not a member of this group")
        self._validate(member)
        profile = member.profile
        self._active.add(profile)
        try:
            while self._turns[profile] < self.max_turns:
                # Clear only this member's event, before checking, so a send
                # between the pending read and the wait re-sets it and is
                # observed; no other member's consumption can clear it.
                self.wake._events[profile].clear()
                # Checkpoint waits out a pause before delivering pending input;
                # messages arriving during the turn stay unconsumed and are
                # picked up by the next checkpoint, never by this turn.
                messages = await context.checkpoint()
                if not messages:
                    await self.wake._wait(profile, self.idle_timeout)
                    continue
                await self._turn(context, member, messages)
                # Conservative acknowledgement: only a successful turn counts
                # against the budget; a failed turn already recorded its error
                # outcome and never consumes this slot again (no retry).
                self._turns[profile] += 1
        except asyncio.CancelledError:
            self._outcomes[profile] = ConversationOutcome(profile, self._turns[profile], "cancelled")
            raise
        finally:
            self._active.discard(profile)
            if not self._active:
                # Last member out releases any peer still inside a long idle
                # wait; idle exit is bounded by max_turns, not idle_timeout.
                self.wake.close()
        self._outcomes[profile] = ConversationOutcome(profile, self._turns[profile], "budget")

    async def _turn(self, context: MemberContext, member, messages) -> None:
        # Message bodies are quoted data, not trusted host instructions.
        profile = member.profile
        prompt = PROMPT_PREFIX + json.dumps([
            {"sender": m.sender, "sender_kind": m.sender_kind, "text": m.text}
            for m in messages
        ])
        if self.knowledge is not None:
            # Read the bounded snapshot before the engine turn: a snapshot
            # failure raises here, so no message is consumed, nothing is
            # published and no turn is charged against the member's budget.
            try:
                snapshot = self.knowledge.snapshot(
                    self.group.id, owner=self.group.owner, member=profile,
                    max_items=self.kbounds[0], max_chars=self.kbounds[1])
                prompt = GroupKnowledge.decorate_prompt(prompt, snapshot)
            except Exception as e:
                self._outcomes[profile] = ConversationOutcome(
                    profile, self._turns[profile], "error")
                raise ConversationError("group knowledge snapshot unavailable") from e
        # Only the engine turn is bounded, never the publish/consume
        # bookkeeping and never the whole reactive loop: the runtime
        # re-checks pause/stop after granting the permit, so a pause requested
        # while this member queued keeps the turn from starting.
        async with context.turn_slot():
            try:
                result = await self.engines[profile].run_turn(
                    s.TurnSend(session=member.session, text=prompt))
            except asyncio.CancelledError:
                self._outcomes[profile] = ConversationOutcome(profile, self._turns[profile], "cancelled")
                raise
            except BaseException as e:
                # Isolate provider failures: leave every input unconsumed, record
                # the reason and fail this member without retrying anything.
                self._outcomes[profile] = ConversationOutcome(profile, self._turns[profile], "error")
                raise ConversationError("member turn raised") from e
        if result.stop_reason == "interrupted":
            self._outcomes[profile] = ConversationOutcome(profile, self._turns[profile], "cancelled")
            raise asyncio.CancelledError
        if result.stop_reason != "end" or result.error:
            self._outcomes[profile] = ConversationOutcome(profile, self._turns[profile], "error")
            raise ConversationError("member turn did not complete")
        # A host stop can land while the engine was finishing and be swallowed
        # into a normal return. Re-check the cancellation request before any
        # publish/consume so the member still reports "cancelled", charges no
        # turn, publishes nothing and leaves every input pending.
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            self._outcomes[profile] = ConversationOutcome(profile, self._turns[profile], "cancelled")
            raise asyncio.CancelledError
        # Publish first, acknowledge only after the reply is durable. Never
        # ack-before-send: an input acknowledged before a failed publication
        # would be lost, and this adapter never retries automatically. A
        # publication failure fails the member with every input still pending.
        reply = result.message.text() if result.message else ""
        if reply:
            try:
                context.send(audience=self.audiences[profile], text=reply)
            except BaseException as e:
                self._outcomes[profile] = ConversationOutcome(profile, self._turns[profile], "error")
                raise ConversationError("member reply publication failed") from e
        # Per-message acknowledgement after success. Publication and
        # acknowledgement are not atomic: a failure acking one message fails
        # the member, leaving earlier inputs consumed and the rest pending
        # (at-least-once, never re-run automatically). The durable receipt
        # shows "failed" and the host reconciles pending input against
        # history; see docs/SWARM-CONVERSATION.md.
        for message in messages:
            try:
                context.consume(message.id)
            except BaseException as e:
                self._outcomes[profile] = ConversationOutcome(profile, self._turns[profile], "error")
                raise ConversationError("member acknowledgement failed") from e


def start_conversation(messages, group: SavedGroup, *, owner: str, adapters,
                       wake: ConversationWake, prompt: str | None = None,
                       max_concurrent: int | None = None):
    """Start a reactive ``GroupRuntime`` for a conversation adapter.

    The host supplies the already-validated ``adapters.adapters()`` mapping and
    the shared ``wake``. An optional explicit host ``prompt`` is delivered as
    a human message to every member (sender_kind "human", audience = all
    member profiles) before the claim; without new input afterwards the loops
    go idle after the budget, so there is no self-trigger loop. The wake is
    set once after start so initial mailbox input is picked up immediately;
    the host must call ``wake.set()`` after every later accepted send.

    ``max_concurrent`` is an explicit host turn-concurrency bound passed
    through to ``GroupRuntime``: ``None`` (the default) keeps full member
    concurrency, otherwise a strict ``int`` in ``1..member count`` bounds how
    many members may be inside an actual engine turn at once (every loop
    still runs; only ``run_turn`` is serialized). The bound is validated
    before the runtime is constructed or any claim is taken.
    """
    from exo.core.group_runtime import GroupRuntime

    runtime = GroupRuntime(messages, group.id, owner=owner, adapters=adapters,
                           max_concurrent=max_concurrent)
    if prompt is not None:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ConversationError("initial prompt must be explicit non-empty host text")
        messages.send_human(group.id, owner=owner,
                            audience=[m.profile for m in group.members], text=prompt)
    runtime.start()
    wake.set()  # initial input (prompt or pre-seeded mailbox) must not wait for idle_timeout
    return runtime
