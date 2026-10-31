"""ExoContextEngine: the Exo core's compressor, plus principal signals kept verbatim.

The engine wraps; it does not summarise. It does two jobs.

On the Exo core loop (``select_context`` on every turn):
1. classifies the principal's messages with ``exo.capture`` and pins each ruling, correction
   and open decision
   it sees, per session, on disk (a message whose author is not the principal is skipped, D-040);
2. when a pinned item is no longer verbatim in the prompt (the core's summary tree folded it away),
   adds a ``<exo-pinned>`` block with it, word for word, to the system message;
3. writes a handoff file whenever the core reports a new summary node (``on_compacted``);
4. after an undo (``on_session_switch(rewound=True)``) drops the pins of the undone turns and the summary nodes
   the core deleted with them, and rewrites each handoff that may quote them, another thread's included (D-041).
The session id comes with every core hook (``session_id=``), so nothing is tracked by hand.

On a host that reports an undo only to its memory provider (Hermes), exo-memory passes the notice on through
``exo.host_undo``, and ``on_host_undo`` drops the pins that only the undone turn held (#389). The pending undo
waits for the session's next turn in ``exo.host_undo``, not in one engine (#580), so the engine that runs that
turn, whichever agent Hermes built it on, drops the earlier turns' pins from the turn's transcript. A pin whose
message a compaction folded stays while the host's transcript still holds that summary row, before the undo as
after it: a compress that folded the window's first row notes the fold with the pins, and an undo after it counts
that row as reaching back (#581). An undo no one reports (Hermes's messaging gateway cuts its transcript and builds
a new agent) is settled by the first turn of the session that a new engine runs for the host: the pins file keeps
each pin's lead, the rows before its message, and the leads of the transcript noted last. A transcript that is the
noted one cut at or before a pin's lead, and holds the pin no more, drops it (#537).

On a flat message list (``compress``, used by hosts and tools):
1. extracts the signals, 2. calls the inner compressor (the core's default summarizer by default),
3. writes a handoff, 4. puts the pinned block into the summary message, before its end marker.

Everything a host reads or writes on a context engine (token counters, thresholds) is forwarded
to the inner compressor.
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import logging
import os
import re
import weakref
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional

from exo import handoff as hf
from exo import host_undo
from exo import provenance
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


def _holder(messages: list[dict[str, Any]]) -> Callable[[str, str], bool]:
    """The test both undo paths apply (D-041): does a principal message in `messages` hold the pin (kind, text)?
    It does when the pin is classified there, whitespace folded, or quoted inside a principal user message. A
    pinned block is the engine's own copy of its pins, never their source, so a block holds nothing."""
    rows = [m for m in messages if isinstance(m, dict)]
    held = [hf.Signal('', hf.strip_pinned(hf.message_text(m))).key() for m in provenance.principal_view(rows)
            if m.get('role') == 'user']
    try:  # a pin is the classified sentence, whitespace folded: classify what is left the same way
        found = {f'{s.kind}:{s.key()}'
                 for group in (*sg.classified(rows), sg.pinned_open_decisions(rows)) for s in group}
    except Exception as e:
        logger.warning('exo-context: signal extraction failed: %s', e)
        found = set()

    def holds(kind: str, text: str) -> bool:
        key = hf.Signal(kind, text).key()
        return f'{kind}:{key}' in found or any(key in u for u in held)
    return holds


def _digest(text: str) -> str:
    """A short name for a pin or a pinned block, whitespace folded, so the pins file holds no more text (#389)."""
    return hashlib.sha256(' '.join(text.split()).encode('utf-8')).hexdigest()[:16]


def _pin_digest(kind: str, text: str) -> str:
    return _digest(f'{kind}:{hf.Signal(kind, text).key()}')


def _blocks(messages: list[dict[str, Any]]) -> set[str]:
    """The digest of each pinned block in `messages`. A compaction writes its block into its summary row, so finding
    the block again in a host's transcript shows that the row is still there (#389)."""
    out = set()
    for m in messages:
        text = hf.message_text(m) if isinstance(m, dict) else ''
        start = text.find(hf.PIN_OPEN)
        while start >= 0 and (end := text.find(hf.PIN_CLOSE, start)) >= 0:
            out.add(_digest(text[start:end + len(hf.PIN_CLOSE)]))
            start = text.find(hf.PIN_OPEN, end)
    return out


def _gone(messages: list[dict[str, Any]], result: list[dict[str, Any]], idx: int) -> list[dict[str, Any]]:
    """The rows of `messages` a compaction did not keep, in order. The rows the result keeps before its summary row
    `idx` are matched from the start and those after it from the end, each text as many times as the result holds
    it. So a ruling said twice word for word, once folded and once kept, still counts as folded, and replies of the
    same text leave the message Hermes merged its summary into last among the rows gone (#389)."""
    rows = [m for m in messages if isinstance(m, dict)]
    kept = [False] * len(rows)
    for side, order in ((result[:idx], range(len(rows))), (result[idx + 1:], range(len(rows) - 1, -1, -1))):
        want = Counter(hf.message_text(m) for m in side if isinstance(m, dict))
        for i in order:
            text = hf.message_text(rows[i])
            if want[text] > 0 and not kept[i]:
                kept[i], want[text] = True, want[text] - 1
    return [m for m, k in zip(rows, kept) if not k]


def _part_of(first: str, msg: dict[str, Any]) -> bool:
    """Is `msg` the window's first row, or what Hermes keeps of it? When /undo N cuts at the row Hermes merged its
    summary into, Hermes keeps that row's summary part up to its end marker (#389)."""
    text = hf.message_text(msg)
    return text == first or (END_MARKER in text and text.strip() in first)


