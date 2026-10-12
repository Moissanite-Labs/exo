"""`exo memory` (D-028, D-040): check each agent's one log and the principal's signed ledger.

    exo memory [verify] [--agent NAME] [--json]   each agent log: positions with no gap, hash chain intact;
                                                  each principal ledger: chain, signatures against pinned keys,
                                                  head anchors and append-only triggers
    exo memory threads [--agent NAME] [--json]    the agent's threads, newest first
"""
from __future__ import annotations

import json
import os
import sys


def register(sp, common) -> None:
    p = sp.add_parser('memory', parents=[common], help="each agent's one log and the signed ledger: verify, threads")
    p.add_argument('action', nargs='?', default='verify', choices=['verify', 'threads'])
    p.add_argument('--agent', help='one agent (seat name, or default); all when omitted')
    p.add_argument('--json', action='store_true')


def _stores(a) -> list:
    """The principal stores to check: --root, --principal or $EXO_PRINCIPAL, else every store in this home."""
    from .store import PrincipalStore, exo_home
    if getattr(a, 'root', None):
        return [PrincipalStore(a.root)]
    pid = getattr(a, 'principal', None) or os.environ.get('EXO_PRINCIPAL')
    if pid:
        return [PrincipalStore.for_principal(pid)]
    d = exo_home() / 'principals'
    return [PrincipalStore(p) for p in sorted(d.iterdir()) if (p / 'ledger.sqlite').is_file()] if d.is_dir() else []


def verify_ledgers(a) -> dict[str, dict]:
    """Each store's ledger checked with the pinned key registry as `trusted` (D-040)."""
    from .keyring import KeyRegistry
    from .ledger import Ledger
    out: dict[str, dict] = {}
    stores = [s for s in _stores(a) if s.ledger_path.is_file()]
    if not stores:
        return out
    reg = KeyRegistry.load()
    for s in stores:
        with Ledger(s.ledger_path) as led:
            out[s.root.name] = led.verify(trusted=reg)
    return out


def cmd_memory(a) -> int:
    from .store import exo_home

    have_sessions = (exo_home() / 'sessions.sqlite').exists()
    if a.action == 'threads':
        from .trace_cmd import _store
        store = _store()
        agents = [a.agent] if a.agent else store.agents()
        rows = [i for i in store.list_sessions(500) if i.agent in agents and i.kind != 'subagent']
        if a.json:
            print(json.dumps([{'id': i.id, 'agent': i.agent, 'kind': i.kind, 'title': i.title,
                               'messages': i.message_count, 'updated': i.updated} for i in rows], indent=2))
        for i in [] if a.json else rows:
            print(f"{i.updated[:16]}  {i.agent:<12} {i.kind:<9} {i.message_count:>4}  {i.id}  {i.title[:50]}")
        return 0
    logs: dict[str, dict] = {}
    if have_sessions:
        from .trace_cmd import _store
        store = _store()
        agents = [a.agent] if a.agent else store.agents()
        logs = {name: store.verify_log(name) for name in agents}
    ledgers = verify_ledgers(a)
    if a.json:
        print(json.dumps({**logs, **({'ledgers': ledgers} if ledgers else {})}, indent=2))
    else:
        for name, r in logs.items():
            print(f"{name}: {r['entries']} entries, " + ('ok' if r['ok'] else f"BROKEN: {r['error']}"))
        if not logs:
            print('no agent logs yet')
        for name, r in ledgers.items():
            if r['ok']:
                note = '' if r.get('anchored') else ' (head not anchored yet: it will be on the next write)'
                print(f"ledger {name}: {r['checked']} events, ok{note}")
            else:
                where = f" at seq {r['seq']}" if 'seq' in r else ''
                print(f"ledger {name}: BROKEN{where}: {r['error']}")
    ok = all(r['ok'] for r in logs.values()) and all(r['ok'] for r in ledgers.values())
    if not logs and not ledgers and not have_sessions:
        print('nothing to verify', file=sys.stderr)
    return 0 if ok else 1
