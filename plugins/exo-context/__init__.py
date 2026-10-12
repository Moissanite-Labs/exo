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
    from exo.dispatcher import register_root, register_subcommand
    try:
        from .exo_context.commands import cmd_branch, cmd_handoff, cmd_resume
    except ImportError:
        from exo_context.commands import cmd_branch, cmd_handoff, cmd_resume  # type: ignore[no-redef]
    P = 'exo-context'
    register_subcommand('branch', lambda rest: cmd_branch(rest),
                        'Write a handoff and print the line that starts a fresh session on it.',
                        plugin=P, args_hint='<topic>')
    register_subcommand('handoff', lambda rest: cmd_handoff(rest),
                        'Write a handoff for this session.', plugin=P, args_hint='[topic]')
    register_subcommand('resume', lambda rest: cmd_resume(rest),
                        'Load the newest handoff for a topic (or a path) into this session.',
                        plugin=P, args_hint='<topic>')
    register_root(ctx)  # no-op when exo-memory already registered /exo
