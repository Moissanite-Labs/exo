"""The real Engine behind exod: router, tools, loop, sessions and plugins wired together.

``create()`` is the factory ``exo serve`` loads by default (``exo.core.engine:create``).
Everything lives under ``$EXO_HOME``: ``config.json`` (models, plugins) and
``sessions.sqlite`` (messages and the event log, so events replay across restarts).

Adapters kept here so the parts stay independent of each other:
- ``SqliteLog``: the server's EventLog over sessions.sqlite; the Hub numbers events.
- ``HubBus``: the loop's event bus, publishing through the Hub (written before sent).
- ``ContextAdapter``: the loop's context calls on top of TreeContext plus an optional
  Hermes-shaped ContextEngine plugin (select_context, update_from_response, on_turn_complete).
- tool bridge: one ToolContext per call (cwd, cancel, emit) and the plugin hook names
  ``pre_tool_call`` / ``post_tool_call`` / ``transform_tool_result`` on Registry hooks.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
from typing import Any, Iterable

from exo.core.agents import Delegator
from exo.core.context import DefaultCompressor, TreeContext, _maybe_await
from exo.core.loop import Loop
from exo.core.mcp_client import McpManager
from exo.core.models.fake import RuleFake
from exo.core.models.router import Router
from exo.core.plugins import PluginManager
from exo.core.scheduler import Scheduler, handle_schedule_edit
from exo.config import Config, ConfigError, check_not_secret, redact
from exo.core.seats import Seat, SeatError, list_seats
from exo.core.seats import load as load_seat
from exo.core.sessions import SessionLocked, SessionStore
from exo.core.skills import SkillIndex, pack_dir, register_skill_tool
from exo.core.tools.builtin import ui as ui_tool
from exo.core.tools.registry import Block, Registry, ToolContext, default_registry
from exo.core.types import ContentPart, Message, ToolCall, ToolResult, ToolSpec, Usage, to_dict
from exo.protocol import schema as s
from exo.server.engine import CommandFailed, Hub

_MISSING = object()
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
    """TreeContext for the loop, plus a plugin ContextEngine when one fills the slot."""

    RECENT = 40  # messages of transcript handed to hooks that take one

    def __init__(self, tree: TreeContext, plugin: Any = None, memory: Any = None) -> None:
        self.tree = tree
        self.plugin = plugin
        self.memory = memory
        self._started: set[str] = set()
        tree.on_pre_compress = self._pre_compress
        tree.on_compacted = self._compacted

    def transcript(self, session: str, last: int | None = None) -> list[dict[str, Any]]:
        """Recent messages as OpenAI-style dicts, for plugin hooks."""
        msgs = self.tree.store.messages(session)[-(last or self.RECENT):]
        return [{"role": m.role, "content": m.text()} for m in msgs]

    async def _pre_compress(self, session: str, msgs: list[Message]) -> str:
        """Memory providers get the messages about to be compacted: ``on_pre_compress(messages)`` may return
        insights for the summary prompt, and ``checkpoint(session_id=, messages=, reason="pre_compress")`` runs."""
        flat = [{"role": m.role, "content": m.text()} for m in msgs]
        if self.memory is None:
            return ""
        _safe(self.memory, "checkpoint", session_id=session, messages=flat, reason="pre_compress")
        out = _safe(self.memory, "on_pre_compress", flat)
        return out if isinstance(out, str) else ""

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

    def note(self, session: str) -> None:
        self.tree.note(session)
        if self.plugin is not None and session not in self._started:
            self._started.add(session)
            _safe(self.plugin, "on_session_start", session, platform="exo")

    async def build(self, session: str, system: str) -> list[Message]:
        msgs = await self.tree.build(session, system)
        pick = getattr(self.plugin, "select_context", None)
        if pick is None:
            return msgs
        flat = [{"role": m.role, "content": m.text()} for m in msgs]
        try:
            out = await _maybe_await(_call(pick, flat, conversation_messages=flat, budget_tokens=0,
                                           session_id=session))
        except Exception:
            return msgs
        if not out:
            return msgs
        return [Message(role=d["role"], content=[ContentPart.of_text(str(d.get("content") or ""))])
                for d in out if d.get("role") in ("system", "user", "assistant")]

    def on_turn_complete(self, session: str, usage: Usage) -> None:
        if self.plugin is not None:
            _safe(self.plugin, "update_from_response", _usage_dict(usage))
            _safe(self.plugin, "on_turn_complete", self.transcript(session), usage=_usage_dict(usage),
                  session_id=session)


def _call(fn: Any, *args: Any, **kw: Any) -> Any:
    """Call a plugin hook with the keyword arguments it accepts (all of them when it takes **kwargs)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kw)
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kw = {k: v for k, v in kw.items() if k in params}
    return fn(*args, **kw)


