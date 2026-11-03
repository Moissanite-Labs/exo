"""Hermes Agent profile databases (`state.db`), opened read-only.

Kept: user and assistant messages with text, from sessions whose `source` is interactive
(a person at a terminal, desktop app or chat platform). Cron, subagent and one-shot sessions
are skipped by default. Assistant rows with only tool calls are skipped.

Many "user" rows are briefs written by another agent. Each user row gets `author_conf`
(high, medium, low). High and medium rows are authored by the principal; low rows are
authored `hermes:unverified-user` with role `user`, so they never pass as the principal's voice.

Provenance: `src = {adapter, key, path, rowid, message_uid?}`, plus `profile`, `source`
(the Hermes session source) and `hermes_session` in the body; session = `hermes:<profile>:<id>`.
Watermark: {"max_id": highest message rowid read, "sources": the session sources read}. The rowid covers only
those sources (a watermark without them read INTERACTIVE). A run that reads a source outside them starts again
from the first row, and `src.key` keeps it from adding a row twice.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .common import Run, author_conf

ADAPTER = 'hermes'
UNVERIFIED = 'hermes:unverified-user'
INTERACTIVE = ('cli', 'desktop', 'tui', 'user', 'whatsapp', 'discord', 'telegram', 'slack', 'signal',
               'matrix', 'imessage', 'sms')


def _db_path(source: Path) -> Path:
    if source.is_dir():
        source = source / 'state.db'
    if not source.is_file():
        raise FileNotFoundError(f'no Hermes state.db at {source}')
    return source


def _profile(db: Path) -> str:
    parent = db.resolve().parent
    return parent.name if parent.parent.name == 'profiles' else 'default'


def connect_ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f'{db.resolve().as_uri()}?mode=ro', uri=True)


def ingest(source_path, ledger, principal_author: str, *, since: str | None = None,
           sources: tuple[str, ...] = INTERACTIVE) -> dict:
    db = _db_path(Path(source_path).expanduser())
    run = Run(ledger, ADAPTER, db, principal_author, since)
    read = set(run.watermark.get('sources', INTERACTIVE))
    max_id = int(run.watermark.get('max_id', 0)) if set(sources) <= read else 0
    con = connect_ro(db)
    try:
        mcols = {r[1] for r in con.execute('PRAGMA table_info(messages)')}
        scols = {r[1] for r in con.execute('PRAGMA table_info(sessions)')}
        opt = [c for c in ('message_uid', '_compressed_summary', 'active') if c in mcols]
        sel = ', '.join(['m.id', 'm.session_id', 'm.role', 'm.content', 'm.timestamp', 's.source',
                         's.title' if 'title' in scols else 'NULL',
                         's.profile_name' if 'profile_name' in scols else 'NULL',
                         's.model' if 'model' in scols else 'NULL'] + [f'm.{c}' for c in opt])
        q = (f'SELECT {sel} FROM messages m JOIN sessions s ON s.id = m.session_id '
             f"WHERE m.id > ? AND m.role IN ('user', 'assistant') "
             f"AND s.source IN ({','.join('?' * len(sources))}) ORDER BY m.id")
        default_profile = _profile(db)
        top = con.execute('SELECT COALESCE(MAX(id), 0) FROM messages').fetchone()[0]
        for row in con.execute(q, (max_id, *sources)):
            mid, sid, role, content, ts, src_kind, title, pname, model = row[:9]
            extra = dict(zip(opt, row[9:]))
            profile = pname or default_profile
            common = dict(src={'path': str(db.resolve()), 'rowid': mid, 'message_uid': extra.get('message_uid')},
                          source_ts=ts, session=f'{ADAPTER}:{profile}:{sid}', profile=profile, source=src_kind,
                          hermes_session=sid, title=title,
                          secondary=True if extra.get('_compressed_summary') else None,
                          inactive=True if extra.get('active') == 0 else None)
            text = content or ''
            if role == 'user':
                conf = author_conf(text)
                trusted = conf != 'low'
                run.emit(key=f'{profile}:{sid}:{mid}', role='principal' if trusted else 'user', text=text,
                         author=principal_author if trusted else UNVERIFIED, author_conf=conf, **common)
            else:
                run.emit(key=f'{profile}:{sid}:{mid}', role='assistant', text=text, author=f'hermes:{profile}',
                         model=model, **common)
    finally:
        con.close()
    return run.finish({'max_id': max(max_id, int(top)), 'sources': sorted(set(sources))})
