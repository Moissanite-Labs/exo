"""exo-bus Hermes plugin entry point.

Needs the `exo` package. In a source checkout it is found at ../../src when not installed.
Tools register always (gated by config through check_fn). The platform adapter registers when the
Hermes gateway modules are importable.
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
    if not hasattr(ctx, "register_platform"):
        return
    try:
        if __package__:
            from .exo_bus_hermes.adapter import register_platform
        else:
            from exo_bus_hermes.adapter import register_platform
        register_platform(ctx)
    except ImportError:
        logger.info("exo-bus: Hermes gateway not importable; platform adapter not registered")
    except Exception:
        logger.warning("exo-bus: failed to register platform adapter", exc_info=True)
