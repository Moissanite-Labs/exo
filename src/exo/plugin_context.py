"""Optional context/memory plugin integration over the core-owned summary tree.

The tree owns persistence, capture acknowledgement and provenance rules. This bridge
only translates plugin callbacks and text selections; it never owns their state.
"""
from __future__ import annotations

import inspect
from typing import Any

from exo import provenance
from exo.core import trace
from exo.core.context import ContextTree, apply_selection
from exo.core.hooks import maybe_await
from exo.core.plugins import call_plugin, safe_plugin_call
from exo.core.types import Message, Usage


def _usage_dict(u: Usage | None) -> dict[str, int]:
    u = u or Usage()
    return {"prompt_tokens": u.input_tokens, "completion_tokens": u.output_tokens,
            "total_tokens": u.input_tokens + u.output_tokens}


class ContextAdapter:
    """TreeContext for the loop, plus a plugin ContextEngine when one fills the slot."""

    RECENT = 40  # messages of transcript handed to hooks that take one

    def __init__(self, tree: ContextTree, plugin: Any = None, memory: Any = None) -> None:
        self.tree = tree
        self.plugin = plugin
        self.memory = memory
        self._started: set[str] = set()
        tree.on_pre_compress = self._pre_compress
        tree.on_capture = self._capture if hasattr(memory, "capture_principal") else None
        tree.on_compacted = self._compacted

    def transcript(self, session: str, last: int | None = None) -> list[dict[str, Any]]:
        """Recent messages as OpenAI-style dicts, for plugin hooks."""
        msgs = self.tree.transcript(session, last or self.RECENT)
        return [provenance.flat(m) for m in msgs]  # D-040: each message says who wrote it

    async def _pre_compress(self, session: str, msgs: list[Message]) -> str:
        """Memory providers get the messages about to be compacted: ``on_pre_compress(messages)`` may return
        insights for the summary prompt, and ``checkpoint(session_id=, messages=, reason="pre_compress")`` runs."""
        flat = [provenance.flat(m) for m in msgs]  # D-040: author and principal flag per message
        if self.memory is None:
            return ""
        safe_plugin_call(self.memory, "checkpoint", session_id=session, messages=flat, reason="pre_compress")
        out = safe_plugin_call(self.memory, "on_pre_compress", flat)
        return out if isinstance(out, str) else ""

    async def _capture(self, session: str, msgs: list[Message]) -> bool:
        """The principal store's capture hook (D-032): ``capture_principal(messages, session_id=, source=)`` returns
        true when it took them. An error is recorded and counts as not captured."""
        from exo.core.context import is_principal

        flat = [{**provenance.flat(m), **({"principal": is_principal(m)} if m.role == "user" else {})}
                for m in msgs]  # a user message that is not the principal (peer, bus, job notice) says so
        try:
            out = call_plugin(self.memory.capture_principal, flat, session_id=session, source="leaf")
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
                safe_plugin_call(obj, "on_compacted", session, node, session_id=session)

    @property
    def on_event(self) -> Any:
        return self.tree.on_event

    @on_event.setter
    def on_event(self, fn: Any) -> None:
        self.tree.on_event = fn

    def cache_key(self, session: str) -> str:
        key = getattr(self.tree, "cache_key", None)
        return key(session) if callable(key) else session

    def note(self, session: str) -> None:
        self.tree.note(session)
        if self.plugin is not None and session not in self._started:
            self._started.add(session)
            safe_plugin_call(self.plugin, "on_session_start", session, platform="exo")

    async def build(self, session: str, system: str, **kw: Any) -> list[Message]:
        if "recall" in inspect.signature(self.tree.build).parameters:
            msgs = await self.tree.build(session, system, **kw)
        else:  # a per-session tree: recall goes in the system message, as before D-028
            recall = kw.get("recall") or ""
            msgs = await self.tree.build(session, (system + "\n\n" if system else "") + "# Recalled memory\n" + recall
                                         if recall else system)
        pick = getattr(self.plugin, "select_context", None)
        if pick is None:
            return msgs
        flat = [{"role": m.role, "content": m.text()} for m in msgs]
        try:
            out = await maybe_await(call_plugin(pick, flat, conversation_messages=flat, budget_tokens=0,
                                           session_id=session))
        except Exception as exc:
            trace.report("context engine select_context", exc, session)
            return msgs
        if not out:
            return msgs
        return apply_selection(msgs, flat, out)

    def on_turn_complete(self, session: str, usage: Usage) -> None:
        if self.plugin is not None:
            safe_plugin_call(self.plugin, "update_from_response", _usage_dict(usage))
            safe_plugin_call(self.plugin, "on_turn_complete", self.transcript(session), usage=_usage_dict(usage),
                  session_id=session)
