"""exo command line.

  exo init <id> [--from-template [PATH]] [--interview]   create a principal store
  exo interview                            ask the next onboarding questions
  exo import-store <path> [--id ID]        adopt an existing store (copied)
  exo config [get|set|roles ...]           read or change config.json; `roles` shows routes
  exo models [--remote PROVIDER]           providers, keys found (yes/no), route per role
  exo decide QUESTION --options a,b,c      ask the decision model; probabilities and route
  exo serve | exo chat [--resume ID]       run the exod server; chat with it
  exo web [--port N ...]                   the command centre web client (needs a running `exo serve`)
  exo   (no command)                       the terminal client in this directory, same as `exo tui`;
                                           flags pass through: exo --resume, exo --seat NAME, exo --no-intro
  exo resume [flags]                       reopen the last session; same as `exo --resume`
  exo tui [--resume] [--session ID] ...     the terminal client (pip install 'exo[tui]')
  exo update [--dry-run]                   upgrade Exo in place, print old -> new, quick self-check
  exo --version                            package version, plus the git sha for a checkout install
  exo trace [SESSION] [--last N] [--json]  how the harness performed; anomaly flags (D-025)
  exo memory [verify|threads]              each agent's one log: check the hash chain, list threads (D-028)
  exo findings [add TITLE|fix ID --in SHA|export --md]  the harness's findings about itself
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
from .models_cmd import register as register_models, cmd_models
from .providers_cmd import register as register_providers
from .decide_cmd import register as register_decide, cmd_decide
from .trace_cmd import register as register_trace, cmd_trace, cmd_findings
from .memory_cmd import register as register_memory, cmd_memory


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

def cmd_wire(a) -> int:
    """Agent wire packets (docs/AGENT-WIRE.md): `render` prints plain English, `check` prints diagnostics."""
    from .wire import WireError, check_line, decode_any, render

    lines = [a.text] if a.text else sys.stdin.read().splitlines()
    bad = 0
    for line in (ln.strip() for ln in lines):
        if not line:
            continue
        try:
            packet, how = decode_any(line)
        except WireError as exc:
            bad += 1
            print('invalid: ' + '; '.join(exc.errors))
            continue
        if a.action == 'render':
            print(render(packet) + ('' if how == 'line' else f'\n(read as {how})') + '\n')
        else:
            errs = check_line(line) if how == 'line' or not line.startswith('{') else []
            print('ok' if how == 'line' else f'ok after {how}: {"; ".join(errs) or "JSON fallback form"}')
    return 1 if bad else 0


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
    register_models(sp, common)
    register_providers(sp, common)
    register_decide(sp, common)
    register_trace(sp, common)
    register_memory(sp, common)
    p = sp.add_parser('web', add_help=False, help='serve the command centre web client (exo-command)')
    p.add_argument('rest', nargs=argparse.REMAINDER, help='passed to exo-command: --socket, --url, --port ...')
    sp.add_parser('resume', add_help=False, help='reopen the last session in the terminal client (exo --resume)')
    p = sp.add_parser('tui', add_help=False, help='the terminal client (Textual); runs the core in-process if no exod')
    p.add_argument('rest', nargs=argparse.REMAINDER, help='passed to exo_tui: --resume, --session, --seat ...')
    sp.add_parser('update', add_help=False, help='upgrade Exo in place; print old -> new; quick self-check')
    p = sp.add_parser('wire', help='read agent wire packets (SYM-2P briefs and receipts) in plain English')
    p.add_argument('action', choices=['render', 'check'])
    p.add_argument('text', nargs='?', help='one packet line or JSON form; stdin (one per line) if absent')
    return ap


def cmd_web(a) -> int:
    """Run exo-command's main(). Its package lives in the plugins folder, which is not on sys.path."""
    dirs = [Path(d) for d in os.environ.get('EXO_PLUGIN_DIRS', '').split(os.pathsep) if d]
    dirs += [exo_home() / 'plugins', Path(__file__).resolve().parents[2] / 'plugins']
    for d in dirs:
        if (d / 'exo-command' / 'exo_command' / '__init__.py').is_file():
            sys.path.insert(0, str(d / 'exo-command'))
            break
    try:
        from exo_command.__main__ import main as web_main
    except ImportError:
        sys.exit('exo web: the exo-command plugin was not found (set EXO_PLUGIN_DIRS to its parent folder)')
    return web_main(a.rest)


def cmd_tui(rest: list[str]) -> int:
    """Run exo_tui.main(). Installed with `exo[tui]`, or found in the source tree's clients/tui."""
    from exo.core.tools.builtin.shell import raise_nofile_limit
    raise_nofile_limit()  # the in-process core runs tools; macOS defaults to 256 open files
    try:
        import exo_tui
    except ImportError:
        src = Path(__file__).resolve().parents[2] / 'clients' / 'tui'
        if not (src / 'exo_tui').is_dir():
            sys.exit("exo tui: the terminal client is not installed (pip install 'exo[tui]')")
        sys.path.insert(0, str(src))
        try:
            import exo_tui
        except ImportError as exc:
            sys.exit(f"exo tui: {exc} (pip install 'exo[tui]')")
    return exo_tui.main(rest) or 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == '--version':
        from .update_cmd import version_string
        print(f'exo {version_string()}')
        return 0
    # Bare `exo`, or a first word that is a flag (not -h/--help/--version): the terminal client, like `hermes`.
    if not args or (args[0].startswith('-') and args[0] not in ('-h', '--help')):
        return cmd_tui(args)
    if args[0] == 'tui':
        return cmd_tui(args[1:])
    if args[0] == 'resume':  # `exo resume [flags]` = `exo --resume [flags]`
        return cmd_tui(['--resume', *args[1:]])
    if args[0] == 'update':
        from .update_cmd import cmd_update
        return cmd_update(args[1:])
    if args and args[0] == 'web':  # argparse's REMAINDER drops a leading option such as --help
        return cmd_web(argparse.Namespace(rest=args[1:]))
    if args and args[0] == 'providers':  # its own quiet parser: an error must never echo a pasted key
        from .providers_cmd import main as providers_main
        return providers_main(args[1:])
    a = build_parser().parse_args(argv)
    if a.cmd == 'init':
        return onboard_init(a)
    if a.cmd == 'import-store':
        return cmd_import_store(a)
    if a.cmd == 'config':
        return cmd_config(a)
    if a.cmd == 'models':
        return cmd_models(a)
    if a.cmd == 'decide':
        return cmd_decide(a)
    if a.cmd == 'trace':
        return cmd_trace(a)
    if a.cmd == 'memory':
        return cmd_memory(a)
    if a.cmd == 'findings':
        return cmd_findings(a)
    if a.cmd == 'upstream':
        return cmd_upstream(a)
    if a.cmd in ('serve', 'chat'):
        return a.func(a)
    if a.cmd == 'wire':
        return cmd_wire(a)
    if a.cmd == 'keygen':
        return cmd_keygen(a)
    store = resolve_store(a)
    return globals()['cmd_' + a.cmd](a, store)


if __name__ == '__main__':
    sys.exit(main())