def _summary_row(msg: dict[str, Any]) -> bool:
    """Is `msg` a compaction's summary row (a host's or this engine's), in any role but system?"""
    return msg.get('role') != 'system' and (bool(msg.get(SUMMARY_FLAG)) or hf.is_summary_text(hf.message_text(msg)))


def _leads(rows: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """The leads in `rows` (#537). The first list has one for each place in `rows`, before each row and after the
    last: a digest of the user and summary rows before it, in order from the start, or '' when there are none.
    Where a pin's message was, this is the pin's lead. The second list has the lead after each user or summary row,
    in order. An undo cuts the tail, so the rows it leaves end at one of these leads."""
    places, chain, lead = [], [], ''
    for m in rows:
        places.append(lead)
        if m.get('role') == 'user' or _summary_row(m):
            lead = _digest(lead + ' ' + _digest(hf.message_text(m)))
            chain.append(lead)
    return places + [lead], chain


def _upto(chain: list[str], reach: dict[str, str]) -> list[str]:
    """`chain` up to its last lead that `reach` names. Only an undo that ends there or earlier can drop a pin."""
    where = {lead: i for i, lead in enumerate(chain)}
    return chain[:max((where[v] + 1 for v in reach.values() if v in where), default=0)]


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
        d['_x_window_sid'] = ''  # the session _x_messages came from (on_compacted may bind another)
        d['_x_resume_block'] = ''
        d['_x_last_handoff'] = None
        d['_x_last_pinned'] = ''
        d['_x_pins'] = {}
        d['_x_nodes'] = {}
        d['_x_handoffs'] = {}  # session -> (path, session of the window it was written from, topic): automatic only
        d['_x_folded'] = {}  # session -> {digest of a pin a compaction folded: digests of the summary blocks it rides}
        d['_x_dropped'] = {}  # session -> {digest of a pin an undo dropped}: no rebind carries it back (#592)
        d['_x_firstrow'] = {}  # session -> {digest of the window's first row: digests of the summary blocks that
        # stand for it, noted when a compress before any undo folded the row}; an undo counts such a row as
        # reaching back (#581)
        # A host's undo until that session's next turn: (session, window without the undone turn, the blocks of the
        # summary rows written since that folded the window's first row or its summary part, seeded from what a
        # compress before the undo noted for that row)
        d['_x_undo'] = None
        d['_x_turn'] = 0  # host_undo's number for the last turn of the window's session that this engine saw
        d['_x_reach'] = {}  # session -> {digest of a pin: its lead in the host's transcript (_leads)} (#537)
        # session -> the leads after each user or summary row of the host's transcript noted last, up to the last
        # pin's lead (#537)
        d['_x_leads'] = {}
        d['_x_ran'] = set()  # the sessions this engine has run a host's turn of: the first one reconciles (#537)
        host_undo.listen(self)  # a host's undo reaches the engine only through exo-memory (#389)

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
        return self._topic_for(self._x_session_id)

    def _topic_for(self, sid: str) -> str:
        return self._x_topic or (f'session-{sid[:12]}' if sid else 'session')

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
        old = kwargs.get('old_session_id')
        if kwargs.get('boundary_reason') == 'compression' and old and str(old) != str(session_id):
            self._carry_pins(str(old), str(session_id))  # in_place false: the pins follow the child (#391)
            host_undo.move(str(old), str(session_id))  # the pending undo follows the window into the child (#580)
            if self._x_window_sid == str(old):  # the child goes on from that window's last turns: an undo reads it
                self._saw(str(session_id))
                if self._x_undo and self._x_undo[0] == str(old):  # the engine's own record re-sids as well
                    self._x_undo = (str(session_id), *self._x_undo[1:])
        if self.inner is not None and hasattr(self.inner, 'on_session_start'):
            self.inner.on_session_start(session_id, **kwargs)

    def bind_session_state(self, session_db: Any, session_id: str) -> None:
        """A reset-only switch (/new, /resume, /branch): Hermes hands the session db and the new id and
        sends no on_session_start, so rebind here or the engine keeps the previous session's pins. A branch
        child inherits its parent's pins, the way a fork's copies keep their author, except a pin the child
        undid: that one stays undone through every later bind (#592)."""
        self._bind(session_id)
        parent = self._parent_of(session_db, str(session_id or ''))
        if parent and parent != str(session_id):
            self._carry_pins(parent, str(session_id))
        if self.inner is not None and hasattr(self.inner, 'bind_session_state'):
            self.inner.bind_session_state(session_db, session_id)

    @staticmethod
    def _parent_of(session_db: Any, sid: str) -> str:
        try:
            for name in ('get_session', 'get'):
                fn = getattr(session_db, name, None)
                if not callable(fn):
                    continue
                row = fn(sid)
                parent = row.get('parent_session_id') if isinstance(row, dict) else getattr(
                    row, 'parent_session_id', None)
                if parent:
                    return str(parent)
        except Exception:
            pass  # an unreadable db names no parent; binding still stands
        return ''

    def _carry_pins(self, old_sid: str, new_sid: str) -> None:
        """Merge one session's pins into another's, oldest first, dropping repeats, and what compactions folded.
        A pin the target session undid stays undone: its digest sits in its file, and the parent's copy is
        skipped, so a rebind of a `/branch` or compression child puts no undone ruling back (#592)."""
        try:
            # Both sessions' files are read again, not a cache another engine may have left stale, as on an undo:
            # the source's, so a pin the source (parent) undid does not resurrect in the child (#645), and the
            # target's, so a pin the child undid stays undone (#592).
            old, new = self._load_pins(old_sid, fresh=True), self._load_pins(new_sid, fresh=True)
            gone = self._x_dropped.get(new_sid, set())
            merged = {}
            for kind in ('ruling', 'correction', 'open_decision'):
                carried = [t for t in old[kind] if _pin_digest(kind, t) not in gone]
                have = {hf.Signal(kind, t).key() for t in carried}
                merged[kind] = carried + [t for t in new[kind] if hf.Signal(kind, t).key() not in have]
            merged['open_decision'] = merged['open_decision'][-12:]  # the cap _observe keeps (#398)
            folded = self._x_folded.setdefault(new_sid, {})
            before = dict(folded)
            for d, ties in self._x_folded.get(old_sid, {}).items():
                folded[d] = list(dict.fromkeys([*folded.get(d, []), *ties]))[-8:]
            noted = self._x_firstrow.get(old_sid)  # the child's window opens on the same rows (#581)
            if noted and not self._x_firstrow.get(new_sid):
                self._x_firstrow[new_sid] = dict(noted)
            if merged != new or folded != before:
                self._x_pins[new_sid] = merged
                self._save_pins(merged, new_sid)
        except Exception as e:  # a failed carry must never block the switch
            logger.warning('exo-context: pin carry failed: %s', e)

    def on_session_reset(self) -> None:
        self._x_messages = []
        self._x_window_sid = ''
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
            self._saw(self._x_session_id)
        _set_live(self)
        if self.inner is not None and hasattr(self.inner, 'on_turn_complete'):
            self.inner.on_turn_complete(messages, usage, **kwargs)

    def _bind(self, session_id: Any) -> None:
        if session_id:
            self._x_session_id = str(session_id)
            _set_live(self)

    def _saw(self, sid: str) -> None:
        """The window now shows the latest turn of `sid`. ``exo.host_undo`` numbers each session's turns, so a host's
        undo reaches only the engine that saw the last one: Hermes can build a new agent on the same session (after
        /reasoning or /fast, say), and the old engine may live on until a full collection (#389)."""
        self._x_window_sid = sid
        self._x_turn = host_undo.turn(sid) if sid else 0

    def on_session_switch(self, new_session_id: str, *, rewound: bool = False,
                          messages: list[dict[str, Any]] | None = None,
                          summaries: dict[str, list[str]] | None = None, **kwargs: Any) -> None:
        """A switch binds the engine to the new session. After an undo the Exo core sends ``rewound=True``, every
        message still visible in that thread and ``summaries`` ({tree key: ids of the summary nodes still stored}).
        Each pin no visible principal message holds goes, and so does each cached summary node the undo deleted (its
        text may quote the undone ruling), so an undone ruling is never put back as the principal's words (D-041).
        A pin whose message was only summarized stays. The undone turns leave the window kept for handoffs when it is
        this thread's, whichever thread the engine is bound to, and each automatic handoff that may quote them is
        written again. The inner compressor gets only the keywords its own ``on_session_switch`` declares."""
        if self.inner is not None and hasattr(self.inner, 'on_session_switch'):  # only the keywords it declares
            fn = self.inner.on_session_switch
            fn(new_session_id, **_accepted_kwargs(fn, {'rewound': rewound, **kwargs}))
        if not rewound or messages is None:
            self._bind(new_session_id)
            return
        sid = str(new_session_id or '')
        holds = _holder(messages)
        pins = self._load_pins(sid)
        kept = {k: [t for t in items if holds(k, t)] for k, items in pins.items()}
        alive = {str(k): set(v) for k, v in (summaries or {}).items()}
        lost = set()
        for owner, cached in self._x_nodes.items():  # any session's cache: an agent tree's nodes go to its latest thread
            gone = [n for n, (_, _, tree) in cached.items() if tree in alive and n not in alive[tree]]
            for n in gone:
                del cached[n]
            if gone:
                lost.add(owner)
        window = None
        if sid == self._x_window_sid:  # the window last seen is this thread's: the undone turns leave it
            have = {hf.message_text(m) for m in messages if isinstance(m, dict)}
            window = [m for m in self._x_messages if isinstance(m, dict) and hf.PIN_OPEN not in hf.message_text(m)
                      and (m.get('role') == 'system' or hf.message_text(m) in have)]
        self._rewound(sid, pins, kept, window, lost)

    def on_host_undo(self, session_id: str) -> None:
        """A host's undo, which only its memory provider hears: exo-memory passes it on through ``exo.host_undo``
        (#389). Hermes's /undo N removes the Nth user message from the end and everything after it, with one notice.
        So when this engine saw that session's last turn, its window's last user message opens an undone turn. A pin
        goes when that turn holds it and nothing left in the window does, unless a compaction folded the pin's
        message into a summary row the window holds. A pin whose message sits in a tail that a compaction held out
        stays too. The pins are read again from disk first, since another engine on the session may have written
        them. A compress before the undo that folded the window's first row has noted the fold with the pins; when
        that row is still this window's first row, its summary blocks seed the pending undo, so the next turn
        counts the row as reaching back (#581). Then the window, the handoffs and the link are cleaned as after
        the core's undo, and the undo is recorded for the session's next turn (``_after_undo``), which drops the
        pins of any earlier turn the same undo took: the record waits in ``exo.host_undo``, so the engine that
        runs that turn resolves it, whichever agent Hermes built that engine on (#580). A second undo before that
        turn keeps the window the first one left, so the rows the second one took still show there."""
        sid = str(session_id or '')
        if not sid or sid != self._x_window_sid or self._x_turn != host_undo.latest(sid):
            return  # another session's window, or a later turn of it ran in another engine: nothing shows the undo
        window = [m for m in self._x_messages if isinstance(m, dict)]
        cut = max((i for i, m in enumerate(window) if m.get('role') == 'user'), default=-1)
        if cut < 0:
            return  # the window holds no turn
        own = [i for i, m in enumerate(window) if m.get('role') == 'system' and hf.PIN_OPEN in hf.message_text(m)]
        seen = [m for i, m in enumerate(window) if i not in own]
        rest = [m for i, m in enumerate(window[:cut]) if i not in own]
        pins = self._load_pins(sid, fresh=True)
        # A summary row in the undone turn is one Hermes merged into that turn's message. Hermes keeps that row's
        # summary part, so the pins it folded stay.
        folded = self._folded(sid, seen)
        undone, left = _holder(window[cut:]), _holder(window[:cut])
        kept = {k: [t for t in items if left(k, t) or not undone(k, t) or folded(k, t)]
                for k, items in pins.items()}
        if not self._x_undo or self._x_undo[0] != sid:
            first = next((hf.message_text(m) for m in rest if m.get('role') != 'system'), None)
            noted = self._x_firstrow.get(sid, {})  # a compress before the undo folded that row; its summary stands
            seeds = set(noted.pop(_digest(first), ())) if first is not None else set()
            self._x_undo = (sid, rest, seeds)
            host_undo.pend(sid, rest, seeds)  # the same record waits under the session, for any engine's turn (#580)
        self._rewound(sid, pins, kept, rest, set())

    def _after_undo(self, kwargs: dict[str, Any]) -> None:
        """The first turn of the session after a host's undo, in whichever engine runs it (#580): the pending undo
        waits in ``exo.host_undo`` for the session's next turn, so the agent Hermes rebuilt picks it up too, and the
        record this engine heard settles here beside it. Hermes's /undo N takes N turns with one notice, so the
        host's whole transcript (``conversation_messages``) shows what went. A pin the window showed goes when that
        transcript shows it no longer. A message shows its pin, and so does a summary row that folded the pin's
        message, so a folded pin goes once the undo took every such row too. A request for another session, or one
        that names no session and carries no user message (Hermes sends the turn's as ``incoming_message``), leaves
        the undo pending. A host that sends no transcript keeps those pins. So does one whose transcript does not
        reach back to where the window started: it was cut short, not undone. A window settled twice drops twice
        to nothing: each pass reads the pins file again fresh, so what the first pass dropped the second finds
        gone."""
        if not (kwargs.get('session_id') or kwargs.get('incoming_message') is not None):
            return  # a request that is no turn leaves the undo for the session's next one
        record = host_undo.take(self._x_session_id)  # the record any engine holds: this turn settles it (#580)
        if record is not None:
            self._undo_drop(self._x_session_id, *record, kwargs)
        if self._x_undo:  # the record this engine heard, the way main keeps it (its #581 seeds included)
            sid, before, anchors = self._x_undo
            if sid != self._x_session_id:
                return  # another session's undo waits for that session's turn in this engine
            self._x_undo = None
            if record is None or record[0] != before:  # the same window is settled by the first pass already
                self._undo_drop(sid, before, anchors, kwargs)

    def _undo_drop(self, sid: str, before: list[dict[str, Any]], anchors: set[str],
                   kwargs: dict[str, Any]) -> None:
        """The drop of one settled undo record, the engine's own or the session's registry one (#580). Hermes's
        transcript decides; the pins are read again from disk first, since another engine on the session may have
        written or dropped them, so a second pass over the same window finds nothing left to drop."""
        live = [m for m in kwargs.get('conversation_messages') or () if isinstance(m, dict)]
        last = max((i for i, m in enumerate(live) if m.get('role') == 'user'), default=-1)
        if last < 0:
            return
        # The transcript must reach back to where the window started: it holds the window's first row or the summary
        # part of it, or the summary row of a compaction since the undo that folded either — before the undo too,
        # when the compress noted the fold (#581). Only an undo of every turn leaves nothing before the new turn's
        # message.
        first = next((hf.message_text(m) for m in before if m.get('role') != 'system'), None)
        if (first is not None and any(m.get('role') != 'system' for m in live[:last])
                and not any(_part_of(first, m) for m in live) and anchors.isdisjoint(_blocks(live))):
            logger.info('exo-context: the transcript after an undo in session %s does not start where the window '
                        'did, so no pin was dropped', sid)
            return
        pins = self._load_pins(sid, fresh=True)
        was, now = _holder(before), _holder(live)
        shown, still = self._folded(sid, before), self._folded(sid, live)
        kept = {k: [t for t in items if now(k, t) or still(k, t) or not (was(k, t) or shown(k, t))]
                for k, items in pins.items()}
        if kept != pins:
            self._rewound(sid, pins, kept, None, set())

    def _reconcile(self, kwargs: dict[str, Any]) -> None:
        """The first turn of a session that this engine runs for a host that sends the turn's ``incoming_message``
        (Hermes): drop each pin the host undid without a notice (#537). Hermes's messaging gateway cuts its
        transcript on ``/undo``, builds a new agent and tells no one, so the pins file still holds the undone pins.
        The host's transcript (``conversation_messages``) before the turn's own message must end at one of the leads
        noted last (``_note_reach``), so it is the noted transcript with its tail cut. A transcript cut short at its
        start fails at its first row, and one that opens like the noted one but goes on past where a lead ended fails
        at its end. A pin then goes when its lead is that one or a later one, no principal message holds it, and no
        summary row carries it (``_folded``). That last test is for a pin a compaction folded and the principal then
        said again: its lead moved to the later message, so an undo of that message alone leaves the summary row
        before the lead. A summary row after a pin's lead cannot be in such a transcript. A pin with no lead (one said
        in the session's first message, or never seen in a host's transcript) stays, and so does every pin when
        nothing comes before the turn's own message. The drop is an undo's: logged as counts, its digests kept out
        of every later carry (#592)."""
        sid, live = self._x_session_id, kwargs.get('conversation_messages')
        if not sid or sid in self._x_ran or kwargs.get('incoming_message') is None or not isinstance(live, list):
            return
        self._x_ran.add(sid)
        pins = self._load_pins(sid, fresh=True)
        reach, noted = self._x_reach.get(sid) or {}, self._x_leads.get(sid) or []
        if not reach:
            return
        rows = [m for m in live if isinstance(m, dict)]
        own, users = kwargs['incoming_message'], [i for i, m in enumerate(rows) if m.get('role') == 'user']
        own = hf.message_text(own) if isinstance(own, dict) else str(own)
        end = users[-1] if users and hf.message_text(rows[users[-1]]) == own else len(rows)
        _, cut = _leads(rows[:end])
        if not cut or len(cut) > len(noted) or noted[len(cut) - 1] != cut[-1]:
            return
        later, now, folded = noted[len(cut) - 1:], _holder(rows), self._folded(sid, rows)

        def undone(kind: str, text: str) -> bool:
            return reach.get(_pin_digest(kind, text)) in later and not now(kind, text) and not folded(kind, text)
        kept = {k: [t for t in items if not undone(k, t)] for k, items in pins.items()}
        if kept != pins:
            self._rewound(sid, pins, kept, None, set())

    def _note_reach(self, kwargs: dict[str, Any], pins: dict[str, list[str]]) -> None:
        """Note the leads of the host's transcript (``conversation_messages``), and each pin's lead at the first
        principal message that holds it, so the first turn of a later engine can tell an undo from a transcript cut
        short (#537). Only a request that sends ``incoming_message`` notes them, as only such a request reconciles.
        A pin the transcript does not hold keeps the lead noted while it did, and one held by the first row has none.
        The pins file keeps the transcript's leads up to the last pin's, as digests, no text."""
        live = kwargs.get('conversation_messages')
        if kwargs.get('incoming_message') is None or not isinstance(live, list) or not any(pins.values()):
            return
        sid, rows = self._x_session_id, [m for m in live if isinstance(m, dict)]
        places, chain = _leads(rows)
        said = [(i, hf.Signal('', hf.strip_pinned(hf.message_text(m))).key())
                for i, m in enumerate(provenance.principal_view(rows)) if m.get('role') == 'user']
        reach = self._x_reach.setdefault(sid, {})
        before = dict(reach)
        for kind, items in pins.items():
            for text in items:
                key = hf.Signal(kind, text).key()
                at = next((i for i, u in said if key in u), None)
                if at is None:
                    continue
                if places[at]:
                    reach[_pin_digest(kind, text)] = places[at]
                else:
                    reach.pop(_pin_digest(kind, text), None)
        chain = _upto(chain, reach)
        if reach != before or chain != self._x_leads.get(sid):
            self._x_leads[sid] = chain
            self._save_pins(pins, sid)

    def _folded(self, sid: str, messages: list[dict[str, Any]]) -> Callable[[str, str], bool]:
        """Does a summary row in `messages` carry the pin (kind, text)? One does when a compaction folded the pin's
        message and wrote the row's block, or wrote that block over an earlier row that carried the pin."""
        ties, here = self._x_folded.get(sid, {}), _blocks(messages)
        return lambda kind, text: not here.isdisjoint(ties.get(_pin_digest(kind, text), ()))

    def _rewound(self, sid: str, pins: dict[str, list[str]], kept: dict[str, list[str]],
                 window: list[dict[str, Any]] | None, lost: set[str]) -> None:
        """The rest of an undo on either path, once the pins to keep are known. ``window`` is the window last seen
        without the undone turns, or None to leave the window be: another thread's, or one the turn under way
        replaces. ``lost`` names the sessions whose cached summary nodes the undo deleted. A drop is logged as counts
        per kind and the session id, no text."""
        changed = kept != pins
        if changed:
            self._x_pins[sid] = kept
            live = {_pin_digest(k, t) for k, items in kept.items() for t in items}
            gone = {_pin_digest(k, t) for k, items in pins.items() for t in items} - live
            self._x_dropped[sid] = self._x_dropped.get(sid, set()) | gone  # no rebind carries them back (#592)
            self._save_pins(kept, sid)
            logger.info('exo-context: an undo in session %s dropped %s', sid,
                        ', '.join(f'{len(pins[k]) - len(kept[k])} {k}' for k in pins))
        if window is not None:
            block = self._block_for(kept)
            self._x_messages = window + ([{'role': 'system', 'content': block}] if block else [])
            self._x_last_pinned = ''
        # Rewrite each automatic handoff that may quote the undone turns: one that lost a summary node (on an agent
        # tree, the thread the node was reported to), this thread's own when its pins changed, and one written from
        # this thread's window. A window that has since been replaced cannot be cleaned, so it is left out.
        before = self._x_last_handoff
        for x, (_, src, topic) in list(self._x_handoffs.items()):
            if x in lost or src == sid or (x == sid and changed):
                self._write_handoff(x, topic, windowless=src != self._x_window_sid)
        # The pinned block links the last handoff: keep that link only to a file that is current, never to one
        # written before the undo by /exo handoff, by a host's compaction, or from a window the undo changed.
        current = {p for p, _, _ in self._x_handoffs.values()}
        self._x_last_handoff = before if before in current else (self._x_handoffs.get(sid) or (None,))[0]

    def select_context(self, request_messages: list[dict[str, Any]], **kwargs: Any):
        self._bind(kwargs.get('session_id'))
        self._after_undo(kwargs)  # a pending undo waits in exo.host_undo for this session's next turn (#580)
        self._reconcile(kwargs)  # an undo no one reported, on this engine's first turn of the session (#537)
        selected = None
        if self.inner is not None and hasattr(self.inner, 'select_context'):
            selected = self.inner.select_context(request_messages, **kwargs)
        msgs = list(selected if selected is not None else request_messages)
        pins = self._observe(msgs)
        hidden = self._hidden(msgs, pins)
        full = self._block_for(pins)
        self._x_messages = list(msgs) + ([{'role': 'system', 'content': full}] if full else [])
        self._saw(self._x_session_id)
        self._note_reach(kwargs, pins)
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

    # --- pins: rulings, corrections and open decisions seen this session, kept verbatim ---

    def _pin_file(self, sid: str | None = None) -> Path:
        sid = re.sub(r'[^A-Za-z0-9_.-]+', '_', (self._x_session_id if sid is None else sid) or 'default')
        return exo_home() / 'context-pins' / f'{sid}.json'

    def _load_pins(self, sid: str | None = None, fresh: bool = False) -> dict[str, list[str]]:
        """This session's pins, what compactions folded (#389) and what an undo dropped (#592), from the cache or
        the pins file. `fresh` reads the file again, keeping the cache when it cannot be read."""
        sid = self._x_session_id if sid is None else sid
        pins = None if fresh else self._x_pins.get(sid)
        if pins is None:
            try:
                data = json.loads(self._pin_file(sid).read_text('utf-8'))
                pins = {k: [str(t) for t in data.get(k, [])] for k in ('ruling', 'correction', 'open_decision')}
                folded = data.get('folded')
                self._x_folded[sid] = {str(d): [str(x) for x in ties] for d, ties in folded.items()
                                       if isinstance(ties, list)} if isinstance(folded, dict) else {}
                dropped = data.get('dropped')
                self._x_dropped[sid] = {str(d) for d in dropped} if isinstance(dropped, list) else set()
                noted = data.get('first_row')  # what a compress noted as folding the window's first row (#581)
                self._x_firstrow[sid] = {str(r): [str(x) for x in blocks] for r, blocks in noted.items()
                                         if isinstance(blocks, list)} if isinstance(noted, dict) else {}
                reach = data.get('reach')  # each pin's lead in the host's transcript (#537)
                self._x_reach[sid] = {str(d): lead for d, lead in reach.items()
                                      if isinstance(lead, str) and lead} if isinstance(reach, dict) else {}
                leads = data.get('leads')  # the leads of the host's transcript noted last (#537)
                self._x_leads[sid] = [str(d) for d in leads] if isinstance(leads, list) else []
            except (OSError, ValueError, AttributeError, TypeError):
                pins = self._x_pins.get(sid) or {'ruling': [], 'correction': [], 'open_decision': []}
            self._x_pins[sid] = pins
        return pins

    def _observe(self, msgs: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Add the rulings, corrections and open decisions visible in `msgs` to this session's pins."""
        pins = self._load_pins()
        try:
            rulings, corrections = sg.classified([m for m in msgs if isinstance(m, dict)])
            decisions = sg.pinned_open_decisions(msgs)
        except Exception as e:  # never block a turn on extraction
            logger.warning('exo-context: signal extraction failed: %s', e)
            return pins
        changed = False
        for kind, found in (('ruling', rulings), ('correction', corrections), ('open_decision', decisions)):
            have = {hf.Signal(kind, t).key() for t in pins[kind]}
            for s in found:
                if s.key() not in have:
                    pins[kind].append(s.text)
                    have.add(s.key())
                    changed = True
        # Open decisions keep only the newest 12, the cap the compaction block and the handoff apply to
        # the merged list: a deferral never expires on its own, so an uncapped list grows the pins file
        # and the per-turn scan over it for the life of the session (#398).
        if len(pins['open_decision']) > 12:
            pins['open_decision'] = pins['open_decision'][-12:]
            changed = True
        if changed:
            self._save_pins(pins)
        return pins

    def _save_pins(self, pins: dict[str, list[str]], sid: str | None = None) -> None:
        """Write this session's pins, the folded ones among them as digests with the blocks they ride (#389), what
        an undo dropped, as digests (#592), the noted first row (#581), each pin's lead and the transcript's leads up
        to the last of them (#537). A digest of a pin live again leaves the dropped list."""
        sid = self._x_session_id if sid is None else sid
        names = {_pin_digest(k, t) for k, items in pins.items() for t in items}
        folded = self._x_folded[sid] = {d: v for d, v in self._x_folded.get(sid, {}).items() if d in names}
        dropped = self._x_dropped[sid] = {d for d in self._x_dropped.get(sid, set()) if d not in names}
        noted = self._x_firstrow.get(sid) or {}
        reach = self._x_reach[sid] = {d: v for d, v in self._x_reach.get(sid, {}).items() if d in names}
        leads = self._x_leads[sid] = _upto(self._x_leads.get(sid) or [], reach)
        try:
            path = self._pin_file(sid)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = dict(pins)
            if folded:
                data['folded'] = folded
            if dropped:
                data['dropped'] = sorted(dropped)
            if noted:
                data['first_row'] = noted
            if reach:
                data['reach'] = reach
            if leads:
                data['leads'] = leads
            path.write_text(json.dumps(data), encoding='utf-8')
        except OSError as e:
            logger.warning('exo-context: could not save pins: %s', e)

    @staticmethod
    def _hidden(msgs: list[dict[str, Any]], pins: dict[str, list[str]]) -> dict[str, list[str]]:
        """The pins that no user message in `msgs` carries verbatim any more."""
        seen = [hf.message_text(m) for m in msgs if isinstance(m, dict) and m.get('role') == 'user']
        return {k: [t for t in items if not any(t.strip() in u for u in seen)] for k, items in pins.items()}

    def _block_for(self, pins: dict[str, list[str]], path: Any = None) -> str:
        sig = hf.Signals(rulings=[hf.Signal('ruling', t) for t in pins.get('ruling', [])],
                         corrections=[hf.Signal('correction', t) for t in pins.get('correction', [])],
                         open_decisions=[hf.Signal('open_decision', t) for t in pins.get('open_decision', [])])
        return hf.render_pinned_block(sig, path, self._x_pin_budget)

    def on_compacted(self, session: str, node: Any, **kwargs: Any) -> None:
        """The core wrote a summary node: write a handoff that carries it and the pinned signals."""
        self._bind(session)
        if not self._x_write_handoffs:
            return
        sid = self._x_session_id
        nodes = self._x_nodes.setdefault(sid, {})  # id -> (start, text, tree key), so undo can drop deleted nodes
        nodes[getattr(node, 'id', len(nodes))] = (getattr(node, 'start', 0), str(getattr(node, 'text', '') or ''),
                                                  str(getattr(node, 'session', '') or sid))
        self._write_handoff(sid)

    def _write_handoff(self, sid: str, topic: str | None = None, windowless: bool = False) -> None:
        """This session's automatic handoff: the summary nodes seen, the window's signals (none when `windowless`) and
        the pins. An undo passes the topic the file was written under, so the rewrite replaces it."""
        nodes = self._x_nodes.get(sid, {})
        summary = '\n\n'.join(t for _, t, _ in sorted(nodes.values(), key=lambda x: x[0]) if t)[-6000:]
        window = [] if windowless else self._x_messages
        topic = topic or self._topic_for(sid)
        try:
            signals = sg.extract(window)
            pins = self._load_pins(sid)
            signals = dataclasses.replace(
                signals,
                rulings=sg.merge(signals.rulings, [hf.Signal('ruling', t) for t in pins['ruling']]),
                corrections=sg.merge(signals.corrections, [hf.Signal('correction', t) for t in pins['correction']]),
                open_decisions=sg.merge(signals.open_decisions,
                                        [hf.Signal('open_decision', t) for t in pins['open_decision']])[-12:])
            path = hf.write_handoff(topic, window, self._x_store, self.handoff_dir,
                                    session=sid or None, principal=self._x_principal, summary=summary,
                                    signals=signals)
            self._x_last_handoff = path
            self._x_handoffs[sid] = (path, '' if windowless else self._x_window_sid, topic)
        except Exception as e:
            self._x_handoffs.pop(sid, None)  # the file on disk may be stale: an undo must not link it
            logger.warning('exo-context: handoff write failed: %s', e)

    # --- compaction --------------------------------------------------------------

    def compress(self, messages: list[dict[str, Any]], current_tokens: Optional[int] = None,
                 focus_topic: Optional[str] = None, force: bool = False, memory_context: str = '',
                 **kwargs: Any) -> list[dict[str, Any]]:
        if self.inner is None:  # the core never calls update_model: build the default inner now
            self._build_inner(model='', context_length=0)
        try:
            pins = self._observe(messages)  # #371: pins observed from principal text carry across summaries
            signals = sg.extract(messages)
            signals = dataclasses.replace(
                signals,
                rulings=sg.merge(signals.rulings, [hf.Signal('ruling', t) for t in pins['ruling']]),
                corrections=sg.merge(signals.corrections, [hf.Signal('correction', t) for t in pins['correction']]),
                open_decisions=sg.merge(signals.open_decisions,
                                        [hf.Signal('open_decision', t) for t in pins['open_decision']])[-12:])
        except Exception as e:  # never block compaction on extraction
            logger.warning('exo-context: signal extraction failed: %s', e)
            signals = None
        call = dict(current_tokens=current_tokens, focus_topic=focus_topic, force=force,
                    memory_context=memory_context, **kwargs)
        result = self.inner.compress(messages, **_accepted_kwargs(self.inner.compress, call))
        if result is messages or not isinstance(result, list):
            return result
        idx = self._summary_index(result, messages)
        if idx is None:
            return result  # inner made no summary (no-op or structural skip)
        raw, result[idx] = result[idx], self._defused(result[idx])
        if signals is None:
            return result

        path = None
        if self._x_write_handoffs:
            try:
                summary = self._summary_body(hf.message_text(raw))
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
            self._fold(messages, result, idx, pins, block)
        return result

    def _fold(self, messages: list[dict[str, Any]], result: list[dict[str, Any]], idx: int,
              pins: dict[str, list[str]], block: str) -> None:
        """Tie to this summary's block each pin whose message the summary folded (not the kept tail), and each pin
        tied to an earlier summary row it folded. A host's undo keeps such a pin while a row with one of its blocks
        is left (#389). The last 8 blocks are kept per pin, in case a host drops a summary it was handed. The last
        message missing from the result was kept, not folded, when the summary row has its role and holds its text
        whole: Hermes merges its summary into the first tail row when no role alternates, and an undo of that row's
        turn keeps only the summary part. A summary row in another role that quotes the message was not merged.
        With no pending undo for this session and the window last seen its own, a fold that covers the window's
        first row (or what Hermes kept of it), or a row already noted for it, is noted with the pins: the row's
        digest and this summary's block, so an undo after the compress counts the row as reaching back (#581). A
        pending undo takes the block into its own list of such rows, also when the fold covers a row already there,
        and so does the record the session keeps in ``exo.host_undo``, so a fold reached in one engine is seen by
        another engine's resolution too (#580)."""
        try:
            gone = _gone(messages, result, idx)
            if (gone and gone[-1].get('role') == result[idx].get('role')
                    and hf.message_text(gone[-1]) in hf.strip_pinned(hf.message_text(result[idx]))):
                gone.pop()  # the summary row's own block quotes the pins, so it is left out of the test
            holds, carried, tie = _holder(gone), _blocks(gone), _digest(block)
            folded = self._x_folded.setdefault(self._x_session_id, {})
            for k, items in pins.items():
                for t in items:
                    d = _pin_digest(k, t)
                    if holds(k, t) or not carried.isdisjoint(folded.get(d, ())):
                        folded[d] = [x for x in folded.get(d, []) if x != tie][-7:] + [tie]
            undo = self._x_undo  # a pending undo's window start, folded here: this summary row now stands for it
            first = next((hf.message_text(m) for m in undo[1] if m.get('role') != 'system'), None) if undo else None
            if first is not None and undo[0] == self._x_session_id and (
                    any(_part_of(first, m) for m in gone) or not undo[2].isdisjoint(carried)):
                undo[2].add(tie)
            elif (not undo or undo[0] != self._x_session_id) and self._x_window_sid == self._x_session_id:
                old = self._x_firstrow.get(self._x_session_id) or {}
                row = next((hf.message_text(m) for m in self._x_messages  # the window's first row, folded here:
                            if isinstance(m, dict) and m.get('role') != 'system'), None)  # note it, for an undo
                d = _digest(row) if row is not None else ''
                if d and (any(_part_of(row, m) for m in gone) or not set(old.get(d, ())).isdisjoint(carried)):
                    self._x_firstrow[self._x_session_id] = {d: [x for x in old.get(d, []) if x != tie][-7:] + [tie]}
            held = host_undo.pending(self._x_session_id)  # the same fold noted in the record the session keeps, so
            first = next((hf.message_text(m) for m in held[0] if m.get('role') != 'system'), None) if held else None
            if first is not None and (any(_part_of(first, m) for m in gone) or not held[1].isdisjoint(carried)):
                held[1].add(tie)  # a fold reached in one engine is seen by another engine's resolution (#580)
            self._save_pins(pins)
        except Exception as e:  # never block compaction on it
            logger.warning('exo-context: folded pins not noted (%s)', type(e).__name__)

    def _summary_index(self, msgs: list[dict[str, Any]], source: Any = ()) -> Optional[int]:
        """The summary the inner just made: flag first, then the inner's own test, then the end marker.

        A message the inner returned as part of its input (a protected head or tail message) is never it,
        however much it quotes the marker (#322 item 2); the scans fall back to any match only when no
        fresh message matches, for an inner that rewrites a message in place."""
        is_summary = getattr(self.inner, '_is_context_summary_message', None)

        def by_flag(m: dict[str, Any]) -> bool:
            return bool(m.get(SUMMARY_FLAG))

        def by_test(m: dict[str, Any]) -> bool:
            try:
                return bool(callable(is_summary) and is_summary(m))
            except Exception:
                return False

        def by_marker(m: dict[str, Any]) -> bool:
            return END_MARKER in hf.message_text(m)

        old = {id(m) for m in source}
        for fresh in (True, False):
            for test in (by_flag, by_test, by_marker):
                for i, m in enumerate(msgs):
                    if isinstance(m, dict) and (not fresh or id(m) not in old) and test(m):
                        return i
        return None

    @staticmethod
    def _summary_body(text: str) -> str:
        cut = text.find(END_MARKER)
        body = text[:cut] if cut >= 0 else text
        if body.lstrip().startswith(SUMMARY_PREFIX):
            body = body.lstrip()[len(SUMMARY_PREFIX):]
        body = provenance.defuse_pins(hf.strip_pinned(body))  # #279: and any other spelling of a tag is escaped
        return body.strip()[:6000]

    @staticmethod
    def _defused(msg: Any) -> Any:
        """The inner's summary message with each pinned-block tag escaped (#279). A summary is never the principal's
        words, but the summarizer may decode an escaped tag in its source or write one, and a live tag in the
        summary would read to the model as the principal's pinned words. A summary row carries nothing into the
        next extraction (#385); exo-context's own block is added after this escaping, and continuity rides on the
        session pins ``compress`` observed from the input."""
        if not isinstance(msg, dict):
            return msg
        content = msg.get('content')
        if isinstance(content, str):
            new = provenance.defuse_pins(content)
        elif isinstance(content, list):
            new = [{**p, 'text': provenance.defuse_pins(p['text'])}
                   if isinstance(p, dict) and isinstance(p.get('text'), str) else p for p in content]
        else:
            return msg
        if new == content:
            return msg
        out = {**msg, 'content': new}
        out.pop('api_content', None)  # a stale sidecar would resend the live tag
        return out

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