def _safe(obj: Any, method: str, *args: Any, **kw: Any) -> Any:
    fn = getattr(obj, method, None)
    try:
        return _call(fn, *args, **kw) if fn else None
    except Exception:  # a plugin failure never breaks a turn
        return None


class CoreEngine:
    """Implements exod's Engine protocol on the wave 1 core."""

    def __init__(self, home: str | os.PathLike, models: dict[str, Any] | None = None, *,
                 registry: Registry | None = None, plugins: PluginManager | None = None,
                 cwd: str | None = None, system_prompt: str = SYSTEM_PROMPT,
                 env: dict[str, str] | None = None, delegation: dict[str, Any] | None = None,
                 seat: Seat | None = None, skill_folders: Iterable[str | os.PathLike] | None = None,
                 mcp: dict[str, dict[str, Any]] | None = None,
                 channels: dict[str, dict[str, Any]] | None = None,
                 tree_cfg: dict[str, Any] | None = None, schedule: bool = False, **loop_kw: Any) -> None:
        self.home = Path(home)
        self.cwd = cwd or os.getcwd()
        self.store = SessionStore(self.home / "sessions.sqlite")
        self.hub = Hub(SqliteLog(self.store))
        self.seat = seat
        models = {**(models or {}), **(seat.models if seat else {})}
        self.router = Router.from_config(models, instances={"fake": RuleFake(models.get("fake"))}, env=env)
        self.registry = registry or default_registry()
        self.plugins = plugins or PluginManager()
        self.plugins.router = self.router  # plugins reach it as ctx.router
        self._approvals: dict[str, tuple[asyncio.Future, str | None]] = {}
        self._open_approvals: dict[str, s.ApprovalRequested] = {}  # what approvals.list shows
        self._always: set[str] = set()
        self._deciders: dict[str, str] = {}
        self._channels: dict[str, Any] = {}
        self._channel_cfg: dict[str, dict[str, Any]] = {**(channels or {}), **(seat.channels if seat else {})}
        self._tasks: set[asyncio.Task] = set()
        self._bridge_plugins()
        if seat and seat.tier and self.router.roles.get(seat.tier):
            self.router.set_role("main", list(self.router.roles[seat.tier]))
        self.skills = SkillIndex.discover([*(seat.skill_folders if seat else []), self.home / "skills"]
                                          if skill_folders is None else skill_folders, self.plugins.skills.values(),
                                          only=seat.skill_load if seat else None)
        register_skill_tool(self.registry, self.skills)
        if self.registry.get("ui") is None:
            ui_tool.register(self.registry, lambda body, sid: self.hub.publish(body, sid))  # D-014 panels
        self.mcp = McpManager({**(mcp or {}), **(seat.mcp if seat else {})}, env)
        self._started: asyncio.Future | None = None
        system_prompt = "\n\n".join(p for p in (system_prompt, seat.soul if seat else "", self.skills.prompt()) if p)
        slots = self.plugins.slots
        tree = TreeContext(self.store, DefaultCompressor(self.router.provider("aux")), **(tree_cfg or {}))
        self.loop = Loop(self.store, self.router.provider("main"), bus=HubBus(self),  # type: ignore[arg-type]
                         context=ContextAdapter(tree, slots.context, slots.memory), memory=slots.memory,
                         tool_specs=self._specs, run_tool=self.run_tool,
                         system_prompt=system_prompt, model=self._main_ref(), **loop_kw)
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
        self.scheduler = Scheduler(self)
        self._schedule = schedule  # serve turns the cron loop on; tests and one-shot engines leave it off

    def _specs(self) -> list[ToolSpec]:
        """Specs the model sees: every toolset, or the seat's (``mcp`` matches every ``mcp:<server>``)."""
        allow = self.seat.toolsets if self.seat else None
        if allow is None:
            return self.registry.specs()
        return [sp for sp in self.registry.specs() if sp.toolset in allow or sp.toolset.split(":")[0] in allow
                or sp.toolset == "skills"]

    async def start(self) -> None:
        """Connect MCP servers once (idempotent; the first command triggers it)."""
        if self._started is None:
            self._started = asyncio.ensure_future(self.mcp.start(self.registry))
            if self._schedule:
                self.scheduler.start()
        await asyncio.shield(self._started)

    async def start_channels(self, link: Any) -> dict[str, str | None]:
        """Run ``factory(link, **config)`` for each channel the seat enables; ``link`` is a protocol Client
        (or any object with ``call`` and ``events``). Returns name -> error (None when running)."""
        out: dict[str, str | None] = {}
        for name, cfg in self._channel_cfg.items():
            entry = self.plugins.channels.get(name)
            if entry is None or name in self._channels:
                out[name] = None if entry else "no plugin registered this channel"
                continue
            try:
                chan = await _maybe_await(entry.factory(link, **(cfg or {})))
                await _maybe_await(chan.start())
                self._channels[name] = chan
                out[name] = None
            except Exception as exc:  # one bad channel never stops the server
                out[name] = f"{type(exc).__name__}: {exc}"
        return out

    async def stop_channels(self) -> None:
        for name, chan in list(self._channels.items()):
            try:
                await _maybe_await(chan.stop())
            except Exception:
                pass
            self._channels.pop(name, None)

    async def close(self) -> None:
        await self.stop_channels()
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
            if slot is pm.slots.memory:
                _safe(slot, "initialize", "", exo_home=str(self.home), hermes_home=str(self.home), platform="exo")
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
        for verdict in await self.plugins.hooks.afire("pre_tool_call", **self._hook_kw(call, ctx)):
            if not isinstance(verdict, dict):
                continue
            if verdict.get("action") == "block":
                return Block(str(verdict.get("message") or "blocked by a plugin"))
            if verdict.get("action") == "approve":
                key = "plugin_rule:" + str(verdict.get("rule_key") or call.name)
                if key in self._always:
                    continue
                who: list[str] = []
                decision = await self.ask(ctx.session, str(verdict.get("message") or call.name), call, spec.risk, who)
                choice = {"allow": "once", "allow_always": "always"}.get(decision, "deny")
                await self.plugins.hooks.afire("post_approval_response", pattern_key=key, choice=choice,
                                               decided_by=who[0] if who else "client", session_id=ctx.session or "")
                if decision == "allow_always":
                    self._always.add(key)
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
            loop.call_soon_threadsafe(self.hub.publish, s.ToolOutput(call_id=call.id, text=text), sid)

        ctx = ToolContext(cwd=(info.cwd if info and info.cwd else self.cwd), session=sid, call_id=call.id,
                          emit=emit, meta={"turn_id": lctx.get("turn_id", "")})
        return await self.registry.call(call, ctx)

    async def ask(self, session: str | None, summary: str, call: ToolCall | None = None,
                  risk: str = "medium", decided_by: list[str] | None = None) -> str:
        """Publish an approval request and wait for ``approval.respond``.

        ``decided_by`` (optional out list) receives the id of the client that answered."""
        rid = s.new_id("a")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._approvals[rid] = (fut, session)
        req = s.ApprovalRequested(request_id=rid, summary=summary, risk=risk, call=call)
        self._open_approvals[rid] = req
        self.hub.publish(req, session)
        try:
            decision = await fut
            if decided_by is not None:
                decided_by.append(self._deciders.pop(rid, "client"))
            return decision
        finally:
            self._approvals.pop(rid, None)
            self._open_approvals.pop(rid, None)
            self._deciders.pop(rid, None)

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
        info = await self.loop.create_session(title=b.title, seat=b.seat, model=b.model or self._main_ref(),
                                              cwd=b.cwd or self.cwd)
        return {"session": to_dict(info)}

    async def _session_list(self, b: s.SessionList) -> dict[str, Any]:
        rows = []
        for i in self.store.list_sessions(b.limit, b.seat):
            st = self.loop._state.get(i.id)  # do not create state for an idle session
            i.status = "live" if st is not None and st.running else "idle"
            rows.append(to_dict(i))
        return {"sessions": rows}

    async def _approvals_list(self, b: s.ApprovalsList) -> dict[str, Any]:
        out = []
        for rid, req in self._open_approvals.items():
            fut, sid = self._approvals.get(rid, (None, None))
            if fut is None or fut.done() or (b.session and sid != b.session):
                continue
            out.append({**to_dict(req), "session": sid})
        return {"approvals": out}

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
        if b.mode == "steer" and self.loop.state(b.session).running:
            await self.loop.send(b.session, b.text, list(b.parts), mode="steer")
            return {"steered": True}
        task = asyncio.create_task(self._turn(b))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"accepted": True}

    async def _turn(self, b: s.TurnSend) -> Any:
        """One turn with the lifecycle hooks around it (the learning loop listens on these)."""
        hooks = self.plugins.hooks
        await hooks.afire("on_turn_start", session_id=b.session, message=b.text)
        try:
            return await self.loop.send(b.session, b.text, list(b.parts), mode=b.mode)
        finally:
            await hooks.afire("on_turn_end", session_id=b.session)

    async def _feedback_send(self, b: s.FeedbackSend) -> dict[str, Any]:
        from exo import learn  # outside the core; learning off just records nothing
        return learn.handle_feedback(self, b)

    async def _ui_respond(self, b: s.UiRespond) -> dict[str, Any]:
        from exo import learn
        return learn.handle_ui_respond(self, b)

    async def _turn_interrupt(self, b: s.TurnInterrupt) -> dict[str, Any]:
        self._info(b.session)
        return {"interrupted": int(self.loop.interrupt(b.session) or self.agents.interrupt(b.session))}

    async def _tasks_list(self, b: s.TasksList) -> dict[str, Any]:
        return {"tasks": self.agents.list(b.session)}

    async def _approval_respond(self, b: s.ApprovalRespond) -> dict[str, Any]:
        fut, sid = self._approvals.get(b.request_id, (None, None))
        if fut is None or fut.done():
            raise CommandFailed("not_found", f"no open approval {b.request_id}")
        self._deciders[b.request_id] = b.client or "client"
        fut.set_result(b.decision)
        self.hub.publish(s.ApprovalResolved(request_id=b.request_id, decision=b.decision,
                                            by=b.client or "client"), sid)
        return {}

    async def _search(self, b: s.Search) -> dict[str, Any]:
        return {"results": self.store.search(b.query, b.limit)}

    async def _models_list(self, b: s.ModelsList) -> dict[str, Any]:
        return {"models": self.router.status()}

    async def _models_set(self, b: s.ModelsSet) -> dict[str, Any]:
        try:
            self.router.set_role(b.role, [b.model])
        except ValueError as exc:
            raise CommandFailed("bad_model", str(exc)) from None
        if b.role == "main":
            self.loop.model = self._main_ref()
        if b.session:
            self.store.update_session(self._info(b.session).id, model=b.model)
        return {"role": b.role, "routes": self.router.roles[b.role]}

    async def _tools_list(self, b: s.ToolsList) -> dict[str, Any]:
        return {"tools": self.registry.schemas()}

    async def _commands_list(self, b: s.CommandsList) -> dict[str, Any]:
        return {"commands": [{"name": c.name, "description": c.description, "args": c.args_hint}
                             for c in sorted(self.plugins.commands.values(), key=lambda c: c.name)]}

    async def _command_run(self, b: s.CommandRun) -> dict[str, Any]:
        cmd = self.plugins.commands.get(b.name)
        if cmd is None:
            raise CommandFailed("not_found", f"no command /{b.name}")
        out = await _maybe_await(cmd.handler(b.args))
        return {"output": out if isinstance(out, (str, dict, list)) or out is None else str(out)}

    async def _schedule_edit(self, b: s.ScheduleEdit) -> dict[str, Any]:
        out = await handle_schedule_edit(self, b)
        if "error" in out:
            raise CommandFailed("bad_request", out["error"])
        return out

    async def _skills_list(self, b: s.SkillsList) -> dict[str, Any]:
        return {"skills": self.skills.listing()}

    async def _seat_list(self, b: s.SeatList) -> dict[str, Any]:
        return {"active": self.seat.name if self.seat else None, "seats": list_seats(self.home),
                "mcp": self.mcp.status(),
                "channels": {n: n in self._channels for n in self._channel_cfg}}

    async def _config_get(self, b: s.ConfigGet) -> dict[str, Any]:
        cfg = Config.load(self.home)
        if b.key is None:
            return {"config": redact(cfg.resolved())}
        value = cfg.get(b.key, _MISSING)
        if value is _MISSING:
            raise CommandFailed("not_found", f"{b.key} is not set")
        return {"key": b.key, "value": redact(value, b.key.split(".")[-1])}

    async def _config_set(self, b: s.ConfigSet) -> dict[str, Any]:
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
    """Factory for ``exo serve --engine exo.core.engine:create``: reads ``$EXO_HOME/config.json``.

    ``seat`` (or ``$EXO_SEAT``, which ``exo serve --seat`` sets) names a file in ``$EXO_HOME/seats``."""
    cfg = Config.load()
    st = None
    if seat or os.environ.get("EXO_SEAT"):
        try:
            st = load_seat(cfg.home, seat or os.environ["EXO_SEAT"])
        except SeatError as exc:
            raise SystemExit(f"exo: {exc}") from None
        if st.principal and not os.environ.get("EXO_PRINCIPAL"):
            os.environ["EXO_PRINCIPAL"] = st.principal
    pm = PluginManager(config=cfg.data.get("plugin_config") or {},
                       memory_choice=cfg.get("memory_provider"), context_choice=cfg.get("context_engine"))
    pm.load_all(enabled=st.plugins if st and st.plugins is not None else cfg.data.get("core_plugins"))
    models = cfg.models()
    folders = [*(st.skill_folders if st else []), cfg.home / "skills"]
    if pack := pack_dir():
        folders.append(pack)
    eng = CoreEngine(cfg.home, models, plugins=pm, cwd=cfg.get("cwd"), seat=st, skill_folders=folders,
                      mcp=cfg.get("mcp"), channels=cfg.get("channels"), system_prompt=cfg.get("system_prompt") or SYSTEM_PROMPT,
                      delegation=cfg.get("delegation"), schedule=cfg.get("schedule", True) is not False,
                      tree_cfg={k: v for k, v in (cfg.get("compression") or {}).items()
                            if k in ("leaf_size", "tail_messages", "view_budget")})
    if cfg.get("learn", {}).get("enabled", True) is not False:
        from exo import learn
        learn.attach(eng)
    return eng


__all__ = ["ContextAdapter", "CoreEngine", "HubBus", "SqliteLog", "create"]
