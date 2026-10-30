"""The real Engine behind exod: router, tools, loop, sessions and plugins wired together.

``exo.compose.create()`` is the factory ``exo serve`` loads by default; it builds a ``CoreEngine``.
Everything lives under ``$EXO_HOME``: ``config.json`` (models, plugins) and
``sessions.sqlite`` (messages and the event log, so events replay across restarts).

Adapters kept here so the parts stay independent of each other:
- ``SqliteLog``: the server's EventLog over sessions.sqlite; the Hub numbers events.
- ``HubBus``: the loop's event bus, publishing through the Hub (written before sent).
- tool bridge: one ToolContext per call (cwd, cancel, emit) and the plugin hook names
  ``pre_tool_call`` / ``post_tool_call`` / ``transform_tool_result`` on Registry hooks.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from exo import diagnostics, provenance
from exo.core import trace, workspace
from exo.core.agents import Delegator
from exo.core import agentlog
from exo.core.context import AgentContext, ContextTree, DefaultCompressor, TurnContext, apply_selection as _apply_selection  # noqa: F401 - legacy named export
from exo.core.hooks import maybe_await
from exo.core.findings import Findings
from exo.core.loop import Loop
from exo.core.mcp_client import McpManager
from exo.core.models.fake import RuleFake
from exo.core.models.router import Router, merge_provider
from exo.model_spend import resolve_spend_policy
from exo.core import jobs as jobs_mod
from exo.core.peer_target import EngineTarget
from exo.core.ingress import STANDING_LINE, PeerConfig
from exo.core.plugins import PluginManager, call_plugin, safe_plugin_call
from exo.config import Config, ConfigError, check_not_secret, redact
from exo.principal import CHANNEL, current_caller, ingress_author, is_principal, require_principal
from exo.core.approvals import ApprovalDesk
from exo.core.seats import Seat, list_seats
from exo.core.sessions import SessionLocked, SessionStore
from exo.core.slots import NotConfigured
from exo.core.skills import SkillIndex, register_skill_tool
from exo.core.tools.builtin import harness as harness_tool
from exo.core.tools.builtin import ui as ui_tool
from exo.core.tools.registry import Block, Registry, ToolContext, default_registry
from exo.core.tools.executor import (
    ExecutionBroker, ExecutorConfigurationError, load_executor, validate_executor,
)
from exo.core.types import ToolCall, ToolResult, ToolSpec, to_dict
from exo.protocol import schema as s
from exo.server.engine import CommandFailed, Hub

_MISSING = object()
TUTORIAL_LINE = (
    "The user is new to Exo and has tutorial mode on. When an Exo concept they may not know comes up (memory "
    "and the summary tree, threads, the approval gate, steering, model routes, peers, trace, findings), explain "
    "it in one plain sentence the first time, then carry on. Do not lecture or repeat it.")
CODE_FIND_LINE = ("Use code_find for unfamiliar code and behavior questions across many files; use grep or read_file "
                  "when you already know the symbol or path.")
SYSTEM_PROMPT = ("You are Exo, a careful coding agent. Use the tools to read, edit and run "
                 "things in the working directory, then answer briefly.")


class SqliteLog:
    """EventLog over the ``events`` table of sessions.sqlite."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def append(self, event: s.Event) -> None:
        self.store.write_event(event)

    def read(self, since: int, session: str | None = None) -> Iterable[s.Event]:
        return self.store.events_since(since, session, limit=1 << 30, with_global=True)

    def last_seq(self) -> int:
        return self.store.last_event_seq()


class HubBus:
    """Stands in for the loop's EventBus: every loop event goes through the server Hub."""

    def __init__(self, engine: CoreRuntime) -> None:
        self.engine = engine

    async def emit(self, body: Any, session: str | None = None) -> s.Event:
        return self.engine.hub.publish(body, session)


