"""``python -m exo_command``: serve the command centre web client.

    python -m exo_command [--socket PATH | --url URL --token-file FILE] [--host H] [--port P]

With no flags it connects to the local daemon at ``$EXO_HOME/run/exod.sock`` and serves
``http://127.0.0.1:7342``. ``exo web`` calls ``main()`` here.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import List, Optional

from .config import load_settings
from .server import WebServer
from .service import CommandCentre


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="exo-command", description="Exo command centre web client")
    p.add_argument("--socket", help="exod Unix socket (default $EXO_HOME/run/exod.sock)")
    p.add_argument("--url", help="exod TCP URL, for example http://127.0.0.1:7341")
    p.add_argument("--token-file", help="bearer token file for --url")
    p.add_argument("--host", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, help="port (default 7342)")
    a = p.parse_args(argv)
    st = load_settings({"socket": a.socket, "url": a.url, "token_file": a.token_file, "host": a.host,
                        "port": a.port})
    try:
        asyncio.run(WebServer(CommandCentre(st)).run(st.host, st.port))
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print("exo-command: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
