"""``python -m exo_command``: serve the command centre web client.

    python -m exo_command [--socket PATH | --url URL --token-file FILE] [--host H] [--port P]

With no flags it connects to the local daemon at ``$EXO_HOME/run/exod.sock`` and serves
``http://127.0.0.1:7342``. ``exo web`` calls ``main()`` here.
"""
from __future__ import annotations

import argparse
import asyncio
import errno
import http.client
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
    p.add_argument("--no-open", action="store_true", help="do not open the page in a browser")
    a = p.parse_args(argv)
    st = load_settings({"socket": a.socket, "url": a.url, "token_file": a.token_file, "host": a.host,
                        "port": a.port})
    try:
        web = WebServer(CommandCentre(st))
        web.open_browser = not a.no_open and sys.stdout.isatty()  # opens a one-time code, never the token (#166)
        asyncio.run(web.run(st.host, st.port))
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print("exo-command: {}".format(busy(st.host, st.port) if exc.errno == errno.EADDRINUSE else exc),
              file=sys.stderr)
        return 1
    return 0


def busy(host: str, port: int) -> str:
    """What to do when the port is taken: by another `exo web` (its page shell answers), or by another program."""
    shown = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    try:  # the page shell is served without the token; http.client ignores proxy settings
        conn = http.client.HTTPConnection(shown, port, timeout=2)
        conn.request("GET", "/")
        ours = b"<title>Exo</title>" in conn.getresponse().read(4096)
        conn.close()
    except (OSError, http.client.HTTPException):
        ours = False
    where = "{}:{}".format("[{}]".format(shown) if ":" in shown else shown, port)
    if not ours:
        return "{} is in use by another program. Pick a free port: exo web --port {}".format(where, port + 1)
    return ("Exo's web client is already running at http://{}/ (its page token lives in that process only).\n"
            "  If its page is open, keep using it. To get back in:\n"
            "  - open the full link it printed in its terminal, with #token=...\n"
            "  - or stop it with Ctrl+C in that terminal and run exo web again\n"
            "  - or start a second one on another port: exo web --port {}".format(where, port + 1))


if __name__ == "__main__":
    raise SystemExit(main())
