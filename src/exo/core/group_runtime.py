"""Internal Swarm composition. The host authenticates users and authorizes adapters.

Contexts route using their assigned member identity. These mutable Python objects
are not a sandbox and must not be exposed directly to models. Adapters explicitly
acknowledge consumed input and reach checkpoints between units of work.
No message triggers work by itself.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from exo.core.group_execution import ExecutionReceipt, GroupExecutions
from exo.core.group_messages import GroupMessage, GroupMessages
from exo.core.group_runner import GroupRunner, MemberStatus
from exo.core.groups import GroupMember


class MemberContext:
    def __init__(self, runtime: GroupRuntime, member: GroupMember):
        self._runtime = runtime
        self.member = member

    async def checkpoint(self) -> list[GroupMessage]:
        """Wait out a pause, then deliver pending input without acknowledging it."""
        runtime = self._runtime
        profile = self.member.profile
        await runtime._wait_unpaused(profile)
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


Adapter = Callable[[MemberContext], Awaitable[None]]


class GroupRuntime:
    """One process-local execution. Only a trusted human host gets controls.

    A durable claim prevents duplicate execution across clients/processes. Adapter
    construction and permission checks stay outside this class. A paused status
    means a checkpoint or pre-entry wait is parked, not that detached work stopped.
    """

    def __init__(self, messages: GroupMessages, group_id: str, *, owner: str,
                 adapters: Mapping[str, Adapter]):
        group = messages.groups.get(group_id, owner=owner)
        if group is None:
            raise ValueError("no group")
        profiles = {m.profile for m in group.members}
        if set(adapters) != profiles or any(not callable(a) for a in adapters.values()):
            raise ValueError("one authorized adapter is required for each member")
        self.group = group
        self._messages = messages
        self._executions = GroupExecutions(messages.groups)
        self._settler: asyncio.Task | None = None
        self._gates = {p: asyncio.Event() for p in profiles}
        for gate in self._gates.values():
            gate.set()
        self._paused: dict[str, int] = {}
        bound = dict(adapters)

        async def run(member):
            context = MemberContext(self, member)
            # Honor pause-before-start without invoking an adapter or delivering input.
            await self._wait_unpaused(member.profile)
            await bound[member.profile](context)

        self._runner = GroupRunner(group, {p: run for p in profiles})

    async def _wait_unpaused(self, profile: str) -> None:
        gate = self._gates[profile]
        if gate.is_set():
            return
        self._paused[profile] = self._paused.get(profile, 0) + 1
        try:
            while not gate.is_set():
                await gate.wait()
        finally:
            remaining = self._paused[profile] - 1
            if remaining:
                self._paused[profile] = remaining
            else:
                del self._paused[profile]

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

    def receipt(self) -> ExecutionReceipt | None:
        """Last durable receipt, not a liveness probe or live member status."""
        return self._executions.get(self.group.id, owner=self.group.owner)

    def status(self) -> tuple[MemberStatus, ...]:
        result = []
        for status in self._runner.status():
            state = status.state
            if state in ("queued", "running") and not self._gates[status.profile].is_set():
                state = "paused" if status.profile in self._paused else "pausing"
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
