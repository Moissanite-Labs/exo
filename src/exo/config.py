"""Exo configuration: one file, `$EXO_HOME/config.json`.

It holds the machine-level choices the installer and the plugins share:

    principal     the active principal id
    hermes_dir    the pinned Hermes checkout (scripts/pin-hermes.sh owns it)
    hermes_home   the HERMES_HOME Exo installed its plugins into
    venv          the Python environment that runs Hermes and Exo
    plugins       the Exo plugins installed into hermes_home
    plugin_selection  the installer's `--plugins` list (or "all"), kept for re-runs and `exo update`
    plugin_config per-plugin settings, keyed by plugin name
    principals    optional explicit store roots, keyed by principal id
    update        `exo update` settings: `update.require_signed` (default false) refuses a target
                  commit not signed by a key in the pinned trust list (docs/INSTALL.md, D-073)

Resolution for each value, first hit wins: the environment, then the file, then the default.
The environment never leaks into the file: `save()` writes only what was set on the object.
Nothing here names a person or a machine (D-004).

    python -m exo.config show | path | roles | get KEY | set KEY VALUE | unset KEY
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .core.private import atomic_write
from .store import ID_RE, exo_home

VERSION = 1
FILENAME = 'config.json'
DEFAULT_PLUGINS = ['exo-memory', 'exo-context', 'exo-gate', 'exo-bus', 'exo-command', 'exo-orchestra']

# key -> environment variable that overrides it
ENV = {
    'principal': 'EXO_PRINCIPAL',
    'hermes_dir': 'EXO_HERMES_DIR',
    'hermes_home': 'EXO_HERMES_HOME',   # not HERMES_HOME: that one usually points at a live install
    'venv': 'EXO_VENV',
}
PATH_KEYS = ('hermes_dir', 'hermes_home', 'venv')
_MISSING = object()


class ConfigError(ValueError):
    pass


# Secrets never go through the file API: names that look like one (`api_key`, `token`, ...) at any level of
# the key path, unless the name ends in `_env` (the NAME of an environment variable). Under `env` and `headers`
# any literal is refused; use "${VAR}" so the secret stays in the environment, a platform key store, or keys.env.
SECRET_NAME = re.compile(r'(^|[_-])(key|token|secret|password|passwd|credentials?|authorization|bearer)s?($|[_-])'
                         r'|api_?key', re.I)
_SECRET_VALUE = re.compile(r'(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|'
                           r'-----BEGIN [A-Z ]*PRIVATE KEY)')
_VAR_REF = re.compile(r'\$\{\w+\}')


def _is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME.search(name)) and not name.endswith('_env')


def _secret_path(path: Sequence[str]) -> bool:
    """Whether a literal at this key path is a secret: a secret-shaped name at any level, or under env or headers.
    Every level counts, so a container named like a secret holds secrets in its leaves (#691)."""
    return any(_is_secret_name(p) or p in ('env', 'headers') for p in path)


def _is_secret(path: Sequence[str], v: str) -> bool:
    """Whether the string `v` at this key path is a secret: a value shaped like a key, even beside a `${VAR}`, or a
    literal at a secret path. `check_not_secret` refuses and `redact` masks exactly these strings."""
    return bool(v) and bool(_SECRET_VALUE.search(v) or (_secret_path(path) and not _VAR_REF.search(v)))


def check_not_secret(key: str, value: Any) -> None:
    """Raise ConfigError when (key, value) would put a secret into config.json."""
    def walk(path: list[str], v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk([*path, str(k)], x)
        elif isinstance(v, list):
            for x in v:
                walk(path, x)
        elif isinstance(v, str) and _is_secret(path, v):
            raise ConfigError(f'{".".join(path)}: refusing to store a secret in config.json; '
                              f'use "${{VAR}}" or a *_env key naming an environment variable')
    walk(key.split('.'), value)


def redact(data: Any, path: Sequence[str] = ()) -> Any:
    """A copy of config data with secret-looking values masked (for config get, config show and the JSON API).
    `path` is the key path of `data`, so a dotted key is judged level by level, as a write is."""
    if isinstance(data, dict):
        return {k: redact(v, [*path, str(k)]) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v, path) for v in data]
    if isinstance(data, str) and _is_secret(path, data):
        return '***'
    return data


def config_path(home: str | os.PathLike | None = None) -> Path:
    return Path(home).expanduser() / FILENAME if home else exo_home() / FILENAME


def _defaults(home: Path) -> dict[str, Any]:
    return {
        'version': VERSION,
        'principal': None,
        'hermes_dir': str(home / 'hermes'),
        'hermes_home': str(home / 'hermes-home'),
        'venv': str(home / 'venv'),
        'plugins': list(DEFAULT_PLUGINS),
        'plugin_config': {},
        'principals': {},
        'update': {'require_signed': False},
    }


def _walk(d: dict, dotted: str, create: bool = False) -> tuple[dict | None, str]:
    parts = dotted.split('.')
    for p in parts[:-1]:
        nxt = d.get(p)
        if not isinstance(nxt, dict):
            if not create:
                return None, parts[-1]
            nxt = d[p] = {}
        d = nxt
    return d, parts[-1]


class Config:
    """`Config.load()` reads `$EXO_HOME/config.json` (missing file = defaults)."""

    def __init__(self, data: dict | None = None, home: str | os.PathLike | None = None):
        self.home = Path(home).expanduser() if home else exo_home()
        self.data: dict = dict(data or {})

    def __repr__(self) -> str:
        return f'Config({str(self.path)!r})'

    @property
    def path(self) -> Path:
        return self.home / FILENAME

    # Parsed file contents per path, keyed on (size, mtime_ns); None means the file was missing.
    # load() is on hot paths (every engine setup, every CLI call), and the file rarely changes.
    _cache: dict[str, tuple[tuple[int, int] | None, dict]] = {}
    _cache_lock = threading.Lock()

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        return st.st_size, st.st_mtime_ns

    @classmethod
    def _cached(cls, path: Path, stamp: tuple[int, int] | None, data: dict) -> None:
        with cls._cache_lock:
            if len(cls._cache) > 64:  # test suites load many throwaway homes; drop, never grow
                cls._cache.clear()
            cls._cache[str(path)] = (stamp, copy.deepcopy(data))

    @classmethod
    def load(cls, home: str | os.PathLike | None = None) -> 'Config':
        """Cached on the file's size and mtime_ns (#255 W1-3): an unchanged file is read and parsed
        once, and each load hands out a deep copy, so mutating one load's values, or set() before a
        save() that fails, never reaches the next load. An external edit that keeps the same size
        and mtime_ns is not seen until one of them moves."""
        cfg = cls(home=home)
        stamp = cls._stamp(cfg.path)
        with cls._cache_lock:
            hit = cls._cache.get(str(cfg.path))
        if hit is not None and hit[0] == stamp:
            cfg.data = copy.deepcopy(hit[1])
            return cfg
        try:
            raw = cfg.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            cls._cached(cfg.path, None, {})
            return cfg
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f'{cfg.path}: not valid JSON ({e})') from e
        if not isinstance(data, dict):
            raise ConfigError(f'{cfg.path}: top level must be an object')
        if int(data.get('version', VERSION)) > VERSION:
            raise ConfigError(f'{cfg.path}: version {data["version"]} is newer than this exo ({VERSION})')
        if cls._stamp(cfg.path) == stamp:  # unchanged while it was read; a changed file re-reads next load
            cls._cached(cfg.path, stamp, data)
        cfg.data = data
        return cfg

    def save(self) -> Path:
        """Atomic write, mode 0600. Unknown keys written by newer tools are kept."""
        self.home.mkdir(parents=True, exist_ok=True)
        out = {'version': VERSION, **{k: v for k, v in self.data.items() if k != 'version'}}
        path = atomic_write(self.path, json.dumps(out, indent=2, sort_keys=True) + '\n')
        stamp = self._stamp(path)
        if stamp is not None:  # the next load serves what was just written, without re-reading it
            self._cached(path, stamp, out)
        return path

    # --- generic access --------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Resolved value: environment, then file, then default. Dotted keys reach into objects."""
        top = key.split('.')[0]
        if top == key and key in ENV and os.environ.get(ENV[key]):
            return os.environ[ENV[key]]
        d, last = _walk(self.data, key)
        if d is not None and last in d:
            return d[last]
        d, last = _walk(_defaults(self.home), key)
        if d is not None and last in d:
            return copy.deepcopy(d[last])
        return default

    def set(self, key: str, value: Any) -> None:
        if key == 'principal' and value is not None and not ID_RE.match(str(value)):
            raise ConfigError(f'bad principal id {value!r}: use lowercase letters, digits, - and _')
        if key == 'plugins' and not (isinstance(value, list) and all(isinstance(x, str) for x in value)):
            raise ConfigError('plugins must be a list of names')
        if key == 'version':
            raise ConfigError('version is managed by exo')
        check_not_secret(key, value)
        d, last = _walk(self.data, key, create=True)
        d[last] = value

    def unset(self, key: str) -> bool:
        d, last = _walk(self.data, key)
        if d is not None and last in d:
            del d[last]
            return True
        return False

    def resolved(self) -> dict:
        """Every known key with its effective value (for `show`)."""
        out = copy.deepcopy({**_defaults(self.home), **self.data})
        for k, var in ENV.items():
            if os.environ.get(var):
                out[k] = os.environ[var]
        out['exo_home'] = str(self.home)
        return out

    # --- typed views -----------------------------------------------------------

    @property
    def exo_home(self) -> Path:
        return self.home

    @property
    def principal(self) -> str | None:
        return self.get('principal') or None

    @property
    def hermes_dir(self) -> Path:
        return Path(self.get('hermes_dir')).expanduser()

    @property
    def hermes_home(self) -> Path:
        return Path(self.get('hermes_home')).expanduser()

    @property
    def venv(self) -> Path:
        return Path(self.get('venv')).expanduser()

    @property
    def plugins(self) -> list[str]:
        return list(self.get('plugins') or [])

    def store_root(self, pid: str | None = None) -> Path:
        pid = pid or self.principal
        if not pid:
            raise ConfigError('no active principal: set EXO_PRINCIPAL or `exo config set principal <id>`')
        if not ID_RE.match(pid):
            raise ConfigError(f'bad principal id {pid!r}')
        explicit = (self.data.get('principals') or {}).get(pid)
        return Path(explicit).expanduser() if explicit else self.home / 'principals' / pid

    def models(self, env: dict | None = None) -> dict:
        """The `models` table for the core Router, with a working default.

        Preserve explicit roles, an explicitly configured fake, or a local server.
        Otherwise use the real catalog. Credential resolution belongs to runtime
        setup, not this synchronous configuration view.
        Keys: environment, then macOS Keychain, Linux Secret Service when `secret-tool` is available, then
        `~/.config/exo/keys.env` (mode 0600; override with `EXO_KEYS_FILE`; see core/models/keys.py).
        """
        m = copy.deepcopy(self.data.get('models') or {})
        if m.get('roles'):
            return m
        fake = {'roles': {'main': ['fake:scripted'], 'aux': ['fake:scripted'], 'decision': []}}
        if isinstance(m.get('fake'), dict):
            return {**m, **fake}
        local = m.get('local')
        if isinstance(local, dict) and local.get('base_url') and local.get('model'):
            prov = {'transport': 'openai', 'base_url': local['base_url'], 'local': True}
            if local.get('key_env'):
                prov['key_env'] = local['key_env']
            ref = f"local:{local['model']}"
            return {**m, 'providers': {**(m.get('providers') or {}), 'local': prov},
                    'roles': {'main': [ref], 'aux': [ref], 'decision': []}}
        return m

    def plugin_config(self, name: str) -> dict:
        return copy.deepcopy((self.data.get('plugin_config') or {}).get(name) or {})

    def set_plugin_config(self, name: str, values: dict, merge: bool = True) -> dict:
        pc = self.data.setdefault('plugin_config', {})
        cur = pc.get(name) if merge and isinstance(pc.get(name), dict) else {}
        pc[name] = {**cur, **values}
        return copy.deepcopy(pc[name])


