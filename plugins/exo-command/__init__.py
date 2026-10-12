"""exo-command: Exo's command centre, a web client of the Exo protocol.

* ``exo_command/``: the client. ``service.CommandCentre`` folds protocol queries and the event
  stream into panels; ``server.WebServer`` serves them (standard library only);
  ``python -m exo_command`` runs it. Hermes readers live in ``exo.ingest.hermes_dashboard``.
* this module: ``register(ctx)`` adds one slash command, ``/exo-command``, which prints a short
  digest of the same panels by asking the running core over its socket.

Nothing here reads another program's database.
"""
from __future__ import annotations

import os
import sys

__version__ = "0.2.0"


def _service():
    try:
        from .exo_command import service  # loaded as a package by the plugin loader
    except ImportError:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from exo_command import service  # type: ignore
    return service


async def _command(raw_args: str = "") -> str:
    service = _service()
    centre = service.CommandCentre()
    try:
        centre.connect()
        await centre.bootstrap()
        return centre.summary()
    except Exception as exc:  # no daemon, bad token: say so, do not trace
        return ("Cannot reach the Exo core ({}: {}). Start it with `exo serve`, or run the web client with "
                "`python -m exo_command`.".format(type(exc).__name__, exc))
    finally:
        await centre.aclose()


def register(ctx) -> None:
    ctx.register_command(
        "exo-command",
        _command,
        description="Command centre digest: approvals, decisions, live sessions, tasks, projects.",
    )
