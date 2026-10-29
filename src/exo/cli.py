"""exo command line.

  exo init <id> [--from-template [PATH]] [--interview]   create a principal store
  exo interview                            ask the next onboarding questions
  exo import-store <path> [--id ID]        adopt an existing store (copied)
  exo config [get|set|roles ...]           read or change config.json; `roles` shows routes
  exo models [--remote PROVIDER]           providers, keys found (yes/no), route per role
  exo decide QUESTION --options a,b,c      ask the decision model; probabilities and route
  exo serve | exo chat [--resume ID]       run the exod server; chat with it
  exo web [--port N ...]                   the command centre web client (starts the shared core if none is running)
  exo   (no command)                       the terminal client in this directory, same as `exo tui`;
                                           flags pass through: exo --resume, exo --seat NAME, exo --no-intro
  exo --skip-permissions                   the terminal client with permissions bypass for this run of the
                                           core: no approval asks except Exo's own records and secrets (D-065)
  exo resume [flags]                       reopen the last session; same as `exo --resume`
  exo tui [--resume] [--session ID] ...     the terminal client (pip install 'exo[tui]')
  exo update [--dry-run]                   upgrade Exo in place, print old -> new, quick self-check
  exo --version                            package version, plus the git sha for a checkout install
  exo trace [SESSION] [--last N] [--json]  how the harness performed; anomaly flags (D-025)
  exo memory [verify|threads]              each agent's one log and the signed ledger: check them, list threads
                                           (D-028, D-040)
  exo taste [list|export-house|review]     the taste layers with a source on every item; build and
                                           review the house taste pack (D-044)
  exo findings [add TITLE|fix ID --in SHA|export --md|backlog [--limit N]]
                                           the harness's findings about itself
  exo upstream report|mark|check|merge     track lifted Hermes modules
  exo find <words...> [-n 8]               ranked nodes
  exo get <id> [--body-only]               print one node
  exo ctx <words...> [--budget 1500]       best nodes packed under a token budget
  exo links <id>                           outgoing and incoming typed edges
  exo lint                                 schema, dangling links, orphans, leaks
  exo graph                                write graph/graph.json
  exo index                                refresh the node table in INDEX.md
  exo log append <kind> ...                append a signed event
  exo log verify                           check chain, signatures against pinned keys, head anchor (D-040)
  exo log anchor [--force]                 anchor the current head (a store made before D-040)
  exo log tail [-n 10] [--kind K]          last events
  exo keygen <name> [--out PATH]           create an Ed25519 key (0600); pinned when made in $EXO_HOME/keys
  exo keys [list|pin NAME PUB|principal ID] the key registry: pinned signers, bus seats, principal ids (D-040)

The active principal comes from --principal, else $EXO_PRINCIPAL. --root points at a
store directory directly. Stores live under $EXO_HOME/principals/<id> (default ~/.exo).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import display_version

if TYPE_CHECKING:
    from .store import PrincipalStore

STOP_HELP = 'stop the background core that exo web / exo serve started'

# Commands that other modules add, in `exo --help` order: name -> (module, handler, help line).
# A module is imported only when its command runs (#260), so `exo --version`, `exo --help` and bare
# `exo` load none of them. The help line repeats the one the module's register() gives, so
# `exo --help` needs no import. tests/test_cli_lazy.py checks that the two agree.
LAZY = {
    'ingest': ('ingest.cli', 'cmd_ingest', 'import conversations into the ledger'),
    'ask': ('ask', 'cmd_ask', 'question queue and interruption budget'),
    'eval': ('eval', 'cmd_eval', 'n=1 evaluation report'),
    'brief': ('brief', 'cmd_brief', 'morning brief (Markdown and HTML)'),
    'consolidate': ('consolidate', 'cmd_consolidate', 'turn new ledger events into belief proposals'),
    'proposals': ('consolidate', 'cmd_proposals', 'review belief proposals'),
    'interview': ('onboard', 'cmd_interview', 'ask the onboarding questions for an existing store'),
    'import-store': ('onboard', 'cmd_import_store', 'adopt an existing hafiz-format store as a principal (copy)'),
    'config': ('onboard', 'cmd_config', 'read and write $EXO_HOME/config.json'),
    'upstream': ('upstream', 'cmd_upstream', 'track lifted upstream code (PROVENANCE.toml)'),
    'serve': ('cli_chat', 'cmd_serve', 'run exod, the core protocol server'),
    'chat': ('cli_chat', 'cmd_chat', 'chat with exod from the terminal'),
    'models': ('models_cmd', 'cmd_models', 'configured providers, keys found, and the route for each role'),
    'providers': ('providers_cmd', 'cmd_providers',
                  'set up model providers: status, add or remove a key, test, default model'),
    'decide': ('decide_cmd', 'cmd_decide',
               'ask the decision model one question; prints probabilities and the route'),
    'trace': ('trace_cmd', 'cmd_trace', 'how the harness performed in a session: per turn timing, routes, flags'),
    'findings': ('trace_cmd', 'cmd_findings', 'what the harness found wrong with itself'),
    'memory': ('memory_cmd', 'cmd_memory', "each agent's one log and the signed ledger: verify, threads"),
    'taste': ('baseline.cli', 'cmd_taste', 'the taste layers: house, learned, pins (D-044)'),
}
# Commands whose handler also takes the principal store.
STORE_CMDS = {'find', 'get', 'ctx', 'links', 'lint', 'graph', 'index', 'log',
              'ingest', 'ask', 'eval', 'brief', 'consolidate', 'proposals', 'interview'}

# Names this module imported at the top before #260, for code that still reads them from here.
_MOVED = {'crypto': 'crypto', 'KINDS': 'ledger', 'Ledger': 'ledger', 'LedgerError': 'ledger',
          'PrincipalStore': 'store', 'exo_home': 'store', 'principal_root': 'store',
          'add_init_args': 'onboard', 'onboard_init': 'onboard', 'register_ingest': 'ingest.cli',
          'register_ask': 'ask', 'register_eval': 'eval', 'register_brief': 'brief',
          'register_consolidate': 'consolidate', 'register_onboard': 'onboard', 'register_upstream': 'upstream',
          'register_chat': 'cli_chat', 'register_models': 'models_cmd', 'register_providers': 'providers_cmd',
          'register_decide': 'decide_cmd', 'register_trace': 'trace_cmd', 'register_memory': 'memory_cmd',
          'register_taste': 'baseline.cli', **{handler: mod for mod, handler, _ in LAZY.values()}}


def __getattr__(name: str) -> Any:
    """A name from `_MOVED`, imported on first use."""
    if name not in _MOVED:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    mod = import_module(f'.{_MOVED[name]}', __package__)
    if name == 'crypto':
        return mod
    if name.startswith('register_'):
        return mod.register
    return getattr(mod, 'cmd_init' if name == 'onboard_init' else name)


def key_path(name: str) -> Path:
    from .store import exo_home
    return exo_home() / 'keys' / f'{name}.key'


def resolve_store(a) -> PrincipalStore:
    from .store import PrincipalStore
    if a.root:
        store = PrincipalStore(a.root)
    else:
        pid = a.principal or os.environ.get('EXO_PRINCIPAL')
        if not pid:
            try:
                from .config import Config
                pid = Config.load().principal
            except Exception:
                pid = None
        if not pid:
            sys.exit('no active principal: pass --principal <id> or --root <dir>, set EXO_PRINCIPAL, '
                     'or `exo config set principal <id>`')
        store = PrincipalStore.for_principal(pid)
    if not store.exists():
        sys.exit(f'no principal store at {store.root}; run: exo init <id>')
    return store


# --- commands -----------------------------------------------------------------

def cmd_init(a) -> int:
    from .ledger import Ledger
    from .store import PrincipalStore, principal_root
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
    from . import crypto
    out = Path(a.out) if a.out else key_path(a.name)
    if out.exists():
        sys.exit(f'{out} exists; refusing to overwrite')
    key = crypto.keygen()
    crypto.save_key(key, out)
    print(f'key {out} (0600)\npub {crypto.pubkey_b64(key)}')
    return 0


def cmd_keys(a) -> int:
    """`exo keys [list]`, `exo keys pin NAME PUB [--seat|--peer] [--replace]`, `exo keys principal ID` (D-040)."""
    from . import crypto
    from .core.seats import ID_RE as SEAT_ID
    from .keyring import KeyRegistry
    reg = KeyRegistry.load()
    if a.keys_cmd == 'pin':
        pub = Path(a.pub).read_text().strip() if Path(a.pub).is_file() else a.pub
        try:
            crypto.unb64(pub)
        except ValueError:
            sys.exit('exo keys pin: PUB must be a base64 public key or a .pub file')
        if a.seat and a.peer:
            sys.exit('exo keys pin: --seat is a bus seat and --peer a peer seat of another home; pass one')
        if a.peer and not SEAT_ID.fullmatch(a.name):
            sys.exit(f'exo keys pin: {a.name!r} is not a seat name (lowercase letters, digits, - and _)')
        if a.seat:
            out = reg.pin_seat(a.name, pub, replace=a.replace)
        else:
            out = reg.pin(a.name, pub, replace=a.replace, peer=a.peer)
        print(f'{a.name}: {out}' + (' (use --replace to change a pinned key)' if out in ('conflict', 'changed') else ''))
        return 0 if out in ('pinned', 'known', 'match') else 1
    if a.keys_cmd == 'principal':
        reg.add_principal(a.name)
        print(f'principal ids: {", ".join(KeyRegistry.load().principals)}')
        return 0
    if getattr(a, 'json', False):
        print(json.dumps(reg.to_dict(), indent=1, sort_keys=True))
        return 0
    print(f'key registry {reg.path}')
    for name, pubs in sorted(reg.keys.items()):
        print(f'  key   {name:<20} {", ".join(pubs)}')
    for seat, pub in sorted(reg.seats.items()):
        print(f'  seat  {seat:<20} {pub}')
    for seat, pubs in sorted(reg.peers.items()):
        print(f'  peer  {seat:<20} {", ".join(pubs)}')
    print(f'  principal ids: {", ".join(reg.principals) or "(none)"}')
    return 0


def cmd_log(a, store) -> int:
    from .ledger import Ledger, LedgerError
    from .ledger_authority.factory import configured
    if a.log_cmd == 'verify' and configured():
        from .ledger_authority.verify import verify_configured
        res = verify_configured(store.ledger_path)
        print(json.dumps(res))
        return 0 if res['ok'] else 1
    if a.log_cmd == 'append':
        from .ledger_authority.factory import open_writable_ledger
        kp = Path(a.key) if a.key else key_path(a.author)
        if not configured() and not kp.exists():
            sys.exit(f'no key at {kp}; run: exo keygen {a.author}')
        if a.body is not None:
            try:
                body = json.loads(a.body)
            except json.JSONDecodeError as e:
                sys.exit(f'--body is not JSON: {e}')
        else:
            body = {'text': a.text if a.text is not None else sys.stdin.read()}
        with open_writable_ledger(store.ledger_path, a.author, kp) as led:
            try:
                ev = led.append(a.kind, body, a.author, session=a.session, refs=a.ref or ())
            except LedgerError as e:
                sys.exit(str(e))
        print(f"{ev['seq']} {ev['id']}")
        return 0
    with Ledger(store.ledger_path) as led:
        if a.log_cmd == 'verify':
            from .keyring import KeyRegistry
            res = led.verify(trusted=KeyRegistry.load())  # D-040: only pinned keys count
            print(json.dumps(res))
            return 0 if res['ok'] else 1
        if a.log_cmd == 'anchor':
            try:
                print(json.dumps(led.anchor(force=a.force)))
            except LedgerError as e:
                print(e, file=sys.stderr)
                return 1
            return 0
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


def build_parser(cmd: str | None = None) -> argparse.ArgumentParser:
    """The `exo` parser. Given the command word, only that command gets its full parser and only its module is
    imported. The others keep their name and help line, so usage, errors and `exo --help` read the same (#260).
    With no command word, every command is built and every module imported."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--principal', help='principal id (default: $EXO_PRINCIPAL)')
    common.add_argument('--root', help='store directory; overrides --principal')

    ap = argparse.ArgumentParser(prog='exo', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', action='version', version=f'exo {display_version()}')
    sp = ap.add_subparsers(dest='cmd', required=True)

    p = sp.add_parser('init', parents=[common], help='create an empty principal store')
    p.add_argument('id')
    p.add_argument('--from-template', nargs='?', const='', default='', metavar='PATH',
                   help='template directory (default: the bundled empty store)')
    if cmd in (None, 'init'):
        from .onboard import add_init_args
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

    p = sp.add_parser('keys', help='pinned keys: the ledger signers, bus seats and principal ids Exo trusts (D-040)')
    kp = p.add_subparsers(dest='keys_cmd')
    q = kp.add_parser('list')
    q.add_argument('--json', action='store_true')
    q = kp.add_parser('pin', help='pin a public key under a name (or a bus seat with --seat)')
    q.add_argument('name')
    q.add_argument('pub', help='base64 public key, or a .pub file')
    q.add_argument('--seat', action='store_true', help='NAME is a bus seat')
    q.add_argument('--peer', action='store_true', help='NAME is a peer seat of another home: checks its peer messages, '
                   'never signs a ledger')
    q.add_argument('--replace', action='store_true', help='replace the key already pinned for NAME')
    q = kp.add_parser('principal', help='mark a ledger author id as the principal')
    q.add_argument('name')

    p = sp.add_parser('log', help='the event ledger')
    if cmd in (None, 'log'):  # `log append` lists the ledger kinds
        from .ledger import KINDS
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
        q = lp.add_parser('anchor', parents=[common], help='write the current head to its anchor files (a store from before D-040)')
        q.add_argument('--force', action='store_true', help='replace an anchor that disagrees with the ledger')
        q = lp.add_parser('tail', parents=[common])
        q.add_argument('-n', type=int, default=10)
        q.add_argument('--kind')
    want = LAZY[cmd][0] if cmd in LAZY else None
    for name, (mod, _, help_line) in LAZY.items():
        if name in sp.choices:  # added with an earlier command by the same register()
            continue
        if cmd is None or mod == want:
            import_module(f'.{mod}', __package__).register(sp, common)
        else:
            sp.add_parser(name, help=help_line)
    p = sp.add_parser('web', add_help=False, help='serve the command centre web client (exo-command)')
    p.add_argument('rest', nargs=argparse.REMAINDER, help='passed to exo-command: --socket, --url, --port ...')
    sp.add_parser('stop', add_help=False, help=STOP_HELP)
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
    from .store import exo_home
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
    if args[0] == 'stop':  # -h prints help and an unknown word is an error: neither stops the core
        argparse.ArgumentParser(prog='exo stop', description=STOP_HELP).parse_args(args[1:])
        from .ensure_core import stop_core
        print(stop_core())
        return 0
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
    a = build_parser(args[0]).parse_args(argv)
    if a.cmd == 'init':
        from .onboard import cmd_init as onboard_init
        return onboard_init(a)
    if a.cmd in LAZY:
        mod, handler, _ = LAZY[a.cmd]
        fn = getattr(import_module(f'.{mod}', __package__), handler)
    else:
        fn = globals()['cmd_' + a.cmd]
    return fn(a, resolve_store(a)) if a.cmd in STORE_CMDS else fn(a)


if __name__ == '__main__':
    sys.exit(main())
