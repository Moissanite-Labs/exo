"""Internal concurrent lifecycle, independent of profile/provider construction.

A trusted host supplies authorized runners for fresh group sessions. This module
never constructs profiles, grants permissions, or retries failed work.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from exo.core.groups import GroupMember, SavedGroup

Runner = Callable[[GroupMember], Awaitable[None]]


@dataclass(frozen=True)
class MemberStatus:
    profile: str
    state: str


class GroupRunner:
    """One process-local supervisor. The host must enforce single ownership.

    Runners must finish cleanup before returning or propagating cancellation.
    An adapter that abandons background work cannot supply a stop receipt.
    """

    def __init__(self, group: SavedGroup, runners: Mapping[str, Runner]):
        profiles = {m.profile for m in group.members}
        if set(runners) != profiles or any(not callable(r) for r in runners.values()):
            raise ValueError("one authorized runner is required for each member")
        self.group = group
        self._runners = dict(runners)
        self._tasks: dict[str, asyncio.Task] = {}
        self._states = {p: "idle" for p in profiles}
        self._started = False

    def status(self) -> tuple[MemberStatus, ...]:
        return tuple(MemberStatus(m.profile, self._states[m.profile]) for m in self.group.members)

    def start(self) -> None:
        if self._started:
            raise ValueError("group execution already started")
        loop = asyncio.get_running_loop()
        self._started = True
        for member in self.group.members:
            self._states[member.profile] = "queued"
            task = loop.create_task(self._run(member))
            self._tasks[member.profile] = task
            task.add_done_callback(lambda t, p=member.profile: self._finished(p, t))

    async def _run(self, member: GroupMember) -> None:
        self._states[member.profile] = "running"
        await self._runners[member.profile](member)

    def _finished(self, profile: str, task: asyncio.Task) -> None:
        # Do not retain or publish arbitrary exception text from provider adapters.
        if task.cancelled():
            self._states[profile] = "stopped"
        elif task.exception() is not None:
            self._states[profile] = "failed"
        else:
            self._states[profile] = "stopped" if self._states[profile] in ("stopping", "stopped") else "completed"

    def stop(self, profile: str | None = None) -> None:
        if profile is not None and profile not in self._states:
            raise ValueError("unknown member")
        for p in self._states if profile is None else (profile,):
            task = self._tasks.get(p)
            if task is not None and not task.done() and self._states[p] != "stopping":
                self._states[p] = "stopping"
                task.cancel()

    async def wait(self) -> tuple[MemberStatus, ...]:
        if not self._started:
            raise ValueError("group execution has not started")
        # Cancelling an observer must not silently cancel participant execution.
        await asyncio.shield(asyncio.gather(*self._tasks.values(), return_exceptions=True))
        for profile, task in self._tasks.items():
            self._finished(profile, task)
        return self.status()
