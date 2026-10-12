"""Built-in tools: fs (read, write, edit, patch), search (glob, grep), shell (bash)."""

from __future__ import annotations

from exo.core.tools.registry import Registry


def register_builtins(reg: Registry) -> None:
    from exo.core.tools.builtin import codefind, fs, search, shell

    fs.register(reg)
    search.register(reg)
    shell.register(reg)
    codefind.register(reg)
