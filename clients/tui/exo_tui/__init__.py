"""Exo terminal client. Entry: ``exo_tui.main(argv)``."""

from __future__ import annotations

import argparse
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
    ExoApp(client, session=a.session, resume=a.resume, seat=a.seat, title=a.title).run()
    return 0
