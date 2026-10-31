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

from exo.core import trace
from exo.core.slots import NotConfigured
from exo.core.tools.registry import Block, ToolContext
from exo.core.types import ToolCall, ToolResult, ToolSpec

if TYPE_CHECKING:
    from exo.core.plugins import LoadedPlugin, Manifest, PluginManager
    from exo.core.approvals import ApprovalDesk
    from exo.core.tools.registry import Registry

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
        """Run every callback once, newest first. A failure is logged. Ctrl-C or SystemExit in one callback
        still lets the rest run, and the first of those is raised at the end."""
        if self.closed:
            return
        self.closed = True
        callbacks, self._callbacks = self._callbacks, []
        interrupted: BaseException | None = None
        for callback in reversed(callbacks):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise TypeError("plugin cleanup returned an awaitable; use synchronous cleanup")
            except Exception:
                logger.warning("plugin %s: cleanup failed", self.owner, exc_info=True)
            except BaseException as exc:  # noqa: BLE001 - raised after the remaining callbacks
                logger.warning("plugin %s: cleanup interrupted", self.owner, exc_info=True)
                interrupted = interrupted or exc
        if interrupted is not None:
            raise interrupted


def requirements(manifest: Manifest) -> tuple[str, ...]:
    raw = manifest.raw.get("requires", [])
    if not isinstance(raw, list) or any(not isinstance(n, str) or not n.strip() or n != n.strip() for n in raw):
        raise ValueError("requires must be a list of non-empty plugin names")
    return tuple(dict.fromkeys(raw))


def dependency_error(manager: PluginManager, manifest: Manifest) -> str | None:
    """Direct loads require prerequisites already loaded; load_all orders them first."""
    if manager.closed:
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
        if manager.closed:
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
    for ep in importlib.metadata.entry_points(group=group):  # discovery order, as before D-088: hooks keep it
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
        for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):  # discovery order, as load_entry_points
            if allow(ep.name):
                candidates.setdefault(ep.name, _Candidate(Manifest(name=ep.name, kind="entry_point"),
                                                           "entry_point", ep))
    return _load(manager, candidates, allow)


def plugin_handler(fn: Any) -> Any:
    def run(args: dict[str, Any], ctx: ToolContext) -> Any:
        kw = {"session_id": ctx.session or "", "task_id": ctx.session or "", "tool_call_id": ctx.call_id}
        return fn(args, **kw)
    async def run_async(args: dict[str, Any], ctx: ToolContext) -> Any:
        return await run(args, ctx)
    return run_async if inspect.iscoroutinefunction(fn) else run


