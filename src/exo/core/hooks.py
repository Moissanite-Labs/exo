"""Named hook points plugins can subscribe to.

Names follow Hermes's ``VALID_HOOKS`` where Exo has the same seam, so exo-gate's
``pre_tool_call`` and ``post_approval_response`` keep working. Callbacks get keyword
arguments only. A failing ``pre_tool_call`` callback returns a block verdict;
other failing callbacks are logged and skipped (fail open).
Required-executor engines propagate failures of the trusted gate's decision and
approval hooks so the registry can block execution.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from exo.core import trace

logger = logging.getLogger(__name__)

VALID_HOOKS: frozenset[str] = frozenset({
    # tools (fired by exo.core.tools)
    "pre_tool_call", "post_tool_call", "transform_tool_result",
    # model calls (fired by exo.core.loop)
    "pre_llm_call", "post_llm_call", "transform_llm_output",
    # sessions and turns
    "on_session_start", "on_session_end", "on_session_reset", "on_turn_start", "on_turn_end",
    "on_compaction",
    # approvals and questions (exo-gate, exo.ask)
    "pre_approval_request", "post_approval_response",
    # commands, delegation, skills
    "pre_command", "subagent_start", "subagent_stop", "on_skill_lifecycle",
})


class HookError(ValueError):
    """Unknown hook name or a callback that is not callable."""


@dataclass
class HookEntry:
    name: str
    callback: Callable[..., Any]
    plugin: str = ""


class HookRegistry:
    """Ordered callbacks per hook name. Registration order is call order."""

    def __init__(self, valid: frozenset[str] | set[str] = VALID_HOOKS) -> None:
        self.valid = frozenset(valid)
        self._hooks: dict[str, list[HookEntry]] = {}

    def register(self, name: str, callback: Callable[..., Any], plugin: str = "") -> HookEntry:
        if name not in self.valid:
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
                r = e.callback(**kwargs)
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
                    **kwargs: Any) -> list[Any]:
        """Like ``fire``; the engine can require a trusted security hook to succeed."""
        out = []
        for e in self.entries(name):
            try:
                r = e.callback(**kwargs)
                if inspect.isawaitable(r):
                    r = await r
            except Exception as exc:
                logger.warning("hook %s from %s failed", name, e.plugin or "?", exc_info=True)
                trace.report(f"hook {name} ({e.plugin or '?'})", exc, kwargs.get("session_id") or None)
                if e.plugin in _fail_closed_plugins:
                    raise HookError("required security hook failed") from None
                if name == "pre_tool_call":
                    out.append({"action": "block", "message": "Tool approval hook failed; repair or reinstall "
                                f"{e.plugin or 'the gate plugin'} before retrying."})
                continue
            if r is not None:
                out.append(r)
        return out

    def first(self, name: str, **kwargs: Any) -> Any:
        """First non-None result, or None."""
        results = self.fire(name, **kwargs)
        return results[0] if results else None
