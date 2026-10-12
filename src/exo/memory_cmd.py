"""`exo memory` (D-028): look at each agent's one log.

    exo memory [verify] [--agent NAME] [--json]   check each log: positions with no gap, hash chain intact
    exo memory threads [--agent NAME] [--json]    the agent's threads, newest first
"""
from __future__ import annotations

import json
import sys


def register(sp, common) -> None:
    p = sp.add_parser('memory', help="each agent's one log: verify it, list its threads")
    p.add_argument('action', nargs='?', default='verify', choices=['verify', 'threads'])
    p.add_argument('--agent', help='one agent (seat name, or default); all when omitted')
    p.add_argument('--json', action='store_true')


def cmd_memory(a) -> int:
    from .trace_cmd import _store

    store = _store()
    agents = [a.agent] if a.agent else store.agents()
    if a.action == 'threads':
        rows = [i for i in store.list_sessions(500) if i.agent in agents and i.kind != 'subagent']
        if a.json:
            print(json.dumps([{'id': i.id, 'agent': i.agent, 'kind': i.kind, 'title': i.title,
                               'messages': i.message_count, 'updated': i.updated} for i in rows], indent=2))
        for i in [] if a.json else rows:
            print(f"{i.updated[:16]}  {i.agent:<12} {i.kind:<9} {i.message_count:>4}  {i.id}  {i.title[:50]}")
        return 0
    out = {name: store.verify_log(name) for name in agents}
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        for name, r in out.items():
            print(f"{name}: {r['entries']} entries, " + ('ok' if r['ok'] else f"BROKEN: {r['error']}"))
        if not out:
            print('no agent logs yet')
    return 0 if all(r['ok'] for r in out.values()) else 1
