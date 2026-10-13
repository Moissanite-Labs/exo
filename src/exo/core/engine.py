"""The real Engine behind exod: router, tools, loop, sessions and plugins wired together.

``exo.compose.create()`` is the factory ``exo serve`` loads by default; it builds a ``CoreEngine``.
Everything lives under ``$EXO_HOME``: ``config.json`` (models, plugins) and
``sessions.sqlite`` (messages and the event log, so events replay across restarts).

Adapters kept here so the parts stay independent of each other:
- ``SqliteLog``: the server's EventLog over sessions.sqlite; the Hub numbers events.
- ``HubBus``: the loop's event bus, publishing through the Hub (written before sent).
- ``ContextAdapter``: the loop's context calls on top of AgentContext plus an optional
  Hermes-shaped ContextEngine plugin (select_context, update_from_response, on_turn_complete).
- tool bridge: one ToolContext per call (cwd, cancel, emit) and the plugin hook names
  ``pre_tool_call`` / ``post_tool_call`` / ``transform_tool_result`` on Registry hooks.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import time
from pathlib import Path
from typing import Any, Iterable

from exo import diagnostics, provenance
from exo.core import trace, workspace
from exo.core.agents import Delegator
from exo.core import agentlog
from exo.core.context import AgentContext, DefaultCompressor
from exo.core.hooks import accepted, maybe_await
from exo.core.findings import Findings
from exo.core.loop import Loop
from exo.core.mcp_client import McpManager
from exo.core.models.fake import RuleFake
from exo.core.models.router import Router
from exo.core import jobs as jobs_mod
from exo.core.peer_target import EngineTarget
from exo.core.ingress import STANDING_LINE, PeerConfig
from exo.core.plugins import PluginManager
from exo.config import Config, ConfigError, check_not_secret, redact
from exo.principal import current_caller, ingress_author, is_principal, require_principal
from exo.core.approvals import ApprovalDesk
from exo.core.seats import Seat, list_seats
from exo.core.sessions import SessionLocked, SessionStore
from exo.core.skills import SkillIndex, register_skill_tool
from exo.core.tools.builtin import harness as harness_tool
from exo.core.tools.builtin import ui as ui_tool
from exo.core.tools.registry import Block, Registry, ToolContext, default_registry
from exo.core.tools.executor import (
    ExecutionBroker, ExecutorConfigurationError, load_executor, validate_executor,
)
from exo.core.types import ContentPart, Message, ToolCall, ToolResult, ToolSpec, Usage, to_dict
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

    def __init__(self, engine: CoreEngine) -> None:
        self.engine = engine

    async def emit(self, body: Any, session: str | None = None) -> s.Event:
        return self.engine.hub.publish(body, session)


def _usage_dict(u: Usage | None) -> dict[str, int]:
    u = u or Usage()
    return {"prompt_tokens": u.input_tokens, "completion_tokens": u.output_tokens,
            "total_tokens": u.input_tokens + u.output_tokens}


class ContextAdapter:
    """The agent's AgentContext for the loop, plus a plugin ContextEngine when one fills the slot."""

    RECENT = 40  # messages of transcript handed to hooks that take one

    def __init__(self, tree: AgentContext, plugin: Any = None, memory: Any = None) -> None:
        self.tree = tree
        self.plugin = plugin
        self.memory = memory
        self._started: set[str] = set()
        tree.on_pre_compress = self._pre_compress
        tree.on_capture = self._capture if hasattr(memory, "capture_principal") else None
        tree.on_compacted = self._compacted

    def transcript(self, session: str, last: int | None = None) -> list[dict[str, Any]]:
        """Recent messages as OpenAI-style dicts, for plugin hooks."""
        msgs = self.tree.store.messages(session)[-(last or self.RECENT):]
        return [provenance.flat(m) for m in msgs]  # D-040: each message says who wrote it

    async def _pre_compress(self, session: str, msgs: list[Message]) -> str:
        """Memory providers get the messages about to be compacted: ``on_pre_compress(messages)`` may return
        insights for the summary prompt, and ``checkpoint(session_id=, messages=, reason="pre_compress")`` runs."""
        flat = [provenance.flat(m) for m in msgs]  # D-040: author and principal flag per message
        if self.memory is None:
            return ""
        _safe(self.memory, "checkpoint", session_id=session, messages=flat, reason="pre_compress")
        out = _safe(self.memory, "on_pre_compress", flat)
        return out if isinstance(out, str) else ""

    async def _capture(self, session: str, msgs: list[Message]) -> bool:
        """The principal store's capture hook (D-032): ``capture_principal(messages, session_id=, source=)`` returns
        true when it took them. An error is recorded and counts as not captured."""
        from exo.core.context import is_principal

        flat = [{**provenance.flat(m), **({"principal": is_principal(m)} if m.role == "user" else {})}
                for m in msgs]  # a user message that is not the principal (peer, bus, job notice) says so
        try:
            out = _call(self.memory.capture_principal, flat, session_id=session, source="leaf")
            out = await maybe_await(out)
        except Exception as exc:
            trace.report(f"{type(self.memory).__name__}.capture_principal", exc, session)
            return False
        return out is not None and out is not False

    def _compacted(self, session: str, node: Any) -> None:
        """``on_compacted(session, node)`` on the context engine and the memory provider; node is a SummaryNode
        (id, level, start, end, text, status)."""
        for obj in (self.plugin, self.memory):
            if obj is not None:
                _safe(obj, "on_compacted", session, node, session_id=session)

    @property
    def on_event(self) -> Any:
        return self.tree.on_event

    @on_event.setter
    def on_event(self, fn: Any) -> None:
        self.tree.on_event = fn

    def cache_key(self, session: str) -> str:
        return self.tree.cache_key(session)

    def note(self, session: str) -> None:
        self.tree.note(session)
        if self.plugin is not None and session not in self._started:
            self._started.add(session)
            _safe(self.plugin, "on_session_start", session, platform="exo")

    async def build(self, session: str, system: str, **kw: Any) -> list[Message]:
        msgs = await self.tree.build(session, system, **kw)  # AgentContext places recall itself (D-028)
        pick = getattr(self.plugin, "select_context", None)
        if pick is None:
            return msgs
        flat = [{"role": m.role, "content": m.text()} for m in msgs]
        try:
            out = await maybe_await(_call(pick, flat, conversation_messages=flat, budget_tokens=0,
                                           session_id=session))
        except Exception as exc:
            trace.report("context engine select_context", exc, session)
            return msgs
        if not out:
            return msgs
        return _apply_selection(msgs, flat, out)

    def on_turn_complete(self, session: str, usage: Usage) -> None:
        if self.plugin is not None:
            _safe(self.plugin, "update_from_response", _usage_dict(usage))
            _safe(self.plugin, "on_turn_complete", self.transcript(session), usage=_usage_dict(usage),
                  session_id=session)


