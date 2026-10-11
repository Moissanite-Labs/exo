"""Ingest adapters: bring a principal's existing conversations into their ledger.

Each adapter is `ingest(source_path, ledger, principal_author, *, since=None) -> stats`.
Sources are opened read-only and nothing is copied outside the ledger. Reruns are
idempotent: an `ingest` event holds the watermark, and every utterance carries a unique
`src.key`. See `common.py` for the event body.
"""
from __future__ import annotations

from importlib import import_module

from .common import author_conf, redact

ADAPTERS = {
    'claude_code': 'claude_code',
    'hermes': 'hermes',
    'claude_ai_export': 'claude_ai_export',
    'chatgpt_export': 'chatgpt_export',
    'grok_export': 'grok_export',
}
ALIASES = {'claude-code': 'claude_code', 'claude_ai': 'claude_ai_export', 'claude-ai': 'claude_ai_export',
           'chatgpt': 'chatgpt_export', 'grok': 'grok_export'}


def adapter(kind: str):
    name = ALIASES.get(kind, kind)
    if name not in ADAPTERS:
        raise KeyError(f'unknown ingest kind {kind!r}; expected one of {sorted(ADAPTERS)}')
    return import_module(f'{__name__}.{ADAPTERS[name]}')


def ingest(kind: str, source_path, ledger, principal_author: str, *, since: str | None = None) -> dict:
    return adapter(kind).ingest(source_path, ledger, principal_author, since=since)


__all__ = ['ADAPTERS', 'adapter', 'author_conf', 'ingest', 'redact']
