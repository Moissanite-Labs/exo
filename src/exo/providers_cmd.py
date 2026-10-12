"""`exo providers`: set up model providers (D-034).

    exo providers                       every provider: status, where its key is (never the value), models
    exo providers add NAME              store a key: prompted with no echo, never taken from the command line
    exo providers test NAME             one tiny live call: latency and an error class
    exo providers remove NAME           delete the key from the Keychain, Secret Service and keys.env
    exo providers default REF           make provider:model the default (first route of the main role)
"""
from __future__ import annotations

import argparse
import getpass
import sys

from . import providers_setup as ps

NO_ARGV_KEY = ("never pass a key on the command line: it lands in shell history and in `ps`. "
               "Run `exo providers add {name}` and paste the key at the prompt. "
               "If you just typed one, remove it from your shell history.")


class QuietParser(argparse.ArgumentParser):
    """An argparse parser whose errors never repeat the command line (it could hold a pasted key)."""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: bad arguments (not repeated here, in case one was a key). "
                     "See --help.\n")


def register(sp, common) -> None:
    """Listed in `exo --help`; `cli.main` sends `exo providers ...` to ``main`` before the shared parser runs."""
    sp.add_parser('providers', help='set up model providers: status, add or remove a key, test, default model')


def build_parser() -> argparse.ArgumentParser:
    p = QuietParser(prog='exo providers', description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='providers_cmd', parser_class=QuietParser)
    q = sub.add_parser('add', help='store a key (prompted, no echo)')
    q.add_argument('name')
    q.add_argument('extra', nargs='*', help=argparse.SUPPRESS)
    q.add_argument('--store', choices=ps.STORES, help='where to keep it (default: Keychain on macOS, '
                   'Secret Service on Linux when secret-tool is present, else keys.env)')
    q.add_argument('--no-test', action='store_true', help='do not run a live test after storing')
    q = sub.add_parser('test', help='one tiny live call to the provider')
    q.add_argument('name')
    q = sub.add_parser('remove', help="delete the provider's key")
    q.add_argument('name')
    q.add_argument('--yes', action='store_true', help='do not ask to confirm')
    q = sub.add_parser('default', help='set the default model: provider:model')
    q.add_argument('ref')
    q.add_argument('--role', default='main')
    return p


def _test_line(r: dict) -> str:
    if r['ok']:
        models = f", {r['models']} models" if r.get('models') is not None else ''
        lat = f" in {r['latency_ms']} ms" if r.get('latency_ms') is not None else ''
        return f"{r['provider']}: ok{lat}{models}"
    return f"{r['provider']}: failed ({r['error_class']}): {r['detail']}"


def _safe_name(name: str | None) -> str:
    """``name`` if it is a known provider, else a placeholder: a pasted key must never be echoed."""
    try:
        return name if name and name in ps.build_router().providers else 'NAME'
    except Exception:
        return 'NAME'


def main(argv: list[str]) -> int:
    a, unknown = build_parser().parse_known_args(argv)
    if unknown:
        print('exo providers: unknown options (not repeated). ' + NO_ARGV_KEY.format(name=_safe_name(getattr(a, 'name', None))),
              file=sys.stderr)
        return 2
    return cmd_providers(a)


def cmd_providers(a) -> int:
    action = getattr(a, 'providers_cmd', None)
    try:
        if action is None:
            print(ps.render(ps.rows()))
            return 0
        if action == 'add':
            if a.extra:
                print('exo providers: ' + NO_ARGV_KEY.format(name=_safe_name(a.name)), file=sys.stderr)
                return 2
            router = ps.build_router()
            spec = ps._spec(router, a.name)
            names = ps.key_vars(spec)
            if not names:
                print(f'exo providers: {a.name} takes no API key ({spec.get("transport", "openai")}'
                      f'{", local" if spec.get("local") else ""})', file=sys.stderr)
                return 2
            if not sys.stdin.isatty():
                print('exo providers add: needs a terminal so the key can be typed without echo. '
                      'Use the TUI (/providers) or the web client (Providers) instead.', file=sys.stderr)
                return 2
            value = getpass.getpass(f'{names[0]} for {a.name} (input hidden): ')
            out = ps.set_key(a.name, value, router=router, store=a.store)
            value = ''  # drop the reference early
            print(f"stored {out['var']} in {out['stored_in']}")
            if out['shadowed_by_env']:
                print(f"note: the environment variable {out['var']} is set and wins over the stored key")
            if not a.no_test:
                print(_test_line(ps.test_provider(a.name, router=ps.build_router())))
            return 0
        if action == 'test':
            r = ps.test_provider(a.name)
            print(_test_line(r))
            return 0 if r['ok'] else 1
        if action == 'remove':
            if not a.yes and sys.stdin.isatty():
                if input(f'remove the key for {a.name}? [y/N] ').strip().lower() not in ('y', 'yes'):
                    print('kept')
                    return 1
            out = ps.remove_key(a.name)
            print(f"removed from: {', '.join(out['removed_from']) or 'nothing (no stored key)'}")
            if out['still_in_env']:
                print(f"still set in the environment: {', '.join(out['still_in_env'])} (unset it in your shell)")
            return 0
        if action == 'default':
            try:
                routes = ps.set_default(a.ref, role=a.role)
            except ValueError:  # its text repeats the ref, which could be a pasted key
                print('exo providers: not a usable model ref: use provider:model with a known provider '
                      'that may serve that role (see `exo providers` and `exo models`)', file=sys.stderr)
                return 1
            print(f"{a.role}: {' > '.join(routes)} (saved to config.json; applies to new sessions)")
            return 0
    except (ps.SetupError, ValueError) as e:
        print(f'exo providers: {e}', file=sys.stderr)
        return 1
    return 2
