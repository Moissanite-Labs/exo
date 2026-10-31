"""Exo terminal client. Entry: ``exo_tui.main(argv)``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def directory(value: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("working directory must be an existing directory")
    return str(path)


def round_limit(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("rounds must be a nonnegative integer") from None
    if n < 0:
        raise argparse.ArgumentTypeError("rounds must be a nonnegative integer")
    return n


def parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="exo-tui", description="Exo terminal client")
    p.add_argument("--resume", action="store_true", help="continue the most recent session")
    p.add_argument("--session", help="session id to continue")
    p.add_argument("--seat", help="seat name (shown in the status line; picks the seat for an in-process core)")
    p.add_argument("--socket", help="exod Unix socket (default $EXO_HOME/run/exod.sock)")
    p.add_argument("--url", help="exod TCP URL")
    p.add_argument("--token-file", help="bearer token file for --url")
    p.add_argument("--title", default="", help="title for a new session")
    p.add_argument("--cwd", help="working directory for new sessions (existing sessions keep their own directory)")
    p.add_argument("--max-rounds", type=round_limit, help="tool rounds per new turn; 0 removes the limit; this is not a time limit")
    permissions = p.add_mutually_exclusive_group()
    permissions.add_argument("--skip-permissions", "--dangerously-skip-permissions", dest="skip_permissions",
                   action="store_true", help="permissions bypass for this run of the core: the gate stops asking, except "
                   "for changes to Exo's own records and reading secrets (D-065). Every auto-approved call is in the ledger")
    permissions.add_argument("--full-access", action="store_true", help="explicitly skip all gate checks for all tool calls in the selected/resumed thread only, including peer, bus and job messages delivered there. Unaddressed peers prefer a running thread, then the most recent eligible thread. Other threads still ask. Restart clears the grant")
    p.add_argument("--no-intro", action="store_true",
                   help="skip the first-run orientation (and do not offer it again on this EXO_HOME)")
    a = p.parse_args(argv)
    if a.cwd and not a.url:
        try:
            a.cwd = directory(a.cwd)
        except ValueError as exc:
            p.error(str(exc))
    return a


def main(argv: list[str] | None = None) -> int:
    a = parse(sys.argv[1:] if argv is None else argv)
    from exo_tui.app import ExoApp
    from exo_tui.conn import connect

    try:
        client = connect(a.socket, a.url, a.token_file, a.seat)
    except Exception as exc:
        print(f"exo-tui: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    from exo.config import exo_home
    from exo_tui.onboarding import Onboarding

    onb = Onboarding(exo_home())
    if a.no_intro and not onb.onboarded:
        onb.mark_onboarded("skipped")
    intro = not a.no_intro and not os.environ.get("EXO_NO_INTRO")  # the env var skips without writing anything
    ExoApp(client, session=a.session, resume=a.resume, seat=a.seat, title=a.title, cwd=a.cwd, auto_intro=intro, boot_anim=True,
           provider_check=True, skip_permissions=a.skip_permissions, full_access=a.full_access, remote=bool(a.url), max_rounds=a.max_rounds).run()
    return 0
