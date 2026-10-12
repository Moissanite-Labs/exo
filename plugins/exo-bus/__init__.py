"""exo-bus plugin entry point (Exo core).

Needs the `exo` package. In a source checkout it is found at ../../src when not installed.
Tools register always (gated by config through check_fn). The Channel registers when the core's
plugin context offers ``register_channel``. Hermes has ``register_platform`` instead, which this plugin does
not use (see README: a bus platform adapter would admit bus text as user input, so it needs its own review).
Peers (peers.py) start on both hosts when enabled.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import exo.bus  # noqa: F401
except ImportError:  # source checkout: plugins/exo-bus -> repo/src
    _src = Path(__file__).resolve().parents[2] / "src"
    if (_src / "exo" / "bus" / "__init__.py").is_file():
        sys.path.insert(0, str(_src))

if __package__:
    from .exo_bus_hermes import tools as _tools
else:  # imported as a bare module (for example by a test runner)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from exo_bus_hermes import tools as _tools


def register(ctx) -> None:
    try:
        _tools.register_tools(ctx)
    except Exception:
        logger.warning("exo-bus: failed to register tools", exc_info=True)
    try:  # peers (D-026): off unless EXO_PEERS=1 or the plugin setting peers.enabled is true
        if __package__:
            from .exo_bus_hermes.peers import register_peers
        else:
            from exo_bus_hermes.peers import register_peers
        register_peers(ctx)
    except Exception:
        logger.warning("exo-bus: failed to start peers", exc_info=True)
    if not hasattr(ctx, "register_channel"):
        return
    try:
        if __package__:
            from .exo_bus_hermes.adapter import register_channel
        else:
            from exo_bus_hermes.adapter import register_channel
        register_channel(ctx)
    except ImportError:
        logger.info("exo-bus: protocol client not importable; channel not registered")
    except Exception:
        logger.warning("exo-bus: failed to register channel", exc_info=True)
