"""Application plugin composition: dependency plans and synchronous resource ownership.

Folder requirements are inspected before importing their code. Python entry points
expose PLUGIN_MANIFEST only after import, so their requirements prevent registration,
not module import. Neither path installs packages or expands an explicit allow list.
"""
from __future__ import annotations

import importlib.metadata
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

if TYPE_CHECKING:
    from exo.core.plugins import LoadedPlugin, Manifest, PluginManager

logger = logging.getLogger(__name__)


class Resources:
    """A terminal, idempotent stack of plugin-owned synchronous cleanup functions."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.closed = False
        self._callbacks: list[Callable[[], Any]] = []

    def add(self, callback: Callable[[], Any]) -> None:
        if self.closed:
            raise RuntimeError(f"{self.owner} is closed")
        if not callable(callback) or inspect.iscoroutinefunction(callback):
            raise TypeError("plugin cleanup must be a synchronous callable")
        self._callbacks.append(callback)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        callbacks, self._callbacks = self._callbacks, []
        for callback in reversed(callbacks):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise TypeError("plugin cleanup returned an awaitable; use synchronous cleanup")
            except Exception:
                logger.warning("plugin %s: cleanup failed", self.owner, exc_info=True)


def requirements(manifest: Manifest) -> tuple[str, ...]:
    raw = manifest.raw.get("requires", [])
    if not isinstance(raw, list) or any(not isinstance(n, str) or not n.strip() or n != n.strip() for n in raw):
        raise ValueError("requires must be a list of non-empty plugin names")
    return tuple(dict.fromkeys(raw))


def dependency_error(manager: PluginManager, manifest: Manifest) -> str | None:
    """Direct loads require prerequisites already loaded; load_all orders them first."""
    if manager._resources.closed:
        return "plugin manager is closed"
    try:
        needed = requirements(manifest)
    except ValueError as exc:
        return str(exc)
    for name in needed:
        rec = manager.plugins.get(name)
        if rec is None or not rec.ok:
            return f"required plugin {name!r} is not loaded successfully"
    return None


@dataclass
class _Candidate:
    manifest: Manifest
    source: str = "dir"
    entry_point: Any = None
    register: Any = None
    prepared: bool = False


def _load(manager: PluginManager, candidates: dict[str, _Candidate],
          allow: Callable[[str], bool]) -> list[LoadedPlugin]:
    from exo.core.plugins import LoadedPlugin

    out: list[LoadedPlugin] = []
    complete: set[str] = set()
    active: list[str] = []

    def failed(name: str, error: str) -> LoadedPlugin:
        candidate = candidates[name]
        rec = LoadedPlugin(candidate.manifest, candidate.source, error=error)
        manager.plugins[name] = rec
        complete.add(name)
        out.append(rec)
        logger.warning("plugin %s: %s", name, error)
        return rec

    def visit(name: str) -> bool:
        if name in complete:
            return manager.plugins[name].ok
        if name in active:
            cycle = active[active.index(name):]
            detail = "dependency cycle: " + " -> ".join([*cycle, name])
            for member in cycle:
                failed(member, detail)
            return False
        candidate = candidates[name]
        manifest = candidate.manifest
        if name in manager.plugins:
            rec = LoadedPlugin(manifest, candidate.source, error="already loaded")
            out.append(rec)
            complete.add(name)
            return manager.plugins[name].ok
        if manager._resources.closed:
            failed(name, "plugin manager is closed")
            return False
        if candidate.source == "entry_point" and not candidate.prepared:
            try:
                obj = candidate.entry_point.load()
            except Exception as exc:
                failed(name, f"import failed: {type(exc).__name__}: {exc}")
                return False
            raw = getattr(obj, "PLUGIN_MANIFEST", None)
            if isinstance(raw, dict):
                manifest.raw, manifest.version = raw, str(raw.get("version", ""))
                manifest.description = str(raw.get("description", ""))
            candidate.register = getattr(obj, "register", obj)
            candidate.prepared = True
        try:
            needed = requirements(manifest)
        except ValueError as exc:
            failed(name, str(exc))
            return False
        active.append(name)
        try:
            for required in needed:
                if not allow(required):
                    failed(name, f"required plugin {required!r} is disabled or excluded")
                    return False
                if required in candidates:
                    ok = visit(required)
                else:
                    loaded = manager.plugins.get(required)
                    ok = loaded is not None and loaded.ok
                if not ok:
                    if name not in complete:
                        failed(name, f"required plugin {required!r} is missing or failed")
                    return False
            if candidate.source == "dir":
                rec = manager.load_folder(manifest)
            else:
                rec = manager._run_register(manifest, candidate.source, candidate.register)
            out.append(rec)
            complete.add(name)
            return rec.ok
        finally:
            active.pop()

    for name in candidates:
        visit(name)
    return out


def load_entry_points(manager: PluginManager, group: str,
                      allow: Callable[[str], bool]) -> list[LoadedPlugin]:
    from exo.core.plugins import Manifest

    candidates: dict[str, _Candidate] = {}
    for ep in sorted(importlib.metadata.entry_points(group=group), key=lambda ep: ep.name):
        if allow(ep.name):
            candidates.setdefault(ep.name, _Candidate(Manifest(name=ep.name, kind="entry_point"),
                                                       "entry_point", ep))
    return _load(manager, candidates, allow)


def load_all(manager: PluginManager, dirs: Iterable[str | Path] | None,
             enabled: Iterable[str] | None, disabled: Iterable[str], entry_points: bool) -> list[LoadedPlugin]:
    from exo.core.plugins import ENTRY_POINT_GROUP, Manifest, default_dirs, discover

    selected = set(enabled) if enabled is not None else None
    denied = set(disabled)

    def allow(name: str) -> bool:
        return name not in denied and (selected is None or name in selected)

    candidates = {m.name: _Candidate(m) for m in discover(default_dirs() if dirs is None else dirs) if allow(m.name)}
    if entry_points:
        for ep in sorted(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: ep.name):
            if allow(ep.name):
                candidates.setdefault(ep.name, _Candidate(Manifest(name=ep.name, kind="entry_point"),
                                                           "entry_point", ep))
    return _load(manager, candidates, allow)
