"""Pure, dependency-aware composition plans for optional harness capabilities.

The absence of ``harness`` configuration preserves legacy discovery. An explicit
profile owns the built-in plugin selection; configured third-party plugins are
kept. Resolving a plan never imports plugins, starts services, or writes config.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from .config import ConfigError


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    plugin: str | None = None
    requires: tuple[str, ...] = ()


# Stable display and loading order; dependencies precede their consumers.
CATALOG = (
    Capability('gate', 'Exact-text tool approvals; required in every profile.', 'exo-gate'),
    Capability('memory', 'Persistent principal memory.', 'exo-memory'),
    Capability('context', 'Plugin context compression.', 'exo-context', ('memory',)),
    Capability('skills', 'Progressively loaded SKILL.md instructions.'),
    Capability('instructions', 'Scoped project instructions from AGENTS.md and compatible files.'),
    Capability('mcp', 'Configured Model Context Protocol servers.'),
    Capability('jobs', 'Background command jobs.'),
    Capability('delegation', 'Subagent delegation.'),
    Capability('peers', 'Messaging between agent processes.'),
    Capability('scheduler', 'Scheduled agent runs.'),
    Capability('learning', 'Evidence-backed memory learning.', requires=('memory',)),
    Capability('bus', 'Configured channel integrations.', 'exo-bus'),
    Capability('web', 'Browser command centre plugin.', 'exo-command'),
    Capability('orchestra', 'Orchestration skills and roles.', 'exo-orchestra', ('delegation', 'skills')),
)
CAPABILITIES = {cap.name: cap for cap in CATALOG}
PROFILES = {
    'lean': ('gate',),
    'coding': ('gate', 'memory', 'context', 'skills', 'instructions', 'mcp', 'jobs', 'delegation'),
    'full': tuple(CAPABILITIES),
}
PROFILE_DESCRIPTIONS = {
    'lean': 'Core coding tools, sessions, tracing and approvals.',
    'coding': 'Memory, project instructions, skills, MCP, jobs and subagents.',
    'full': 'Every optional capability, with services configured separately.',
}


@dataclass
class HarnessPlan:
    profile: str
    capabilities: tuple[str, ...]
    plugins: list[str] | None
    settings: dict[str, Any]
    explicit: bool
    reasons: dict[str, str] = field(default_factory=dict)

    def enabled(self, name: str) -> bool:
        if name not in CAPABILITIES:
            raise ConfigError(f'unknown harness capability {name!r}')
        return name in self.capabilities

    def summary(self) -> dict[str, Any]:
        """Safe inspection payload: no provider, environment, or plugin config values."""
        setting_disabled = set()
        for name, key in (('delegation', 'delegation'), ('jobs', 'jobs'), ('peers', 'peers'),
                          ('learning', 'learn')):
            value = self.settings.get(key)
            if isinstance(value, dict) and value.get('enabled') is False:
                setting_disabled.add(name)
        if self.settings.get('schedule') is False:
            setting_disabled.add('scheduler')
        return {'profile': self.profile, 'explicit': self.explicit,
                'capabilities': list(self.capabilities),
                'disabled': [name for name in CAPABILITIES if name not in self.capabilities],
                'plugins': self.plugins,
                'details': {name: {'selected': name in self.capabilities,
                                   'reason': self.reasons[name],
                                   'setting_disabled': name in setting_disabled}
                            for name in CAPABILITIES},
                'note': 'Selection permits a capability; its own settings can still disable it. '
                        'Plugin availability and external services are not probed.'}


def _names(value: Any, label: str, *, known: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise ConfigError(f'{label} must be a list of nonempty names')
    if known and (unknown := sorted(set(value) - CAPABILITIES.keys())):
        raise ConfigError(f'{label}: unknown capabilities: {", ".join(unknown)}')
    return list(dict.fromkeys(value))


def _legacy_plugins(value: Any) -> list[str] | None:
    """The names the loader matches without a profile. It reads core_plugins as a set and refuses nothing, so an
    entry that is not a nonempty name, or a string value, selects no plugin."""
    if value is None:
        return None
    if not isinstance(value, (list, dict)):
        return []
    return list(dict.fromkeys(name for name in value if isinstance(name, str) and name))


def _disable(settings: dict[str, Any], key: str) -> None:
    old = settings.get(key)
    settings[key] = {**(old if isinstance(old, dict) else {}), 'enabled': False}


def resolve(data: dict[str, Any], *, env: Mapping[str, str] | None = None) -> HarnessPlan:
    """Resolve a profile and dependency closure without changing ``data``.

    ``EXO_PROFILE`` overrides only the base profile, retaining configured enable
    and disable lists. Disabled dependencies are errors, never silently enabled.
    Profiles restrict existing subsystem settings; they do not erase configuration
    or turn on a service explicitly disabled in its own settings.
    """
    if not isinstance(data, dict):
        raise ConfigError('harness configuration must be an object')
    env = os.environ if env is None else env
    override = env.get('EXO_PROFILE')
    settings = copy.deepcopy(data)
    if 'harness' not in data and not override:
        # Without a profile, a config that starts on main still starts: only a profile checks core_plugins.
        return HarnessPlan('legacy', tuple(n for n in CAPABILITIES if n != 'instructions'),
                           _legacy_plugins(data.get('core_plugins')), settings, False,
                           {name: 'default:off' if name == 'instructions' else 'default:legacy'
                            for name in CAPABILITIES})
    plugins = data.get('core_plugins')
    if plugins is not None:
        plugins = _names(plugins, 'core_plugins', known=False)
    raw = data.get('harness', {})
    if not isinstance(raw, dict):
        raise ConfigError('harness must be an object')
    if unknown := sorted(str(key) for key in raw if key not in {'profile', 'enable', 'disable'}):
        raise ConfigError(f'unknown harness settings: {", ".join(unknown)}')
    profile = override or raw.get('profile', 'full')
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ConfigError(f'{"EXO_PROFILE" if override else "harness.profile"} must be lean, coding or full')
    enable = set(_names(raw.get('enable', []), 'harness.enable'))
    disable = set(_names(raw.get('disable', []), 'harness.disable'))
    if both := sorted(enable & disable):
        raise ConfigError(f'capabilities cannot be both enabled and disabled: {", ".join(both)}')
    if 'gate' in disable:
        raise ConfigError('gate is required in every harness profile and cannot be disabled')
    selected = (set(PROFILES[profile]) | enable) - disable
    reasons = {name: ('disabled:explicit' if name in disable else 'explicit enable' if name in enable
                      else f'profile:{profile}' if name in selected else 'default:off')
               for name in CAPABILITIES}

    def include(name: str, trail: tuple[str, ...] = ()) -> None:
        if name in disable:
            raise ConfigError(f'harness capability {" -> ".join((*trail, name))} is required but disabled; '
                              'enable it or disable its dependants')
        if name not in selected:
            reasons[name] = f'dependency:{trail[-1]}'
            selected.add(name)
        for dep in CAPABILITIES[name].requires:
            include(dep, (*trail, name))

    for name in CAPABILITIES:
        if name in selected:
            include(name)
    ordered = tuple(name for name in CAPABILITIES if name in selected)
    builtins = {cap.plugin for cap in CATALOG if cap.plugin}
    selected_plugins = [cap.plugin for cap in CATALOG if cap.name in selected and cap.plugin]
    selected_plugins.extend(name for name in plugins or [] if name not in builtins)
    settings['core_plugins'] = selected_plugins
    for capability, key in (('delegation', 'delegation'), ('jobs', 'jobs'), ('peers', 'peers'),
                            ('learning', 'learn')):
        if capability not in selected:
            _disable(settings, key)
    if 'scheduler' not in selected:
        settings['schedule'] = False
    if 'mcp' not in selected:
        settings['mcp'] = {}
    if 'bus' not in selected:
        settings['channels'] = {}
    return HarnessPlan(profile, ordered, selected_plugins, settings, True, reasons)
