"""ChatGPT data export (`conversations.json`, alone, in a directory, or in the export zip).

Format: a list of conversations {id | conversation_id, title, create_time, current_node,
mapping}. `mapping` is a tree of nodes {id, parent, children, message}; a message is
{id, author: {role}, create_time, content: {content_type, parts}, recipient, metadata}.
Kept: user and assistant messages with string parts, content type text or multimodal_text,
addressed to "all" (assistant messages to a tool are tool calls), not visually hidden.
Every branch is kept, because edited prompts are corrections. `branch` is "main" for nodes
on the path to `current_node`, else "alt".

Provenance: `src = {adapter, key, path, member, conversation, node}`; session =
`chatgpt:<conversation id>`. Watermark: {"max_ts": newest message time}.
"""
from __future__ import annotations

from pathlib import Path

from .common import Run, TsCursor, load_export_json

ADAPTER = 'chatgpt_export'
ASSISTANT = 'chatgpt'


def _main_path(mapping: dict, leaf) -> set:
    out, node = set(), leaf
    while node and node in mapping and node not in out:
        out.add(node)
        node = mapping[node].get('parent')
    return out


def ingest(source_path, ledger, principal_author: str, *, since: str | None = None) -> dict:
    path = Path(source_path).expanduser()
    data, member = load_export_json(path, ('conversations.json',))
    run = Run(ledger, ADAPTER, path, principal_author, since)
    cur = TsCursor(run)
    convs = data if isinstance(data, list) else data.get('conversations', [])
    for conv in convs:
        cid = conv.get('conversation_id') or conv.get('id')
        mapping = conv.get('mapping') or {}
        main = _main_path(mapping, conv.get('current_node'))
        nodes = sorted(mapping.items(), key=lambda kv: ((kv[1].get('message') or {}).get('create_time') or 0, kv[0]))
        for nid, node in nodes:
            m = node.get('message') or {}
            role = (m.get('author') or {}).get('role')
            if role not in ('user', 'assistant'):
                continue
            content = m.get('content') or {}
            meta = m.get('metadata') or {}
            if content.get('content_type') not in ('text', 'multimodal_text') or m.get('recipient', 'all') != 'all' \
                    or meta.get('is_visually_hidden_from_conversation'):
                continue
            ts = m.get('create_time')
            if cur.old(ts):
                continue
            text = '\n'.join(p for p in content.get('parts') or [] if isinstance(p, str))
            human = role == 'user'
            run.emit(key=f'{cid}:{nid}', role='principal' if human else 'assistant', text=text,
                     author=principal_author if human else ASSISTANT,
                     src={'path': str(path.resolve()), 'member': member, 'conversation': cid, 'node': nid,
                          'message': m.get('id')},
                     source_ts=ts, session=f'chatgpt:{cid}', title=conv.get('title') or None,
                     branch='main' if nid in main else 'alt', model=meta.get('model_slug'))
    run.stats['conversations'] = len(convs)
    return run.finish(cur.watermark())
