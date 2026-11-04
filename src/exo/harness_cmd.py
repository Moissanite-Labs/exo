"""Inspect and select modular harness profiles without loading the runtime."""
from __future__ import annotations

import copy
import json
import os
import sys

from .config import Config, ConfigError
from .harness import CATALOG, PROFILES, PROFILE_DESCRIPTIONS, resolve

HELP = 'inspect and select modular harness profiles'


def register(sp, common) -> None:
    p = sp.add_parser('harness', help=HELP)
    sub = p.add_subparsers(dest='harness_action')
    sub.add_parser('list', help='list profiles and capabilities')
    q = sub.add_parser('show', help='show the effective composition without loading plugins')
    q.add_argument('--json', action='store_true')
    q = sub.add_parser('use', help='select a preset, clearing capability overrides')
    q.add_argument('profile', choices=tuple(PROFILES))
    q.add_argument('--dry-run', action='store_true')
    for action in ('enable', 'disable'):
        q = sub.add_parser(action, help=f'{action} optional capabilities')
        q.add_argument('names', nargs='+')
        q.add_argument('--dry-run', action='store_true')


def _unselected(plan) -> list[str]:
    """Discovered third-party plugins that an explicit profile does not load. Only plugin.yaml files and
    entry-point names are read: no plugin code is imported."""
    if not plan.explicit:
        return []
    import importlib.metadata
    from .core.plugins import ENTRY_POINT_GROUP, default_dirs, discover
    builtin = {cap.plugin for cap in CATALOG if cap.plugin}
    names = [m.name for m in discover(default_dirs())]
    names += [ep.name for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)]
    return sorted({name for name in names if name not in builtin and name not in (plan.plugins or ())})


def _render(plan) -> str:
    plugins = ', '.join(plan.plugins) if plan.plugins is not None else 'legacy discovery'
    summary = plan.summary()
    disabled_settings = [name for name, detail in summary['details'].items()
                         if detail['selected'] and detail['setting_disabled']]
    return (f'Profile: {plan.profile}\n'
            f'Capabilities: {", ".join(plan.capabilities)}\n'
            f'Disabled: {", ".join(summary["disabled"]) or "none"}\n'
            f'Plugins: {plugins or "none"}\n'
            f'Disabled by subsystem settings: {", ".join(disabled_settings) or "none"}\n'
            + (f'Not loaded: {", ".join(unselected)}. These discovered plugins are outside the profile; '
               'list them in core_plugins to keep them.\n' if (unselected := _unselected(plan)) else '')
            + 'Availability is not probed; services still need their own configuration.')


def cmd_harness(a) -> int:
    action = a.harness_action or 'show'
    if action == 'list':
        for name, description in PROFILE_DESCRIPTIONS.items():
            print(f'{name}: {description}')
        print('\nCapabilities:')
        for cap in CATALOG:
            deps = f' Requires: {", ".join(cap.requires)}.' if cap.requires else ''
            print(f'  {cap.name}: {cap.description}{deps}')
        return 0
    try:
        cfg = Config.load()
        if action == 'show':
            plan = resolve(cfg.data)
            summary = {**plan.summary(), 'unselected_plugins': _unselected(plan)}
            print(json.dumps(summary, indent=2) if getattr(a, 'json', False) else _render(plan))
            if os.environ.get('EXO_PROFILE') and not getattr(a, 'json', False):
                print('EXO_PROFILE overrides the saved profile.')
            return 0
        candidate = copy.deepcopy(cfg.data)
        if action == 'use':
            candidate['harness'] = {'profile': a.profile}
        else:
            # Validate the saved graph before editing it. The environment affects
            # runtime selection, never the persisted profile being edited here.
            if not resolve(candidate, env={}).explicit:  # an override would silently switch legacy to full
                raise ConfigError('no profile is saved; select one first with exo harness use lean, coding or full')
            raw = candidate['harness']
            opposite = 'disable' if action == 'enable' else 'enable'
            raw[action] = list(dict.fromkeys([*raw.get(action, []), *a.names]))
            raw[opposite] = [name for name in raw.get(opposite, []) if name not in a.names]
            if not raw[opposite]:
                raw.pop(opposite, None)
        plan = resolve(candidate, env={})
        # Fail before saving when the environment would make this graph invalid.
        effective = resolve(candidate)
        print(_render(plan))
        if a.dry_run:
            print('Dry run: config is unchanged.')
        else:
            cfg.set('harness', candidate['harness'])
            cfg.save()
            print('Saved. Restart Exo to apply the profile.')
        if os.environ.get('EXO_PROFILE'):
            print(f'EXO_PROFILE overrides the saved profile; effective profile: {effective.profile}.')
        return 0
    except ConfigError as exc:
        print(f'exo harness: {exc}', file=sys.stderr)
        return 2
