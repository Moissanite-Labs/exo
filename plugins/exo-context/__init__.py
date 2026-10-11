"""Hermes plugin entry for exo-context.

Registers `ExoContextEngine` (select it with `context.engine: exo-context` in config.yaml) and the
`/exo` command (`branch`, `handoff`, `resume`). Settings come from the environment:
`EXO_PRINCIPAL` (principal id), `EXO_HOME`, `EXO_HANDOFF_DIR`, `EXO_LAUNCH_TEMPLATE`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _ensure_exo_importable() -> None:
    """The `exo` package is normally installed beside Hermes; fall back to `$EXO_SRC` or this repo."""
    try:
        import exo  # noqa: F401
        return
    except ImportError:
        pass
    for cand in (os.environ.get('EXO_SRC'), _HERE.parent.parent / 'src'):
        if cand and (Path(cand) / 'exo' / '__init__.py').is_file():
            sys.path.insert(0, str(cand))
            return


_ensure_exo_importable()

try:
    from .exo_context import ExoContextEngine, handle
except ImportError:  # loaded as a plain module rather than a package
    sys.path.insert(0, str(_HERE))
    from exo_context import ExoContextEngine, handle  # type: ignore[no-redef]


def register(ctx) -> None:
    ctx.register_context_engine(ExoContextEngine())
    ctx.register_command('exo', handle, description='Exo sessions: branch, handoff, resume',
                         args_hint='branch|handoff|resume <topic>')
