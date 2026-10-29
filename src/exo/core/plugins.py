"""Plugin loader and registration context.

A plugin is a folder with ``plugin.yaml`` and an ``__init__.py`` exposing ``register(ctx)``
(Hermes's format, D-010), or an installed package advertising an ``exo.plugins`` entry point.
``PluginContext`` offers the method names the six Exo plugins already call, with
Hermes-compatible signatures, so wave 2 re-homing is import changes.

A plugin whose ``register`` raises is rolled back: everything it registered is removed and the
error is kept on its ``LoadedPlugin`` record. One bad plugin never stops the core.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from exo.core import trace
from exo.core.hooks import HookRegistry, accepted
from exo.core.slots import Slots
from exo.core.types import Risk, ToolSpec

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "exo.plugins"
MANIFEST = "plugin.yaml"


def call_plugin(fn: Any, *args: Any, **kw: Any) -> Any:
    """Call a plugin hook with the keyword arguments it accepts (all of them when it takes **kwargs)."""
    return fn(*args, **accepted(fn, kw))


def safe_plugin_call(obj: Any, method: str, *args: Any, **kw: Any) -> Any:
    fn = getattr(obj, method, None)
    try:
        return call_plugin(fn, *args, **kw) if fn else None
    except Exception as exc:  # a plugin failure never breaks a turn, but it is recorded
        trace.report(f"{type(obj).__name__}.{method}", exc, kw.get("session_id"))
        return None


class PluginError(RuntimeError):
    """A plugin could not be found, parsed or registered."""


@dataclass
class Manifest:
    name: str
    version: str = ""
    description: str = ""
    kind: str = ""
    path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def config_default(self, key: str) -> Any:
        spec = (self.raw.get("config_schema") or {}).get(key)
        return spec.get("default") if isinstance(spec, dict) else None


@dataclass
class ToolEntry:
    spec: ToolSpec
    handler: Callable[..., Any]
    plugin: str = ""
    check_fn: Callable[[], bool] | None = None
    requires_env: list[str] = field(default_factory=list)
    is_async: bool = False
    emoji: str = ""

    def available(self) -> bool:
        if any(not os.environ.get(v) for v in self.requires_env):
            return False
        if self.check_fn is None:
            return True
        try:
            return bool(self.check_fn())
        except Exception:
            logger.warning("tool %s: check_fn failed", self.spec.name, exc_info=True)
            return False


@dataclass
class CommandEntry:
    name: str
    handler: Callable[..., Any]
    description: str = ""
    args_hint: str = ""
    argument_mode: str | None = None
    plugin: str = ""


@dataclass
class ChannelEntry:
    name: str
    factory: Callable[..., Any]
    description: str = ""
    plugin: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillEntry:
    name: str
    path: Path
    description: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict)
    plugin: str = ""


@dataclass
class LoadedPlugin:
    manifest: Manifest
    source: str  # "dir" or "entry_point"
    error: str | None = None
    registered: list[tuple[str, str]] = field(default_factory=list)  # (kind, name)

    @property
    def ok(self) -> bool:
        return self.error is None


def read_manifest(folder: Path) -> Manifest:
    from exo.gate import parse_yaml  # stdlib YAML subset already used for policies
    folder = Path(folder)
    f = folder / MANIFEST
    try:
        raw = parse_yaml(f.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise PluginError(f"{f}: {e}") from None
    if not isinstance(raw, dict) or not raw.get("name"):
        raise PluginError(f"{f}: needs a mapping with a name")
    return Manifest(name=str(raw["name"]), version=str(raw.get("version", "")),
                    description=str(raw.get("description", "") or "").strip(),
                    kind=str(raw.get("kind", "") or ""), path=folder, raw=raw)


def discover(dirs: Iterable[str | Path]) -> list[Manifest]:
    """Plugin folders directly under each dir (a dir that is itself a plugin counts too)."""
    found: list[Manifest] = []
    seen: set[str] = set()
    for d in dirs:
        d = Path(d).expanduser()
        if not d.is_dir():
            continue
        cands = [d] if (d / MANIFEST).is_file() else sorted(p for p in d.iterdir() if p.is_dir())
        for p in cands:
            if not ((p / MANIFEST).is_file() and (p / "__init__.py").is_file()):
                continue
            try:
                m = read_manifest(p)
            except PluginError as e:
                logger.warning("%s", e)
                continue
            if m.name in seen:
                logger.warning("plugin %s found twice; keeping the first (%s)", m.name, p)
                continue
            seen.add(m.name)
            found.append(m)
    return found


def default_dirs() -> list[Path]:
    """``$EXO_PLUGIN_DIRS`` (os.pathsep list), then ``$EXO_HOME/plugins``."""
    out = [Path(p) for p in os.environ.get("EXO_PLUGIN_DIRS", "").split(os.pathsep) if p]
    home = Path(os.environ.get("EXO_HOME") or Path.home() / ".exo")
    return out + [home / "plugins"]


def _module_name(name: str) -> str:
    return "exo_plugins." + (re.sub(r"\W", "_", name) or "plugin")


def import_folder(m: Manifest) -> Any:
    """Import a plugin folder as a package so its relative imports work."""
    assert m.path is not None
    modname = _module_name(m.name)
    owner = getattr(sys.modules.get(modname), "__exo_plugin__", m.name)
    if owner != m.name:  # a-b and a_b share a module name: the second would replace the first's code (exo-desktop #52)
        raise PluginError(f"plugin {m.name!r} imports as {modname}, which plugin {owner!r} already uses; rename one")
    if "exo_plugins" not in sys.modules:
        pkg = type(sys)("exo_plugins")
        pkg.__path__ = []
        sys.modules["exo_plugins"] = pkg
    spec = importlib.util.spec_from_file_location(modname, m.path / "__init__.py",
                                                  submodule_search_locations=[str(m.path)])
    if spec is None or spec.loader is None:
        raise PluginError(f"cannot import {m.path}")
    for stale in [k for k in sys.modules if k.startswith(modname + ".")]:
        del sys.modules[stale]  # a fresh load never reuses another folder's submodules
    mod = importlib.util.module_from_spec(spec)
    setattr(mod, "__exo_plugin__", m.name)
    sys.modules[modname] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(modname, None)
        raise
    return mod


class PluginContext:
    """What ``register(ctx)`` sees. One per plugin; records undo steps for rollback."""

    def __init__(self, manager: PluginManager, manifest: Manifest) -> None:
        self.manager = manager
        self.manifest = manifest
        self.plugin = manifest.name
        self._undo: list[Callable[[], None]] = []
        self.registered: list[tuple[str, str]] = []

    @property
    def router(self) -> Any:
        """The engine's model Router (``exo.core.models.router.aux_sync(ctx.router, ...)`` for a side call);
        None until an engine owns the manager."""
        return self.manager.router

    def _track(self, kind: str, name: str, undo: Callable[[], None]) -> None:
        self._undo.append(undo)
        self.registered.append((kind, name))

    def rollback(self) -> None:
        for undo in reversed(self._undo):
            try:
                undo()
            except Exception:
                logger.warning("plugin %s: rollback step failed", self.plugin, exc_info=True)
        self._undo.clear()
        self.registered.clear()

    # ---- config

    def get_config(self, key: str, default: Any = None) -> Any:
        """Plugin setting: manager config for this plugin, then the manifest default."""
        conf = self.manager.config.get(self.plugin) or {}
        if key in conf:
            return conf[key]
        d = self.manifest.config_default(key)
        return default if d is None else d

    def get_explicit_config(self, key: str) -> Any:
        """The value set in config for this plugin, or None. No manifest default: a plugin that also reads
        environment variables uses this so an env var can beat the default but not an explicit setting."""
        return (self.manager.config.get(self.plugin) or {}).get(key)

    # ---- registrations

    def register_tool(self, name: str, toolset: str, schema: dict | None = None,
                      handler: Callable[..., Any] | None = None, check_fn: Callable[[], bool] | None = None,
                      requires_env: list | None = None, is_async: bool = False, description: str = "",
                      emoji: str = "", override: bool = False, risk: Risk = "low") -> ToolEntry:
        if handler is None:
            raise PluginError(f"tool {name!r} has no handler")
        tools = self.manager.tools
        if name in tools and not override:
            raise PluginError(f"tool {name!r} already registered by {tools[name].plugin or 'core'}")
        schema = schema or {}
        spec = ToolSpec(name=name, description=description or schema.get("description", ""),
                        parameters=schema.get("parameters") or {"type": "object", "properties": {}},
                        risk=risk, toolset=toolset)
        env = [e if isinstance(e, str) else e.get("name", "") for e in requires_env or []]
        entry = ToolEntry(spec, handler, self.plugin, check_fn, [e for e in env if e], is_async, emoji)
        previous = tools.get(name)
        tools[name] = entry

        def undo() -> None:
            if tools.get(name) is entry:
                if previous is None:
                    del tools[name]
                else:
                    tools[name] = previous
        self._track("tool", name, undo)
        return entry

    def register_hook(self, hook_name: str, callback: Callable[..., Any]) -> Any:
        entry = self.manager.hooks.register(hook_name, callback, self.plugin)
        self._track("hook", hook_name, lambda: self.manager.hooks.unregister(entry))
        return entry

    def register_command(self, name: str, handler: Callable[..., Any], description: str = "",
                         args_hint: str = "", argument_mode: str | None = None) -> CommandEntry:
        clean = name.lower().strip().lstrip("/").replace(" ", "-")
        if not clean:
            raise PluginError("empty command name")
        cmds = self.manager.commands
        if clean in cmds:
            raise PluginError(f"command /{clean} already registered by {cmds[clean].plugin or 'core'}")
        entry = CommandEntry(clean, handler, description or "Plugin command", args_hint.strip(),
                             argument_mode, self.plugin)
        cmds[clean] = entry
        self._track("command", clean, lambda: cmds.pop(clean, None))
        return entry

    def _fill(self, slot: str, kind: str, obj: Any) -> bool:
        active = self.manager.slots.fill(slot, obj, self.plugin)
        self._track(kind, str(obj.name), lambda: self.manager.slots.release(slot, obj))
        return active

    def register_memory_provider(self, provider: Any) -> bool:
        return self._fill("memory", "memory_provider", provider)

    def register_context_engine(self, engine: Any) -> bool:
        return self._fill("context", "context_engine", engine)

    def register_channel(self, name: str, factory: Callable[..., Any], description: str = "",
                         **meta: Any) -> ChannelEntry:
        chans = self.manager.channels
        if name in chans:
            raise PluginError(f"channel {name!r} already registered by {chans[name].plugin}")
        entry = ChannelEntry(name, factory, description, self.plugin, meta)
        chans[name] = entry
        self._track("channel", name, lambda: chans.pop(name, None))
        return entry

    def register_skill(self, name: str, path: str | Path, description: str = "",
                       frontmatter: dict[str, Any] | None = None) -> SkillEntry:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"skill {name!r}: {path} not found")
        key = name if ":" in name else f"{self.plugin}:{name}"
        skills = self.manager.skills
        if key in skills:
            raise ValueError(f"skill {key!r} already registered")
        entry = SkillEntry(key, path, description, dict(frontmatter or {}), self.plugin)
        skills[key] = entry
        self._track("skill", key, lambda: skills.pop(key, None))
        return entry


class PluginManager:
    """Owns everything plugins register. The loop, tools and server read from here."""

    def __init__(self, config: dict[str, dict[str, Any]] | None = None,
                 memory_choice: str | None = None, context_choice: str | None = None,
                 hooks: HookRegistry | None = None, executor_required: bool = False) -> None:
        self.config = dict(config or {})
        self.hooks = hooks or HookRegistry()
        self.router: Any = None  # set by the engine: the model Router, for plugins that make side calls
        self._executor_required = executor_required
        self.slots = Slots(memory_choice=memory_choice, context_choice=context_choice)
        self.tools: dict[str, ToolEntry] = {}
        self.commands: dict[str, CommandEntry] = {}
        self.channels: dict[str, ChannelEntry] = {}
        self.skills: dict[str, SkillEntry] = {}
        self.plugins: dict[str, LoadedPlugin] = {}

    @property
    def executor_required(self) -> bool:
        return self._executor_required

    @executor_required.setter
    def executor_required(self, value: bool) -> None:
        # A manager already attached to a required engine cannot downgrade its security hooks.
        self._executor_required = self._executor_required or bool(value)

    def _run_register(self, manifest: Manifest, source: str, register: Any) -> LoadedPlugin:
        rec = LoadedPlugin(manifest, source)
        if manifest.name in self.plugins:
            rec.error = "already loaded"
            return rec
        ctx = PluginContext(self, manifest)
        try:
            if not callable(register):
                raise PluginError("no register(ctx) function")
            register(ctx)
        except Exception as e:
            ctx.rollback()
            rec.error = f"{type(e).__name__}: {e}"
            logger.warning("plugin %s failed to register: %s", manifest.name, rec.error)
        rec.registered = list(ctx.registered)
        self.plugins[manifest.name] = rec
        return rec

    def load_folder(self, folder: str | Path | Manifest) -> LoadedPlugin:
        m = folder if isinstance(folder, Manifest) else read_manifest(Path(folder))
        if m.name in self.plugins:
            return LoadedPlugin(m, "dir", error="already loaded")
        try:
            mod = import_folder(m)
        except Exception as e:
            rec = LoadedPlugin(m, "dir", error=f"import failed: {type(e).__name__}: {e}")
            self.plugins[m.name] = rec
            logger.warning("plugin %s: %s", m.name, rec.error)
            return rec
        return self._run_register(m, "dir", getattr(mod, "register", None))

    def load_entry_points(self, group: str = ENTRY_POINT_GROUP,
                          allow: Callable[[str], bool] = lambda n: True) -> list[LoadedPlugin]:
        out = []
        for ep in importlib.metadata.entry_points(group=group):
            if not allow(ep.name):
                continue
            m = Manifest(name=ep.name, kind="entry_point")
            try:
                obj = ep.load()
            except Exception as e:
                rec = LoadedPlugin(m, "entry_point", error=f"import failed: {type(e).__name__}: {e}")
                self.plugins.setdefault(ep.name, rec)
                out.append(rec)
                continue
            raw = getattr(obj, "PLUGIN_MANIFEST", None)
            if isinstance(raw, dict):
                m.raw, m.version = raw, str(raw.get("version", ""))
                m.description = str(raw.get("description", ""))
            out.append(self._run_register(m, "entry_point", getattr(obj, "register", obj)))
        return out

    def load_all(self, dirs: Iterable[str | Path] | None = None, enabled: Iterable[str] | None = None,
                 disabled: Iterable[str] = (), entry_points: bool = True) -> list[LoadedPlugin]:
        """Load folders from ``dirs`` (default ``default_dirs()``) then entry points.

        ``enabled`` (if given) is an allow list; ``disabled`` always wins.
        """
        allow_set = set(enabled) if enabled is not None else None
        deny = set(disabled)

        def allow(name: str) -> bool:
            return name not in deny and (allow_set is None or name in allow_set)

        out = [self.load_folder(m) for m in discover(default_dirs() if dirs is None else dirs)
               if allow(m.name)]
        if entry_points:
            out += self.load_entry_points(allow=allow)
        return out

    def tool_specs(self) -> list[ToolSpec]:
        """Specs of plugin tools whose check passes, sorted by name."""
        return [t.spec for _, t in sorted(self.tools.items()) if t.available()]

    def errors(self) -> dict[str, str]:
        return {n: p.error for n, p in self.plugins.items() if p.error}
