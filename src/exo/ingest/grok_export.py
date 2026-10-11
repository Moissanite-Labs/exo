"""Grok (xAI) data export: the export zip, its directory, or `prod-grok-backend.json`.

Format: {"conversations": [{"conversation": {id, title, create_time, ...},
"responses": [{"response": {_id, conversation_id, sender, message, model,
create_time: {"$date": {"$numberLong": ms}}, parent_response_id, partial, thinking_trace,
steps, web_search_results, ...}}]}], "projects", "tasks", "media_posts"}.
`sender` is "human" or "assistant" (any case). Kept: `message` text. Dropped: thinking
traces, tool steps, search results, media.

Provenance: `src = {adapter, key, path, member, conversation, response}`; session =
`grok:<conversation id>`. Watermark: {"max_ts": newest response time}.
"""
from __future__ import annotations

from pathlib import Path

from .common import Run, TsCursor, load_export_json

ADAPTER = 'grok_export'
ASSISTANT = 'grok'


def _ts(v):
    if isinstance(v, dict):
        v = v.get('$date', v)
        if isinstance(v, dict):
            v = v.get('$numberLong')
    return v


def ingest(source_path, ledger, principal_author: str, *, since: str | None = None) -> dict:
    path = Path(source_path).expanduser()
    data, member = load_export_json(path, ('prod-grok-backend.json',))
    run = Run(ledger, ADAPTER, path, principal_author, since)
    cur = TsCursor(run)
    convs = data.get('conversations', []) if isinstance(data, dict) else data
    for item in convs:
        conv = item.get('conversation') or {}
        cid = conv.get('id')
        for i, wrap in enumerate(item.get('responses') or []):
            r = wrap.get('response') or wrap
            ts = _ts(r.get('create_time'))
            if cur.old(ts):
                continue
            sender = (r.get('sender') or '').lower()
            if sender not in ('human', 'assistant'):
                continue
            rid = r.get('_id') or f'{cid}#{i}'
            human = sender == 'human'
            run.emit(key=f'{cid}:{rid}', role='principal' if human else 'assistant', text=r.get('message') or '',
                     author=principal_author if human else ASSISTANT,
                     src={'path': str(path.resolve()), 'member': member, 'conversation': cid, 'response': rid},
                     source_ts=ts, session=f'grok:{cid}', title=conv.get('title') or None,
                     model=None if human else r.get('model'), partial=True if r.get('partial') else None)
    run.stats['conversations'] = len(convs)
    return run.finish(cur.watermark())
