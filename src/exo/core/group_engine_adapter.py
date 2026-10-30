"""Trusted-host adapters connecting saved group members to CoreEngine turns.

The host supplies authorized engines and explicit reply destinations. This module
neither constructs profiles nor changes permissions. Each invocation performs one
turn. GroupRuntime owns claims, concurrency, pause and cancellation.

Optionally the host may pass a ``knowledge`` reader so that each member turn also
receives a bounded snapshot of group knowledge as quoted, untrusted reference
data in the member's own user prompt. It is off by default: with no reader the
prompt is byte-identical to the knowledge-free prompt. Nothing here publishes
knowledge or speaks for a member.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from exo.core.group_knowledge import PROMPT_PREFIX, GroupKnowledge
from exo.core.group_runtime import Adapter, MemberContext
from exo.core.groups import SavedGroup
from exo.protocol import schema as s


class AdapterError(ValueError):
    """Invalid binding or unsuccessful turn. No automatic retry."""


class GroupEngineAdapter:
    def __init__(self, group: SavedGroup, engines: Mapping[str, Any], *,
                 audiences: Mapping[str, list[str]],
                 knowledge: GroupKnowledge | None = None,
                 max_items: int = 20, max_chars: int = 16_000):
        profiles = {m.profile for m in group.members}
        if set(engines) != profiles or set(audiences) != profiles:
            raise AdapterError("one engine and audience required per member")
        if len({id(e) for e in engines.values()}) != len(profiles):
            raise AdapterError("members must not share an engine")
        if len({m.session for m in group.members}) != len(profiles):
            raise AdapterError("members must not share a session")
        self.group = group
        self.engines = dict(engines)
        self.audiences = {p: list(a) for p, a in audiences.items()}
        # Validate bounds up front, before any turn can run.
        self.knowledge = knowledge
        self.bounds = GroupKnowledge.bounds(max_items, max_chars) if knowledge else None
        for member in group.members:
            self._validate(member)
            audience = self.audiences[member.profile]
            if not audience or len(set(audience)) != len(audience) or set(audience) - (profiles - {member.profile}):
                raise AdapterError("invalid audience")
            if group.mode == "conductor":
                if member.profile == group.conductor:
                    if len(audience) != 1:
                        raise AdapterError("conductor must target one worker")
                elif audience != [group.conductor]:
                    raise AdapterError("worker must target conductor")

    def _validate(self, member):
        engine = self.engines[member.profile]
        info = engine.store.get_session(member.session)
        if engine.agent != member.profile or info is None or info.agent != member.profile or info.kind != "private":
            raise AdapterError("engine/session binding does not match private member")

    def adapters(self) -> dict[str, Adapter]:
        return {m.profile: self._run for m in self.group.members}

    async def _run(self, context: MemberContext) -> None:
        member = context.member
        if member not in self.group.members:
            raise AdapterError("context is not a member of this group")
        self._validate(member)
        messages = await context.checkpoint()
        # Message bodies are quoted data, not trusted host instructions.
        prompt = PROMPT_PREFIX + json.dumps([
            {"sender": m.sender, "sender_kind": m.sender_kind, "text": m.text}
            for m in messages
        ])
        if self.knowledge is not None:
            # Read the snapshot before the engine turn: a snapshot failure
            # raises here, so no message is consumed and no turn is charged.
            try:
                snapshot = self.knowledge.snapshot(
                    self.group.id, owner=self.group.owner, member=member.profile,
                    max_items=self.bounds[0], max_chars=self.bounds[1])
                prompt = GroupKnowledge.decorate_prompt(prompt, snapshot)
            except Exception as e:
                raise AdapterError("group knowledge snapshot unavailable") from e
        result = await self.engines[member.profile].run_turn(
            s.TurnSend(session=member.session, text=prompt))
        if result.stop_reason == "interrupted":
            raise asyncio.CancelledError
        if result.stop_reason != "end" or result.error:
            raise AdapterError("member turn did not complete")
        # Conservative acknowledgement: an unsuccessful turn leaves inputs pending.
        # Receipt recovery still requires reconciliation; this is not exactly-once.
        reply = result.message.text() if result.message else ""
        if reply:
            context.send(audience=self.audiences[member.profile], text=reply)
        for message in messages:
            context.consume(message.id)
