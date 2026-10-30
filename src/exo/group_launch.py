"""Trusted launcher for one swarm group (docs/SWARM-LAUNCH.md).

``create`` is the one-call trusted-Python path from an explicit profile list to a
running group: it validates everything up front, assembles the ``GroupHost`` (its
session store, the authorized profile set and a ``GroupComposition`` engine factory)
and then builds the owner engine through ``exo.compose.create`` with the injected
host. It never reads a protocol command, never discovers members implicitly and
never activates config or environment: the only inputs are the explicit
``profiles`` (plus the optional ``plugins`` profile) and the ambient ``$EXO_HOME``
config that ``compose`` already loads. The owner seat is the ``seat`` argument, else
``$EXO_SEAT`` (read, never set), else ``"default"`` — exactly what ``compose``
resolves; it must never appear in ``profiles``.

Validation is fail-closed before allocation: profile IDs, owner eligibility and the
plugin profile are checked before the config is read; named member seats are loaded
(and their declared names checked) before the session store is opened. Member
engines do not exist until ``GroupHost.start`` invokes the factory, so every
failure inside ``create`` needs only the synchronous ``SessionStore.close()`` —
never an event loop — which keeps ``create`` safe to call from a running loop.
Async teardown lives in ``GroupLaunch.aclose``: settle the host first (cancellation
of the caller only re-awaits the host's shielded shared cleanup — the store is
never closed under a still-settling host), then close the owner engine, then close
the store. ``asyncio.run`` appears nowhere in this module.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable
from pathlib import Path

from exo.compose import create as compose_create
from exo.config import Config
from exo.core import seats
from exo.core.group_composition import GroupComposition, GroupPlugins
from exo.core.group_host import GroupHost
from exo.core.groups import GroupStore, SavedGroup
from exo.core.sessions import SessionStore


class LaunchError(RuntimeError):
    """A launch input was invalid or the launch failed; nothing runs on a bad launch."""


def _validate_profiles(profiles: Iterable[str]) -> list[str]:
    """Explicit profile IDs only: valid IDs, at least two, no duplicates. No allocation."""
    if isinstance(profiles, (str, bytes)) or not isinstance(profiles, Iterable):
        raise LaunchError("profiles must be an explicit collection of profile IDs, not a string")
    names: list[str] = []
    for profile in profiles:
        if not isinstance(profile, str) or not seats.ID_RE.fullmatch(profile):
            raise LaunchError(f"profile {profile!r} is not a valid profile ID")
        if profile in names:
            raise LaunchError(f"duplicate profile {profile!r}")
        names.append(profile)
    if len(names) < 2:
        raise LaunchError("a group needs at least two distinct profiles")
    return names


def _effective_owner(seat: str | None) -> str:
    """The owner seat name: the argument, else $EXO_SEAT (read only), else "default"."""
    if seat is not None and (not isinstance(seat, str) or not seats.ID_RE.fullmatch(seat)):
        raise LaunchError(f"seat name {seat!r} is not a valid seat ID")
    owner = seat or os.environ.get("EXO_SEAT") or "default"
    if not seats.ID_RE.fullmatch(owner):
        raise LaunchError(f"effective owner seat {owner!r} is not a valid seat ID")
    return owner


def _audiences_for(group: SavedGroup) -> dict[str, list[str]]:
    """The fixed authorized audiences derived from the stored group mode (mirrors GroupHost)."""
    profiles = [m.profile for m in group.members]
    if group.mode == "conductor":
        first_peer = next(p for p in profiles if p != group.conductor)
        return {p: ([group.conductor] if p != group.conductor else [first_peer]) for p in profiles}
    return {p: [q for q in profiles if q != p] for p in profiles}


def create(seat: str | None = None, *, profiles: Iterable[str],
           plugins: GroupPlugins | None = None) -> "GroupLaunch":
    """Launch one swarm group for the effective owner seat and return a :class:`GroupLaunch`.

    ``profiles`` is the explicit, exclusive membership (>= 2 unique seat IDs); the
    owner seat is rejected as a member before anything is allocated. Only the named
    member seats are loaded from ``$EXO_HOME/seats`` — no directory scans. Members
    run ``cfg.models()`` against the one shared session DB and the injected
    ``GroupPlugins`` (``None`` means the empty profile: no folders, nothing enabled —
    plugins are disabled by default). The group is created in conductor mode with the
    first profile as conductor; call ``GroupLaunch.start`` to bind engines and run it.
    """
    names = _validate_profiles(profiles)
    owner = _effective_owner(seat)
    if owner in names:
        raise LaunchError(f"owner seat {owner!r} must not be a group member profile")
    if plugins is not None and not isinstance(plugins, GroupPlugins):
        raise LaunchError("plugins must be a GroupPlugins instance or None")

    cfg = Config.load()  # ambient $EXO_HOME; never written, never activated here
    loaded = {}
    for name in names:  # named seats only; no discovery of what else exists
        try:
            loaded_seat = seats.load(cfg.home, name)
        except seats.SeatError as exc:
            raise LaunchError(f"profile seat {name!r} could not be loaded: {exc}") from exc
        if loaded_seat.name != name:
            raise LaunchError(f"seat file {name!r} declares the name {loaded_seat.name!r}")
        loaded[name] = loaded_seat

    home = Path(cfg.home)
    try:
        store = SessionStore(str(home / "sessions.sqlite"))
    except Exception as exc:
        # SessionStore creation is sqlite work (WAL pragma, schema migration): a
        # locked, corrupt or unwritable sessions.sqlite raises a raw sqlite3 or
        # OSError verbatim. Surfaced as LaunchError because the launch cannot
        # proceed without the backing store; the sqlite cause stays chained.
        raise LaunchError(f"session store {home / 'sessions.sqlite'} could not be opened: {exc}") from exc
    try:
        composition = GroupComposition(loaded, owner=owner, home=home, models=cfg.models(),
                                       cwd=cfg.get("cwd"), env=dict(os.environ),
                                       plugins=plugins if plugins is not None else GroupPlugins())
        host = GroupHost(GroupStore(store), owner=owner, known_profiles=frozenset(names),
                         engine_factory=composition)
        group = host.create(mode="conductor", members=list(names), conductor=names[0])
        engine = compose_create(seat, group_host=host)
    except LaunchError:
        store.close()
        raise
    except Exception as exc:
        # No member engines exist yet (the factory only runs at GroupHost.start), and
        # compose builds the owner engine only after validating the host, so the
        # synchronous DB close is the whole cleanup — deliberately no event loop here.
        store.close()
        raise LaunchError(f"group launch failed: {exc}") from exc
    except BaseException:
        store.close()  # SystemExit/KeyboardInterrupt propagate unchanged, store released
        raise
    return GroupLaunch(host=host, engine=engine, group=group, store=store, composition=composition)


class GroupLaunch:
    """One launched group: the owner engine, its host and the stored group definition."""

    def __init__(self, *, host: GroupHost, engine, group: SavedGroup,
                 store: SessionStore, composition: GroupComposition) -> None:
        self.host = host
        self.engine = engine  # the owner's CoreEngine, built by compose with the host injected
        self.group = group
        self.store = store
        self._composition = composition

    @property
    def members(self) -> dict:
        """Member engines keyed by profile (empty until ``start`` builds them)."""
        return self._composition.engines

    @property
    def closed(self) -> bool:
        """Whether the host has fully settled its shared cleanup (store may close)."""
        return self.host._closed  # noqa: SLF001 - same-package launcher

    def start(self) -> tuple:
        """Bind the member engines via the host factory and start the run once."""
        return self.host.start(self.group.id, audiences=_audiences_for(self.group))

    async def wait(self) -> tuple:
        """Await the group's settlement."""
        return await self.host.wait(self.group.id)

    async def aclose(self) -> None:
        """Close everything this launch owns; every part attempted, first failure raised.

        Process control — cancellation, ``SystemExit``, ``KeyboardInterrupt`` —
        is never converted to ``LaunchError``: cleanup has run by then, so the
        original exception propagates unchanged (an ordinary part failure that
        happened first stays visible as its ``__cause__``). The host is always
        left safely settled before the backing store is closed: a cancellation
        of this caller does not abandon the host's shielded shared cleanup —
        it is awaited out to settlement first. The caught cancellation is
        recorded once and re-raised after cleanup; it is deliberately never
        re-armed via ``cancel()``/``uncancel()`` (a close that raises without
        yielding would silently drop the re-armed request). Repeated
        cancellations only re-await that one shielded cleanup task (which
        keeps running under the shield); there is no unprotected await
        anywhere in this teardown and no busy spin: each retry blocks on the
        cleanup task itself.
        """
        errors: list[BaseException] = []
        while True:
            try:
                await self.host.aclose()  # settles runtimes, then the factory closes the members
                break
            except asyncio.CancelledError as exc:
                # This caller was cancelled; the host's shared cleanup task
                # keeps running under its shield. Record the cancellation
                # ONCE for the tail below (repeated cancel() calls re-deliver
                # here while the retry re-awaits the SAME still-running task;
                # they are deduplicated, never re-raised mid-teardown) and
                # never re-arm it via cancel()/uncancel(): the next await —
                # e.g. an engine close that raises without yielding — would
                # silently drop a re-armed request.
                if not any(isinstance(e, asyncio.CancelledError) for e in errors):
                    errors.append(exc)
                if self.closed:
                    break  # already settled; the store close is now safe
                # Not settled: loop and re-await the SAME still-running
                # shared cleanup task (host.aclose re-shield-awaits it, so
                # this blocks until settlement — never a spin). The store is
                # never closed under a live cleanup: there is deliberately
                # no unprotected await anywhere on this path.
            except BaseException as exc:  # noqa: BLE001
                # Ordinary host close failure: record it and keep closing the
                # remaining parts — every part is always attempted.
                errors.append(exc)
                break
        try:
            await self.engine.close()  # the owner engine; its host close is idempotent by now
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            try:
                self.store.close()  # synchronous DB cleanup, always — never on an event loop
            except BaseException as exc:  # noqa: BLE001
                # A store close failure must never mask the recorded
                # cancellation or any other first failure: it is recorded
                # like every other part failure instead.
                errors.append(exc)
        if errors:
            first = errors[0]
            # Cancellation, SystemExit and KeyboardInterrupt are process
            # control, never launch failures: cleanup has already run, so
            # propagate them unchanged rather than masking them in
            # LaunchError. An earlier ordinary failure must not mask the
            # cancellation either — it stays chained as the cause.
            for exc in errors:
                if isinstance(exc, (asyncio.CancelledError, SystemExit, KeyboardInterrupt)):
                    if first is exc:
                        raise exc
                    raise exc from first
            raise LaunchError(f"group launch close failed: {first}") from first