class CoreRuntime:
    """Turn orchestration with a core-owned tree and an injected context view."""

    def __init__(self, home: str | os.PathLike, models: dict[str, Any] | None = None, *,
                 registry: Registry | None = None, plugins: PluginManager | None = None,
                 executor: ExecutionBroker | None = None, executor_required: bool = False,
                 cwd: str | None = None, system_prompt: str = SYSTEM_PROMPT,
                 env: dict[str, str] | None = None, delegation: dict[str, Any] | None = None,
                 seat: Seat | None = None, skill_folders: Iterable[str | os.PathLike] | None = None,
                 mcp: dict[str, dict[str, Any]] | None = None,
                 channels: dict[str, dict[str, Any]] | None = None,
                 tree_cfg: dict[str, Any] | None = None, schedule: bool = False,
                 context_factory: Callable[[ContextTree], TurnContext] | None = None,
                 trace_cfg: dict[str, Any] | None = None, peers: dict[str, Any] | None = None,
                 jobs: dict[str, Any] | None = None, peers_dir: str | os.PathLike | None = None,
                 attach_peers: Any = None, attach_scheduler: Any = None, **loop_kw: Any) -> None:
        # Fail before opening stores, loading adapters or registering host handlers.
        required = executor_required or os.environ.get("EXO_TOOL_EXECUTOR_REQUIRED") == "1"
        if executor is not None:
            executor = validate_executor(executor)
        elif registry is not None and registry.executor is not None:
            executor = validate_executor(registry.executor)
        else:
            executor, loaded_required = load_executor()
            required = required or loaded_required
        required = required or bool(registry and registry.executor_required) or bool(plugins and plugins.executor_required)
        if required and executor is None:
            raise ExecutorConfigurationError("Required tool executor is unavailable")
        mcp_cfg = {**(mcp or {}), **(seat.mcp if seat else {})}
        if required and mcp_cfg and getattr(executor, "supports_mcp_startup", False) is not True:
            raise ExecutorConfigurationError("Required tool executor does not support MCP transport startup")
        plugins = plugins or PluginManager()
        plugins.executor_required = required
        if required:
            gate = plugins.plugins.get("exo-gate")
            roots = getattr(executor, "trusted_plugin_dirs", ())
            if not isinstance(roots, (tuple, list)) or not all(
                    isinstance(p, str) and Path(p).is_absolute() for p in roots):
                raise ExecutorConfigurationError("executor trusted_plugin_dirs must contain absolute paths")
            if (gate is None or not gate.ok or gate.source != "dir" or gate.manifest.path is None
                    or not any(gate.manifest.path.resolve().is_relative_to(Path(p).resolve()) for p in roots)
                    or any(not any(e.plugin == "exo-gate" for e in plugins.hooks.entries(point))
                           for point in ("pre_tool_call", "post_approval_response"))):
                raise ExecutorConfigurationError("Required executor needs a trusted folder exo-gate with security hooks")
        self._bus_target: Any  # attached lazily by the bus ingress adapter
        self.executor = executor
        self.executor_required = required
        self.home = Path(home)
        self.cwd_note = ""  # why the default cwd is not the one asked for (shown at session start)
        self.memory_init_error = ""  # why exo-memory failed to start (shown at session start)
        self.memory_off = ""  # why memory is off until set up (NotConfigured): a quiet notice, never a finding
        self.cwd = self._seat_cwd(seat) or cwd or os.getcwd()  # the seat's own folder beats the shared default
        self._cwd_seen: dict[str, set[str]] = {}  # session -> folders already warned about (D-032)
        self.handoff_sources: dict[str, Any] = {"jobs": lambda session: []}  # D-032: facts for /handoff; key -> fn(session)
        self.store = SessionStore(self.home / "sessions.sqlite")
        self.hub = Hub(SqliteLog(self.store), visible=self.event_visible)
        self.seat = seat
        self.env_allow = tuple(seat.env_allow) if seat else ()  # [tools] env_allow, see tool_env
        self.env_deny = tuple(seat.env_deny) if seat else ()  # [tools] env_deny (D-039)
        self.agent = seat.name if seat else "default"  # one agent, one memory: every thread of this seat (D-028)
        self.log_view = agentlog.AgentLogView(self.store, self.agent)
        self._writer: Any = None  # the agent's writer lock, taken by claim_writer() (create() does)
        self._group_host: Any = None  # trusted host bound only by compose.create(..., group_host=...); close() awaits it first
        principal_spend = (models or {}).get("spend")
        active_principal = (seat.principal if seat else None) or os.environ.get("EXO_PRINCIPAL")
        if seat and seat.principal and os.environ.get("EXO_PRINCIPAL") not in (None, "", seat.principal):
            raise ValueError("seat principal conflicts with EXO_PRINCIPAL")
        selection = resolve_spend_policy(principal_spend, principal=active_principal)
        over, models = (seat.models if seat else {}), dict(models or {})  # the seat's tables win entry by entry, like [trace]
        for k, v in over.items():  # a provider entry merges key by key with the router's rule, so it keeps config's URL
            old = models.get(k)
            if isinstance(v, dict) and isinstance(old, dict):
                v = {**old, **({n: merge_provider(old.get(n), p) for n, p in v.items()} if k == "providers" else v)}
            models[k] = v
        if principal_spend is not None:
            models["spend"] = principal_spend
        elif seat and "spend" in seat.models and selection.source is None:
            raise ValueError("model-spend policy belongs to the principal, not a seat")
        self.router = Router.from_config(models, instances={"fake": RuleFake(models.get("fake"))}, env=env,
                                         principal=active_principal)
        self._automatic_main = "main" not in (models.get("roles") or {}) and not (seat and seat.tier)
        self.registry = registry or default_registry(executor=executor, executor_required=required)
        self.registry.executor = executor
        self.registry.executor_required = required or executor is not None
        self.plugins = plugins
        self.plugins.router = self.router  # plugins reach it as ctx.router
        self.approvals = ApprovalDesk(lambda body, sid: self.hub.publish(body, sid), self._approval_waited)
        self.channels: dict[str, Any] = {}  # running channel plugins by name
        self._channel_cfg: dict[str, dict[str, Any]] = {**(channels or {}), **(seat.channels if seat else {})}
        self._tasks: set[asyncio.Task] = set()
        trace_cfg = {**(trace_cfg or {}), **(seat.trace if seat else {})}  # the seat's [trace] wins (D-032)
        self.thresholds = trace.Thresholds.of(trace_cfg)
        self.findings = Findings(self.store, self.thresholds)
        self._trace_on = trace_cfg.get("enabled", True) is not False
        trace.add_sink(self._on_harness_error)
        self.peer_cfg = PeerConfig.of({**(peers or {}), **(seat.peers if seat else {})})  # D-026
        self.peer_cfg_dir = peers_dir
        self._bridge_plugins()
        if seat and seat.tier and self.router.roles.get(seat.tier):
            self.router.set_role("main", list(self.router.roles[seat.tier]))
        self.skills = SkillIndex.discover([*(seat.skill_folders if seat else []), self.home / "skills"]
                                          if skill_folders is None else skill_folders, self.plugins.skills.values(),
                                          only=seat.skill_load if seat else None)
        register_skill_tool(self.registry, self.skills)
        if self.registry.get("ui") is None:
            ui_tool.register(self.registry, lambda body, sid: self.hub.publish(body, sid))  # D-014 panels
        harness_tool.register(self.registry, self.trace_text, self.findings.file)  # D-025
        self._peer_target = EngineTarget(self)  # peers, bus and job notices reach sessions through it
        self.peers = attach_peers(self) if attach_peers else None  # D-026: exo.peers.PeerService, from exo.compose
        self.jobs = jobs_mod.attach(self, {**(jobs or {}), **(seat.jobs if seat else {})})  # D-032: background jobs
        self.handoff_sources["jobs"] = self._open_jobs  # /handoff lists the agent's running background jobs
        self.mcp = McpManager(mcp_cfg, env)
        self._started: asyncio.Future | None = None
        self._prompt_head = system_prompt
        self._prompt_tail = [self.skills.prompt(), CODE_FIND_LINE if self.registry.specs(names=["code_find"]) else "",
                             STANDING_LINE if self.peer_cfg.enabled else ""]
        self._soul_sig = self._soul_stat()
        system_prompt = self._compose_prompt()
        slots = self.plugins.slots
        self.context_tree = tree = AgentContext(self.store, DefaultCompressor(self.router.provider("aux")), **(tree_cfg or {}))
        agentlog.register_zoom(self.registry, self.log_view)
        self.loop = Loop(self.store, self.router.provider("main"), bus=HubBus(self),  # type: ignore[arg-type]
                         context=context_factory(tree) if context_factory is not None else tree, memory=slots.memory,
                         tool_specs=self._specs, run_tool=self.run_tool,
                         system_prompt=system_prompt, model=self._main_ref(), **loop_kw)
        self.loop.on_turn_end = self.observe_turn
        self.loop.before_turn = self._refresh_soul
        self.loop.system_extra = self._tutorial_line
        signer = None
        if seat:  # receipts are signed with the seat key (made on first use, mode 0600)
            from exo import crypto
            from exo.core.seats import ensure_key
            try:
                signer = crypto.Signer.from_file(seat.name, ensure_key(seat.key_path(self.home)))
            except Exception:
                signer = None
        self.agents = Delegator(self, delegation, signer=signer)  # the delegate tool and the tasks table
        self.agents.install()
        self.scheduler = attach_scheduler(self) if attach_scheduler else None  # exo.scheduler, from exo.compose
        self._schedule = schedule  # serve turns the cron loop on; tests and one-shot engines leave it off

    def _compose_prompt(self) -> str:
        return "\n\n".join(p for p in (self._prompt_head, self.seat.soul if self.seat else "", *self._prompt_tail) if p)

    def _soul_stat(self) -> tuple[int, int] | None:
        path = self.seat.soul_path if self.seat else ""
        try:
            st = os.stat(path) if path else None
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size) if st else None

    def _refresh_soul(self) -> str:
        """Turn start: re-read the seat's SOUL.md if its mtime or size changed (one stat), and return the soul's
        short hash for turn.started. An unreadable file keeps the last good text."""
        seat = self.seat
        if seat is None or not seat.soul_path:
            return ""
        sig = self._soul_stat()
        if sig != self._soul_sig:
            self._soul_sig = sig
            try:
                text = Path(seat.soul_path).read_text("utf-8").strip() if sig else ""
            except (OSError, UnicodeDecodeError) as exc:
                trace.report("soul reload", exc)
                text = seat.soul
            if text != seat.soul:
                seat.soul = text
                self.loop.system_prompt = self._compose_prompt()
        return hashlib.sha1(seat.soul.encode()).hexdigest()[:8] if seat.soul else ""

    def _specs(self) -> list[ToolSpec]:
        """Specs the model sees: every toolset, or the seat's (``mcp`` matches every ``mcp:<server>``)."""
        allow = self.seat.toolsets if self.seat else None
        if allow is None:
            return self.registry.specs()
        return [sp for sp in self.registry.specs() if sp.toolset in allow or sp.toolset.split(":")[0] in allow
                or sp.toolset in ("skills", "harness", "peers") or sp.name == agentlog.ZOOM.name]

    async def start(self) -> None:
        """Connect MCP servers once (idempotent; the first command triggers it, the next retries a failed start)."""
        if self._started is None:
            if self.peers is not None and self.peers.autostart:
                await self.peers.start()
            if self._writer is not None:  # only the one writer may call a running job lost
                await self.jobs.recover()
            self._started = asyncio.ensure_future(self._start_runtime())
            if self._schedule and self.scheduler is not None:
                self.scheduler.start()
        try:
            await asyncio.shield(self._started)
        except BaseException:
            # A start that raised (the _main_status provider probe, any error) or was cancelled is cleared to retry.
            task = self._started
            if task is not None and task.done() and (task.cancelled() or task.exception() is not None):
                self._started = None
            raise

    async def _main_status(self, table: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        from exo import providers_setup
        state = await asyncio.to_thread(providers_setup.main_status, self.router, self.home,
                                        automatic=self._automatic_main, table=table)
        if state["suggested"] and self._automatic_main:
            self.router.prefer("main", state["suggested"])
            self.loop.model = self._main_ref()
        return state

    async def _start_runtime(self) -> None:
        if self._automatic_main:
            await self._main_status()
        await self.mcp.start(self.registry)

    async def start_channels(self, link: Any) -> dict[str, str | None]:
        """Run ``factory(link, **config)`` for each channel the seat enables; ``link`` is exod's ``ChannelLink``
        (or any object with ``call`` and ``events``). Returns name -> error (None when running)."""
        out: dict[str, str | None] = {}
        for name, cfg in self._channel_cfg.items():
            entry = self.plugins.channels.get(name)
            if entry is None or name in self.channels:
                out[name] = None if entry else "no plugin registered this channel"
                continue
            try:
                chan = await maybe_await(entry.factory(link, **(cfg or {})))
                await maybe_await(chan.start())
                self.channels[name] = chan
                out[name] = None
            except Exception as exc:  # one bad channel never stops the server
                out[name] = f"{type(exc).__name__}: {exc}"
                trace.report(f"channel {name} start", exc)
        return out

    async def stop_channels(self) -> None:
        for name, chan in list(self.channels.items()):
            try:
                await maybe_await(chan.stop())
            except Exception as exc:
                trace.report(f"channel {name} stop", exc)
            self.channels.pop(name, None)

    def claim_writer(self) -> None:
        """Become the one writer of this agent's log and tree (raises agentlog.WriterBusy if another process is)."""
        if self._writer is None:
            self._writer = agentlog.claim_writer(self.home, self.agent)
            self.agents.fail_stale()  # only the writer may call this agent's leftover delegate tasks dead

    async def close(self) -> None:
        host = self._group_host
        if host is not None:  # first: a bound trusted host stops before any owner resource is released
            await host.aclose()  # a failure or caller cancellation propagates; everything below stays available
        self.jobs.target.closed = True  # from here on no job notice starts a turn, while channels and peers stop too
        agentlog.release_writer(self._writer)
        self._writer = None
        await self.stop_channels()
        errors: list[BaseException] = []  # every step runs; the first error is raised at the end
        for step in (self._stop_peers, self.jobs.close, self._close_scheduler, self._close_mcp):
            try:
                await step()  # jobs end with exod
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                errors.append(exc)
        trace.remove_sink(self._on_harness_error)  # after the steps: a notice that fails in jobs.close is recorded
        try:
            self.store.close()  # last: the hub is closed before this (serve), so nothing writes events after it
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        if errors:
            raise errors[0]

    async def _stop_peers(self) -> None:
        if self.peers is not None:
            await self.peers.stop()

    async def _close_scheduler(self) -> None:
        if self.scheduler is not None:
            try:
                await self.scheduler.stop()
            finally:  # close() is final and runs even if stop() raised
                self.scheduler.close()

    async def _close_mcp(self) -> None:
        await self.mcp.close()  # always: a start cleared after failing may have left servers running
        self._started = None

    def _main_ref(self) -> str:
        chain = self.router.chain("main")
        return chain[0].ref if chain else ""

    # ------------------------------------------------------------ tools and plugins

    def _bridge_plugins(self) -> None:
        pm = self.plugins
        for name, entry in sorted(pm.tools.items()):
            self.registry.register(entry.spec, self._plugin_handler(entry.handler), entry.available, replace=True)
        for slot in (pm.slots.memory, pm.slots.context):
            if slot is None:
                continue
            if slot is pm.slots.memory:  # agent_log: the one log, so the provider keeps no second one (D-028), lazy start too
                fn = getattr(slot, "initialize", None)
                if fn is not None:
                    try:
                        call_plugin(fn, "", exo_home=str(self.home), hermes_home=str(self.home), platform="exo",
                              agent_log=self.log_view)
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
                    n, args, session_id=ctx.session, **(self._memory_kw(ctx.session) if mem else {})))(
                    slot, spec.name, slot is pm.slots.memory)
                self.registry.register(spec, handler, replace=True)
        self.registry.add_hook("pre_tool_call", self._pre_hook)
        self.registry.add_hook("post_tool_call", self._post_hook)

    def _memory_kw(self, session: str | None) -> dict[str, Any]:
        """A memory tool call's keywords; ``agent_context="private"`` in or under a private thread: write nothing
        (D-028)."""
        kw = {"exo_home": str(self.home), "agent_log": self.log_view}
        return {**kw, "agent_context": "private"} if self.store.private(session) else kw

    @staticmethod
    def _plugin_handler(fn: Any) -> Any:
        async def run(args: dict[str, Any], ctx: ToolContext) -> Any:
            kw = {"session_id": ctx.session or "", "task_id": ctx.session or "", "tool_call_id": ctx.call_id}
            if inspect.iscoroutinefunction(fn):
                return await fn(args, **kw)
            return await asyncio.to_thread(fn, args, **kw)
        return run

    def _hook_kw(self, call: ToolCall, ctx: ToolContext) -> dict[str, Any]:
        return {"tool_name": call.name, "args": call.arguments, "session_id": ctx.session or "",
                "task_id": ctx.session or "", "tool_call_id": call.id, "turn_id": ctx.meta.get("turn_id", ""),
                "author": ctx.meta.get("author", ""),   # who started the turn (D-040): the gate lowers only for
                                                        # the principal's turn (#598)
                "risk": tool.spec.risk if (tool := self.registry.get(call.name)) else "low",
                "cwd": str(ctx.cwd or "")}   # the folder relative paths resolve from (the gate reads them there, #209)

    async def _pre_hook(self, call: ToolCall, spec: ToolSpec, ctx: ToolContext) -> Any:
        bound_mode = self.registry.executor_required
        if not self.executor_required and not self.plugins.hooks.has("pre_tool_call"):  # required: create() checks
            return Block("No tool approval gate is available. Reinstall Exo's plugins or enable exo-gate "
                         "in core_plugins (and the seat's plugins list), then restart Exo.")
        # A configured broker binds arguments without relaxing optional policy hooks.
        required_hooks = frozenset({"exo-gate"}) if self.executor_required else frozenset()
        for verdict in await self.plugins.hooks.afire("pre_tool_call", _fail_closed_plugins=required_hooks,
                                                     _isolate_arguments=bound_mode,
                                                     **self._hook_kw(call, ctx)):
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
                await self.plugins.hooks.afire("post_approval_response", pattern_key=key, choice=choice,
                                               _fail_closed_plugins=frozenset({"exo-gate"}) if bound_mode else frozenset(),
                                               decided_by=who[0] if who else "unknown", session_id=ctx.session or "",
                                               **correlation)
                if decision == "allow_always" and not once:
                    if not verdict.get("persist"):  # a plugin that sets persist keeps its own scoped grants (D-033)
                        self.approvals.always.add(key)
                elif decision != "allow":
                    return Block("the user denied this call")
        return None

    async def _post_hook(self, call: ToolCall, result: ToolResult, ctx: ToolContext) -> Any:
        kw = {**self._hook_kw(call, ctx), "result": result.text()}
        await self.plugins.hooks.afire("post_tool_call", **kw)
        for text in await self.plugins.hooks.afire("transform_tool_result", **kw):
            if isinstance(text, str):
                return ToolResult.of_text(call.id, text, result.is_error)
        return None

    async def run_tool(self, call: ToolCall, lctx: dict[str, Any]) -> ToolResult:
        """The loop's tool runner: a fresh ToolContext per call."""
        sid = lctx.get("session")
        info = self.store.get_session(sid) if sid else None
        loop = asyncio.get_running_loop()
        # Chunks a tool thread queues between two loop turns go out as one tool.output: one publish (and one
        # sessions.sqlite write) per turn instead of thousands, so a reader that keeps up never nears MAX_LAG (D-079).
        pending: list[str] = []
        lock = threading.Lock()

        def flush() -> None:
            with lock:
                text = "".join(pending)
                pending.clear()
            self.hub.publish(s.ToolOutput(call_id=call.id, text=text), sid)

        def emit(text: str) -> None:  # tool threads call this; publish on the event loop
            try:
                from exo.core.models.keys import redact_secrets
                text = redact_secrets(text, keep_markers=True)  # bash already redacted it across reads
            except Exception:
                pass
            with lock:
                pending.append(text)
                if len(pending) > 1:  # a flush is already queued and takes this chunk too
                    return
            loop.call_soon_threadsafe(flush)

        self.jobs.loop = loop
        if call.name == "bash" and sid:
            self._guard_cwd(sid, call, info.cwd if info and info.cwd else self.cwd)
        ctx = ToolContext(cwd=(info.cwd if info and info.cwd else self.cwd), session=sid, call_id=call.id,
                          emit=emit, meta={"turn_id": lctx.get("turn_id", ""), "author": lctx.get("author", ""),
                                           "jobs": self.jobs, "env_allow": self.env_allow,
                                           "env_deny": self.env_deny})
        return await self.registry.call(call, ctx)

    def _open_jobs(self, session: str | None = None) -> list[dict[str, Any]]:
        """Running background jobs of this agent (jobs belong to the agent, not one thread), for /handoff."""
        now = time.time()
        return [{"id": r["id"], "command": trace.scrub(r["command"], 200), "state": r["state"],
                 "elapsed": jobs_mod.fmt_elapsed(jobs_mod._elapsed(r["started"], now)), "thread": r["session"],
                 "log": r["log"]} for r in self.jobs.rows() if r["state"] == jobs_mod.ACTIVE]

    def _seat_cwd(self, seat: Seat | None) -> str | None:
        """The seat's default cwd (``[session] cwd``). A folder that does not exist is ignored, and said so."""
        if not seat or not seat.cwd:
            return None
        p = Path(seat.cwd).expanduser()
        if p.is_dir():
            return str(p)
        self.cwd_note = f"the seat's default cwd {seat.cwd} is not a folder"
        return None

    def _guard_cwd(self, session: str, call: ToolCall, cwd: str) -> None:
        """D-032: a one-line notice when a shell call works outside any git repo (its cwd argument, or a leading
        ``cd``). Once per folder per session. Only the user sees it; the model's tool result is untouched."""
        args = call.arguments if isinstance(call.arguments, dict) else {}
        where = Path(cwd).expanduser() / str(args.get("cwd") or ".")
        where = workspace.leading_cd(str(args.get("command") or ""), where) or where
        key = str(where)
        seen = self._cwd_seen.setdefault(session, set())
        if key in seen:
            return
        seen.add(key)
        if (note := workspace.cwd_notice(where, "bash")) and where.is_dir():
            self.hub.publish(s.Notice(text=note, level="warn"), session)

    def _approval_waited(self, session: str, ms: int) -> None:
        self.loop.approval_ms[session] = self.loop.approval_ms.get(session, 0) + ms

    # ------------------------------------------------------------ dispatch

    async def handle(self, command: s.Command, hub: Hub) -> dict[str, Any]:
        self.hub = hub
        if self.seat is not None:
            hub.visible = self.event_visible
        await self.start()
        method = getattr(self, "_" + command.type.replace(".", "_"), None)
        if method is None:
            raise CommandFailed("unsupported", f"{command.type} is not supported yet")
        return await method(command.body)

    def event_visible(self, event: s.Event) -> bool:
        """Unattributed persisted events cannot be safely assigned to a loaded profile."""
        if self.seat is None:
            return True
        info = self.store.get_session(event.session) if event.session else None
        return info is not None and info.agent == self.agent

    def _info(self, sid: str) -> s.SessionInfo:
        info = self.store.get_session(sid)
        if info is None or (self.seat is not None and info.agent != self.agent):
            # Ownership comes from the stored agent, never the client-visible seat label.
            # Use the missing-session response so another profile's existence is not disclosed.
            raise CommandFailed("not_found", f"no session {sid}")
        info.cwd = info.cwd or self.cwd  # report the same fallback used by tools for older sessions
        info.status = "live" if (st := self.loop._state.get(sid)) is not None and st.running else "idle"
        return info

    async def _session_create(self, b: s.SessionCreate) -> dict[str, Any]:
        if b.kind not in ("", "thread", "private"):
            raise CommandFailed("bad_request", f"a client can start a thread or a private thread, not {b.kind!r}")
        seat = b.seat  # an unseated core keeps whatever the client asked for (""/None included)
        if self.seat is not None:
            seat = b.seat or self.seat.name
            if seat != self.seat.name:
                raise CommandFailed(
                    "bad_request", f"this core runs the {self.seat.name!r} seat; it cannot start a thread for seat {seat!r}"
                )
        info = await self.loop.create_session(title=b.title, seat=seat, model=b.model or self._main_ref(),
                                              cwd=b.cwd or self.cwd, agent=self.agent, kind=b.kind or "thread")
        why = self.cwd_note if not b.cwd else ""
        if note := workspace.cwd_notice(info.cwd or self.cwd, why):  # D-032: at session start
            self._cwd_seen.setdefault(info.id, set()).add(str(Path(info.cwd or self.cwd).resolve()))
            self.hub.publish(s.Notice(text=note, level="warn"), info.id)
        if self.memory_init_error:  # the provider failed before any session: say so up front
            self.hub.publish(s.Notice(
                text=f"exo-memory did not start: {self.memory_init_error}; "
                     "memory tools will fail until it is fixed", level="warn"), info.id)
        if self.memory_off:
            self.hub.publish(s.Notice(text=self.memory_off, level="info"), info.id)
        return {"session": to_dict(info)}

    async def _session_list(self, b: s.SessionList) -> dict[str, Any]:
        rows = []
        for i in self.store.list_sessions(b.limit, b.seat, agent=self.agent if self.seat is not None else None):
            st = self.loop._state.get(i.id)  # do not create state for an idle session
            i.status = "live" if st is not None and st.running else "idle"
            rows.append(to_dict(i))
        return {"sessions": rows}

    async def _approvals_list(self, b: s.ApprovalsList) -> dict[str, Any]:
        return {"approvals": self.approvals.listing(b.session),  # one await-free copy (D-060)
                "cursor": {"instance": self.hub.instance, "seq": self.hub.seq}}

    async def _session_get(self, b: s.SessionGet) -> dict[str, Any]:
        info, queued = self._info(b.session), [m.id for m in self.loop.state(b.session).steer]  # chips a client still draws
        return {"session": to_dict(info), "messages": to_dict(self.store.messages(b.session)), "steers": queued}

    async def _session_resume(self, b: s.SessionResume) -> dict[str, Any]:
        self._info(b.session)
        await self.loop.resume(b.session)
        return await self._session_get(s.SessionGet(session=b.session))

    async def _session_fork(self, b: s.SessionFork) -> dict[str, Any]:
        self._info(b.session)
        try:
            info = await self.loop.fork(b.session, b.at_message, b.title)
        except KeyError as exc:
            raise CommandFailed("not_found", f"no message {exc}") from None
        return {"session": to_dict(info)}

    async def _session_undo(self, b: s.SessionUndo) -> dict[str, Any]:
        self._info(b.session)
        removed = self.store.undo(b.session, b.turns)
        if removed and (rewound := getattr(self.loop.context, "rewound", None)):
            rewound(b.session)  # the context plugin drops what it kept from the undone turns (D-041)
        info = self.store.update_session(b.session)
        self.hub.publish(s.SessionUpdated(session=info), b.session)
        return {"session": to_dict(info), "removed": removed}

    async def _turn_send(self, b: s.TurnSend) -> dict[str, Any]:
        self._info(b.session)
        try:
            self.store.assert_open(b.session)
        except SessionLocked as exc:  # a finished child session takes no new turns
            raise CommandFailed("session_locked", str(exc)) from None
        if b.session in self.agents.loops:  # typing into a running subagent steers it, never a new turn (D-070, D-052)
            require_principal("steer a subagent")
            if not self.loop.state(b.session).accepting_steers:  # starting or finishing: no round can take it
                raise CommandFailed("conflict", "the subagent is starting or finishing and takes no message now")
            b.mode = "steer"
        meta = {"author": ingress_author()}  # snapshot before dispatch returns and revokes its caller
        if b.mode == "steer" and self.loop.state(b.session).accepting_steers:
            await self.loop.send(b.session, b.text, list(b.parts), mode="steer", meta=meta)  # no await inside
            queue = self.loop.state(b.session).steer
            return {"steered": True, "message_id": (queue[-1].id if queue else "") or ""}
        self._spawn_turn(b, meta)
        return {"accepted": True}

    def start_turn(self, session: str, text: str, meta: dict[str, Any] | None = None) -> None:
        """Start a turn in the background (a peer woke an idle session)."""
        self._spawn_turn(s.TurnSend(session=session, text=text), meta)

    def _spawn_turn(self, b: s.TurnSend, meta: dict[str, Any] | None) -> None:
        task = asyncio.create_task(self.run_turn(b, meta))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run_turn(self, b: s.TurnSend, meta: dict[str, Any] | None = None, loop: Loop | None = None) -> Any:
        """One turn with the lifecycle hooks around it (the learning loop listens on these). ``loop`` is the
        scheduler's per-run loop; it shares this engine's context and memory."""
        meta = dict(meta or {})
        if not meta.get("author"):
            meta["author"] = provenance.SYSTEM  # internal wakeups need an explicit source to speak for anyone
        hooks = self.plugins.hooks
        await hooks.afire("on_turn_start", session_id=b.session, message=b.text)
        try:
            return await (loop or self.loop).send(b.session, b.text, list(b.parts), mode=b.mode,
                                                 meta=meta)
        finally:
            await hooks.afire("on_turn_end", session_id=b.session)

    # ------------------------------------------------------------ peers (D-026)

    async def _peers(self, b: s.Peers) -> dict[str, Any]:
        if b.action == "bus":  # D-040: the bus channel's inbound path, whether or not peers are on
            if current_caller() != CHANNEL:  # it pins a seat's key on first sight: never from a socket client
                raise CommandFailed("forbidden", "only a channel the core runs can hand in a bus message")
            from exo.bus.ingress import bus_ingress
            try:
                return await bus_ingress(self, b)
            except ValueError as exc:
                raise CommandFailed("bad_request", str(exc)) from None
        if self.peers is None or not await self.peers.ready():
            raise CommandFailed("unsupported", "peers are turned off for this seat")
        if b.action == "send" and b.session:
            self._info(b.session)
        via = b.via if is_principal(current_caller()) else "agent"  # "human" is a body claim (D-039)
        try:
            return await self.peers.command(b, via)
        except ValueError as exc:
            raise CommandFailed("bad_request", str(exc)) from None

    async def _feedback_send(self, b: s.FeedbackSend) -> dict[str, Any]:
        require_principal("send feedback")
        from exo import learn  # outside the core; learning off just records nothing
        out = learn.handle_feedback(self, b)
        # The principal's own finding (D-025). Findings are global: none for a private thread or its child (D-028)
        if b.verdict in ("reject", "should_have_been") and not self.store.private(b.session):
            note = (b.text or "").strip()
            self.findings.file(f"principal:{b.verdict}:{b.session}:{b.message_id or 'last'}",
                               f"Answer {'rejected' if b.verdict == 'reject' else 'should have been different'}"
                               + (f": {note[:80]}" if note else ""), source="principal", severity="papercut",
                               detail=note or "no note", session=b.session, model=self.loop.model)
        return out

    async def _ui_respond(self, b: s.UiRespond) -> dict[str, Any]:
        require_principal("respond to a panel")
        from exo import learn
        return learn.handle_ui_respond(self, b)

    async def _turn_interrupt(self, b: s.TurnInterrupt) -> dict[str, Any]:
        self._info(b.session)
        return {"interrupted": int(self.loop.interrupt(b.session) or self.agents.interrupt(b.session)
                                   or bool(self.scheduler and self.scheduler.interrupt(b.session)))}

    async def _tasks_list(self, b: s.TasksList) -> dict[str, Any]:
        return {"tasks": self.agents.list(b.session)}

    async def _approval_respond(self, b: s.ApprovalRespond) -> dict[str, Any]:
        self.approvals.respond(b)
        return {}

    async def _search(self, b: s.Search) -> dict[str, Any]:
        return {"results": self.store.search(b.query, b.limit,
                                             owner=self.agent if self.seat is not None else None)}

    async def _models_list(self, b: s.ModelsList) -> dict[str, Any]:
        from exo.models_cmd import available_models  # network on a cold cache, so off the event loop
        try:
            avail = await asyncio.wait_for(asyncio.to_thread(available_models, self.router, self.home), 25)
        except Exception:
            avail = []
        return {"models": self.router.status(), "available": avail, "spend": self.router.spend_status()}

    async def _models_set(self, b: s.ModelsSet) -> dict[str, Any]:
        require_principal("change models")
        try:
            self.router.prefer(b.role, b.model)  # the old routes stay as fallbacks
        except ValueError as exc:
            raise CommandFailed("bad_model", str(exc)) from None
        if b.role == "main":
            self._automatic_main = False
            self.loop.model = self._main_ref()
        if b.session:
            self.store.update_session(self._info(b.session).id, model=b.model)
        return {"role": b.role, "routes": self.router.roles[b.role]}

    async def _tools_list(self, b: s.ToolsList) -> dict[str, Any]:
        return {"tools": self.registry.schemas()}

    async def _commands_list(self, b: s.CommandsList) -> dict[str, Any]:
        own = [{"name": "context", "description": "what the next model call carries: parts and token estimates", "args": ""},
               {"name": "compact", "description": "summarize now what is eligible in this agent's memory", "args": ""},
               {"name": "trace", "description": "how the harness performed this session", "args": "[last N turns]"},
               {"name": "findings", "description": "harness findings by state", "args": ""}]
        if getattr(self, "work_checkpoint", None) is not None:
            own.append({"name": "checkpoint", "description": "record-derived continuity index", "args": ""})
        if getattr(self, "work_notebook", None) is not None:
            own.append({"name": "work", "description": "saved working notes for this conversation", "args": "[show|history]"})
        return {"commands": own + [{"name": c.name, "description": c.description, "args": c.args_hint}
                                   for c in sorted(self.plugins.commands.values(), key=lambda c: c.name)]}

    # ------------------------------------------------------------ self-observation (D-025)

    def _on_harness_error(self, body: s.HarnessError, session: str | None) -> None:
        """Sink for ``trace.report``: publish the error. Outside a running turn, or in a subagent's (its loop has no
        turn-end check, and a parent's reads only direct children), it also becomes a finding right away."""
        if session and self.store.get_session(session) is None:
            return  # another engine's session
        self.hub.publish(body, session)
        st = self.loop._state.get(session) if session else None
        if self._trace_on and (st is None or not st.running or session in self.agents.loops):
            self.findings.file(f"harness.error:{body.where}:{body.error_type}", f"Harness error in {body.where}",
                               severity="bug", detail=f"{body.error_type}: {body.message}", session=session)

    async def observe_turn(self, session: str, turn_id: str) -> None:
        """After a turn: apply the anomaly rules (no model call), file findings, verify fixed ones."""
        if not self._trace_on:
            return
        if (t := trace.last_turn(self.store, session)) is None:
            return
        flags = trace.detect(t, self.thresholds)
        for f in flags:
            self.findings.file_flag(f)
        self.findings.observe(t, {f.signature for f in flags})

    def trace_text(self, session: str | None, last: int = 10, calls: bool = False) -> str:
        """What ``exo_trace`` and ``/trace`` show (exo.diagnostics.trace_text)."""
        return diagnostics.trace_text(self, session, last, calls)

    async def context_text(self, session: str | None) -> str:
        return await diagnostics.context_text(self, session)

    async def context_report(self, session: str | None) -> dict[str, Any]:
        return await diagnostics.context_report(self, session)

    async def compact_text(self, session: str | None) -> str:
        return await diagnostics.compact_text(self, session)

    async def handoff_facts(self, session: str | None, path_arg: str = "") -> dict[str, Any]:
        return await diagnostics.handoff_facts(self, session, path_arg)

    async def _command_run(self, b: s.CommandRun) -> dict[str, Any]:
        out = await diagnostics.builtin_command(self, b)
        if out is not diagnostics.MISSING:
            return {"output": out}
        cmd = self.plugins.commands.get(b.name)
        if cmd is None:
            raise CommandFailed("not_found", f"no command /{b.name}")
        out = await maybe_await(cmd.handler(b.args))
        return {"output": out if isinstance(out, (str, dict, list)) or out is None else str(out)}

    async def _schedule_edit(self, b: s.ScheduleEdit) -> dict[str, Any]:
        if self.scheduler is None:
            raise CommandFailed("unsupported", "scheduled jobs are not available in this engine")
        out = await self.scheduler.edit(b)
        if "error" in out:
            raise CommandFailed("bad_request", out["error"])
        return out

    async def _skills_list(self, b: s.SkillsList) -> dict[str, Any]:
        return {"skills": self.skills.listing()}

    async def _seat_list(self, b: s.SeatList) -> dict[str, Any]:
        return {"active": self.seat.name if self.seat else None, "seats": list_seats(self.home),
                "mcp": self.mcp.status(),
                "channels": {n: n in self.channels for n in self._channel_cfg}}

    def _tutorial_line(self) -> str:
        """D-030: one stable line while tutorial mode is on, appended after the rest of the stable system text.
        Off, the system text is byte-identical to what it was before the mode existed."""
        try:
            return TUTORIAL_LINE if Config.load(self.home).get("tutorial.enabled") is True else ""
        except ConfigError:
            return ""

    async def _config_get(self, b: s.ConfigGet) -> dict[str, Any]:
        cfg = Config.load(self.home)
        if b.key is None:
            return {"config": redact(cfg.resolved())}
        value = cfg.get(b.key, _MISSING)
        if value is _MISSING:
            raise CommandFailed("not_found", f"{b.key} is not set")
        return {"key": b.key, "value": redact(value, b.key.split("."))}

    async def _config_set(self, b: s.ConfigSet) -> dict[str, Any]:
        require_principal("change settings")
        cfg = Config.load(self.home)
        try:
            if b.value is None:
                check_not_secret(b.key, "")
                cfg.unset(b.key)
            else:
                cfg.set(b.key, b.value)
            cfg.save()
        except ConfigError as exc:
            raise CommandFailed("bad_config", str(exc)) from None
        return {"key": b.key, "saved": True, "applies": "next start"}


if TYPE_CHECKING:
    # Static users depend on the core runtime; application selection stays lazy at runtime.
    CoreEngine = CoreRuntime


def create(seat: str | None = None) -> CoreRuntime:
    """Compatibility path for ``--engine exo.core.engine:create``; the factory lives in ``exo.compose``."""
    from exo.compose import create as compose
    return compose(seat)


def __getattr__(name: str) -> Any:
    # Legacy named imports compose the application; importing CoreRuntime stays independent.
    if name == "CoreEngine":
        from exo.application import CoreEngine
        return CoreEngine
    if name == "ContextAdapter":
        from exo.plugin_context import ContextAdapter
        return ContextAdapter
    raise AttributeError(name)


__all__ = ["ContextAdapter", "CoreEngine", "CoreRuntime", "HubBus", "SqliteLog", "create"]  # noqa: F822 - lazy exports
