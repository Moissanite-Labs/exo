"""Internal Swarm composition. The host authenticates users and authorizes adapters.

Contexts bind routing identity, not an OS sandbox. Do not expose these Python
objects directly to models. Adapters must explicitly acknowledge consumed input
and reach checkpoints between units of work. No message triggers work by itself.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager

from exo.core.group_execution import GroupExecutions
from exo.core.group_messages import GroupMessage, GroupMessages
from exo.core.group_runner import GroupRunner, MemberStatus
from exo.core.groups import GroupMember


def validate_max_concurrent(max_concurrent: object, member_count: int) -> int:
    """Validate the bound before a factory, an engine or a claim is touched.

    ``None`` disables the bound (full member concurrency, the historical
    behavior). Otherwise it must be a strict ``int`` (``bool`` is rejected even
    though it is an ``int`` subclass) between 1 and the member count inclusive,
    so a bound can never exclude a member from ever taking a turn.
    """
    if max_concurrent is None:
        return member_count
    if type(max_concurrent) is not int:
        raise ValueError("max_concurrent must be an int bound or None")
    if not 1 <= max_concurrent <= member_count:
        raise ValueError("max_concurrent must be between 1 and the member count")
    return max_concurrent


class MemberContext:
    def __init__(self, runtime: GroupRuntime, member: GroupMember):
        self._runtime = runtime
        self.member = member

    async def checkpoint(self) -> list[GroupMessage]:
        """Wait out a pause, then deliver pending input without acknowledging it."""
        runtime = self._runtime
        profile = self.member.profile
        gate = runtime._gates[profile]
        if not gate.is_set():
            runtime._paused.add(profile)
            try:
                await gate.wait()
            finally:
                runtime._paused.discard(profile)
        return runtime._messages.pending(runtime.group.id, owner=runtime.group.owner, member=profile)

    def consume(self, message_id: str) -> None:
        runtime = self._runtime
        runtime._messages.consume(runtime.group.id, owner=runtime.group.owner,
                                  member=self.member.profile, message_id=message_id)

    def send(self, *, audience: list[str], text: str) -> GroupMessage:
        runtime = self._runtime
        return runtime._messages.send(runtime.group.id, owner=runtime.group.owner,
                                      sender=self.member.profile, audience=audience, text=text)

    def history(self, *, after: int = 0, limit: int = 100) -> list[GroupMessage]:
        runtime = self._runtime
        return runtime._messages.history(runtime.group.id, owner=runtime.group.owner,
                                         member=self.member.profile, after=after, limit=limit)

    def turn_slot(self):
        """Runtime-owned bound around one actual engine turn.

        Wrap only ``engine.run_turn`` — never a whole conversation loop — so a
        bounded member still observes pause/stop between turns. The runtime
        re-reads its checkpoint after the permit is acquired, so a pause or stop
        requested while this turn was queued prevents the engine turn.
        """
        return self._runtime.turn_slot(self.member.profile)


Adapter = Callable[[MemberContext], Awaitable[None]]


class GroupRuntime:
    """One process-local execution. Only a trusted human host gets controls.

    A durable claim prevents duplicate execution across clients/processes. Adapter
    construction and permission checks stay outside this class. A paused status
    means the adapter is waiting at a checkpoint, not that detached work stopped.

    ``max_concurrent`` bounds how many members may be inside an actual engine
    turn (``turn_slot``) at once, not how many member loops exist: every member
    still starts, keeps its claim and observes pause/stop between turns. A
    waiter that acquires a slot re-checks the runtime checkpoint (pause/stop)
    before handing the slot to the adapter, so a pause requested while it was
    queued prevents the new turn rather than starting one. Permits are released
    on cancellation. ``max_concurrent=None`` keeps every member concurrent.
    """

    def __init__(self, messages: GroupMessages, group_id: str, *, owner: str,
                 adapters: Mapping[str, Adapter], max_concurrent: int | None = None):
        group = messages.groups.get(group_id, owner=owner)
        if group is None:
            raise ValueError("no group")
        profiles = {m.profile for m in group.members}
        if set(adapters) != profiles or any(not callable(a) for a in adapters.values()):
            raise ValueError("one authorized adapter is required for each member")
        bound = validate_max_concurrent(max_concurrent, len(profiles))
        self.group = group
        self._messages = messages
        self._executions = GroupExecutions(messages.groups)
        self._settler: asyncio.Task | None = None
        self._gates = {p: asyncio.Event() for p in profiles}
        for gate in self._gates.values():
            gate.set()
        self._paused: set[str] = set()
        # Runtime-owned turn permits: acquired only around a real engine turn,
        # never around a whole conversation loop, so pause/stop stay responsive.
        self.max_concurrent = bound
        self._turn_slots = asyncio.Semaphore(bound)
        self._turn_denied: set[str] = set()
        # Members currently waiting for a turn permit; status reports them as
        # "queued" (they hold no engine turn yet) rather than "running".
        self._turn_waiting: dict[str, int] = {}
        authorized = dict(adapters)

        async def run(member):
            context = MemberContext(self, member)
            # Honor pause-before-start without invoking an adapter or delivering input.
            gate = self._gates[member.profile]
            if not gate.is_set():
                self._paused.add(member.profile)
                try:
                    await gate.wait()
                finally:
                    self._paused.discard(member.profile)
            await authorized[member.profile](context)

        self._runner = GroupRunner(group, {p: run for p in profiles})

    async def _await_turn_clearance(self, profile: str) -> None:
        """Wait out a pause and honor a pending stop before starting a turn."""
        if self._runner.stopping(profile):
            raise asyncio.CancelledError
        gate = self._gates[profile]
        if not gate.is_set():
            self._paused.add(profile)
            try:
                await gate.wait()
            finally:
                self._paused.discard(profile)
            # A host stop while queued/paused must not start a new engine turn.
            if self._runner.stopping(profile):
                raise asyncio.CancelledError

    @asynccontextmanager
    async def turn_slot(self, profile: str):
        """Bound concurrency around one real engine turn; re-check after acquire.

        The permit is runtime-owned, so every adapter (one-shot or reactive)
        shares the same bound. Only one engine turn per member exists at a time,
        and the runtime checkpoint is re-read after the permit is acquired, so a
        pause requested while this member was queued keeps the turn from
        starting until resume. Cancellation in the queue or inside the turn
        releases the permit (``asyncio.Semaphore`` does that in ``acquire``),
        leaving peers unblocked.
        """
        if profile not in self._gates:
            raise ValueError("unknown member")
        while True:
            await self._await_turn_clearance(profile)
            self._turn_waiting[profile] = self._turn_waiting.get(profile, 0) + 1
            try:
                await self._turn_slots.acquire()
            finally:
                self._turn_waiting[profile] -= 1
                if not self._turn_waiting[profile]:
                    del self._turn_waiting[profile]
            # Re-read the runtime checkpoint after the permit: a pause or stop
            # requested while this turn was queued must prevent the engine turn.
            if self._runner.stopping(profile):
                self._turn_slots.release()
                self._turn_denied.add(profile)
                raise asyncio.CancelledError
            if self._gates[profile].is_set():
                break
            # Paused while queued: never hold a permit while paused (that would
            # stall every peer), so release it and wait out the pause, then queue
            # again. The turn starts only after a host resume.
            self._turn_slots.release()
            self._turn_denied.add(profile)
        self._turn_denied.discard(profile)
        try:
            yield self
        finally:
            self._turn_slots.release()

    def turn_denied(self, profile: str) -> bool:
        """Whether this member's last queued turn was refused after its permit.

        ``True`` means the member acquired a permit but the runtime checkpoint
        (a pause requested while it was queued, or a host stop) refused the turn,
        so no engine turn started. Cleared when a turn actually begins.
        """
        return profile in self._turn_denied

    def turn_waiting(self, profile: str) -> bool:
        """Whether this member is currently queued for a turn permit."""
        return bool(self._turn_waiting.get(profile))

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        receipt = self._executions.claim(self.group.id, owner=self.group.owner)
        # Claim first: even a partial start failure must not permit automatic retry.
        self._runner.start()
        self._settler = loop.create_task(self._settle(receipt.id))
        # Retrieve background errors without logging adapter text. wait() still raises.
        self._settler.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    async def _settle(self, execution_id: str) -> None:
        statuses = await self._runner.wait()
        self._executions.finish(self.group.id, owner=self.group.owner, execution_id=execution_id,
                                members={s.profile: s.state for s in statuses})

    def receipt(self):
        """Last durable receipt, not a liveness probe or live member status."""
        return self._executions.get(self.group.id, owner=self.group.owner)

    def status(self) -> tuple[MemberStatus, ...]:
        result = []
        for status in self._runner.status():
            state = status.state
            if state in ("queued", "running") and not self._gates[status.profile].is_set():
                state = "paused" if status.profile in self._paused else "pausing"
            elif state == "running" and self.turn_waiting(status.profile):
                # Scheduled but holding no engine turn yet: a scheduling fact,
                # not evidence that a model call is active.
                state = "queued"
            result.append(MemberStatus(status.profile, state))
        return tuple(result)

    def _targets(self, profile):
        if profile is not None and profile not in self._gates:
            raise ValueError("unknown member")
        return self._gates if profile is None else (profile,)

    def pause(self, profile: str | None = None) -> None:
        for p in self._targets(profile):
            self._gates[p].clear()

    def resume(self, profile: str | None = None) -> None:
        for p in self._targets(profile):
            self._gates[p].set()

    def stop(self, profile: str | None = None) -> None:
        self._runner.stop(profile)

    async def wait(self) -> tuple[MemberStatus, ...]:
        if self._settler is None:
            raise ValueError("group execution has not started")
        await asyncio.shield(self._settler)
        return self.status()

    def send_human(self, *, audience: list[str], text: str) -> GroupMessage:
        return self._messages.send_human(self.group.id, owner=self.group.owner,
                                         audience=audience, text=text)

    def observe(self, *, after: int = 0, limit: int = 100) -> list[GroupMessage]:
        return self._messages.observe(self.group.id, owner=self.group.owner, after=after, limit=limit)