def load(home: str | os.PathLike | None = None) -> Config:
    return Config.load(home)


# --- command line -------------------------------------------------------------

def _parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog='exo config', description='read and write $EXO_HOME/config.json')
    sp = ap.add_subparsers(dest='op', required=True)
    sp.add_parser('show', help='every key with its effective value, secrets masked')
    sp.add_parser('path', help='where the file lives')
    sp.add_parser('roles', help='resolved model route per role (no secrets)')
    p = sp.add_parser('get'); p.add_argument('key')
    p = sp.add_parser('set', help='VALUE is parsed as JSON when it can be, else kept as text')
    p.add_argument('key'); p.add_argument('value')
    p = sp.add_parser('unset'); p.add_argument('key')
    a = ap.parse_args(argv)
    try:
        cfg = Config.load()
        if a.op == 'path':
            print(cfg.path)
        elif a.op == 'roles':
            from .models_cmd import build_router
            for role, refs in build_router().resolved_roles().items():
                print(f"{role:<9} {' > '.join(refs) or '-'}")
        elif a.op == 'show':
            print(json.dumps(redact(cfg.resolved()), indent=2, sort_keys=True))
        elif a.op == 'get':
            v = cfg.get(a.key, _MISSING)
            if v is _MISSING:
                print(f'exo config: {a.key} is not set', file=sys.stderr)
                return 1
            v = redact(v, a.key.split('.'))
            print(v if isinstance(v, str) else json.dumps(v))
        elif a.op == 'set':
            cfg.set(a.key, _parse_value(a.value))
            cfg.save()
        elif a.op == 'unset':
            if cfg.unset(a.key):
                cfg.save()
    except ConfigError as e:
        print(f'exo config: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
