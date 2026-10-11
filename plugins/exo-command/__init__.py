"""exo-command: Exo's command centre as a Hermes plugin.

Two halves, one directory:

* ``dashboard/``: the Hermes web dashboard plugin (manifest, ``plugin_api.py`` router, vanilla
  JS bundle). Hermes discovers it from ``<home>/plugins/exo-command/dashboard/manifest.json``
  and mounts the router at ``/api/plugins/exo-command/`` once the plugin is in
  ``plugins.enabled``.
* this module: ``register(ctx)`` adds one slash command, ``/exo-command``, which prints a short
  digest from the same read-only service.

Nothing here writes to a Hermes database.
"""
from __future__ import annotations

import os
import sys

__version__ = "0.1.0"


def _service():
    try:
        from .exo_command import service  # loaded as a package by the Hermes plugin loader
    except ImportError:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from exo_command import service  # type: ignore
    return service


def _command(raw_args: str = "") -> str:
    return _service().default_centre().summary()


def register(ctx) -> None:
    ctx.register_command(
        "exo-command",
        _command,
        description="Command centre digest: decisions waiting, live agents, kanban, projects (read-only).",
    )
