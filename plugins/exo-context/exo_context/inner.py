"""The inner compressor: the Exo core's default summarizer behind a Hermes-shaped surface.

The core loop does not call ``compress`` (it builds each prompt from a summary tree, see
``exo.core.context``); this class serves hosts and tools that hold a flat message list, and
the ``/exo`` commands. It keeps the first and last messages, summarizes the middle with the
core's ``DefaultCompressor`` (aux model role) and returns one summary message.
Config comes from the ``compression`` table of ``$EXO_HOME/config.json`` (``exo.config``).
"""
from __future__ import annotations

from typing import Any, Optional

from exo.core.context import DefaultCompressor, render_message
from exo.core.slots import ContextEngine
from exo.core.types import ContentPart, Message
from exo.ext import compat

SUMMARY_PREFIX = '[CONTEXT COMPACTION] Earlier turns were compacted into the notes below.\n\n'
END_MARKER = '--- END OF CONTEXT SUMMARY'
END_LINE = END_MARKER + ' respond to the message below, not the summary above ---'
SUMMARY_FLAG = '_compressed_summary'

# `compression:` keys of config.json that map onto engine attributes
_CFG_KEYS = ('threshold_percent', 'protect_first_n', 'protect_last_n', 'model_thresholds')
_ALIASES = {'threshold': 'threshold_percent'}


def compression_kwargs(home: Any = None) -> dict[str, Any]:
    """The user's ``compression`` settings from exo.config, as engine attributes."""
    try:
        from exo.config import Config
        cfg = Config.load(home).get('compression') or {}
    except Exception:
        return {}
    if not isinstance(cfg, dict):
        return {}
    out = {}
    for k, v in cfg.items():
        k = _ALIASES.get(k, k)
        if k in _CFG_KEYS and v is not None:
            out[k] = dict(v) if isinstance(v, dict) else v
    return out


def _msg(d: dict[str, Any]) -> Message:
    role = d.get('role') if d.get('role') in ('system', 'user', 'assistant', 'tool') else 'user'
    text = d.get('content')
    if isinstance(text, list):
        text = '\n'.join(p.get('text', '') for p in text if isinstance(p, dict))
    return Message(role=role, content=[ContentPart.of_text(str(text or ''))])


class CoreInner(ContextEngine):
    """Threshold bookkeeping plus a one-summary ``compress`` on the core's DefaultCompressor."""

    def __init__(self, *, model: str = '', context_length: int = 0, router: Any = None,
                 budget_tokens: int = 600, **kwargs: Any) -> None:
        self._router = router
        self._budget = budget_tokens
        self.model = model
        for k, v in {**compression_kwargs(), **kwargs}.items():
            if k in _CFG_KEYS:
                setattr(self, k, v)
        self.model_thresholds = getattr(self, 'model_thresholds', {}) or {}
        if context_length:
            self.update_model(model, context_length)

    @property
    def name(self) -> str:
        return 'exo-core-compressor'

    def update_from_response(self, usage: dict[str, Any]) -> None:
        self.last_prompt_tokens = int(usage.get('prompt_tokens', 0) or 0)
        self.last_completion_tokens = int(usage.get('completion_tokens', 0) or 0)
        self.last_total_tokens = int(usage.get('total_tokens', 0) or 0)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        used = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        return bool(self.threshold_tokens) and used >= self.threshold_tokens

    def has_content_to_compress(self, messages: list[dict[str, Any]]) -> bool:
        return len(messages) > self.protect_first_n + self.protect_last_n

    def _summarize(self, text: str, previous: str | None, extra: str) -> str:
        router = self._router or compat.get_router()
        comp = DefaultCompressor(router.provider('aux'), budget_tokens=self._budget)
        return compat.run_sync(comp.summarize(text, previous, extra))

    def compress(self, messages: list[dict[str, Any]], current_tokens: Optional[int] = None,
                 focus_topic: Optional[str] = None, force: bool = False,
                 memory_context: str = '') -> list[dict[str, Any]]:
        head, tail = self.protect_first_n, self.protect_last_n
        if len(messages) <= head + tail:
            return messages
        middle = messages[head:len(messages) - tail]
        previous = None
        body = []
        for m in middle:
            if m.get(SUMMARY_FLAG):
                previous = str(m.get('content') or '').split(END_MARKER)[0].replace(SUMMARY_PREFIX, '').strip()
            else:
                body.append(render_message(_msg(m)))
        extra = (f'Focus topic: {focus_topic}\n' if focus_topic else '') + (memory_context or '')
        text = self._summarize('\n\n'.join(body), previous, extra)
        self.compression_count += 1
        summary = {'role': 'user', 'content': SUMMARY_PREFIX + text + '\n\n' + END_LINE, SUMMARY_FLAG: True}
        return list(messages[:head]) + [summary] + list(messages[len(messages) - tail:])


def default_inner_factory(*, model: str = '', context_length: int = 0, **kwargs: Any) -> CoreInner:
    """Engine's default inner compressor. Host-specific kwargs (base_url, api_key, ...) are ignored."""
    own = {k: v for k, v in kwargs.items() if k in _CFG_KEYS or k in ('router', 'budget_tokens')}
    return CoreInner(model=model, context_length=context_length, **own)
