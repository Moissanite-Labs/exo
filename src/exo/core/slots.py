# Lifted from hermes-agent: agent/memory_provider.py, agent/context_engine.py
# Upstream commit: dce1e9b37581dd62e480a9064dc04a709c2940d3
# Licence: MIT, see LICENSES/hermes-agent-MIT.txt. Tracked in PROVENANCE.toml.
"""Memory and context slots: the two exclusive plugin seats of the Exo core.

``MemoryProvider`` and ``ContextEngine`` keep Hermes's method names and defaults so the
exo-memory and exo-context plugins move over with import changes only. Exo edits: storage
kwarg is ``exo_home`` (``hermes_home`` is still passed for old plugins), no redaction or
compression-marker imports, ``update_model`` resolves per-model thresholds inline, and
``Slots`` enforces one active provider per slot.
"""

from __future__ import annotations

import contextvars
import copy
import json
import logging
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- helpers


def ctx_bound(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Bind ``fn`` to the caller's contextvars so it runs the same in another thread."""
    ctx = contextvars.copy_context()
    return lambda *args, **kwargs: ctx.run(fn, *args, **kwargs)


def spawn_context_thread(target: Callable[..., Any], *, name: str, daemon: bool = True,
                         args: tuple = (), kwargs: Optional[dict[str, Any]] = None) -> threading.Thread:
    """Unstarted thread running ``target`` under the spawner's contextvars. Memory-provider
    background jobs (prefetch, sync, writer loops) should go through this."""
    return threading.Thread(target=ctx_bound(target), args=args, kwargs=kwargs, name=name, daemon=daemon)


PRE_COMPRESS_CHECKPOINT_API_VERSION = 2
INDICATOR_GLYPH = "\U0001f9e0"


@dataclass(frozen=True)
class RecallStatus:
    """What the last prefetch injected (``count == 0``: content without a discrete count)."""

    provider_label: str
    count: int
    glyph: str = INDICATOR_GLYPH


TRIVIAL_PROMPT_RE = re.compile(
    r'^(yes|no|ok|okay|sure|thanks|thank you|y|n|yep|nope|yeah|nah|'
    r'hi|hey|hello|yo|sup|'
    r'continue|go ahead|do it|proceed|got it|cool|nice|great|done|next|lgtm|k)'
    r'[\s!?.:;,"' + "'" + r'~\u2018\u2019\u201c\u201d\u2014\u2013\u2026()\[\]{}<>*&^%$#@!+=`\u00a0]*$',
    re.IGNORECASE,
)


def is_trivial_prompt(text: Optional[str]) -> bool:
    """True for empty input, slash commands and bare greetings or acknowledgements."""
    stripped = (text or "").strip()
    if not stripped or stripped.startswith("/"):
        return True
    return bool(TRIVIAL_PROMPT_RE.match(stripped))


# ---------------------------------------------------------------- memory slot


class MemoryProvider(ABC):
    """Base class for memory providers. One is active per core (the memory slot).

    Lifecycle: initialize, then per turn system_prompt_block / prefetch / sync_turn,
    tool dispatch, shutdown; plus optional ``on_*`` hooks.
    """

    pre_compress_checkpoint_api_version = 1

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (for example 'exo-memory')."""

    @abstractmethod
    def is_available(self) -> bool:
        """Configured and ready? Check config and deps only, no network."""

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None:
        """Called once at startup. kwargs include ``exo_home`` (also as ``hermes_home``) and
        ``platform``; may include ``agent_context`` (primary, subagent, cron, flush; skip writes
        when not primary), ``agent_identity``, ``agent_workspace``, ``parent_session_id``, ``user_id``."""

    def unavailable_reason(self) -> str:
        return ""

    def system_prompt_block(self) -> str:
        """Static system-prompt text; "" to skip. Recall goes through prefetch()."""
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall context for the coming turn ("" if none). Must be fast: return cached results."""
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue a background recall after each turn; prefetch() consumes it next turn."""

    def recall_status(self) -> Optional[RecallStatus]:
        """What the most recent prefetch injected (None = no indicator)."""
        return None

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: Optional[list[dict[str, Any]]] = None,
                  turn_author: Optional[dict[str, Any]] = None) -> None:
        """Persist a completed turn without blocking."""

    @abstractmethod
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Function schemas ({"name", "description", "parameters"}); [] if none."""

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        """Handle one of this provider's tools; returns a JSON string."""
        raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

    def shutdown(self) -> None:
        """Flush queues, close connections."""

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Per-turn tick. kwargs may include remaining_tokens, model, platform, author_id."""

    def identity_signature(self) -> dict[str, Any]:
        return {}

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """End-of-session extraction; real session boundaries only."""

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "",
                          reset: bool = False, rewound: bool = False, **kwargs) -> None:
        """Session id changed mid-process (resume, fork, reset, compaction)."""

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        """Insights from messages about to be compacted, fed into the summary prompt."""
        return ""

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        """Parent-side observation of a finished delegation."""

    def get_config_schema(self) -> list[dict[str, Any]]:
        return []

    def save_config(self, values: dict[str, Any], exo_home: str) -> None:
        """Write non-secret setup values to the provider's own config."""

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: Optional[dict[str, Any]] = None) -> None:
        """Mirror a built-in memory write (action add, replace or remove)."""

    def backup_paths(self) -> list[str]:
        return []


# ---------------------------------------------------------------- context slot


