"""Tool registry: specs, handlers, availability checks, risk, toolsets and hooks.

A tool is a ``ToolSpec`` (what the model sees) plus a handler and an optional
``check_fn`` (is the tool usable here, e.g. a binary on PATH). ``risk`` is the
hint exo-gate reads. Toolsets group tools so a seat or subagent can narrow
what a model sees.

Hook points (names match the Hermes hooks Exo plugins already use):

- ``pre_tool_call(call, spec, ctx)``: return ``Block(reason)`` (or a plain
  string reason) to stop the call; the model gets an error result with the
  reason. Return ``None`` to allow. Only ordinary mode permits argument rewrites.
- ``post_tool_call(call, result, ctx)``: return a ``ToolResult`` to replace the
  result, or ``None`` to keep it.

Hooks and handlers may be sync or async. Sync handlers run in a worker thread
so a slow shell command never blocks the event loop. ``Registry.call`` never
raises for tool failures: they come back as ``ToolResult(is_error=True)``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from exo.core.hooks import maybe_await
from exo.core.types import ContentPart, ToolCall, ToolResult, ToolSpec, to_dict
from exo.core.tools.executor import (
    ExecutionBroker, ExecutionRequest, thaw_arguments, validate_executor,
)

PRE_TOOL_CALL = "pre_tool_call"
POST_TOOL_CALL = "post_tool_call"
HOOK_POINTS = (PRE_TOOL_CALL, POST_TOOL_CALL)

MAX_RESULT_CHARS = 100_000
MAX_RESULT_IMAGE_BYTES = 4 * 1024 * 1024  # UTF-8 data/URL field bytes, not remote asset download sizes


def _redact_result(result: ToolResult) -> ToolResult:
    """Cut known key values out of a tool result before it reaches the model, the transcript or a client."""
    try:
        from exo.core.models.keys import known_secret_values, redact_secrets
        vals = known_secret_values()
        if vals:
            for part in result.content:
                if part.type == "text" and part.text:
                    part.text = redact_secrets(part.text, vals)
    except Exception:  # no raw or partially scrubbed result may cross this boundary
        return ToolResult.of_text(result.call_id, "Tool output withheld: secret redaction failed.", is_error=True)
    return result


def _cap_result(result: ToolResult) -> ToolResult:
    """Cap aggregate text and image payloads, dropping whole over-budget images with a visible marker."""
    image_room = MAX_RESULT_IMAGE_BYTES
    kept = []
    dropped = 0
    for part in result.content:
        if part.type == "image":
            data, url = part.data or "", part.url or ""
            size = len(data) + len(url)
            # Base64 is ASCII: reject by length before any encoding/allocation. For a non-ASCII URL,
            # count UTF-8 one character at a time, never making another payload-sized byte string.
            if size <= image_room and not (data.isascii() and url.isascii()):
                size = sum(len(c.encode("utf-8", "replace")) for value in (data, url) for c in value)
            if size > image_room:
                dropped += 1
                continue
            image_room -= size
        kept.append(part)
    result.content = kept
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
    if dropped:
        result.content.append(ContentPart.of_text(f"[omitted {dropped} image part(s): aggregate image payload exceeds "
                                                 f"{MAX_RESULT_IMAGE_BYTES} bytes]"))
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


class Registry:
    def __init__(self, *, executor: ExecutionBroker | None = None,
                 executor_required: bool = False) -> None:
        self._tools: dict[str, Tool] = {}
        self._hooks: dict[str, list[Hook]] = {p: [] for p in HOOK_POINTS}
        self.executor = validate_executor(executor) if executor is not None else None
        self.executor_required = executor_required or executor is not None

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
            return ToolResult.of_text(call.id, f"Tool {call.name!r} is not available here", is_error=True)
        identity = (call.id, call.name)
        request = None
        if self.executor_required:
            try:
                request = ExecutionRequest(call.id, call.name, call.arguments, ctx.cwd, ctx.session)
                call = ToolCall(request.call_id, request.tool_name, thaw_arguments(request.arguments))
                bound_arguments = json.dumps(call.arguments, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                return ToolResult.of_text(call.id, f"Invalid executor request: {exc}", is_error=True)
        for hook in list(self._hooks[PRE_TOOL_CALL]):
            try:
                verdict = await maybe_await(hook(call, tool.spec, ctx))
                if request is not None and ((call.id, call.name, ctx.cwd, ctx.session) !=
                        (request.call_id, request.tool_name, request.cwd, request.session)
                        or json.dumps(call.arguments, sort_keys=True, allow_nan=False) != bound_arguments):
                    verdict = Block("required pre_tool_call hooks cannot change call identity, arguments or context")
            except Exception as exc:  # a broken hook fails closed
                verdict = Block(f"pre_tool_call hook failed: {exc}")
            if isinstance(verdict, str):
                verdict = Block(verdict)
            if isinstance(verdict, Block):
                if request is not None:
                    call = ToolCall(request.call_id, request.tool_name, thaw_arguments(request.arguments))
                    ctx.cwd, ctx.session = request.cwd, request.session
                result = ToolResult.of_text(identity[0], f"Blocked: {verdict.reason}", is_error=True)
                return await self._post(call, result, ctx)
        if (call.id, call.name) != identity:
            return await self._post(call, ToolResult.of_text(identity[0],
                "Blocked: pre_tool_call hooks may rewrite arguments, not call identity", is_error=True), ctx)
        err = validate_args(tool.spec.parameters, call.arguments)
        if err:
            return await self._post(call, ToolResult.of_text(
                call.id, f"Invalid arguments for {call.name}: {err}", is_error=True), ctx)
        if self.executor is not None or self.executor_required:
            result = await self._execute(tool, call, ctx, request)
        else:
            result = await self._run(tool, call, ctx)
        return await self._post(call, result, ctx)

    async def _execute(self, tool: Tool, call: ToolCall, ctx: ToolContext,
                       request: ExecutionRequest | None = None) -> ToolResult:
        """The broker is the only dispatch authority once configured or required."""
        call_id, tool_name = call.id, call.name
        try:
            if self.executor is None:
                raise ToolError("Required tool executor is unavailable")
            request = request or ExecutionRequest(call.id, call.name, call.arguments, ctx.cwd, ctx.session)
            trusted_ctx = ToolContext(cwd=request.cwd, session=request.session, call_id=request.call_id,
                                     cancel=ctx.cancel, emit=ctx.emit, meta=dict(ctx.meta))

            async def run_trusted() -> ToolResult:
                frozen_call = ToolCall(request.call_id, request.tool_name, thaw_arguments(request.arguments))
                return await self._run(tool, frozen_call, trusted_ctx)

            result = await self.executor.execute(request, tool, run_trusted)
            if not isinstance(result, ToolResult):
                raise ToolError("Tool executor must return ToolResult")
            result.call_id = request.call_id
            return _cap_result(result)
        except asyncio.CancelledError:
            ctx.cancel.set()
            raise
        except ToolError as exc:
            return ToolResult.of_text(call_id, str(exc), is_error=True)
        except Exception as exc:
            return ToolResult.of_text(call_id, f"{tool_name} executor failed: {type(exc).__name__}: {exc}",
                                      is_error=True)

    async def _run(self, tool: Tool, call: ToolCall, ctx: ToolContext) -> ToolResult:
        args = dict(call.arguments)
        try:
            if inspect.iscoroutinefunction(tool.handler):
                out = await tool.handler(args, ctx)
            else:
                out = await asyncio.to_thread(tool.handler, args, ctx)
                out = await maybe_await(out)
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
        return _cap_result(ToolResult.of_text(call.id, str(out)))

    async def _post(self, call: ToolCall, result: ToolResult, ctx: ToolContext) -> ToolResult:
        for hook in list(self._hooks[POST_TOOL_CALL]):
            try:
                replaced = await maybe_await(hook(call, result, ctx))
            except Exception:
                continue  # post hooks observe; a failure must not lose the result
            if isinstance(replaced, ToolResult):
                result = replaced
        return _redact_result(result)

    def call_sync(self, call: ToolCall, ctx: ToolContext | None = None) -> ToolResult:
        """Blocking wrapper for scripts and tests (not for use inside a running loop)."""
        return asyncio.run(self.call(call, ctx))


def default_registry(*, executor: ExecutionBroker | None = None,
                     executor_required: bool = False) -> Registry:
    """A registry with the built-in fs, search and shell tools."""
    from exo.core.tools.builtin import register_builtins
    reg = Registry(executor=executor, executor_required=executor_required)
    register_builtins(reg)
    return reg

