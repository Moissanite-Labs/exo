"""`exo ingest <kind> <path> [--since TS]`.

`exo.cli` lists `ingest` in its `LAZY` table. It imports this module, calls `register(sp, common)` and
runs `cmd_ingest` only when `exo ingest` runs.

The ledger is the active store's ledger (`--principal` / `--root`), or `--ledger PATH`.
Events are signed by the ingest seat key (`--as`, default `ingest`, key at
$EXO_HOME/keys/<name>.key). Principal rows are authored `--author` (default: the principal id).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..store import exo_home
from . import ADAPTERS, ALIASES, ingest


def register(sp, common) -> None:
    p = sp.add_parser('ingest', parents=[common], help='import conversations into the ledger')
    p.add_argument('kind', choices=sorted(ADAPTERS) + sorted(ALIASES))
    p.add_argument('path', help='source file, directory or export zip (read-only)')
    p.add_argument('--since', help='only rows at or after this time (ISO date or timestamp)')
    p.add_argument('--author', help="principal's author name (default: the principal id)")
    p.add_argument('--as', dest='seat', default='ingest', help='signing seat name (default: ingest)')
    p.add_argument('--key', help='signing key path (default: $EXO_HOME/keys/<seat>.key)')
    p.add_argument('--ledger', help='ledger path (default: <store>/ledger.sqlite)')


def _key_path(a) -> Path:
    if a.key:
        return Path(a.key)
    return exo_home() / 'keys' / f'{a.seat}.key'


def cmd_ingest(a, store=None) -> int:
    from exo.ledger_authority.factory import configured, open_writable_ledger
    kp = _key_path(a)
    if not configured() and not kp.exists():
        sys.exit(f'no key at {kp}; run: exo keygen {a.seat}')
    if a.ledger:
        ledger_path = Path(a.ledger)
    elif store is not None:
        ledger_path = store.ledger_path
    else:
        sys.exit('no ledger: pass --ledger PATH, --principal ID or --root DIR')
    author = a.author or getattr(a, 'principal', None) or os.environ.get('EXO_PRINCIPAL') \
        or (store.root.name if store is not None else None)
    if not author:
        sys.exit('no principal author: pass --author NAME')
    if not Path(a.path).expanduser().exists():
        sys.exit(f'no source at {a.path}')
    with open_writable_ledger(ledger_path, a.seat, kp) as led:
        try:
            stats = ingest(a.kind, a.path, led, author, since=a.since)
        except (ValueError, FileNotFoundError, KeyError) as e:
            sys.exit(str(e))
    try:
        os.chmod(ledger_path, 0o600)
    except OSError:
        pass
    print(json.dumps(stats, sort_keys=True))
    return 0
