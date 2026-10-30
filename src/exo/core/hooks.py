"""Named hook points plugins can subscribe to.

Exo accepts only hooks its core currently fires. Several additional names are known from
Hermes plugin declarations, but registering them here would silently do nothing, so they
are rejected until Exo adds a fire site. Callbacks get keyword arguments only: the ones each declares, or all of
them when it takes ``**kwargs``, so a keyword the core adds later never breaks an older callback. A failing
``pre_tool_call`` callback returns a block verdict; other failing callbacks are logged and
skipped (fail open). The engine also refuses to execute tools when no ``pre_tool_call``
callback is registered. Required-executor engines propagate failures of the trusted gate's
decision and approval hooks so the registry can block execution.
"""

from __future__ import annotations

import inspect
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from exo.core import trace

logger = logging.getLogger(__name__)

VALID_HOOKS: frozenset[str] = frozenset({
    "pre_tool_call", "post_tool_call", "transform_tool_result", "post_approval_response",
    "on_turn_start", "on_turn_end", "delegated_approval_audit", "delegated_approval_validate",
})

# These names occur in Hermes plugin contracts or older Exo declarations, but Exo has no
# corresponding fire site. Keep them named so registration fails with a useful explanation.
KNOWN_UNFIRED_HOOKS: frozenset[str] = frozenset({
    "pre_llm_call", "post_llm_call", "transform_llm_output",
    "on_session_start", "on_session_end", "on_session_finalize", "on_session_reset", "on_compaction",
    "pre_approval_request", "pre_command", "subagent_start", "subagent_stop", "on_skill_lifecycle",
    "pre_auxiliary_call", "post_auxiliary_call", "pre_gateway_dispatch",
    "kanban_task_completed", "kanban_task_blocked",
})


async def maybe_await(value: Any) -> Any:
    """A sync or async callback's result: awaited when it is awaitable, else as it is."""
    return await value if inspect.isawaitable(value) else value


def accepted(fn: Callable[..., Any], kw: dict[str, Any]) -> dict[str, Any]:
    """The keyword arguments ``fn`` declares (all of them when it takes **kwargs, or has no signature)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return kw
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kw
    return {k: v for k, v in kw.items() if k in params}


class HookError(ValueError):
    """Unknown hook name or a callback that is not callable."""


@dataclass
class HookEntry:
    name: str
    callback: Callable[..., Any]
    plugin: str = ""


class HookRegistry:
    """Ordered callbacks per hook name. Registration order is call order."""

    def __init__(self, valid: frozenset[str] | set[str] | None = None) -> None:
        # A custom set remains useful for isolated consumers and tests. Production registries
        # default to hooks the core actually fires.
        self.valid = frozenset(VALID_HOOKS if valid is None else valid)
        self._hooks: dict[str, list[HookEntry]] = {}

    def register(self, name: str, callback: Callable[..., Any], plugin: str = "") -> HookEntry:
        if name not in self.valid:
            if name in KNOWN_UNFIRED_HOOKS:
                raise HookError(f"hook {name!r} is known but is not fired by the Exo core")
            raise HookError(f"unknown hook {name!r}")
        if not callable(callback):
            raise HookError(f"hook {name!r} callback is not callable")
        entry = HookEntry(name, callback, plugin)
        self._hooks.setdefault(name, []).append(entry)
        return entry

    def unregister(self, entry: HookEntry) -> None:
        entries = self._hooks.get(entry.name, [])
        if entry in entries:
            entries.remove(entry)

    def has(self, name: str) -> bool:
        return bool(self._hooks.get(name))

    def entries(self, name: str) -> list[HookEntry]:
        return list(self._hooks.get(name, []))

    def names(self) -> list[str]:
        return sorted(n for n, e in self._hooks.items() if e)

    def fire(self, name: str, **kwargs: Any) -> list[Any]:
        """Call every callback in order; return their non-None results.

        Async callbacks are not awaited here (use ``afire``); they are closed and
        skipped, with a block verdict for ``pre_tool_call``.
        """
        out = []
        for e in self.entries(name):
            try:
                r = e.callback(**accepted(e.callback, kwargs))
            except Exception as exc:
                logger.warning("hook %s from %s failed", name, e.plugin or "?", exc_info=True)
                trace.report(f"hook {name} ({e.plugin or '?'})", exc, kwargs.get("session_id") or None)
                if name == "pre_tool_call":
                    out.append({"action": "block", "message": "Tool approval hook failed; repair or reinstall "
                                f"{e.plugin or 'the gate plugin'} before retrying."})
                continue
            if inspect.isawaitable(r):
                if hasattr(r, "close"):
                    r.close()
                logger.warning("hook %s from %s is async; use afire", name, e.plugin or "?")
                if name == "pre_tool_call":
                    out.append({"action": "block", "message": "Tool approval hook requires async execution; "
                                "use afire before executing tools."})
                continue
            if r is not None:
                out.append(r)
        return out

    async def afire(self, name: str, *, _fail_closed_plugins: frozenset[str] = frozenset(),
                    _isolate_arguments: bool = False, _with_plugins: bool = False,
                    **kwargs: Any) -> list[Any]:
        """Like ``fire``; the engine can require a trusted security hook to succeed."""
        out = []
        for e in self.entries(name):
            try:
                # Broker-bound tool hooks inspect independent JSON copies of the request.
                call_kw = {**kwargs, "args": deepcopy(kwargs["args"])} if _isolate_arguments else kwargs
                r = await maybe_await(e.callback(**accepted(e.callback, call_kw)))
            except Exception as exc:
                logger.warning("hook %s from %s failed", name, e.plugin or "?", exc_info=True)
                trace.report(f"hook {name} ({e.plugin or '?'})", exc, kwargs.get("session_id") or None)
                if e.plugin in _fail_closed_plugins:
                    raise HookError("required security hook failed") from None
                if name == "pre_tool_call" and not _fail_closed_plugins:  # required mode: only the gate fails closed
                    blocked = {"action": "block", "message": "Tool approval hook failed; repair or reinstall "
                               f"{e.plugin or 'the gate plugin'} before retrying."}
                    out.append((e.plugin, blocked) if _with_plugins else blocked)
                continue
            if r is not None:
                out.append((e.plugin, r) if _with_plugins else r)
        return out

    def fire_trusted(self, name: str, *, plugin: str, **kwargs: Any) -> Any:
        """A security capability has exactly one named owner and never falls back to other plugins."""
        entries = [entry for entry in self.entries(name) if entry.plugin == plugin]
        if len(entries) != 1:
            raise HookError("trusted security capability unavailable")
        try:
            result = entries[0].callback(**accepted(entries[0].callback, kwargs))
            if inspect.isawaitable(result):
                if hasattr(result, "close"):
                    result.close()
                raise HookError("security capability must complete synchronously")
            return result
        except Exception:
            raise HookError("trusted security capability failed") from None

    def first(self, name: str, **kwargs: Any) -> Any:
        """First non-None result, or None."""
        results = self.fire(name, **kwargs)
        return results[0] if results else None