def _apply_selection(msgs: list[Message], flat: list[dict[str, Any]], out: list[dict[str, Any]]) -> list[Message]:
    """Turn a ``select_context`` result back into messages. The hook sees text only, so a dict it passes through
    unchanged maps back to the original Message (its tool calls and results survive); only changed or new dicts
    (an injected system block, an edited turn) become plain text messages. A tool message it did not keep goes."""
    out_msgs: list[Message] = []
    at = 0
    for d in out:
        role = d.get("role")
        hit = next((j for j in range(at, len(flat)) if flat[j]["role"] == role and flat[j]["content"] == (d.get("content") or "")), None)
        if hit is not None:
            out_msgs.append(msgs[hit])
            at = hit + 1
        elif role in ("system", "user", "assistant"):
            out_msgs.append(Message(role=role, content=[ContentPart.of_text(str(d.get("content") or ""))]))
    return out_msgs


def _call(fn: Any, *args: Any, **kw: Any) -> Any:
    """Call a plugin hook with the keyword arguments it accepts (all of them when it takes **kwargs)."""
    return fn(*args, **accepted(fn, kw))


def _safe(obj: Any, method: str, *args: Any, **kw: Any) -> Any:
    fn = getattr(obj, method, None)
    try:
        return _call(fn, *args, **kw) if fn else None
    except Exception as exc:  # a plugin failure never breaks a turn, but it is recorded
        trace.report(f"{type(obj).__name__}.{method}", exc, kw.get("session_id"))
        return None


