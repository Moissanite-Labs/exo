"""Plugin entry for exo-context on the Exo core.

Registers `ExoContextEngine` (select it with `context_engine: exo-context` in `$EXO_HOME/config.json`,
or leave it as the only engine) and the `/exo` subcommands `branch`, `handoff`, `resume`.
Settings: `plugin_config.exo-context` in config.json (`pin_char_budget`, `write_handoffs`,
`handoff_dir`, `principal`), then the environment: `EXO_PRINCIPAL`, `EXO_HOME`, `EXO_HANDOFF_DIR`,
`EXO_LAUNCH_TEMPLATE`. The `compression` table of config.json tunes the inner compressor.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _ensure_exo_importable() -> None:
    """The `exo` package is normally installed; fall back to `$EXO_SRC` or this repo."""
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
    from .exo_context import ExoContextEngine, handle as handle
except ImportError:  # loaded as a plain module rather than a package
    sys.path.insert(0, str(_HERE))
    from exo_context import ExoContextEngine, handle as handle  # type: ignore[no-redef]


def register(ctx) -> None:
    get = getattr(ctx, 'get_config', None)
    opt = (lambda k, d=None: get(k, d)) if get else (lambda k, d=None: d)
    ctx.register_context_engine(ExoContextEngine(
        principal=opt('principal'), handoff_dir=opt('handoff_dir'),
        compressor_kwargs={'router_getter': lambda: getattr(ctx, 'router', None)},
        pin_char_budget=int(opt('pin_char_budget', 6000)),
        write_handoffs=str(opt('write_handoffs', True)).lower() not in ('0', 'false', 'no', 'off')))
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
    register_root(ctx)  # idempotent per plugin manager; a no-op when exo-memory registered /exo
