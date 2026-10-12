"""Tool registry: specs, handlers, availability checks, risk, toolsets and hooks.

A tool is a ``ToolSpec`` (what the model sees) plus a handler and an optional
``check_fn`` (is the tool usable here, e.g. a binary on PATH). ``risk`` is the
hint exo-gate reads. Toolsets group tools so a seat or subagent can narrow
what a model sees.

Hook points (names match the Hermes hooks Exo plugins already use):

- ``pre_tool_call(call, spec, ctx)``: return ``Block(reason)`` (or a plain
  string reason) to stop the call; the model gets an error result with the
  reason. Return ``None`` to allow. A hook may rewrite ``call.arguments``.
- ``post_tool_call(call, result, ctx)``: return a ``ToolResult`` to replace the
  result, or ``None`` to keep it.

Hooks and handlers may be sync or async. Sync handlers run in a worker thread
so a slow shell command never blocks the event loop. ``Registry.call`` never
raises for tool failures: they come back as ``ToolResult(is_error=True)``.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from exo.core.types import ToolCall, ToolResult, ToolSpec, to_dict

PRE_TOOL_CALL = "pre_tool_call"
POST_TOOL_CALL = "post_tool_call"
HOOK_POINTS = (PRE_TOOL_CALL, POST_TOOL_CALL)

MAX_RESULT_CHARS = 100_000


def _redact_result(result: ToolResult) -> ToolResult:
    """Cut known key values out of a tool result before it reaches the model, the transcript or a client."""
    try:
        from exo.core.models.keys import known_secret_values, redact_secrets
        vals = known_secret_values()
        if vals:
            for part in result.content:
                if part.type == "text" and part.text:
                    part.text = redact_secrets(part.text, vals)
    except Exception:  # redaction must never lose a result
        pass
    return result


def _cap_result(result: ToolResult) -> ToolResult:
    """The same character cap a plain string result gets, over all the text parts of a ``ToolResult``."""
    room = MAX_RESULT_CHARS
    for part in result.content:
        if part.type != "text" or not part.text:
            continue
        if len(part.text) > room:
            cut = len(part.text) - room
            part.text = part.text[:room] + f"\n[truncated {cut} chars]"
            room = 0
        else:
            room -= len(part.text)
    return result


@dataclass
class ToolContext:
    """Per-call environment handed to handlers and hooks."""

    cwd: str = "."
    session: str | None = None
    call_id: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)
    emit: Callable[[str], None] | None = None  # partial output (tool.output events)
    meta: dict[str, Any] = field(default_factory=dict)

    def resolve(self, path: str) -> Path:
        """Resolve ``path`` against ``cwd`` (``~`` expanded)."""
        p = Path(path).expanduser()
        return p if p.is_absolute() else Path(self.cwd).expanduser() / p


@dataclass
class Block:
    """Returned by a ``pre_tool_call`` hook to stop a call."""

    reason: str


Handler = Callable[..., Any]  # (args: dict, ctx: ToolContext) -> str | ToolResult | awaitable
Hook = Callable[..., Any]


@dataclass
class Tool:
    spec: ToolSpec
    handler: Handler
    check_fn: Callable[[], bool] | None = None

    def available(self) -> bool:
        if self.check_fn is None:
            return True
        try:
            return bool(self.check_fn())
        except Exception:
            return False


class ToolError(Exception):
    """Raise from a handler for an expected failure (message goes to the model as is)."""


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,),
    "array": (list,), "object": (dict,), "null": (type(None),),
}


def validate_args(schema: dict[str, Any], args: Any) -> str | None:
    """Shallow JSON Schema check (required keys, top-level types, enums). None if fine."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    for key in schema.get("required", []):
        if key not in args:
            return f"missing required argument {key!r}"
    props = schema.get("properties", {})
    for key, value in args.items():
        prop = props.get(key)
        if prop is None:
            if schema.get("additionalProperties") is False:
                return f"unknown argument {key!r}"
            continue
        types = prop.get("type")
        if isinstance(types, str):
            types = [types]
        if types:
            ok = any(isinstance(value, _JSON_TYPES.get(t, (object,)))
                     and not (t in ("integer", "number") and isinstance(value, bool))
                     for t in types)
            if not ok:
                return f"argument {key!r} must be {' or '.join(types)}"
        if "enum" in prop and value not in prop["enum"]:
            return f"argument {key!r} must be one of {prop['enum']}"
    return None


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._hooks: dict[str, list[Hook]] = {p: [] for p in HOOK_POINTS}

    # ------------------------------------------------------------ registration

    def register(self, spec: ToolSpec, handler: Handler,
                 check_fn: Callable[[], bool] | None = None, replace: bool = False) -> Tool:
        if spec.name in self._tools and not replace:
            raise ValueError(f"tool {spec.name!r} already registered")
        tool = Tool(spec, handler, check_fn)
        self._tools[spec.name] = tool
        return tool

    def tool(self, name: str, description: str, parameters: dict[str, Any] | None = None, *,
             risk: str = "low", toolset: str = "core",
             check_fn: Callable[[], bool] | None = None) -> Callable[[Handler], Handler]:
        """Decorator form of ``register``."""
        def deco(fn: Handler) -> Handler:
            spec = ToolSpec(name=name, description=description, risk=risk, toolset=toolset,  # type: ignore[arg-type]
                            parameters=parameters or {"type": "object", "properties": {}})
            self.register(spec, fn, check_fn)
            return fn
        return deco

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def add_hook(self, point: str, fn: Hook) -> None:
        if point not in self._hooks:
            raise ValueError(f"unknown hook point {point!r}; expected one of {HOOK_POINTS}")
        self._hooks[point].append(fn)

    def remove_hook(self, point: str, fn: Hook) -> None:
        if fn in self._hooks.get(point, []):
            self._hooks[point].remove(fn)

    # ------------------------------------------------------------ lookup

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def toolsets(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for name in self.names():
            out.setdefault(self._tools[name].spec.toolset, []).append(name)
        return out

    def specs(self, toolsets: list[str] | None = None, names: list[str] | None = None,
              available_only: bool = True) -> list[ToolSpec]:
        """Specs to hand a provider, sorted by name (stable for prompt caching)."""
        out = []
        for name in self.names():
            tool = self._tools[name]
            if toolsets is not None and tool.spec.toolset not in toolsets:
                continue
            if names is not None and name not in names:
                continue
            if available_only and not tool.available():
                continue
            out.append(tool.spec)
        return out

    def schemas(self, **kw: Any) -> list[dict[str, Any]]:
        """``specs()`` as plain JSON-ready dicts (``tools.list`` results)."""
        return [to_dict(s) for s in self.specs(**kw)]

    # ------------------------------------------------------------ execution

    async def call(self, call: ToolCall, ctx: ToolContext | None = None) -> ToolResult:
        ctx = ctx or ToolContext()
        ctx.call_id = ctx.call_id or call.id
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult.of_text(call.id, f"Unknown tool {call.name!r}. "
                                      f"Available: {', '.join(self.names())}", is_error=True)
        if not tool.available():
            return ToolResult.of_text(call.id, f"Tool {call.name!r} is not available here",
                                      is_error=True)
        for hook in list(self._hooks[PRE_TOOL_CALL]):
            try:
                verdict = await _maybe_await(hook(call, tool.spec, ctx))
            except Exception as exc:  # a broken hook fails closed
                verdict = Block(f"pre_tool_call hook failed: {exc}")
            if isinstance(verdict, str):
                verdict = Block(verdict)
            if isinstance(verdict, Block):
                result = ToolResult.of_text(call.id, f"Blocked: {verdict.reason}", is_error=True)
                return await self._post(call, result, ctx)
        err = validate_args(tool.spec.parameters, call.arguments)
        if err:
            return await self._post(call, ToolResult.of_text(
                call.id, f"Invalid arguments for {call.name}: {err}", is_error=True), ctx)
        result = await self._run(tool, call, ctx)
        return await self._post(call, result, ctx)

    async def _run(self, tool: Tool, call: ToolCall, ctx: ToolContext) -> ToolResult:
        args = dict(call.arguments)
        try:
            if inspect.iscoroutinefunction(tool.handler):
                out = await tool.handler(args, ctx)
            else:
                out = await asyncio.to_thread(tool.handler, args, ctx)
                out = await _maybe_await(out)
        except asyncio.CancelledError:
            ctx.cancel.set()
            raise
        except ToolError as exc:
            return ToolResult.of_text(call.id, str(exc), is_error=True)
        except Exception as exc:
            return ToolResult.of_text(call.id, f"{call.name} failed: {type(exc).__name__}: {exc}",
                                      is_error=True)
        if isinstance(out, ToolResult):
            out.call_id = call.id
            return _cap_result(out)
        text = out if isinstance(out, str) else str(out)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f"\n[truncated {len(text) - MAX_RESULT_CHARS} chars]"
        return ToolResult.of_text(call.id, text)

    async def _post(self, call: ToolCall, result: ToolResult, ctx: ToolContext) -> ToolResult:
        for hook in list(self._hooks[POST_TOOL_CALL]):
            try:
                replaced = await _maybe_await(hook(call, result, ctx))
            except Exception:
                continue  # post hooks observe; a failure must not lose the result
            if isinstance(replaced, ToolResult):
                result = replaced
        return _redact_result(result)

    def call_sync(self, call: ToolCall, ctx: ToolContext | None = None) -> ToolResult:
        """Blocking wrapper for scripts and tests (not for use inside a running loop)."""
        return asyncio.run(self.call(call, ctx))


def default_registry() -> Registry:
    """A registry with the built-in fs, search and shell tools."""
    from exo.core.tools.builtin import register_builtins
    reg = Registry()
    register_builtins(reg)
    return reg