class ContextEngine(ABC):
    """Base class for context engines. One is active per core (the context slot).

    Lifecycle: on_session_start, update_from_response after each model call,
    should_compress / compress per turn, on_session_end at real boundaries only.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (for example 'compressor')."""

    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0
    threshold_percent: float = 0.75
    protect_first_n: int = 3
    protect_last_n: int = 6
    emit_automatic_compaction_status: bool = True

    @abstractmethod
    def update_from_response(self, usage: dict[str, Any]) -> None:
        """Track token usage after each model call (prompt_tokens, completion_tokens, total_tokens)."""

    @abstractmethod
    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        """True if compaction should fire this turn."""

    def should_compress_info(self, prompt_tokens: int | None = None) -> tuple[bool, str | None]:
        return self.should_compress(prompt_tokens), None

    @abstractmethod
    def compress(self, messages: list[dict[str, Any]], current_tokens: Optional[int] = None,
                 focus_topic: Optional[str] = None, force: bool = False,
                 memory_context: str = "") -> list[dict[str, Any]]:
        """Compact ``messages`` into a valid list that fits the budget."""

    def prune_tool_results_only(self, messages: list[dict[str, Any]],
                                current_tokens: int | None = None) -> tuple[list[dict[str, Any]], int]:
        return messages, 0

    def select_context(self, request_messages: list[dict[str, Any]], *,
                       conversation_messages: list[dict[str, Any]] | None = None,
                       incoming_message: dict[str, Any] | None = None,
                       budget_tokens: int = 0) -> list[dict[str, Any]] | None:
        """Optionally replace the context for this request only. None leaves it unchanged."""
        return None

    def on_turn_complete(self, messages: list[dict[str, Any]], usage: dict[str, Any] | None = None,
                         **kwargs: Any) -> None:
        return

    def should_compress_preflight(self, messages: list[dict[str, Any]]) -> bool:
        return False

    def should_defer_preflight_to_real_usage(self, rough_tokens: int) -> bool:
        return False

    def get_automatic_compaction_status_message(self, *, phase: str, default_message: str,
                                                **context: Any) -> str | None:
        return default_message if self.emit_automatic_compaction_status else None

    def has_content_to_compress(self, messages: list[dict[str, Any]]) -> bool:
        return True

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Session begins. kwargs may include exo_home, platform, model."""

    def on_session_end(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Real session boundary, never per turn."""

    def on_session_reset(self) -> None:
        # 0, not -1: 0 means "no real usage yet, use the rough estimate".
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []

    def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs) -> str:
        return json.dumps({"error": f"Unknown context engine tool: {name}"})

    def get_status(self) -> dict[str, Any]:
        last_prompt = max(self.last_prompt_tokens, 0)
        return {
            "last_prompt_tokens": last_prompt,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": min(100, last_prompt / self.context_length * 100) if self.context_length else 0,
            "compression_count": self.compression_count,
        }

    def clone_for_agent(self) -> ContextEngine:
        """Per-agent copy so a child's update_model() cannot change the parent's budget."""
        return copy.deepcopy(self)

    def update_model(self, model: str, context_length: int, base_url: str = "", api_key: str = "",
                     provider: str = "", api_mode: str = "") -> None:
        """Model switch: recompute threshold_tokens. ``model_thresholds`` (substring to
        percent, longest match wins) overrides the configured percent, snapshotted once."""
        self.context_length = context_length
        if not hasattr(self, "_config_threshold_percent"):
            self._config_threshold_percent = self.threshold_percent
        pct = self._config_threshold_percent
        overrides = getattr(self, "model_thresholds", {}) or {}
        hits = [k for k in overrides if k and k.lower() in (model or "").lower()]
        if hits:
            pct = float(overrides[max(hits, key=len)])
        self.threshold_percent = pct
        self.threshold_tokens = int(context_length * pct)


# ---------------------------------------------------------------- slot holder

_REQUIRED = {
    "memory": ("name", "is_available", "initialize", "get_tool_schemas"),
    "context": ("name", "update_from_response", "should_compress", "compress"),
}


class SlotError(ValueError):
    """A slot rule was broken (second provider, wrong shape)."""


@dataclass
class Slots:
    """Holds the one memory provider and the one context engine.

    With no choice set, the first registration fills the slot and a second raises
    SlotError. With ``memory_choice`` / ``context_choice`` set (config), only the named
    provider fills the slot; others are kept as candidates and do not raise.
    Duck-typed on purpose so plugins still subclassing Hermes's bases load during wave 2.
    """

    memory_choice: str | None = None
    context_choice: str | None = None
    memory: Any = None
    context: Any = None
    owners: dict[str, str] = field(default_factory=dict)
    candidates: dict[str, dict[str, Any]] = field(default_factory=lambda: {"memory": {}, "context": {}})

    def fill(self, slot: str, obj: Any, owner: str = "") -> bool:
        """Put ``obj`` in ``slot``. Returns True if it became active."""
        if slot not in _REQUIRED:
            raise SlotError(f"unknown slot {slot!r}")
        missing = [a for a in _REQUIRED[slot] if not hasattr(obj, a)]
        if missing:
            raise SlotError(f"{slot} provider from {owner or '?'} lacks {missing}")
        name = str(obj.name)
        choice = getattr(self, f"{slot}_choice")
        self.candidates[slot][name] = obj
        if choice and name != choice:
            logger.info("slot %s: %s registered but %s is selected", slot, name, choice)
            return False
        current = getattr(self, slot)
        if current is not None:
            raise SlotError(f"{slot} slot already held by {current.name!r} (from "
                            f"{self.owners.get(slot) or '?'}); {name!r} from {owner or '?'} refused")
        setattr(self, slot, obj)
        self.owners[slot] = owner
        return True

    def release(self, slot: str, obj: Any) -> None:
        """Undo a fill (used when a plugin fails part way through register)."""
        self.candidates[slot].pop(str(getattr(obj, "name", "")), None)
        if getattr(self, slot) is obj:
            setattr(self, slot, None)
            self.owners.pop(slot, None)
