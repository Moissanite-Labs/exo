"""Exo terminal client. Entry: ``exo_tui.main(argv)``."""

from __future__ import annotations

import argparse
import os
import sys


def parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="exo-tui", description="Exo terminal client")
    p.add_argument("--resume", action="store_true", help="continue the most recent session")
    p.add_argument("--session", help="session id to continue")
    p.add_argument("--seat", help="seat name (shown in the status line; picks the seat for an in-process core)")
    p.add_argument("--socket", help="exod Unix socket (default $EXO_HOME/run/exod.sock)")
    p.add_argument("--url", help="exod TCP URL")
    p.add_argument("--token-file", help="bearer token file for --url")
    p.add_argument("--title", default="", help="title for a new session")
    p.add_argument("--no-intro", action="store_true",
                   help="skip the first-run orientation (and do not offer it again on this EXO_HOME)")
    return p.parse_args(argv)


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
    ExoApp(client, session=a.session, resume=a.resume, seat=a.seat, title=a.title, auto_intro=intro, boot_anim=True).run()
    return 0
