"""Group composition: one authorized CoreEngine per swarm group member (docs/SWARM-COMPOSITION.md).

The host injects the trusted owner and an explicit authorized profile -> Seat mapping. This
module reads no metadata, config or seat files and authorizes nothing implicitly: the stored
group's owner and full membership are validated against that mapping BEFORE anything is
constructed. Construction is synchronous (GroupHost's EngineFactory is a plain Callable);
cleanup is async via aclose(). Failures fail closed: partial engines are retained for
aclose(), never retried, and another member's writer claim is never taken over. A composition
is single-use: after the first call (success or failure) it rejects further calls until
aclose() completes, and a failed aclose() retains the failed engines so it can be retried.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

from exo.application import CoreEngine
from exo.core.groups import SavedGroup
from exo.core.plugins import PluginManager
from exo.core.seats import Seat


class CompositionError(RuntimeError):
    """Owner, membership or construction failure: the composition fails closed."""


class GroupComposition:
    """Synchronous EngineFactory for GroupHost with async cleanup.

    Every member gets a distinct real CoreEngine against the same home (one shared session
    DB) bound to the exact authorized Seat for its profile, with the writer lock claimed.
    """

    def __init__(self, profiles: Mapping[str, Seat], *, owner: str,
                 home: str | os.PathLike, models: dict[str, Any] | None = None,
                 cwd: str | None = None, env: Mapping[str, str] | None = None) -> None:
        if not isinstance(owner, str) or not owner.strip():
            raise CompositionError("owner is required")
        if isinstance(profiles, (str, bytes)) or not profiles:
            raise CompositionError("an explicit authorized profile -> Seat mapping is required")
        seats: dict[str, Seat] = {}
        for profile, seat in profiles.items():
            if not isinstance(seat, Seat):
                raise CompositionError(f"profile {profile!r} is not bound to a Seat")
            if seat.name != profile:  # the mapping is exact: no aliasing or renaming
                raise CompositionError(f"seat name {seat.name!r} does not match profile {profile!r}")
            seats[profile] = seat
        self._owner = owner
        self._seats = seats
        self._home = home
        self._models = models
        self._cwd = cwd
        self._env = dict(env or {})
        self._engines: dict[str, CoreEngine] = {}  # retained for async aclose()

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def profiles(self) -> frozenset[str]:
        return frozenset(self._seats)

    @property
    def engines(self) -> Mapping[str, CoreEngine]:
        """Engines retained so far (successful call or partial failure), keyed by profile."""
        return dict(self._engines)

    def __call__(self, group: SavedGroup) -> Mapping[str, CoreEngine]:
        if self._engines:
            raise CompositionError("engines are already retained; aclose() before composing again")
        # Validate BEFORE any construction: correct owner, every member authorized, no duplicates.
        if group.owner != self._owner:
            raise CompositionError(f"group {group.id!r} belongs to {group.owner!r}, not {self._owner!r}")
        members = [m.profile for m in group.members]
        if len(set(members)) != len(members):
            raise CompositionError("duplicate group member profiles")
        unknown = [p for p in members if p not in self._seats]
        if unknown:
            raise CompositionError("group members outside the authorized mapping: " + ", ".join(unknown))
        try:
            for profile in members:
                engine = CoreEngine(self._home, self._models, env=dict(self._env), cwd=self._cwd,
                                    plugins=PluginManager(), seat=self._seats[profile])
                # Retain BEFORE claiming: if the claim raises, the engine still gets closed.
                self._engines[profile] = engine
                engine.claim_writer()  # sync; raises if another writer holds this seat's log
        except Exception as exc:
            # Fail closed: retain the partial set (incl. the engine whose claim failed) so
            # aclose() can clean it up asynchronously. No retry, no takeover of another
            # writer's claim, and no nested event loop here.
            raise CompositionError(f"group {group.id!r} composition failed: {exc}") from exc
        return dict(self._engines)

    async def aclose(self) -> None:
        """Close every retained engine (from a successful call or a partial failure).

        Every engine is attempted; the first close error is raised at the end. References
        are cleared only for engines whose close succeeded, so a failed close can be retried
        by calling aclose() again.
        """
        errors: list[BaseException] = []
        for profile, engine in list(self._engines.items()):
            try:
                await engine.close()
            except BaseException as exc:  # noqa: BLE001 - every engine still attempted
                errors.append(exc)
            else:
                self._engines.pop(profile, None)  # clear only on success; retain failures for retry
        if errors:
            raise CompositionError(f"engine close failed: {errors[0]}") from errors[0]
