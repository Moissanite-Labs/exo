"""Human-only credential setup. No secret argument and no secret-bearing protocol command."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys

from exo.core import github_auth as auth
from exo.core.models import keys
from exo import providers_setup
from exo.envscrub import scrub_env
from exo.store import exo_home


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'Invalid GitHub authentication arguments; run exo github-auth --help.\n')


def register(sp, common) -> None:
    """Advertise lazily; cli.main routes setup to our quiet secret-safe parser."""
    parser = sp.add_parser('github-auth', help='per-seat GitHub authentication: configure, login, status, test, remove')
    parser.add_argument('rest', nargs=argparse.REMAINDER)


def cmd_github_auth(a) -> int:
    return main(a.rest)


def main(argv=None) -> int:
    p = QuietParser(prog='exo github-auth', description='Per-seat GitHub credentials for trusted agent shells')
    p.add_argument('action', choices=('configure', 'login', 'status', 'test', 'remove'))
    p.add_argument('--seat', default=os.environ.get('EXO_SEAT') or 'default')
    p.add_argument('--host', default='github.com')
    p.add_argument('--mode', choices=('token', 'host'), default='token')
    p.add_argument('--store', choices=('keychain', 'secret-service', 'file'))
    p.add_argument('--token-stdin', action='store_true', help='read token from stdin instead of a hidden prompt')
    a = p.parse_args(argv)
    home = exo_home()
    try:
        if a.action == 'configure':
            value = None
            if a.mode == 'token':
                if not a.token_stdin and not sys.stdin.isatty():
                    raise auth.AuthError('Use --token-stdin for noninteractive setup')
                value = sys.stdin.read(65537) if a.token_stdin else getpass.getpass('GitHub token (hidden): ')
            result = auth.configure(home, a.seat, a.mode, a.host, value, a.store)
        elif a.action == 'login':
            # Force private on-disk storage; gh's shared OS keychain must not
            # associate this seat's login with a host-machine account.
            auth._identity(home, a.seat, a.host)
            old = auth.binding(home, a.seat)
            if old.get('mode') in ('token', 'gh-login') and (old.get('mode') != 'gh-login' or old.get('host') != a.host):
                raise auth.AuthError('Remove the previous binding before changing login mode or hostname')
            if old.get('cleanup_pending'):
                raise auth.AuthError('Complete previous credential cleanup before logging in')
            _, ghdir = auth.paths(home, a.seat)
            from exo.core.private import private_dir
            private_dir(ghdir)
            env = scrub_env()
            env['GH_CONFIG_DIR'] = str(ghdir)
            proc = subprocess.run(['gh', 'auth', 'login', '--hostname', a.host, '--insecure-storage', '--git-protocol', 'https', '--skip-ssh-key'], env=env)
            if proc.returncode:
                raise auth.AuthError('GitHub login did not complete; existing seat binding was retained')
            result = auth.configure(home, a.seat, 'gh-login', a.host)
        elif a.action == 'status':
            result = auth.status(home, a.seat)
        elif a.action == 'remove':
            result = auth.remove(home, a.seat)
        else:
            env = auth.child_env(scrub_env(), home, a.seat)
            proc = subprocess.run(['gh', 'api', 'user', '--hostname', auth.binding(home, a.seat).get('host', a.host), '--jq', '.login'], env=env,
                                  capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
            result = {'seat': a.seat, 'ok': proc.returncode == 0}
            if proc.returncode == 0:
                result['login'] = keys.redact_secrets(proc.stdout.strip())
            else:
                result['error'] = 'GitHub authentication test failed; check token, permissions and hostname'
        print(json.dumps(result))
        if a.action in ('configure', 'login') and not result.get('available', True):
            return 1
        return 0 if result.get('ok', True) and not result.get('cleanup_pending') else 1
    except (auth.AuthError, providers_setup.SetupError, ValueError, OSError, subprocess.SubprocessError, EOFError):
        print('GitHub authentication could not be configured or used; check settings and secret-store availability.', file=sys.stderr)
        return 1
