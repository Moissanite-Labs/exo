"""`/exo branch|handoff|resume` slash commands.

The Exo core calls a plugin command as `handler(raw_args) -> str`. The handlers act on the engine that
saw the latest turn (`engine.live_engine()`), which holds the transcript snapshot and the
handoff location. Every handler returns the text to show; nothing here prints.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any, Optional

from exo import handoff as hf

from . import engine as eng
from . import signals as sg

USAGE = """Usage:
  /exo branch <topic>     write a handoff and print the line that starts a fresh session on it
  /exo handoff [topic]    write a handoff for this session
  /exo resume <topic>     load the newest handoff for a topic (or a path) into this session"""


def _engine(engine: Any = None) -> Optional[Any]:
    return engine if engine is not None else eng.live_engine()


def _store(engine: Any = None) -> Any:
    st = getattr(engine, 'store', None) if engine is not None else None
    if st is None and os.environ.get('EXO_PRINCIPAL'):
        try:
            from exo.store import PrincipalStore
            st = PrincipalStore.for_principal(os.environ['EXO_PRINCIPAL'])
        except Exception:
            st = None
    return st


def _out_dir(engine: Any = None):
    if engine is not None:
        return engine.handoff_dir
    return hf.default_handoff_dir(None, os.environ.get('EXO_PRINCIPAL'))


def _write(topic: str, engine: Any) -> tuple[Path, hf.Signals]:
    messages = engine.messages if engine is not None else []
    signals = sg.extract(messages)
    store = getattr(engine, '_x_store', None) if engine is not None else None
    path = hf.write_handoff(topic, messages, store, _out_dir(engine),
                            session=getattr(engine, '_x_session_id', None) or None,
                            principal=getattr(engine, '_x_principal', None), signals=signals)
    if engine is not None:
        engine.__dict__['_x_last_handoff'] = path
    return path, signals


def _counts(s: hf.Signals) -> str:
    return (f'{len(s.rulings)} ruling(s), {len(s.corrections)} correction(s), '
            f'{len(s.open_decisions)} open decision(s) kept verbatim')


def cmd_handoff(topic: str = '', engine: Any = None) -> str:
    engine = _engine(engine)
    if engine is None or not engine.messages:
        return 'Nothing to hand off yet: no turns seen in this session.'
    topic = topic.strip() or engine.topic
    path, signals = _write(topic, engine)
    return f'Handoff written: {path}\n{_counts(signals)}.'


def cmd_branch(topic: str, engine: Any = None) -> str:
    topic = topic.strip()
    if not topic:
        return 'Give the branch a topic: /exo branch <topic>'
    engine = _engine(engine)
    if engine is None or not engine.messages:
        return 'Nothing to branch yet: no turns seen in this session.'
    path, signals = _write(topic, engine)
    return '\n'.join([
        f'Handoff written: {path}',
        f'{_counts(signals)}.',
        'Start the fresh session with:',
        f'  {hf.launch_line(topic, path)}',
        f'Or, inside any new session: /exo resume {hf.slugify(topic)}',
    ])


def resume(topic_or_path: str, engine: Any = None, budget: int = 1500) -> tuple[Optional[hf.Handoff], str]:
    """Load a handoff and build the text to inject. Returns (handoff or None, text or error)."""
    arg = topic_or_path.strip()
    if not arg:
        return None, 'Name the topic to resume: /exo resume <topic>'
    p = Path(arg).expanduser()
    path = p if p.suffix == '.md' and p.is_file() else hf.find_handoff(arg, _out_dir(engine))
    if path is None:
        return None, f'No handoff found for "{arg}" in {_out_dir(engine)}.'
    h = hf.load_handoff(path)
    return h, hf.resume_text(h, _store(engine), budget)


def cmd_resume(topic_or_path: str, engine: Any = None) -> str:
    engine = _engine(engine)
    h, text = resume(topic_or_path, engine)
    if h is None:
        return text
    if engine is None:
        return 'No live session to inject into; paste this as the first message.\n\n' + text
    engine.resume_block = text
    engine.topic = hf.slugify(h.topic)
    return (f'Resumed "{h.topic}" from {h.path}.\n'
            f'{len(h.rulings)} ruling(s), {len(h.corrections)} correction(s), '
            f'{len(h.open_decisions)} open decision(s); context injected for the next turns.\n\n' + text)


def handle(raw_args: str = '', engine: Any = None) -> str:
    """Entry point for `/exo <sub> [args]`."""
    try:
        parts = shlex.split(raw_args or '')
    except ValueError:
        parts = (raw_args or '').split()
    if not parts or parts[0] in ('help', '-h', '--help'):
        return USAGE
    sub, rest = parts[0].lower(), ' '.join(parts[1:])
    if sub == 'branch':
        return cmd_branch(rest, engine)
    if sub == 'handoff':
        return cmd_handoff(rest, engine)
    if sub == 'resume':
        return cmd_resume(rest, engine)
    return f'Unknown subcommand "{sub}".\n{USAGE}'
