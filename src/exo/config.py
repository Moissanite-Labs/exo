"""Exo configuration: one file, `$EXO_HOME/config.json`.

It holds the machine-level choices the installer and the plugins share:

    principal     the active principal id
    hermes_dir    the pinned Hermes checkout (scripts/pin-hermes.sh owns it)
    hermes_home   the HERMES_HOME Exo installed its plugins into
    venv          the Python environment that runs Hermes and Exo
    plugins       the Exo plugins installed into hermes_home
    plugin_config per-plugin settings, keyed by plugin name
    principals    optional explicit store roots, keyed by principal id

Resolution for each value, first hit wins: the environment, then the file, then the default.
The environment never leaks into the file: `save()` writes only what was set on the object.
Nothing here names a person or a machine (D-004).

    python -m exo.config show | path | get KEY | set KEY VALUE | unset KEY
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

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

    @classmethod
    def load(cls, home: str | os.PathLike | None = None) -> 'Config':
        cfg = cls(home=home)
        try:
            raw = cfg.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return cfg
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f'{cfg.path}: not valid JSON ({e})') from e
        if not isinstance(data, dict):
            raise ConfigError(f'{cfg.path}: top level must be an object')
        if int(data.get('version', VERSION)) > VERSION:
            raise ConfigError(f'{cfg.path}: version {data["version"]} is newer than this exo ({VERSION})')
        cfg.data = data
        return cfg

    def save(self) -> Path:
        """Atomic write, mode 0600. Unknown keys written by newer tools are kept."""
        self.home.mkdir(parents=True, exist_ok=True)
        out = {'version': VERSION, **{k: v for k, v in self.data.items() if k != 'version'}}
        fd, tmp = tempfile.mkstemp(prefix='.config.', dir=self.home)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(out, f, indent=2, sort_keys=True)
                f.write('\n')
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return self.path

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

        First hit wins: explicit `models.roles`; a scripted `models.fake`; a local
        OpenAI-compatible server in `models.local` ({"base_url", "model"}); the catalog
        (Anthropic) when ANTHROPIC_API_KEY is set; else the offline fake model.
        Keys come from environment variables only; no credential file is read.
        """
        env = os.environ if env is None else env
        m = copy.deepcopy(self.data.get('models') or {})
        if m.get('roles'):
            return m
        fake = {'roles': {'main': ['fake:scripted'], 'aux': ['fake:scripted'], 'decision': []}}
        if m.get('fake'):
            return {**m, **fake}
        local = m.get('local')
        if isinstance(local, dict) and local.get('base_url') and local.get('model'):
            prov = {'transport': 'openai', 'base_url': local['base_url'], 'local': True}
            if local.get('key_env'):
                prov['key_env'] = local['key_env']
            ref = f"local:{local['model']}"
            return {**m, 'providers': {**(m.get('providers') or {}), 'local': prov},
                    'roles': {'main': [ref], 'aux': [ref], 'decision': []}}
        if env.get('ANTHROPIC_API_KEY'):
            return m
        return {**m, **fake}

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
    sp.add_parser('show', help='every key with its effective value')
    sp.add_parser('path', help='where the file lives')
    p = sp.add_parser('get'); p.add_argument('key')
    p = sp.add_parser('set', help='VALUE is parsed as JSON when it can be, else kept as text')
    p.add_argument('key'); p.add_argument('value')
    p = sp.add_parser('unset'); p.add_argument('key')
    a = ap.parse_args(argv)
    try:
        cfg = Config.load()
        if a.op == 'path':
            print(cfg.path)
        elif a.op == 'show':
            print(json.dumps(cfg.resolved(), indent=2, sort_keys=True))
        elif a.op == 'get':
            v = cfg.get(a.key, _MISSING)
            if v is _MISSING:
                print(f'exo config: {a.key} is not set', file=sys.stderr)
                return 1
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