class CoreEngine:
    """Implements exod's Engine protocol on the wave 1 core."""

    def __init__(self, home: str | os.PathLike, models: dict[str, Any] | None = None, *,
                 registry: Registry | None = None, plugins: PluginManager | None = None,
                 executor: ExecutionBroker | None = None, executor_required: bool = False,
                 cwd: str | None = None, system_prompt: str = SYSTEM_PROMPT,
                 env: dict[str, str] | None = None, delegation: dict[str, Any] | None = None,
                 seat: Seat | None = None, skill_folders: Iterable[str | os.PathLike] | None = None,
                 mcp: dict[str, dict[str, Any]] | None = None,
                 channels: dict[str, dict[str, Any]] | None = None,
                 tree_cfg: dict[str, Any] | None = None, schedule: bool = False,
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
        required = required or bool(registry and registry.executor_required)
        if required and executor is None:
            raise ExecutorConfigurationError("Required tool executor is unavailable")
        mcp_cfg = {**(mcp or {}), **(seat.mcp if seat else {})}
        if required and mcp_cfg and getattr(executor, "supports_mcp_startup", False) is not True:
            raise ExecutorConfigurationError("Required tool executor does not support MCP transport startup")
        self.executor = executor
        self.executor_required = required
        self.home = Path(home)
        self.cwd_note = ""  # why the default cwd is not the one asked for (shown at session start)
        self.cwd = self._seat_cwd(seat) or cwd or os.getcwd()  # the seat's own folder beats the shared default
        self._cwd_seen: dict[str, set[str]] = {}  # session -> folders already warned about (D-032)
        self.handoff_sources: dict[str, Any] = {"jobs": lambda session: []}  # D-032: facts for /handoff; key -> fn(session)
        self.store = SessionStore(self.home / "sessions.sqlite")
        self.hub = Hub(SqliteLog(self.store))
        self.seat = seat
        self.env_allow = tuple(seat.env_allow) if seat else ()  # [tools] env_allow, see tool_env
        self.env_deny = tuple(seat.env_deny) if seat else ()  # [tools] env_deny (D-039)
        self.agent = seat.name if seat else "default"  # one agent, one memory: every thread of this seat (D-028)
        self.log_view = agentlog.AgentLogView(self.store, self.agent)
        self._writer: Any = None  # the agent's writer lock, taken by claim_writer() (create() does)
        models = {**(models or {}), **(seat.models if seat else {})}
        self.router = Router.from_config(models, instances={"fake": RuleFake(models.get("fake"))}, env=env)
        self._automatic_main = "main" not in (models.get("roles") or {}) and not (seat and seat.tier)
        self.registry = registry or default_registry(executor=executor, executor_required=required)
        self.registry.executor = executor
        self.registry.executor_required = required or executor is not None
        self.plugins = plugins or PluginManager()
        self.plugins.executor_required = required
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
        tree = AgentContext(self.store, DefaultCompressor(self.router.provider("aux")), **(tree_cfg or {}))
        agentlog.register_zoom(self.registry, self.log_view)
        self.loop = Loop(self.store, self.router.provider("main"), bus=HubBus(self),  # type: ignore[arg-type]
                         context=ContextAdapter(tree, slots.context, slots.memory), memory=slots.memory,
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
        """Connect MCP servers once (idempotent; the first command triggers it)."""
        if self._started is None:
            if self.peers is not None and self.peers.autostart:
                await self.peers.start()
            if self._writer is not None:  # only the one writer may call a running job lost
                await self.jobs.recover()
            self._started = asyncio.ensure_future(self._start_runtime())
            if self._schedule and self.scheduler is not None:
                self.scheduler.start()
        await asyncio.shield(self._started)

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
        """Run ``factory(link, **config)`` for each channel the seat enables; ``link`` is a protocol Client
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

    async def close(self) -> None:
        agentlog.release_writer(self._writer)
        self._writer = None
        trace.remove_sink(self._on_harness_error)
        await self.stop_channels()
        if self.peers is not None:
            await self.peers.stop()
        await self.jobs.close()  # jobs end with exod
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self._started is not None:
            await self.mcp.close()
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
            if slot is pm.slots.memory:  # agent_log: the one log, so the provider keeps no second one (D-028)
                _safe(slot, "initialize", "", exo_home=str(self.home), hermes_home=str(self.home), platform="exo",
                      agent_log=self.log_view)
            for schema in _safe(slot, "get_tool_schemas") or []:
                spec = ToolSpec(name=schema["name"], description=schema.get("description", ""),
                                parameters=schema.get("parameters") or {"type": "object", "properties": {}},
                                toolset="memory" if slot is pm.slots.memory else "context")
                handler = (lambda o, n: lambda args, ctx: o.handle_tool_call(n, args, session_id=ctx.session))(
                    slot, spec.name)
                self.registry.register(spec, handler, replace=True)
        self.registry.add_hook("pre_tool_call", self._pre_hook)
        self.registry.add_hook("post_tool_call", self._post_hook)

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
                "risk": tool.spec.risk if (tool := self.registry.get(call.name)) else "low"}

    async def _pre_hook(self, call: ToolCall, spec: ToolSpec, ctx: ToolContext) -> Any:
        if not self.executor_required and not self.plugins.hooks.has("pre_tool_call"):  # required: create() checks
            return Block("No tool approval gate is available. Reinstall Exo's plugins or enable exo-gate "
                         "in core_plugins (and the seat's plugins list), then restart Exo.")
        required_hooks = frozenset({"exo-gate"}) if self.executor_required else frozenset()
        for verdict in await self.plugins.hooks.afire("pre_tool_call", _fail_closed_plugins=required_hooks,
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
                await self.plugins.hooks.afire("post_approval_response", pattern_key=key, choice=choice,
                                               _fail_closed_plugins=required_hooks,
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

        def emit(text: str) -> None:  # tool threads call this; publish on the event loop
            try:
                from exo.core.models.keys import redact_secrets
                text = redact_secrets(text)
            except Exception:
                pass
            loop.call_soon_threadsafe(self.hub.publish, s.ToolOutput(call_id=call.id, text=text), sid)

        self.jobs.loop = loop
        if call.name == "bash" and sid:
            self._guard_cwd(sid, call, info.cwd if info and info.cwd else self.cwd)
        ctx = ToolContext(cwd=(info.cwd if info and info.cwd else self.cwd), session=sid, call_id=call.id,
                          emit=emit, meta={"turn_id": lctx.get("turn_id", ""), "jobs": self.jobs, "env_allow": self.env_allow,
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
        await self.start()
        method = getattr(self, "_" + command.type.replace(".", "_"), None)
        if method is None:
            raise CommandFailed("unsupported", f"{command.type} is not supported yet")
        return await method(command.body)

    def _info(self, sid: str) -> s.SessionInfo:
        info = self.store.get_session(sid)
        if info is None:
            raise CommandFailed("not_found", f"no session {sid}")
        return info

    async def _session_create(self, b: s.SessionCreate) -> dict[str, Any]:
        if b.kind not in ("", "thread", "private"):
            raise CommandFailed("bad_request", f"a client can start a thread or a private thread, not {b.kind!r}")
        info = await self.loop.create_session(title=b.title, seat=b.seat, model=b.model or self._main_ref(),
                                              cwd=b.cwd or self.cwd, agent=self.agent, kind=b.kind or "thread")
        why = self.cwd_note if not b.cwd else ""
        if note := workspace.cwd_notice(info.cwd or self.cwd, why):  # D-032: at session start
            self._cwd_seen.setdefault(info.id, set()).add(str(Path(info.cwd or self.cwd).resolve()))
            self.hub.publish(s.Notice(text=note, level="warn"), info.id)
        return {"session": to_dict(info)}

    async def _session_list(self, b: s.SessionList) -> dict[str, Any]:
        rows = []
        for i in self.store.list_sessions(b.limit, b.seat):
            st = self.loop._state.get(i.id)  # do not create state for an idle session
            i.status = "live" if st is not None and st.running else "idle"
            rows.append(to_dict(i))
        return {"sessions": rows}

    async def _approvals_list(self, b: s.ApprovalsList) -> dict[str, Any]:
        return {"approvals": self.approvals.listing(b.session)}

    async def _session_get(self, b: s.SessionGet) -> dict[str, Any]:
        info = self._info(b.session)
        return {"session": to_dict(info), "messages": to_dict(self.store.messages(b.session))}

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
        info = self.store.update_session(b.session)
        self.hub.publish(s.SessionUpdated(session=info), b.session)
        return {"session": to_dict(info), "removed": removed}

    async def _turn_send(self, b: s.TurnSend) -> dict[str, Any]:
        self._info(b.session)
        try:
            self.store.assert_open(b.session)
        except SessionLocked as exc:  # a finished child session takes no new turns
            raise CommandFailed("session_locked", str(exc)) from None
        meta = {"author": ingress_author()}  # snapshot before dispatch returns and revokes its caller
        if b.mode == "steer" and self.loop.state(b.session).running:
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
        if b.verdict in ("reject", "should_have_been"):  # the principal's own finding (D-025)
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
        return {"results": self.store.search(b.query, b.limit)}

    async def _models_list(self, b: s.ModelsList) -> dict[str, Any]:
        from exo.models_cmd import available_models  # network on a cold cache, so off the event loop
        try:
            avail = await asyncio.wait_for(asyncio.to_thread(available_models, self.router, self.home), 25)
        except Exception:
            avail = []
        return {"models": self.router.status(), "available": avail}

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
        return {"commands": own + [{"name": c.name, "description": c.description, "args": c.args_hint}
                                   for c in sorted(self.plugins.commands.values(), key=lambda c: c.name)]}

    # ------------------------------------------------------------ self-observation (D-025)

    def _on_harness_error(self, body: s.HarnessError, session: str | None) -> None:
        """Sink for ``trace.report``: publish the error. Outside a running turn (no turn-end check is coming)
        it also becomes a finding right away."""
        if session and self.store.get_session(session) is None:
            return  # another engine's session
        self.hub.publish(body, session)
        st = self.loop._state.get(session) if session else None
        if self._trace_on and (st is None or not st.running):
            self.findings.file(f"harness.error:{body.where}:{body.error_type}", f"Harness error in {body.where}",
                               severity="bug", detail=f"{body.error_type}: {body.message}", session=session)

    async def observe_turn(self, session: str, turn_id: str) -> None:
        """After a turn: apply the anomaly rules (no model call), file findings, verify fixed ones."""
        if not self._trace_on:
            return
        for f in trace.turn_flags(self.store, session, self.thresholds):
            self.findings.file_flag(f)
        self.findings.sweep()

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
        return {"key": b.key, "value": redact(value, b.key.split(".")[-1])}

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


def create(seat: str | None = None) -> CoreEngine:
    """Compatibility path for ``--engine exo.core.engine:create``; the factory lives in ``exo.compose``."""
    from exo.compose import create as compose
    return compose(seat)


__all__ = ["ContextAdapter", "CoreEngine", "HubBus", "SqliteLog", "create"]
