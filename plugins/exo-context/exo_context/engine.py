"""ExoContextEngine: the Exo core's compressor, plus principal signals kept verbatim.

The engine wraps; it does not summarise. It does two jobs.

On the Exo core loop (``select_context`` on every turn):
1. classifies the principal's messages with ``exo.capture`` and pins each ruling and correction
   it sees, per session, on disk;
2. when a pinned item is no longer verbatim in the prompt (the core's summary tree folded it away),
   adds a ``<exo-pinned>`` block with it, word for word, to the system message;
3. writes a handoff file whenever the core reports a new summary node (``on_compacted``).
The session id comes with every core hook (``session_id=``), so nothing is tracked by hand.

On a flat message list (``compress``, used by hosts and tools):
1. extracts the signals, 2. calls the inner compressor (the core's default summarizer by default),
3. writes a handoff, 4. puts the pinned block into the summary message, before its end marker.

Everything a host reads or writes on a context engine (token counters, thresholds) is forwarded
to the inner compressor.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import logging
import os
import re
import weakref
from pathlib import Path
from typing import Any, Callable, Optional

from exo import handoff as hf
from exo.store import exo_home

from exo.core.slots import ContextEngine

from . import signals as sg
from .inner import END_MARKER, SUMMARY_FLAG, SUMMARY_PREFIX, default_inner_factory

logger = logging.getLogger(__name__)

ENGINE_NAME = 'exo-context'

# Attributes a host reads and writes directly on the engine: always the inner's values.
_PROXY = ('last_prompt_tokens', 'last_completion_tokens', 'last_total_tokens', 'threshold_tokens',
          'context_length', 'compression_count', 'threshold_percent', 'protect_first_n', 'protect_last_n',
          'emit_automatic_compaction_status')
# Optional ContextEngine methods: forwarded so the inner compressor's overrides keep working.
_DELEGATE = ('should_compress_info', 'prune_tool_results_only', 'should_compress_preflight',
             'should_defer_preflight_to_real_usage', 'get_automatic_compaction_status_message',
             'has_content_to_compress', 'on_session_end', 'get_tool_schemas', 'handle_tool_call',
             'get_status')
_LIVE: Optional[weakref.ReferenceType] = None


def live_engine() -> Optional['ExoContextEngine']:
    """The engine that saw the most recent turn (commands act on it)."""
    return _LIVE() if _LIVE is not None else None


def _set_live(engine: 'ExoContextEngine') -> None:
    global _LIVE
    _LIVE = weakref.ref(engine)


def _accepted_kwargs(fn: Callable, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


def _proxy(name: str) -> property:
    default = getattr(ContextEngine, name, 0)

    def get(self):
        inner = self.__dict__.get('_x_inner')
        if inner is not None:
            return getattr(inner, name)
        return self.__dict__['_x_local'].get(name, default)

    def set_(self, value):
        inner = self.__dict__.get('_x_inner')
        if inner is not None:
            setattr(inner, name, value)
        else:
            self.__dict__['_x_local'][name] = value
    return property(get, set_)


def _delegator(name: str) -> Callable:
    base = getattr(ContextEngine, name)

    def method(self, *args, **kwargs):
        inner = self.__dict__.get('_x_inner')
        if inner is not None and hasattr(inner, name):
            return getattr(inner, name)(*args, **kwargs)
        return base(self, *args, **kwargs)
    method.__name__ = name
    method.__doc__ = f'Forwarded to the inner compressor ({name}).'
    return method


class ExoContextEngine(ContextEngine):
    """Context engine that keeps the principal's words verbatim across compaction."""

    def __init__(self, *, inner: Any = None, inner_factory: Callable[..., Any] | None = None,
                 store: Any = None, principal: str | None = None, handoff_dir: str | os.PathLike | None = None,
                 pin_char_budget: int = 6000, write_handoffs: bool = True,
                 compressor_kwargs: dict[str, Any] | None = None):
        d = self.__dict__
        d['_x_local'] = {}
        d['_x_pending'] = {}
        d['_x_inner'] = inner
        d['_x_config'] = dict(inner_factory=inner_factory, store=store, principal=principal,
                              handoff_dir=handoff_dir, pin_char_budget=pin_char_budget,
                              write_handoffs=write_handoffs, compressor_kwargs=compressor_kwargs)
        d['_x_factory'] = inner_factory or default_inner_factory
        d['_x_compressor_kwargs'] = dict(compressor_kwargs or {})
        d['_x_store'] = store
        d['_x_principal'] = principal or os.environ.get('EXO_PRINCIPAL')
        d['_x_handoff_dir'] = handoff_dir
        d['_x_pin_budget'] = pin_char_budget
        d['_x_write_handoffs'] = write_handoffs
        d['_x_session_id'] = ''
        d['_x_topic'] = ''
        d['_x_messages'] = []
        d['_x_resume_block'] = ''
        d['_x_last_handoff'] = None
        d['_x_last_pinned'] = ''
        d['_x_pins'] = {}
        d['_x_nodes'] = {}

    # --- attribute routing ---------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name.startswith('__') or name.startswith('_x_'):
            raise AttributeError(name)
        d = self.__dict__
        if name in d.get('_x_pending', {}):
            return d['_x_pending'][name]
        inner = d.get('_x_inner')
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith('_x_') or isinstance(getattr(type(self), name, None), property):
            object.__setattr__(self, name, value)
            return
        inner = self.__dict__.get('_x_inner')
        if inner is not None:
            setattr(inner, name, value)
        else:
            self.__dict__['_x_pending'][name] = value

    # --- identity and configuration -----------------------------------------------

    @property
    def name(self) -> str:
        return ENGINE_NAME

    @property
    def inner(self) -> Any:
        return self.__dict__.get('_x_inner')

    @property
    def store(self) -> Any:
        st = self._x_store
        if st is None and self._x_principal:
            try:
                from exo.store import PrincipalStore
                st = PrincipalStore.for_principal(self._x_principal)
            except Exception:
                st = None
        return st

    @property
    def handoff_dir(self):
        if self._x_handoff_dir:
            return self._x_handoff_dir
        return hf.default_handoff_dir(self._x_store, self._x_principal)

    @property
    def topic(self) -> str:
        return self._x_topic or (f'session-{self._x_session_id[:12]}' if self._x_session_id else 'session')

    @topic.setter
    def topic(self, value: str) -> None:
        self._x_topic = value or ''

    @property
    def messages(self) -> list:
        return list(self._x_messages)

    @property
    def last_handoff(self):
        return self._x_last_handoff

    @property
    def last_pinned_block(self) -> str:
        return self._x_last_pinned

    @property
    def resume_block(self) -> str:
        return self._x_resume_block

    @resume_block.setter
    def resume_block(self, value: str) -> None:
        self._x_resume_block = value or ''

    def clone_for_agent(self) -> 'ExoContextEngine':
        clone = type(self)(**self._x_config)
        if self.inner is not None:  # rare: the shared instance was already bound to a model
            import copy
            clone.__dict__['_x_inner'] = copy.deepcopy(self.inner)
        clone.__dict__['_x_pending'].update(self._x_pending)
        return clone

    def _build_inner(self, **model_kwargs: Any) -> Any:
        inner = self._x_factory(**model_kwargs, **self._x_compressor_kwargs)
        for k, v in self._x_pending.items():
            setattr(inner, k, v)
        for k, v in self._x_local.items():
            if k in ('threshold_percent', 'protect_first_n', 'protect_last_n', 'emit_automatic_compaction_status'):
                setattr(inner, k, v)
        self._x_pending.clear()
        self.__dict__['_x_inner'] = inner
        return inner

    # --- ContextEngine contract ---------------------------------------------------

    def update_model(self, model: str, context_length: int, base_url: str = '', api_key: str = '',
                     provider: str = '', api_mode: str = '') -> None:
        kw = dict(model=model, context_length=context_length, base_url=base_url, api_key=api_key,
                  provider=provider, api_mode=api_mode)
        if self.inner is None:
            self._build_inner(**kw)
        self.inner.update_model(**kw)

    def update_from_response(self, usage: dict[str, Any]) -> None:
        if self.inner is not None:
            self.inner.update_from_response(usage)
            return
        self._x_local['last_prompt_tokens'] = usage.get('prompt_tokens', 0)
        self._x_local['last_completion_tokens'] = usage.get('completion_tokens', 0)
        self._x_local['last_total_tokens'] = usage.get('total_tokens', 0)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        return bool(self.inner.should_compress(prompt_tokens)) if self.inner is not None else False

    def on_session_start(self, session_id: str, **kwargs) -> None:
        self._x_session_id = session_id or ''
        _set_live(self)
        if self.inner is not None and hasattr(self.inner, 'on_session_start'):
            self.inner.on_session_start(session_id, **kwargs)

    def on_session_reset(self) -> None:
        self._x_messages = []
        self._x_resume_block = ''
        if self.inner is not None and hasattr(self.inner, 'on_session_reset'):
            self.inner.on_session_reset()
        else:
            ContextEngine.on_session_reset(self)

    def on_turn_complete(self, messages: list[dict[str, Any]], usage: dict[str, Any] | None = None,
                         **kwargs: Any) -> None:
        self._bind(kwargs.get('session_id'))
        if messages:  # the core passes the real recent transcript
            self._x_messages = list(messages)
        _set_live(self)
        if self.inner is not None and hasattr(self.inner, 'on_turn_complete'):
            self.inner.on_turn_complete(messages, usage, **kwargs)

    def _bind(self, session_id: Any) -> None:
        if session_id:
            self._x_session_id = str(session_id)
            _set_live(self)

    def select_context(self, request_messages: list[dict[str, Any]], **kwargs: Any):
        self._bind(kwargs.get('session_id'))
        selected = None
        if self.inner is not None and hasattr(self.inner, 'select_context'):
            selected = self.inner.select_context(request_messages, **kwargs)
        msgs = list(selected if selected is not None else request_messages)
        pins = self._observe(msgs)
        hidden = self._hidden(msgs, pins)
        full = self._block_for(pins)
        self._x_messages = list(msgs) + ([{'role': 'system', 'content': full}] if full else [])
        parts = [self._block_for(hidden, self._x_last_handoff) if hidden else '', self._x_resume_block]
        extra = '\n\n'.join(p for p in parts if p)
        if not extra:
            return selected
        self._x_last_pinned = extra
        if msgs and isinstance(msgs[0], dict) and msgs[0].get('role') == 'system':
            first = dict(msgs[0])
            first['content'] = hf.content_text(first.get('content')) + '\n\n' + extra
            first.pop('api_content', None)
            msgs[0] = first
        else:
            msgs.insert(0, {'role': 'system', 'content': extra})
        return msgs

    # --- pins: rulings and corrections seen this session, kept verbatim ---------------

    def _pin_file(self) -> Path:
        sid = re.sub(r'[^A-Za-z0-9_.-]+', '_', self._x_session_id or 'default')
        return exo_home() / 'context-pins' / f'{sid}.json'

    def _load_pins(self) -> dict[str, list[str]]:
        sid = self._x_session_id
        pins = self._x_pins.get(sid)
        if pins is None:
            try:
                data = json.loads(self._pin_file().read_text('utf-8'))
                pins = {k: [str(t) for t in data.get(k, [])] for k in ('ruling', 'correction')}
            except (OSError, ValueError, AttributeError):
                pins = {'ruling': [], 'correction': []}
            self._x_pins[sid] = pins
        return pins

    def _observe(self, msgs: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Add the rulings and corrections visible in `msgs` to this session's pins."""
        pins = self._load_pins()
        try:
            rulings, corrections = sg.classified([m for m in msgs if isinstance(m, dict)])
        except Exception as e:  # never block a turn on extraction
            logger.warning('exo-context: signal extraction failed: %s', e)
            return pins
        changed = False
        for kind, found in (('ruling', rulings), ('correction', corrections)):
            have = {hf.Signal(kind, t).key() for t in pins[kind]}
            for s in found:
                if s.key() not in have:
                    pins[kind].append(s.text)
                    have.add(s.key())
                    changed = True
        if changed:
            try:
                path = self._pin_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(pins), encoding='utf-8')
            except OSError as e:
                logger.warning('exo-context: could not save pins: %s', e)
        return pins

    @staticmethod
    def _hidden(msgs: list[dict[str, Any]], pins: dict[str, list[str]]) -> dict[str, list[str]]:
        """The pins that no user message in `msgs` carries verbatim any more."""
        seen = [hf.message_text(m) for m in msgs if isinstance(m, dict) and m.get('role') == 'user']
        return {k: [t for t in items if not any(t.strip() in u for u in seen)] for k, items in pins.items()}

    def _block_for(self, pins: dict[str, list[str]], path: Any = None) -> str:
        sig = hf.Signals(rulings=[hf.Signal('ruling', t) for t in pins.get('ruling', [])],
                         corrections=[hf.Signal('correction', t) for t in pins.get('correction', [])])
        return hf.render_pinned_block(sig, path, self._x_pin_budget)

    def on_compacted(self, session: str, node: Any, **kwargs: Any) -> None:
        """The core wrote a summary node: write a handoff that carries it and the pinned signals."""
        self._bind(session)
        if not self._x_write_handoffs:
            return
        sid = self._x_session_id
        nodes = self._x_nodes.setdefault(sid, {})
        nodes[getattr(node, 'id', len(nodes))] = (getattr(node, 'start', 0), str(getattr(node, 'text', '') or ''))
        summary = '\n\n'.join(t for _, t in sorted(nodes.values(), key=lambda x: x[0]) if t)[-6000:]
        try:
            signals = sg.extract(self._x_messages)
            pins = self._load_pins()
            signals = dataclasses.replace(
                signals,
                rulings=sg.merge(signals.rulings, [hf.Signal('ruling', t) for t in pins['ruling']]),
                corrections=sg.merge(signals.corrections, [hf.Signal('correction', t) for t in pins['correction']]))
            path = hf.write_handoff(self.topic, self._x_messages, self._x_store, self.handoff_dir,
                                    session=sid or None, principal=self._x_principal, summary=summary,
                                    signals=signals)
            self._x_last_handoff = path
        except Exception as e:
            logger.warning('exo-context: handoff write failed: %s', e)

    # --- compaction --------------------------------------------------------------

    def compress(self, messages: list[dict[str, Any]], current_tokens: Optional[int] = None,
                 focus_topic: Optional[str] = None, force: bool = False, memory_context: str = '',
                 **kwargs: Any) -> list[dict[str, Any]]:
        if self.inner is None:  # the core never calls update_model: build the default inner now
            self._build_inner(model='', context_length=0)
        try:
            signals = sg.extract(messages)
        except Exception as e:  # never block compaction on extraction
            logger.warning('exo-context: signal extraction failed: %s', e)
            signals = None
        call = dict(current_tokens=current_tokens, focus_topic=focus_topic, force=force,
                    memory_context=memory_context, **kwargs)
        result = self.inner.compress(messages, **_accepted_kwargs(self.inner.compress, call))
        if signals is None or result is messages or not isinstance(result, list):
            return result
        idx = self._summary_index(result)
        if idx is None:
            return result  # inner made no summary (no-op or structural skip)

        path = None
        if self._x_write_handoffs:
            try:
                summary = self._summary_body(hf.message_text(result[idx]))
                path = hf.write_handoff(focus_topic or self.topic, messages, self._x_store,
                                        self.handoff_dir, session=self._x_session_id or None,
                                        principal=self._x_principal, summary=summary, signals=signals)
                self._x_last_handoff = path
            except Exception as e:
                logger.warning('exo-context: handoff write failed: %s', e)
        block = hf.render_pinned_block(signals, path, self._x_pin_budget)
        self._x_last_pinned = block
        if block:
            result[idx] = self._with_block(result[idx], block)
        return result

    def _summary_index(self, msgs: list[dict[str, Any]]) -> Optional[int]:
        is_summary = getattr(self.inner, '_is_context_summary_message', None)
        for i, m in enumerate(msgs):
            if not isinstance(m, dict):
                continue
            if m.get(SUMMARY_FLAG):
                return i
            if callable(is_summary):
                try:
                    if is_summary(m):
                        return i
                except Exception:
                    pass
            if END_MARKER in hf.message_text(m):
                return i
        return None

    @staticmethod
    def _summary_body(text: str) -> str:
        cut = text.find(END_MARKER)
        body = text[:cut] if cut >= 0 else text
        if body.lstrip().startswith(SUMMARY_PREFIX):
            body = body.lstrip()[len(SUMMARY_PREFIX):]
        body = hf.strip_pinned(body)
        return body.strip()[:6000]

    @staticmethod
    def _insert(text: str, block: str) -> str:
        cut = text.find(END_MARKER)
        if cut < 0:
            return text.rstrip() + '\n\n' + block
        return text[:cut].rstrip() + '\n\n' + block + '\n\n' + text[cut:]

    def _with_block(self, msg: dict[str, Any], block: str) -> dict[str, Any]:
        new = dict(msg)
        content = new.get('content')
        if isinstance(content, list):
            parts = [dict(p) if isinstance(p, dict) else p for p in content]
            target = next((i for i, p in enumerate(parts) if isinstance(p, dict)
                           and isinstance(p.get('text'), str) and END_MARKER in p['text']), None)
            if target is None:
                target = max((i for i, p in enumerate(parts) if isinstance(p, dict)
                              and isinstance(p.get('text'), str)), default=None)
            if target is None:
                parts.append({'type': 'text', 'text': block})
            else:
                parts[target]['text'] = self._insert(parts[target]['text'], block)
            new['content'] = parts
        else:
            new['content'] = self._insert(hf.content_text(content), block)
        new.pop('api_content', None)  # a stale sidecar would resend the pre-pin bytes
        return new


for _n in _PROXY:
    setattr(ExoContextEngine, _n, _proxy(_n))
for _n in _DELEGATE:
    setattr(ExoContextEngine, _n, _delegator(_n))
del _n
