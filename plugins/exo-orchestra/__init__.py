"""exo-orchestra Hermes plugin entry point.

Needs nothing beyond the standard library. In a source checkout the skill pack is found at
../../skills, so the plugin can run straight from the repo.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__:
    from .exo_orchestra.orchestra import register as _register
else:  # imported as a bare module (for example by a test runner)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from exo_orchestra.orchestra import register as _register


def register(ctx) -> None:
    _register(ctx)
