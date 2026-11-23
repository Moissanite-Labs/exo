"""Handoff files: a self-contained Markdown note that lets a fresh session pick up a workstream.

A handoff keeps the principal's own words. Rulings and corrections are quoted verbatim, never
paraphrased, because a summary that drops the principal's voice loses the part that matters most.

Public API:

    extract_signals(messages)                      -> Signals (rulings, corrections, open decisions, ...)
    write_handoff(topic, messages, store, out_dir) -> Path of the written file
    load_handoff(path)                             -> Handoff
    find_handoff(topic, out_dir)                   -> newest Path for a topic, or None
    resume_text(handoff, store, budget)            -> text to inject into a fresh session
    render_pinned_block(signals) / parse_pinned_blocks(text)

Messages are OpenAI-format dicts (`role`, `content` as a string or a list of parts, optional
`tool_calls`). Nothing here names a person, machine or path; the default output directory is
`$EXO_HOME/principals/<id>/handoffs/` and every location is a parameter.

Seam: principal signal extraction prefers `exo.capture` (rulings and corrections extraction,
written separately). When that module is missing or fails, the minimal local heuristics below
run instead. Both paths return verbatim text slices.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import provenance
from .core.private import atomic_write
from .store import PrincipalStore, exo_home, principal_root

FORMAT_VERSION = 1
PIN_OPEN, PIN_CLOSE = provenance.PIN_OPEN, provenance.PIN_CLOSE
ATTR_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
DECISIONS = "Decisions made (principal's words, verbatim)"
TITLE_RE = re.compile(r'^# (?:Handoff:\s*)?(.+)$', re.M)
# Text that marks a host-written compaction summary; its body is not the principal speaking.
SUMMARY_MARKERS = ('END OF CONTEXT SUMMARY', '[CONTEXT COMPACTION', '[CONTEXT SUMMARY]')
MAX_VERBATIM = 600          # a user message up to this length is kept whole
MAX_BRIEF = 3000

RULING_RE = re.compile(
    r"\b(always|never|from now on|going forward|in future|don'?t|do not|must|only ever|make sure|"
    r"ruling|rule is|policy|i want|i'd like|i prefer|we prefer|let'?s go with|go with|go ahead|"
    r"approved?|decided|decision is|no more|stop (?:doing|using)|keep (?:it|them|cost)|"
    r"use \S+(?: \S+)? (?:not|instead of) )", re.I)
CORRECTION_RE = re.compile(
    r"(^\s*(?:no\b|nope\b|wrong\b|not that\b|that'?s not\b|that is not\b|actually\b))|"
    r"\b(i said|i meant|should have|should've|you missed|you forgot|not what i (?:asked|meant|said)|"
    r"undo that|revert that|that'?s wrong|was wrong)\b", re.I)
DEFER_RE = re.compile(r"\b(decide later|park (?:this|that|it)|hold off|not decided|undecided|tbd|"
                      r"let me think)\b", re.I)
QUESTION_RE = re.compile(r"\b(should i|shall i|do you want|would you like|which|prefer|ok to|okay to|"
                         r"approve|confirm|go ahead)\b", re.I)
OPEN_MARK_RE = re.compile(r'^\s*(?:[-*\d.)\s]*)(?:\*\*)?(decision needed|open decision|open question|'
                          r'needs your call|your call|waiting on you|open)(?:\*\*)?\s*[:\-]', re.I)
PROCESS_RE = re.compile(
    r"\b(pid[ =:#]?\d+|running in (?:the )?background|background (?:process|job|task|run|lane)|"
    r"nohup|tmux|launchd|systemctl|crontab|cron job|--run-budget|still running|is running|"
    r"listening on|watcher|daemon)\b", re.I)
NEXT_HEAD_RE = re.compile(r'^\s*(?:#+\s*|\*\*)?(next steps?|next|to do|todo)(?:\*\*)?\s*:?\s*(?:\*\*)?\s*$', re.I)
NEXT_LINE_RE = re.compile(r'^\s*(?:[-*]\s*)?(?:\*\*)?next(?: step)?(?:\*\*)?\s*:\s*(.+)$', re.I)
CHECKBOX_RE = re.compile(r'^\s*[-*]\s*\[ \]\s*(.+)$')
PATH_RE = re.compile(r'(?<![\w/.~-])((?:~|/)[\w.~@+-]*(?:/[\w.@+-]+)+\.[A-Za-z0-9]{1,8})\b')
PATH_KEYS = ('path', 'file_path', 'filepath', 'file', 'target', 'filename', 'dest', 'destination')
WRITE_TOOL_RE = re.compile(r'(write|patch|edit|create|apply|replace|append|move|delete|rm)', re.I)
SENT_SPLIT_RE = re.compile(r'(?<=[.!?])\s+|\n+')


# --- data ----------------------------------------------------------------------

@dataclass
class Signal:
    kind: str                    # ruling | correction | open_decision | process
    text: str                    # verbatim
    index: int | None = None     # message index in the transcript it came from
    ts: str | None = None
    carried: bool = False        # came from an earlier pinned block

    def key(self) -> str:
        return re.sub(r'\s+', ' ', self.text).strip().lower()


@dataclass
class Signals:
    rulings: list[Signal] = field(default_factory=list)
    corrections: list[Signal] = field(default_factory=list)
    open_decisions: list[Signal] = field(default_factory=list)
    processes: list[Signal] = field(default_factory=list)
    brief: str = ''
    files_written: list[str] = field(default_factory=list)
    files_mentioned: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    last_state: str = ''
    message_count: int = 0

    def pinned(self) -> list[Signal]:
        return self.rulings + self.corrections + self.open_decisions

    def empty(self) -> bool:
        return not self.pinned()


@dataclass
class Handoff:
    path: Path
    topic: str
    created: str
    meta: dict[str, str]
    text: str
    sections: dict[str, str]
    rulings: list[str]
    corrections: list[str]
    open_decisions: list[str]
    next_steps: list[str]


# --- message helpers -------------------------------------------------------------

def content_text(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and isinstance(part.get('text'), str):
                out.append(part['text'])
        return '\n'.join(out)
    if isinstance(content, dict) and isinstance(content.get('text'), str):
        return content['text']
    return str(content)


def message_text(msg: dict) -> str:
    return content_text(msg.get('content')) if isinstance(msg, dict) else ''


def is_summary_text(text: str) -> bool:
    return any(m in text for m in SUMMARY_MARKERS) or PIN_OPEN in text


def _ts(msg: dict) -> str | None:
    for k in ('timestamp', 'ts', 'created_at'):
        v = msg.get(k)
        if v:
            return str(v)
    return None


def _principal_turns(messages: list[dict]) -> list[tuple[int, dict, str]]:
    out = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or m.get('role') != 'user':
            continue
        if m.get('principal') is False or provenance.author_of(m) != provenance.PRINCIPAL:
            continue  # D-040: a peer, bus or job message is not the principal's ruling
        text = message_text(m).strip()
        if len(text) < 2 or is_summary_text(text):
            continue
        out.append((i, m, text))
    return out


def _verbatim_slices(text: str, pattern: re.Pattern) -> list[str]:
    """Whole message if short, else the matching sentences or lines (verbatim substrings)."""
    if len(text) <= MAX_VERBATIM:
        return [text]
    return [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip() and pattern.search(s)]


# --- local heuristics (fallback for exo.capture) --------------------------------

def local_extract_rulings(messages: list[dict]) -> list[Signal]:
    out = []
    for i, m, text in _principal_turns(messages):
        if RULING_RE.search(text):
            out.extend(Signal('ruling', s, i, _ts(m)) for s in _verbatim_slices(text, RULING_RE))
    return out


def local_extract_corrections(messages: list[dict]) -> list[Signal]:
    out = []
    for i, m, text in _principal_turns(messages):
        if CORRECTION_RE.search(text):
            out.extend(Signal('correction', s, i, _ts(m)) for s in _verbatim_slices(text, CORRECTION_RE))
    return out


def _coerce_capture(items: Any, kind: str) -> list[Signal]:
    out = []
    for it in items or []:
        if isinstance(it, Signal):
            out.append(it)
        elif isinstance(it, str):
            out.append(Signal(kind, it))
        elif isinstance(it, dict):
            text = it.get('text') or it.get('quote') or it.get('verbatim')
            if text:
                out.append(Signal(kind, str(text), it.get('index'), it.get('ts')))
        else:
            text = getattr(it, 'text', None)
            if text:
                out.append(Signal(kind, str(text), getattr(it, 'index', None), getattr(it, 'ts', None)))
    return out


def extract_rulings_and_corrections(messages: list[dict]) -> tuple[list[Signal], list[Signal]]:
    """Use exo.capture when present (the seam), else the local heuristics. D-040: a user message whose author,
    read through `provenance.author_of` with its meta tags, is not the principal's (peer, bus, job, a context
    note) is never a ruling."""
    messages = provenance.principal_view(messages)  # such a message becomes role "other", which both paths skip
    try:
        from . import capture
        found = capture.extract_signals(messages, skip=lambda m: is_summary_text(message_text(m)))
        def pick(kind: str) -> list[Signal]:
            out = []
            for x in found:
                if x.kind != kind:
                    continue
                whole = message_text(messages[x.index]).strip() if 0 <= x.index < len(messages) else ''
                # capture classifies a sentence; the principal's own words are kept whole when short
                text = whole if whole and len(whole) <= MAX_VERBATIM else x.text
                out.append(Signal(kind, text, x.index, _ts(messages[x.index])))
            return out
        return pick('ruling'), pick('correction')
    except Exception:
        return local_extract_rulings(messages), local_extract_corrections(messages)


def _open_decisions(messages: list[dict]) -> list[Signal]:
    messages = provenance.principal_view(messages)  # D-040: a deferral in a peer, bus or job row is not open
    last_user = max((i for i, _, _ in _principal_turns(messages)), default=-1)
    out: list[Signal] = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        role, text = m.get('role'), message_text(m)
        if not text or is_summary_text(text):
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if role == 'assistant':
                if OPEN_MARK_RE.match(s) or (i > last_user and s.endswith('?') and QUESTION_RE.search(s)):
                    out.append(Signal('open_decision', s, i, _ts(m)))
            elif role == 'user' and DEFER_RE.search(s):
                out.append(Signal('open_decision', s, i, _ts(m)))
    return out


def _tool_calls(msg: dict) -> Iterable[tuple[str, dict]]:
    for tc in msg.get('tool_calls') or []:
        fn = tc.get('function', tc) if isinstance(tc, dict) else getattr(tc, 'function', None)
        if fn is None:
            continue
        name = fn.get('name', '') if isinstance(fn, dict) else getattr(fn, 'name', '')
        args = fn.get('arguments', {}) if isinstance(fn, dict) else getattr(fn, 'arguments', {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {'_raw': args}
        yield str(name or ''), args if isinstance(args, dict) else {}


def _processes(messages: list[dict]) -> list[Signal]:
    out = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        if m.get('role') in ('assistant', 'user'):
            text = message_text(m)
            if text and not is_summary_text(text):
                for line in text.splitlines():
                    if PROCESS_RE.search(line):
                        out.append(Signal('process', line.strip()[:300], i, _ts(m)))
        for name, args in _tool_calls(m):
            cmd = args.get('command') or args.get('cmd')
            if isinstance(cmd, str) and (args.get('background') or re.search(r'(&\s*$|\bnohup\b)', cmd)):
                out.append(Signal('process', f'{name}: {cmd.strip()[:280]}', i, _ts(m)))
    return out


def _files(messages: list[dict]) -> tuple[list[str], list[str]]:
    written: list[str] = []
    mentioned: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        for name, args in _tool_calls(m):
            for k in PATH_KEYS:
                v = args.get(k)
                if isinstance(v, str) and v.strip():
                    (written if WRITE_TOOL_RE.search(name) else mentioned).append(v.strip())
        if m.get('role') in ('assistant', 'user'):
            text = message_text(m)
            if not is_summary_text(text):
                mentioned.extend(PATH_RE.findall(text))
    written = _dedupe_str(written)
    mentioned = [p for p in _dedupe_str(mentioned) if p not in written]
    return written[:40], mentioned[:40]


def _next_steps(messages: list[dict]) -> list[str]:
    assistants = [m for m in messages if isinstance(m, dict) and m.get('role') == 'assistant'
                  and message_text(m).strip() and not is_summary_text(message_text(m))]
    out: list[str] = []
    for m in assistants[-3:]:
        in_next, got = False, 0
        for line in message_text(m).splitlines():
            s = line.strip()
            if NEXT_HEAD_RE.match(s):
                in_next, got = True, 0
                continue
            mm = NEXT_LINE_RE.match(s) or CHECKBOX_RE.match(s)
            if mm:
                out.append(mm.group(1).strip())
                continue
            if not in_next:
                continue
            if s.startswith('#') or (not s and got):
                in_next = False      # a heading, or a blank line after items, ends the list
            elif s:
                out.append(re.sub(r'^([-*]|\d+[.)])\s*', '', s))
                got += 1
    return _dedupe_str(out)[:12]


def _brief(messages: list[dict]) -> str:
    turns = _principal_turns(messages)
    if not turns:
        return ''
    text = turns[0][2]
    if len(text) > MAX_BRIEF:
        text = text[:MAX_BRIEF].rstrip() + '\n[brief truncated; full text in the session log]'
    return text


def _last_state(messages: list[dict]) -> str:
    for m in reversed(messages):
        if isinstance(m, dict) and m.get('role') == 'assistant':
            text = message_text(m).strip()
            if text and not is_summary_text(text):
                return text[:1500] + ('\n[truncated]' if len(text) > 1500 else '')
    return ''


def _dedupe_str(items: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for s in items:
        k = s.strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _dedupe(signals: Iterable[Signal]) -> list[Signal]:
    seen, out = set(), []
    for s in signals:
        if s.key() and s.key() not in seen:
            seen.add(s.key())
            out.append(s)
    return out


# --- pinned block --------------------------------------------------------------

_PIN_SECTIONS = (('Rulings', 'ruling', 'rulings'), ('Corrections', 'correction', 'corrections'),
                 ('Open decisions', 'open_decision', 'open_decisions'))


def _quote(text: str) -> str:
    return '\n'.join(f'> {ln}' if ln.strip() else '>' for ln in text.strip().splitlines())


def _unquote_items(block: str) -> list[str]:
    items, cur = [], None
    for ln in block.splitlines():
        if ln.startswith('>'):
            body = ln[2:] if ln.startswith('> ') else ln[1:]
            cur = [] if cur is None else cur
            cur.append(body)
        else:
            if cur is not None:
                items.append('\n'.join(cur).strip())
            cur = None
    if cur is not None:
        items.append('\n'.join(cur).strip())
    return [i for i in items if i]


def _pinned_spans(text: str) -> Iterable[tuple[int, int, int, int]]:
    """(start, body_start, body_end, end) of each closed pinned block, in order. A linear `find`
    scan (#322): the first open tag with no close after it ends the scan, since no later open can
    close either. The lazy regex this replaces rescanned to the end from every unclosed open, so
    a run of them cost quadratic time."""
    pos = 0
    while True:
        start = text.find(PIN_OPEN, pos)
        if start < 0:
            return
        body_start = start + len(PIN_OPEN)
        body_end = text.find(PIN_CLOSE, body_start)
        if body_end < 0:
            return
        pos = body_end + len(PIN_CLOSE)
        yield start, body_start, body_end, pos


def parse_pinned_blocks(text: str) -> dict[str, list[str]]:
    """Read every `<exo-pinned>` block in `text` back into verbatim items per kind."""
    out: dict[str, list[str]] = {k: [] for _, k, _ in _PIN_SECTIONS}
    for _, body_start, body_end, _ in _pinned_spans(text):
        body = text[body_start:body_end]
        for title, kind, _ in _PIN_SECTIONS:
            m = re.search(r'^### ' + re.escape(title) + r'\s*$(.*?)(?=^### |\Z)', body, re.S | re.M)
            if m:
                out[kind].extend(_unquote_items(m.group(1)))
    return out


def strip_pinned(text: str) -> str:
    """`text` without any `<exo-pinned>` blocks."""
    parts, pos = [], 0
    for start, _, _, end in _pinned_spans(text):
        parts.append(text[pos:start])
        pos = end
    parts.append(text[pos:])
    return ''.join(parts)


def render_pinned_block(signals: Signals, handoff_path: str | os.PathLike | None = None,
                        char_budget: int = 6000) -> str:
    """Verbatim block for the compacted transcript. Oldest items drop first past the budget;
    the handoff file always keeps all of them."""
    if signals.empty():
        return ''
    head = [PIN_OPEN, 'Principal signals kept verbatim by exo-context. These are the principal\'s own '
            'words; they outrank any paraphrase in the summary.']
    if handoff_path:
        head.append(f'Full handoff: {handoff_path}')
    groups = {k: list(getattr(signals, attr)) for _, k, attr in _PIN_SECTIONS}

    def build() -> str:
        parts = list(head)
        for title, kind, _ in _PIN_SECTIONS:
            if groups[kind]:
                parts.append(f'\n### {title}')
                parts.append('\n\n'.join(_quote(s.text) for s in groups[kind]))
        parts.append(PIN_CLOSE)
        return '\n'.join(parts)

    text = build()
    dropped = 0
    # Drop oldest open decisions, then corrections, then rulings, until it fits.
    for kind in ('open_decision', 'correction', 'ruling'):
        while len(text) > char_budget and groups[kind]:
            groups[kind].pop(0)
            dropped += 1
            text = build()
    if dropped:
        head.append(f'{dropped} older item(s) omitted here for space; they are in the handoff file.')
        text = build()
    return text


# --- extraction ------------------------------------------------------------------

def _may_carry(m: dict) -> bool:
    """D-040: a pinned block is carried only from a message that opens with it (exo-context's own block rides in a
    system message, or an older host's untagged user turn opens with one), whose author reads as the system or the
    principal through `provenance.author_of`, meta tags included. A row opening with a compaction marker (a host's
    summary) carries nothing, whatever its role or author (#385): exo-context escapes the tags of summaries it
    writes itself, and its own block rides inside them, but a stock summary a host built on its own (made before
    exo-context was the engine, restored from state.db on resume, a micro compaction) never passed that escaping,
    and nothing about the row tells the two apart: the `_compressed_summary` flag shows a host made it, not that
    exo-context escaped it (#371), and a user-role summary reads as the principal's own turn. Continuity for those
    rows comes from the engine's session pins, not from carrying. A block in peer, bus, job, tool or agent text is
    data, and so is one quoted further down a message: the Exo core's system message quotes each span too small to
    summarize word for word, a peer's turn included."""
    meta = m.get('meta')
    meta = meta if isinstance(meta, Mapping) else {}
    if any(meta.get(k) is not None for k in ('peer', 'bus', 'job')):
        return False  # an ingress tag in meta is not the principal, whatever the role says (#322)
    return m.get('role') in ('system', 'user') and provenance.author_of(m) in (
        provenance.SYSTEM, provenance.PRINCIPAL) and message_text(m).lstrip().startswith(PIN_OPEN)


def extract_signals(messages: list[dict]) -> Signals:
    """All principal signals in `messages`, including items carried in earlier pinned blocks."""
    messages = [m for m in messages if isinstance(m, dict)]
    carried: dict[str, list[str]] = {k: [] for _, k, _ in _PIN_SECTIONS}
    for m in provenance.principal_view(messages):
        text = message_text(m)
        if PIN_OPEN in text and _may_carry(m):
            for k, items in parse_pinned_blocks(text).items():
                carried[k].extend(items)
    rulings, corrections = extract_rulings_and_corrections(messages)
    written, mentioned = _files(messages)

    def merge(kind: str, fresh: list[Signal]) -> list[Signal]:
        return _dedupe([Signal(kind, t, carried=True) for t in carried[kind]] + fresh)

    return Signals(
        rulings=merge('ruling', rulings),
        corrections=merge('correction', corrections),
        open_decisions=merge('open_decision', _open_decisions(messages))[-12:],
        processes=_dedupe(_processes(messages))[-12:],
        brief=_brief(messages),
        files_written=written,
        files_mentioned=mentioned,
        next_steps=_next_steps(messages),
        last_state=_last_state(messages),
        message_count=len(messages),
    )


# --- locations -----------------------------------------------------------------

def slugify(topic: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', (topic or '').lower()).strip('-')
    return s[:60].strip('-') or 'session'


def default_handoff_dir(store: PrincipalStore | None = None, principal: str | None = None) -> Path:
    """`$EXO_HANDOFF_DIR`, else `<store>/handoffs`, else `$EXO_HOME/principals/<id>/handoffs`,
    else `$EXO_HOME/handoffs` when no principal is known."""
    env = os.environ.get('EXO_HANDOFF_DIR')
    if env:
        return Path(env).expanduser()
    if store is not None:
        return Path(store.root) / 'handoffs'
    pid = principal or os.environ.get('EXO_PRINCIPAL')
    if pid:
        return principal_root(pid) / 'handoffs'
    return exo_home() / 'handoffs'


def find_handoff(topic: str, out_dir: str | os.PathLike) -> Path | None:
    """Newest handoff for `topic` in `out_dir` (file names sort by date)."""
    d = Path(out_dir).expanduser()
    if not d.is_dir():
        return None
    slug = slugify(topic)
    cands = [p for p in d.glob(f'{slug}-*.md') if re.fullmatch(re.escape(slug) + r'-\d{8}(-\d+)?', p.stem)]
    if not cands:
        exact = d / f'{slug}.md'
        return exact if exact.exists() else None
    return max(cands, key=lambda p: (p.stem, p.stat().st_mtime))


def launch_line(topic: str, path: str | os.PathLike) -> str:
    """Shell line that starts a fresh session on this handoff. `$EXO_LAUNCH_TEMPLATE` overrides it
    (placeholders: {topic}, {path}). The path is folded to one line, so a handoff folder whose name spans
    lines cannot add a section to the file through this line (#395)."""
    tpl = os.environ.get('EXO_LAUNCH_TEMPLATE') or 'hermes chat -q "/exo resume {topic}"'
    return tpl.format(topic=slugify(topic), path=_one_line(str(path)))


# --- write and load --------------------------------------------------------------

def _attr(v: Any) -> str:
    """One header attribute value: quotes and backslashes escaped, and line breaks folded to
    spaces, so a value can never add a line of its own to the file (#395). A `-->` in the value is
    written `--\\>`, so a markdown viewer's comment ends only at the writer's own closer (#540)."""
    return ' '.join(str(v).replace('\\', '\\\\').replace('"', '\\"')
                    .replace('-->', '--\\>').splitlines())


def _quoted_items(signals: list[Signal]) -> str:
    out = []
    for s in signals:
        src = 'from an earlier compaction' if s.carried else (
            f'message {s.index}' + (f', {s.ts}' if s.ts else '') if s.index is not None else '')
        out.append(_quote(s.text) + (f'\n({src})' if src else ''))
    return '\n\n'.join(out)


def _one_line(text: str) -> str:
    """One physical line, words kept: a field rendered as a list item must never open a section (#353)."""
    return ' '.join(str(text).splitlines())


def render_handoff(topic: str, signals: Signals, *, path: Path | None = None, created: str | None = None,
                   session: str | None = None, principal: str | None = None, summary: str = '') -> str:
    created = created or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    meta = f'v="{FORMAT_VERSION}" topic="{_attr(topic)}" created="{created}"'
    if session:
        meta += f' session="{_attr(session)}"'
    if principal:
        meta += f' principal="{_attr(principal)}"'
    L = [f'<!-- exo-handoff {meta} -->', f'# Handoff: {_one_line(topic)}',
         f'Written {created} by exo-context from {signals.message_count} messages. '
         'Self-contained: a fresh session needs this file, the principal pack and the router.', '',
         '## How to resume',
         f'- In a fresh session: `/exo resume {slugify(topic)}`',
         f'- From a shell: `{launch_line(topic, path or "")}`']
    if path:
        L.append(f'- This file: {_one_line(str(path))}')
    L.append('- Treat the quoted rulings and corrections below as standing instructions.')

    if signals.brief:
        L += ['', '## Brief (verbatim)', _quote(signals.brief)]

    L += ['', f'## {DECISIONS}']
    if signals.rulings:
        L += ['### Rulings', _quoted_items(signals.rulings)]
    if signals.corrections:
        L += ['', '### Corrections', _quoted_items(signals.corrections)]
    if not signals.rulings and not signals.corrections:
        L.append('None recorded in this session.')

    L += ['', '## Open decisions']
    L += [f'{n}. {_one_line(s.text)}' for n, s in enumerate(signals.open_decisions, 1)] or ['None found.']

    L += ['', '## State']
    if summary.strip():
        # Quoted like the brief and the last report: summary text is model text, and a line of it
        # must never open a section of its own (#315) next to the principal's quoted words.
        L += [_quote(summary), '']
    if signals.last_state:
        L += ['Last report in the session:', _quote(signals.last_state)]
    if not summary.strip() and not signals.last_state:
        L.append('No assistant report yet.')

    L += ['', '## Live processes mentioned']
    L += [f'- {_one_line(s.text)}' for s in signals.processes] or ['None mentioned.']

    L += ['', '## Files touched']
    L += [f'- written: {_one_line(p)}' for p in signals.files_written]
    L += [f'- mentioned: {_one_line(p)}' for p in signals.files_mentioned]
    if not signals.files_written and not signals.files_mentioned:
        L.append('None recorded.')

    L += ['', '## Next steps']
    L += [f'{n}. {_one_line(s)}' for n, s in enumerate(signals.next_steps, 1)] or [
        'None written down. Start from the open decisions and the last report.']
    return '\n'.join(L).rstrip() + '\n'


def write_handoff(topic: str, messages: list[dict], store: PrincipalStore | None = None,
                  out_dir: str | os.PathLike | None = None, *, session: str | None = None,
                  principal: str | None = None, summary: str = '', signals: Signals | None = None,
                  now: datetime | None = None) -> Path:
    """Write `<out_dir>/<topic-slug>-<YYYYMMDD>.md` and return its path. Same topic on the same day
    overwrites; carried pinned items keep earlier rulings in the new file."""
    now = now or datetime.now(timezone.utc)
    d = Path(out_dir).expanduser() if out_dir else default_handoff_dir(store, principal)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f'{slugify(topic)}-{now.strftime("%Y%m%d")}.md'
    sig = signals or extract_signals(messages)
    if principal is None and store is not None:
        principal = Path(store.root).name
    text = render_handoff(topic, sig, path=path, created=now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                          session=session, principal=principal, summary=summary)
    atomic_write(path, text, mode=None)
    return path


def _sections(text: str, start: int, read: set[int]) -> dict[str, str]:
    """Body of each `## ` heading the reader reads, from `start`, bounded at the next `## ` line of
    any kind. First of a name wins: a later duplicate (an old file's unquoted summary, a hand edit)
    never replaces the real section, which the writer puts first (#315). A heading the reader does
    not read opens no section; its lines stay in the section it sits under."""
    out: dict[str, str] = {}
    ms = [m for m in re.finditer(r'^## (.+?)\s*$', text, re.M) if m.start() >= start]
    for i, m in enumerate(ms):
        if m.start() in read:
            nxt = ms[i + 1].start() if i + 1 < len(ms) else len(text)
            out.setdefault(m.group(1).strip(), text[m.end():nxt].strip('\n'))
    return out


def _subsection(body: str, title: str) -> str:
    m = re.search(r'^### ' + re.escape(title) + r'\s*$(.*?)(?=^### |\Z)', body, re.S | re.M)
    return m.group(1) if m else ''


def _numbered(body: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r'^\d+\.\s+(.+)$', body, re.M)]


def _header(text: str) -> tuple[str, int]:
    """The exo-handoff header's attribute text and the index just past its end, or ('', 0) when there
    is none. The closing --> counts only outside a quoted value: a --> inside topic="..." is data, not
    the end of the comment (#395)."""
    m = re.search(r'<!--\s*exo-handoff\s+', text)
    if not m:
        return '', 0
    i, quoted = m.end(), False
    while i < len(text):
        ch = text[i]
        if quoted and ch == '\\':
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        elif not quoted and (text.startswith('-->', i) or text.startswith('--!>', i)):
            return text[m.end():i], text.index('>', i) + 1
        i += 1
    return '', 0


def _recovered(attrs: str) -> dict[str, str]:
    """Header attribute values with the writer's escapes undone: `-->` first, then quotes, then
    backslashes, the reverse of how `_attr` wrote them (#540). An older writer escaped only quotes
    and backslashes, and an older file holds `-->` raw, which this reads as itself."""
    return {k: v.replace('--\\>', '-->').replace('\\"', '"').replace('\\\\', '\\')
            for k, v in ATTR_RE.findall(attrs)}


def _title_span(text: str, end: int, topic: str) -> tuple[int, int] | None:
    """(start, past-end) of the title span, or None. The writer writes the title from the topic, so
    the `# ` line must say what the header's recovered topic says (#395); a title that spans lines
    instead is an older writer's raw topic, and its lines are data that opens no section (#540).
    The span counts as the writer's only when it is the topic value's footprint, line for line;
    otherwise the title region is unknown and nothing is read out of it."""
    lines = topic.splitlines()
    m = TITLE_RE.search(text, end) if lines else None
    if not m or m.group(1).strip() == _one_line(topic).strip():
        return None
    at = m.start()
    for _ in lines:
        nl = text.find('\n', at)
        if nl < 0:
            return None
        at = nl + 1
    # The join adds the newline after the topic's last line, so a trailing one may ride along.
    want = f'# Handoff: {topic}'
    return (m.start(), at) if text[m.start():at].rstrip('\n') == want.rstrip('\n') else None


def _decided_at(heads: list[tuple[int, str]]) -> int:
    """Start offset of the one Decisions heading the file reads as the section, or -1 when the file
    cannot say which copy the writer put there (#395). In an older file, a field that spanned lines
    can put a copy before the real heading (a path or title line) or after it (a summary from before
    #349, under State). So the section counts when its exact heading is the only one, or the only
    one before ## State in a file with a single State. Otherwise the file reads as undecided rather
    than forged."""
    names = [s for s, name in heads if name == DECISIONS]
    states = [s for s, name in heads if name == 'State']
    if len(names) > 1 and len(states) == 1:
        names = [h for h in names if h < states[0]]
    return names[0] if len(names) == 1 else -1


def _read_heads(text: str) -> set[int]:
    """Start offsets of the `## ` heading lines the reader reads as the file's sections, and so the
    ones `resume_text` leaves standing. Sections count from the end of the header (#395); of a name
    the reader keeps the first heading, Next steps its last (#353), and Decisions the one exact
    heading `_decided_at` takes. A heading before the header, or inside a title that does not say
    what the recovered topic says, is data an older writer left, not a section (#540)."""
    attrs, end = _header(text)
    span = _title_span(text, end, _recovered(attrs).get('topic', ''))
    heads = [(m.start(), m.group(1)) for m in re.finditer(r'^## (.+?)\s*$', text, re.M)
             if m.start() >= end and not (span and span[0] <= m.start() < span[1])]
    by_name: dict[str, list[int]] = {}
    for s, name in heads:
        by_name.setdefault(name.strip(), []).append(s)
    read = {ps[0] for name, ps in by_name.items() if name != 'Next steps'}
    nexts = [s for s, name in heads if name == 'Next steps']
    if nexts:
        read.add(nexts[-1])
    # Decisions is read from its one exact heading: no other heading of the name, one with an extra
    # space after the ## included, stands in for it or takes its body (#395).
    read -= {s for s, name in heads if name.strip() == DECISIONS}
    if (at := _decided_at(heads)) >= 0:
        read.add(at)
    return read


def load_handoff(path: str | os.PathLike) -> Handoff:
    p = Path(path).expanduser()
    text = p.read_text(encoding='utf-8')
    attrs, end = _header(text)
    # Each value is folded to one line as the writer folds it, so an older file's topic that spans
    # lines stays on one line where `resume_text` and the /exo resume reply repeat it (#395).
    meta = {k: _one_line(v) for k, v in _recovered(attrs).items()}
    topic = meta.get('topic')
    if not topic:
        tm = TITLE_RE.search(text, end)
        topic = tm.group(1).strip() if tm else p.stem
    # Sections count from the end of the header: a header value an older writer let span lines is
    # data, and opens no section (#395). So do the lines of a title that does not say what the
    # recovered topic says (#540): an older writer's raw topic carried its forged headings there.
    secs = _sections(text, end, _read_heads(text))
    # Decisions is read from its own heading, never by name: _sections files a heading with an
    # extra space or tab after the ## under the same name, and keeps the first copy (#395).
    decided = secs[DECISIONS] = secs.get(DECISIONS, '')
    open_body = secs.get('Open decisions', '')
    return Handoff(
        path=p, topic=topic, created=meta.get('created', ''), meta=meta, text=text, sections=secs,
        rulings=_unquote_items(_subsection(decided, 'Rulings')),
        corrections=_unquote_items(_subsection(decided, 'Corrections')),
        open_decisions=_numbered(open_body),
        next_steps=_numbered(secs.get('Next steps', '')),
    )


def _standing(text: str, end: int) -> str:
    """The file from the header end with every `## ` heading line the reader does not read as the
    section demoted to a `### ` line (#540): an older writer's path, launch, title, list or summary
    line could carry a forged section into the system message next to the principal's real one. The
    copies the reader reads stand. The match takes the spellings a markdown reader takes as the
    heading (#540): up to three leading spaces, and after the `##` a space, a tab or the line's end.
    A fourth leading space is an indented code block, not a heading, and the line stands as
    written. Fenced code blocks are not skipped, as before."""
    read = _read_heads(text)
    out, pos = [], end
    for m in re.finditer(r'^ {0,3}##(?:[ \t][^\n]*)?$', text, re.M):
        if m.start() < end:
            continue
        out.append(text[pos:m.start()])
        out.append(m.group(0) if m.start() in read else m.group(0).replace('##', '###', 1))
        pos = m.end()
    out.append(text[pos:])
    return ''.join(out)


def resume_text(handoff: Handoff, store: PrincipalStore | None = None, budget: int = 1500) -> str:
    """Working context for a fresh session: the handoff plus a retrieval pass over the store. It goes into the
    system message, so every pinned-block tag in it is escaped (#279): the file quotes the agent's report and a
    summary, and records the principal's words as quoted items under their own headings, never as a pinned block."""
    # The header goes in on one line: a value an older writer let span lines must not stand as a
    # section next to the principal's words (#395). Nor does any other line's: every `## ` heading
    # the reader did not read as the section is demoted, so an older file's raw path, launch, title,
    # list and summary lines reach the model as data, not as sections (#540).
    end = _header(handoff.text)[1]
    text = _one_line(handoff.text[:end]) + _standing(handoff.text, end)
    parts = [f'Resuming "{handoff.topic}" from the handoff at {handoff.path}. The quoted rulings and '
             'corrections are the principal\'s own words; follow them.', '', text.strip()]
    if store is not None and store.exists():
        query = ' '.join([handoff.topic] + handoff.open_decisions[:3] + handoff.next_steps[:3])
        try:
            from .retrieve import assemble
            got = assemble(store, query[:500], budget)
            if got.text.strip():
                parts += ['', '## Retrieved from the principal store', got.text.strip()]
        except Exception as e:  # retrieval is a bonus; the handoff stands alone
            parts += ['', f'(Retrieval pass skipped: {e})']
    return provenance.defuse_pins('\n'.join(parts).rstrip() + '\n')
