"""exo-gate Hermes plugin entry point.

Needs the `exo` package. In a source checkout it is found at ../../src when it is not
installed, so the plugin can run straight from the repo.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import exo.gate  # noqa: F401
except ImportError:  # source checkout: plugins/exo-gate -> repo/src
    _src = Path(__file__).resolve().parents[2] / "src"
    if (_src / "exo" / "gate.py").is_file():
        sys.path.insert(0, str(_src))

if __package__:
    from .exo_gate.hook import register as _register
else:  # imported as a bare module (for example by a test runner)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from exo_gate.hook import register as _register


def register(ctx) -> None:
    _register(ctx)
