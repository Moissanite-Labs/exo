"""claude.ai data export (`conversations.json`, alone, in a directory, or in the export zip).

Format: a list of conversations, each {uuid, name, created_at, updated_at, chat_messages}.
A chat message is {uuid, sender: "human" | "assistant", text, content: [{type, text}],
created_at, attachments: [{file_name, extracted_content}], files}. `text` is used when set,
otherwise the text blocks of `content`. Attachment contents are not ingested; their file
names are listed.

Provenance: `src = {adapter, key, path, member, conversation, message}`; session =
`claude_ai:<conversation uuid>`. Watermark: {"max_ts": newest message time}.
"""
from __future__ import annotations

from pathlib import Path

from .common import Run, TsCursor, load_export_json

ADAPTER = 'claude_ai_export'
ASSISTANT = 'claude.ai'


def _text(m: dict) -> str:
    t = m.get('text')
    if isinstance(t, str) and t.strip():
        return t
    blocks = m.get('content') or []
    return '\n'.join(b.get('text', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'text')


def ingest(source_path, ledger, principal_author: str, *, since: str | None = None) -> dict:
    path = Path(source_path).expanduser()
    data, member = load_export_json(path, ('conversations.json',))
    run = Run(ledger, ADAPTER, path, principal_author, since)
    cur = TsCursor(run)
    convs = data if isinstance(data, list) else data.get('conversations', [])
    for conv in convs:
        cid = conv.get('uuid') or conv.get('id')
        for i, m in enumerate(conv.get('chat_messages') or []):
            ts = m.get('created_at')
            if cur.old(ts):
                continue
            sender = (m.get('sender') or '').lower()
            if sender not in ('human', 'assistant'):
                continue
            mid = m.get('uuid') or f'{cid}#{i}'
            names = [a.get('file_name') for a in (m.get('attachments') or []) + (m.get('files') or [])
                     if isinstance(a, dict) and a.get('file_name')]
            human = sender == 'human'
            run.emit(key=f'{cid}:{mid}', role='principal' if human else 'assistant', text=_text(m),
                     author=principal_author if human else ASSISTANT,
                     src={'path': str(path.resolve()), 'member': member, 'conversation': cid, 'message': mid},
                     source_ts=ts, session=f'claude_ai:{cid}', title=conv.get('name') or None,
                     attachments=names or None)
    run.stats['conversations'] = len(convs)
    return run.finish(cur.watermark())
