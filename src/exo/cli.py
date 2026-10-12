"""exo command line.

  exo init <id> [--from-template [PATH]] [--interview]   create a principal store
  exo interview                            ask the next onboarding questions
  exo import-store <path> [--id ID]        adopt an existing store (copied)
  exo config [get|set ...]                 read or change config.json
  exo serve | exo chat [--resume ID]       run the exod server; chat with it
  exo upstream report|mark|check|merge     track lifted Hermes modules
  exo find <words...> [-n 8]               ranked nodes
  exo get <id> [--body-only]               print one node
  exo ctx <words...> [--budget 1500]       best nodes packed under a token budget
  exo links <id>                           outgoing and incoming typed edges
  exo lint                                 schema, dangling links, orphans, leaks
  exo graph                                write graph/graph.json
  exo index                                refresh the node table in INDEX.md
  exo log append <kind> ...                append a signed event
  exo log verify                           check chain and signatures
  exo log tail [-n 10] [--kind K]          last events
  exo keygen <name> [--out PATH]           create an Ed25519 key (0600)

The active principal comes from --principal, else $EXO_PRINCIPAL. --root points at a
store directory directly. Stores live under $EXO_HOME/principals/<id> (default ~/.exo).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, crypto
from .ledger import KINDS, Ledger, LedgerError
from .store import PrincipalStore, exo_home, principal_root
from .ingest.cli import register as register_ingest, cmd_ingest  # noqa: F401  (dispatched via globals)
from .ask import register as register_ask, cmd_ask  # noqa: F401
from .eval import register as register_eval, cmd_eval  # noqa: F401
from .brief import register as register_brief, cmd_brief  # noqa: F401
from .consolidate import register as register_consolidate, cmd_consolidate, cmd_proposals  # noqa: F401
from .onboard import (register as register_onboard, add_init_args, cmd_init as onboard_init,  # noqa: F401
                      cmd_interview, cmd_import_store, cmd_config)
from .upstream import register as register_upstream, cmd_upstream  # noqa: F401
from .cli_chat import register as register_chat


def key_path(name: str) -> Path:
    return exo_home() / 'keys' / f'{name}.key'


def resolve_store(a) -> PrincipalStore:
    if a.root:
        store = PrincipalStore(a.root)
    else:
        pid = a.principal or os.environ.get('EXO_PRINCIPAL')
        if not pid:
            sys.exit('no active principal: pass --principal <id> or --root <dir>, or set EXO_PRINCIPAL')
        store = PrincipalStore.for_principal(pid)
    if not store.exists():
        sys.exit(f'no principal store at {store.root}; run: exo init <id>')
    return store


# --- commands -----------------------------------------------------------------

def cmd_init(a) -> int:
    root = Path(a.root) if a.root else principal_root(a.id)
    template = a.from_template or None
    try:
        store = PrincipalStore.create(root, template=template)
    except (FileExistsError, FileNotFoundError) as e:
        sys.exit(str(e))
    Ledger(store.ledger_path).close()
    os.chmod(store.ledger_path, 0o600)
    report = store.lint()
    print(f'created principal store {a.id} at {store.root}')
    print(report.render().splitlines()[-1])
    return 0


def cmd_find(a, store) -> int:
    for i, s in store.find(a.words, n=a.n):
        print(f"{i:38s} {s:5.1f}  {(store.nodes[i].get('summary') or '')[:110]}")
    return 0


def cmd_get(a, store) -> int:
    if a.id not in store.nodes:
        sys.exit(f'no node {a.id}; try: exo find {a.id.split("/")[-1]}')
    print(store.render(a.id, a.body_only))
    return 0


def cmd_ctx(a, store) -> int:
    print(store.ctx(a.words, budget=a.budget).text)
    return 0


def cmd_links(a, store) -> int:
    links = store.links(a.id)
    print('out:')
    for r, t in links['out']:
        print(f'  -{r}-> {t}')
    print('in:')
    for r, s in links['in']:
        print(f'  {s} -{r}->')
    return 0


def cmd_lint(a, store) -> int:
    report = store.lint()
    print(report.render())
    return 0 if report.ok else 1


def cmd_graph(a, store) -> int:
    g = store.graph()
    print(f"graph: {len(g['nodes'])} nodes, {len(g['edges'])} edges -> graph/graph.json")
    return 0


def cmd_index(a, store) -> int:
    print(f'index: {store.index()} nodes listed')
    return 0


def cmd_keygen(a) -> int:
    out = Path(a.out) if a.out else key_path(a.name)
    if out.exists():
        sys.exit(f'{out} exists; refusing to overwrite')
    key = crypto.keygen()
    crypto.save_key(key, out)
    print(f'key {out} (0600)\npub {crypto.pubkey_b64(key)}')
    return 0


def cmd_log(a, store) -> int:
    if a.log_cmd == 'append':
        kp = Path(a.key) if a.key else key_path(a.author)
        if not kp.exists():
            sys.exit(f'no key at {kp}; run: exo keygen {a.author}')
        if a.body is not None:
            try:
                body = json.loads(a.body)
            except json.JSONDecodeError as e:
                sys.exit(f'--body is not JSON: {e}')
        else:
            body = {'text': a.text if a.text is not None else sys.stdin.read()}
        with Ledger(store.ledger_path, crypto.Signer.from_file(a.author, kp)) as led:
            try:
                ev = led.append(a.kind, body, a.author, session=a.session, refs=a.ref or ())
            except LedgerError as e:
                sys.exit(str(e))
        print(f"{ev['seq']} {ev['id']}")
        return 0
    with Ledger(store.ledger_path) as led:
        if a.log_cmd == 'verify':
            res = led.verify()
            print(json.dumps(res))
            return 0 if res['ok'] else 1
        events = list(led.iter(kind=a.kind))[-a.n:]
        for ev in events:
            body = json.dumps(ev['body'], ensure_ascii=False)
            print(f"{ev['seq']:>5} {ev['ts']} {ev['author']:<14} {ev['kind']:<16} {ev['id'][:12]} {body[:100]}")
    return 0


# --- parser -------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--principal', help='principal id (default: $EXO_PRINCIPAL)')
    common.add_argument('--root', help='store directory; overrides --principal')

    ap = argparse.ArgumentParser(prog='exo', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', action='version', version=f'exo {__version__}')
    sp = ap.add_subparsers(dest='cmd', required=True)

    p = sp.add_parser('init', parents=[common], help='create an empty principal store')
    p.add_argument('id')
    p.add_argument('--from-template', nargs='?', const='', default='', metavar='PATH',
                   help='template directory (default: the bundled empty store)')
    add_init_args(p)

    p = sp.add_parser('find', parents=[common]); p.add_argument('words', nargs='+'); p.add_argument('-n', type=int, default=8)
    p = sp.add_parser('get', parents=[common]); p.add_argument('id'); p.add_argument('--body-only', action='store_true')
    p = sp.add_parser('ctx', parents=[common]); p.add_argument('words', nargs='+'); p.add_argument('--budget', type=int, default=1500)
    p = sp.add_parser('links', parents=[common]); p.add_argument('id')
    for c in ('lint', 'graph', 'index'):
        sp.add_parser(c, parents=[common])

    p = sp.add_parser('keygen', help='create an Ed25519 signing key')
    p.add_argument('name')
    p.add_argument('--out', help='key path (default: $EXO_HOME/keys/<name>.key)')

    p = sp.add_parser('log', help='the event ledger')
    lp = p.add_subparsers(dest='log_cmd', required=True)
    q = lp.add_parser('append', parents=[common])
    q.add_argument('kind', choices=sorted(KINDS))
    q.add_argument('--author', required=True)
    q.add_argument('--key', help='signing key (default: $EXO_HOME/keys/<author>.key)')
    g = q.add_mutually_exclusive_group()
    g.add_argument('--body', help='event body as a JSON object')
    g.add_argument('--text', help='shorthand for --body {"text": TEXT}; stdin if neither is given')
    q.add_argument('--session')
    q.add_argument('--ref', action='append', help='referenced event id (repeatable)')
    lp.add_parser('verify', parents=[common])
    q = lp.add_parser('tail', parents=[common])
    q.add_argument('-n', type=int, default=10)
    q.add_argument('--kind')
    register_ingest(sp, common)
    register_ask(sp, common)
    register_eval(sp, common)
    register_brief(sp, common)
    register_consolidate(sp, common)
    register_onboard(sp, common)
    register_upstream(sp, common)
    register_chat(sp, common)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == 'init':
        return onboard_init(a)
    if a.cmd == 'import-store':
        return cmd_import_store(a)
    if a.cmd == 'config':
        return cmd_config(a)
    if a.cmd == 'upstream':
        return cmd_upstream(a)
    if a.cmd in ('serve', 'chat'):
        return a.func(a)
    if a.cmd == 'keygen':
        return cmd_keygen(a)
    store = resolve_store(a)
    return globals()['cmd_' + a.cmd](a, store)


if __name__ == '__main__':
    sys.exit(main())
