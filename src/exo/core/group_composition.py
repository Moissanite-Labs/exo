"""Group composition: one authorized CoreEngine per swarm group member (docs/SWARM-COMPOSITION.md).

The host injects the trusted owner and an explicit authorized profile -> Seat mapping. This
module reads no metadata, config or seat files and authorizes nothing implicitly: the stored
group's owner and full membership are validated against that mapping BEFORE anything is
constructed. Construction is synchronous (GroupHost's EngineFactory is a plain Callable);
cleanup is async via aclose(). Failures fail closed: partial engines are retained for
aclose(), never retried, and another member's writer claim is never taken over. A composition
is single-use: after the first call (success or failure) it rejects further calls until
aclose() completes, and a failed aclose() retains the failed engines so it can be retried.

Plugins are never discovered implicitly: no entry points, no ``Config.load``, no default
plugin folders. The host may inject a ``GroupPlugins`` profile with explicit plugin folders,
an allow-list of enabled plugin names (``()`` by default — inject folders and nothing
loads until the optimistically-passed ``enabled=None`` names every folder plugin — never
"all installed plugins"), a deep-copied-per-member plugin config, memory/context backend
choices, extra skill folders, and an optional host executor + ``executor_required`` pair
(passed through unchanged to every engine, which enforces it exactly like compose does — it
is never silently downgraded here). Each member's Seat carries its own ``[plugins] enabled``
list; a seat without one uses the injected default (or, absent that, the names found in the
injected folders) when the host opts in with ``enabled=None``). The Seat's ``exo-memory`` identity (``exo_home``, ``agent_id``,
``principal``, absolute ``principal_store``) is bound into that member's config copy, and
ALL member bindings and ``principal_store`` paths are prevalidated before any engine (or
plugin) is constructed, so a bad seat fails closed with no partial construction.

Opt-in ``workspace_check=True`` adds the G10 preflight (``exo.core.group_workspace``) to that
same pre-construction validation: the effective cwd of every member — the seat's own folder when
it exists, else the injected ``cwd``, else the process folder, exactly as the engine resolves it
— must be an absolute existing directory, and no two members may share a folder, nest inside
each other, or sit in one git working tree. This reads paths only: it does not change any
engine's cwd, lock anything, create anything or act as a sandbox, and it is off by default so
existing compositions keep their behaviour.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from exo.application import CoreEngine
from exo.core.group_workspace import WorkspaceError, validate_workspaces
from exo.core.groups import SavedGroup
from exo.core.plugins import PluginError, PluginManager, discover
from exo.core.seats import Seat
from exo.core.skills import pack_dir


class CompositionError(RuntimeError):
    """Owner, membership or construction failure: the composition fails closed."""


@dataclass(frozen=True)
class GroupPlugins:
    """Host-injected plugin profile shared by every member engine (explicit, never implicit).

    ``dirs`` are the only plugin folders ever considered (no entry points, no
    ``$EXO_HOME/plugins``); ``enabled`` is the allow-list applied when a seat has no
    ``[plugins] enabled`` list of its own. The safe default is the empty tuple: inject
    ``dirs`` and nothing loads until the host names plugins or passes ``enabled=None``
    (opt-in to every name discovered in ``dirs`` — explicitly NOT "all installed
    plugins"). ``config`` is deep-copied per
    member before the seat's exo-memory identity is merged in, so members never share
    mutable config. ``executor``/``executor_required`` are passed through to every engine
    unchanged: required mode is enforced by the engine exactly as in compose, never
    silently weakened here.
    """
    dirs: tuple[str | os.PathLike, ...] = ()
    enabled: tuple[str, ...] | None = ()
    config: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    memory_choice: str | None = None
    context_choice: str | None = None
    skill_folders: tuple[str | os.PathLike, ...] = ()
    executor: Any = None
    executor_required: bool = False


class GroupComposition:
    """Synchronous EngineFactory for GroupHost with async cleanup.

    Every member gets a distinct real CoreEngine against the same home (one shared session
    DB) bound to the exact authorized Seat for its profile, with the writer lock claimed.
    """

    def __init__(self, profiles: Mapping[str, Seat], *, owner: str,
                 home: str | os.PathLike, models: dict[str, Any] | None = None,
                 cwd: str | None = None, env: Mapping[str, str] | None = None,
                 plugins: GroupPlugins | None = None, workspace_check: bool = False) -> None:
        if not isinstance(owner, str) or not owner.strip():
            raise CompositionError("owner is required")
        if not isinstance(workspace_check, bool):  # strict: no truthiness, no 1/0, no "yes"
            raise CompositionError(
                f"workspace_check must be True or False, not {type(workspace_check).__name__}")
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
        self._plugins = plugins or GroupPlugins()
        self._workspace_check = workspace_check
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

    @property
    def workspace_check(self) -> bool:
        """Whether the opt-in G10 workspace preflight runs before construction."""
        return self._workspace_check

    # -------------------------------------------------- per-member binding (prevalidated)

    def _effective_cwd(self, profile: str) -> str:
        """The folder this member's engine will work in, resolved exactly as the engine resolves it.

        ``CoreEngine`` prefers the seat's own ``[session] cwd`` when that folder exists (and otherwise
        silently falls back), then the injected ``cwd``, then the process folder. Checking anything else
        would validate a folder the member never uses.
        """
        seat = self._seats[profile]
        if seat.cwd:
            p = Path(seat.cwd).expanduser()
            if p.is_dir():
                return str(p)
        return self._cwd or os.getcwd()

    def _check_workspaces(self, members: list[str]) -> None:
        """G10 preflight over every member's effective cwd; a refusal is a ``CompositionError``."""
        try:
            validate_workspaces({profile: self._effective_cwd(profile) for profile in members})
        except WorkspaceError as exc:
            raise CompositionError(f"workspace check failed: {exc}") from exc

    def _member_binding(self, profile: str) -> dict[str, Any]:
        """The member's plugin config + enabled list; every value validated before any build."""
        seat = self._seats[profile]
        config = copy.deepcopy(dict(self._plugins.config))
        memory = config.setdefault("exo-memory", {})
        memory["exo_home"] = str(self._home)
        memory["agent_id"] = seat.name
        if seat.principal:
            memory["principal"] = seat.principal
        if seat.principal_store:
            root = Path(seat.principal_store).expanduser()
            if not root.is_absolute():
                raise CompositionError(
                    f"profile {profile!r}: seat principal store must be an absolute path")
            memory["store_root"] = str(root)
        if seat.plugins is not None:
            enabled = list(seat.plugins)
        elif self._plugins.enabled is not None:
            enabled = list(self._plugins.enabled)
        else:
            # Explicitly never "all installed plugins": only names in the injected folders
            # (a missing or empty folder enables nothing).
            try:
                enabled = [m.name for m in discover(self._plugins.dirs)]
            except PluginError as exc:
                raise CompositionError(f"profile {profile!r}: plugin folders unreadable: {exc}") from exc
        return {"config": config, "enabled": enabled}

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
        # Opt-in G10 preflight: also before any binding, plugin load or engine construction (and for a
        # one-member group too, so its own path checks are the same as everyone else's).
        if self._workspace_check:
            self._check_workspaces(members)
        try:
            # Prevalidate ALL member bindings (incl. every principal_store) before any
            # plugin is loaded or engine constructed, so a bad seat fails closed with no
            # partial construction.
            bindings = {profile: self._member_binding(profile) for profile in members}
            for profile in members:
                seat = self._seats[profile]
                binding = bindings[profile]
                pm = PluginManager(config=binding["config"],
                                   executor_required=self._plugins.executor_required,
                                   memory_choice=self._plugins.memory_choice,
                                   context_choice=self._plugins.context_choice)
                # Explicit folders + allow list only; entry_points=False: no implicit discovery.
                pm.load_all(dirs=self._plugins.dirs, enabled=binding["enabled"],
                            entry_points=False)
                failed = sorted(pm.errors())  # a plugin that fails to load fails the composition
                if failed:
                    raise CompositionError(f"profile {profile!r}: plugins failed to load: "
                                           + ", ".join(failed))
                folders: list[str | os.PathLike] = [*seat.skill_folders,
                                                    *self._plugins.skill_folders]
                if pack := pack_dir():
                    folders.append(pack)
                engine = CoreEngine(self._home, self._models, env=dict(self._env), cwd=self._cwd,
                                    plugins=pm, seat=seat, skill_folders=folders,
                                    executor=self._plugins.executor,
                                    executor_required=self._plugins.executor_required,
                                    mcp=copy.deepcopy(seat.mcp),
                                    channels=copy.deepcopy(seat.channels))
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