class PluginToolBridge:
    """Bind plugin tools and translate lifecycle verdicts through explicit collaborators.

    Registration order and Hermes-compatible slot keywords are preserved. The bridge
    owns no engine, session state or approval policy: the existing registry and
    ApprovalDesk remain authoritative, including required-executor enforcement.
    """

    def __init__(self, manager: PluginManager, registry: Registry, *, home: Path,
                 agent_log: Any, private: Callable[[str | None], bool],
                 approvals: ApprovalDesk, required: Callable[[], bool]) -> None:
        from exo.core.plugins import call_plugin, safe_plugin_call
        self.manager, self.registry, self.home = manager, registry, home
        self.agent_log, self.private, self.approvals, self.required = agent_log, private, approvals, required
        self.memory_off = self.memory_init_error = ""
        pm = self.manager
        for name, entry in sorted(pm.tools.items()):
            self.registry.register(entry.spec, plugin_handler(entry.handler), entry.available, replace=True)
        for slot in (pm.slots.memory, pm.slots.context):
            if slot is None:
                continue
            if slot is pm.slots.memory:  # agent_log: the one log, so the provider keeps no second one (D-028), lazy start too
                fn = getattr(slot, "initialize", None)
                if fn is not None:
                    try:
                        call_plugin(fn, "", exo_home=str(self.home), hermes_home=str(self.home), platform="exo",
                              agent_log=self.agent_log)
                    except NotConfigured as exc:
                        self.memory_off = str(exc)
                    except Exception as exc:
                        # Loud at session start, not on the first exo_note: record why memory is down.
                        trace.report("memory.initialize", exc, None)
                        self.memory_init_error = str(exc) or type(exc).__name__
            for schema in safe_plugin_call(slot, "get_tool_schemas") or []:
                spec = ToolSpec(name=schema["name"], description=schema.get("description", ""),
                                parameters=schema.get("parameters") or {"type": "object", "properties": {}},
                                toolset="memory" if slot is pm.slots.memory else "context")
                handler = (lambda o, n, mem: lambda args, ctx: o.handle_tool_call(
                    n, args, session_id=ctx.session, **(self.memory_kw(ctx.session) if mem else {})))(
                    slot, spec.name, slot is pm.slots.memory)
                self.registry.register(spec, handler, replace=True)

    def memory_kw(self, session: str | None) -> dict[str, Any]:
        """A memory tool call's keywords; ``agent_context="private"`` in or under a private thread: write nothing
        (D-028)."""
        kw = {"exo_home": str(self.home), "agent_log": self.agent_log}
        return {**kw, "agent_context": "private"} if self.private(session) else kw

    def hook_kw(self, call: ToolCall, ctx: ToolContext) -> dict[str, Any]:
        return {"tool_name": call.name, "args": call.arguments, "session_id": ctx.session or "",
                "task_id": ctx.session or "", "tool_call_id": call.id, "turn_id": ctx.meta.get("turn_id", ""),
                "author": ctx.meta.get("author", ""),   # who started the turn (D-040): the gate lowers only for
                                                        # the principal's turn (#598)
                "risk": tool.spec.risk if (tool := self.registry.get(call.name)) else "low",
                "cwd": str(ctx.cwd or "")}   # the folder relative paths resolve from (the gate reads them there, #209)

    async def pre_hook(self, call: ToolCall, spec: ToolSpec, ctx: ToolContext) -> Any:
        bound_mode = self.registry.executor_required
        if not self.required() and not self.manager.hooks.has("pre_tool_call"):  # required: create() checks
            return Block("No tool approval gate is available. Reinstall Exo's plugins or enable exo-gate "
                         "in core_plugins (and the seat's plugins list), then restart Exo.")
        # A configured broker binds arguments without relaxing optional policy hooks.
        required_hooks = frozenset({"exo-gate"}) if self.required() else frozenset()
        for verdict in await self.manager.hooks.afire("pre_tool_call", _fail_closed_plugins=required_hooks,
                                                     _isolate_arguments=bound_mode,
                                                     **self.hook_kw(call, ctx)):
            if not isinstance(verdict, dict):
                continue
            if verdict.get("action") == "block":
                return Block(str(verdict.get("message") or "blocked by a plugin"))
            if verdict.get("action") == "approve":
                key = "plugin_rule:" + str(verdict.get("rule_key") or call.name)
                once = bool(verdict.get("once"))  # e.g. credentials: asked every time, never a standing allow
                if key in self.approvals.always and not once:
                    continue
                who: list[str] = []
                decision = await self.approvals.ask(  # D-033: who may answer, and the exact text it binds to
                    ctx.session, str(verdict.get("message") or call.name), call, spec.risk, who,
                    options=["allow", "deny"] if once else None, approvers=[str(a) for a in verdict.get("approvers") or []],
                    binding=str(verdict.get("binding") or ""))
                choice = {"allow": "once", "allow_always": "always"}.get(decision, "deny")
                correlation = {"approval_key": verdict["approval_key"]} if verdict.get("approval_key") else {}
                # Never dispatch a broker-bound call after its gate approval callback failed.
                await self.manager.hooks.afire("post_approval_response", pattern_key=key, choice=choice,
                                               _fail_closed_plugins=frozenset({"exo-gate"}) if bound_mode else frozenset(),
                                               decided_by=who[0] if who else "unknown", session_id=ctx.session or "",
                                               **correlation)
                if decision == "allow_always" and not once:
                    if not verdict.get("persist"):  # a plugin that sets persist keeps its own scoped grants (D-033)
                        self.approvals.always.add(key)
                elif decision != "allow":
                    return Block("the user denied this call")
        return None

    async def post_hook(self, call: ToolCall, result: ToolResult, ctx: ToolContext) -> Any:
        kw = {**self.hook_kw(call, ctx), "result": result.text()}
        await self.manager.hooks.afire("post_tool_call", **kw)
        for text in await self.manager.hooks.afire("transform_tool_result", **kw):
            if isinstance(text, str):
                return ToolResult.of_text(call.id, text, result.is_error)
        return None
