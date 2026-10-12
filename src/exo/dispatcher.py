"""One `/exo` slash command for every Exo plugin.

Hermes gives each plugin its own commands. Exo wants one front door, so plugins register
subcommands into this shared registry and a single plugin registers `/exo` with Hermes:

    # any Exo plugin, inside its register(ctx)
    from exo.dispatcher import register_subcommand
    register_subcommand('branch', cmd_branch, 'Write a handoff and start a fresh session on it.',
                        plugin='exo-context', args_hint='<topic>')

    # exo-memory (the always-on plugin), inside its register(ctx)
    from exo.dispatcher import register_root
    register_root(ctx)

A plugin that may run without exo-memory can call `register_root(ctx)` too. It registers
`/exo` once per process and is a no-op after that.

A handler takes the rest of the line as one string and returns the text to show (or None).
It may be sync or async; Hermes resolves an awaitable result. Every plugin imports this module
from the installed `exo` package, so they all share one registry.
"""
from __future__ import annotations

import difflib
import shlex
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

ROOT = 'exo'
ROOT_DESCRIPTION = 'Exo: memory, context, gates and the command centre. /exo help lists subcommands.'
BUILTINS = ('help',)


@dataclass(frozen=True)
class Subcommand:
    name: str
    handler: Callable[[str], Any]
    help: str = ''
    plugin: str = ''
    args_hint: str = ''
    aliases: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        return (self.help.strip().splitlines() or [''])[0]


_lock = threading.RLock()
_subs: dict[str, Subcommand] = {}
_aliases: dict[str, str] = {}
_root_registered = False


def _clean(name: str) -> str:
    n = (name or '').strip().lstrip('/').lower()
    if not n or any(c.isspace() for c in n):
        raise ValueError(f'bad subcommand name {name!r}')
    return n


def register_subcommand(name: str, handler: Callable[[str], Any], help: str = '', *,
                        plugin: str = '', args_hint: str = '', aliases: tuple[str, ...] | list[str] = (),
                        replace: bool = False) -> Subcommand:
    """Add `/exo <name>`. Re-registering by the same plugin (a reload) replaces quietly.
    Taking a name another plugin owns raises ValueError unless `replace=True`."""
    if not callable(handler):
        raise TypeError('handler must be callable')
    n = _clean(name)
    al = tuple(_clean(a) for a in aliases)
    with _lock:
        for key in (n, *al):
            if key in BUILTINS:
                raise ValueError(f'/exo {key} is built in')
            owner = _subs.get(key) or _subs.get(_aliases.get(key, ''))
            if owner and owner.plugin != plugin and not replace:
                raise ValueError(f'/exo {key} is already registered by {owner.plugin or "another plugin"}')
        unregister_subcommand(n)
        for a in al:
            if a in _subs:
                unregister_subcommand(a)
            _aliases[a] = n
        sub = Subcommand(n, handler, help or '', plugin or '', (args_hint or '').strip(), al)
        _subs[n] = sub
        return sub


def unregister_subcommand(name: str) -> bool:
    n = _clean(name)
    with _lock:
        sub = _subs.pop(n, None)
        if sub is None:
            return False
        for a in sub.aliases:
            _aliases.pop(a, None)
        return True


def unregister_plugin(plugin: str) -> int:
    """Drop every subcommand a plugin registered (for plugin unload)."""
    with _lock:
        names = [s.name for s in _subs.values() if s.plugin == plugin]
        for n in names:
            unregister_subcommand(n)
        return len(names)


def subcommands() -> list[Subcommand]:
    with _lock:
        return sorted(_subs.values(), key=lambda s: s.name)


def lookup(name: str) -> Optional[Subcommand]:
    try:
        n = _clean(name)
    except ValueError:
        return None
    with _lock:
        return _subs.get(n) or _subs.get(_aliases.get(n, ''))


def reset() -> None:
    """Empty the registry (tests)."""
    global _root_registered
    with _lock:
        _subs.clear()
        _aliases.clear()
        _root_registered = False


# --- help -------------------------------------------------------------------------

def _usage(s: Subcommand) -> str:
    return f'/{ROOT} {s.name}' + (f' {s.args_hint}' if s.args_hint else '')


def help_text(name: str | None = None) -> str:
    if name:
        s = lookup(name)
        if s is None:
            return _unknown(name)
        lines = [f'Usage: {_usage(s)}']
        if s.help.strip():
            lines += ['', s.help.strip()]
        if s.aliases:
            lines += ['', 'Aliases: ' + ', '.join(s.aliases)]
        if s.plugin:
            lines += [f'From: {s.plugin}']
        return '\n'.join(lines)
    subs = subcommands()
    if not subs:
        return (f'/{ROOT}: no subcommands are registered yet. Exo plugins add them when they load '
                f'(check plugins.enabled in the Hermes config).')
    width = max(len(_usage(s)) for s in subs)
    lines = [f'Usage: /{ROOT} <subcommand> [args]', '']
    for s in subs:
        tail = f'  ({s.plugin})' if s.plugin else ''
        lines.append(f'  {_usage(s):<{width}}  {s.summary}{tail}')
    lines += ['', f'/{ROOT} help <subcommand> shows more.']
    return '\n'.join(lines)


def _unknown(name: str) -> str:
    names = [s.name for s in subcommands()] + list(_aliases)
    close = difflib.get_close_matches(name.lower(), names, n=1)
    hint = f' Did you mean "{close[0]}"?' if close else ''
    return f'Unknown subcommand "{name}".{hint} /{ROOT} help lists them.'


# --- routing ----------------------------------------------------------------------

def split(raw_args: str) -> tuple[str, str]:
    """`'branch my topic'` -> `('branch', 'my topic')`. The rest keeps its original text."""
    text = (raw_args or '').strip()
    if not text:
        return '', ''
    head, _, rest = text.partition(' ')
    try:
        head = shlex.split(head)[0] if head else head
    except (ValueError, IndexError):
        pass
    return head.lower(), rest.strip()


def dispatch(raw_args: str = '') -> Any:
    """Entry point Hermes calls for `/exo ...`. Returns text, None, or an awaitable."""
    sub, rest = split(raw_args)
    if sub in ('', 'help', '-h', '--help'):
        return help_text(rest.split()[0] if rest.split() else None)
    s = lookup(sub)
    if s is None:
        return _unknown(sub)
    if rest in ('-h', '--help'):
        return help_text(s.name)
    try:
        return s.handler(rest)
    except Exception as e:  # a broken plugin must not take the whole command down
        return f'/{ROOT} {s.name} failed: {type(e).__name__}: {e}'


handle = dispatch


def args_hint() -> str:
    names = [s.name for s in subcommands()]
    return '|'.join(names) if names else '<subcommand>'


def register_root(ctx: Any, force: bool = False) -> bool:
    """Register `/exo` with Hermes through a plugin context. Once per process unless `force`.
    Returns True when this call registered it."""
    global _root_registered
    with _lock:
        commands = getattr(getattr(ctx, 'manager', None), 'commands', None)
        if commands is not None:  # an Exo PluginManager: once per manager, not once per process
            if ROOT in commands and not force:
                return False
            ctx.register_command(ROOT, dispatch, description=ROOT_DESCRIPTION, args_hint='<subcommand> [args]')
            return True
        if _root_registered and not force:
            return False
        ctx.register_command(ROOT, dispatch, description=ROOT_DESCRIPTION, args_hint='<subcommand> [args]')
        _root_registered = True
        return True


def root_registered() -> bool:
    return _root_registered
