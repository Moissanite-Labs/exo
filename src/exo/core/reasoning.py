"""Saved per-route effort for future MAIN turns in one core seat."""
from __future__ import annotations

import json
from pathlib import Path

from exo.core.models.reasoning import capability
from exo.core.private import atomic_write, private_dir
from exo.principal import require_principal
from exo.server.engine import CommandFailed

CAPABILITY = 'main-route-reasoning-v1'
UNAVAILABLE = 'Saved effort preferences are unavailable. Existing settings are used; reset saved efforts to repair them.'


class ReasoningSettings:
    def __init__(self, home, seat, router):
        self.path = Path(home) / 'state' / 'reasoning' / (seat + '.json')
        self.router = router

    def load(self):
        try:
            with self.path.open(encoding="utf-8") as handle:
                raw = handle.read(524289)
        except FileNotFoundError:
            return {}
        if len(raw) > 524288:
            raise ValueError('Reasoning preferences exceed the size limit')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('Invalid reasoning preferences')
        if any(not isinstance(route, str) or value not in ('none', 'low', 'medium', 'high')
               for route, value in data.items()):
            raise ValueError('Invalid reasoning preferences')
        return data

    def snapshot(self, session, route):
        try:
            value = self.load().get(route)
        except (ValueError, OSError, TypeError, RecursionError):
            return {'_exo_reasoning_warning': UNAVAILABLE}
        if value is None:
            return {}
        if not isinstance(value, str):
            raise ValueError('Invalid saved reasoning level')
        provider, _, model = route.partition(':')
        if value not in capability(self.router.providers.get(provider, {}), model)['levels']:
            return {}  # configuration changed: stale preferences must not force unknown parameters
        return {'reasoning': value, '_exo_reasoning': {'requested': value, 'route': route}}

    def command(self, engine, raw):
        require_principal('change reasoning effort')
        try:
            if not isinstance(raw, str) or len(raw) > 4096:
                raise ValueError('Invalid reasoning request')
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get('action') not in ('list', 'set', 'reset'):
                raise ValueError('Choose list, set or reset')
            route = engine.loop.model or engine._main_ref()
            provider, _, model = route.partition(':')
            cap = capability(self.router.providers.get(provider, {}), model)
            available = True
            if body['action'] == 'reset':
                private_dir(self.path.parent)
                atomic_write(self.path, '{}\n')
            try:
                data = self.load()
            except (ValueError, OSError, TypeError, RecursionError):
                data, available = {}, False
            if body['action'] == 'set':
                if not available:
                    raise ValueError('Reset unavailable preferences before saving')
                if 'level' not in body:
                    raise ValueError('Choose a level or explicit inherit')
                level = body['level']
                if body.get('route') != route:
                    raise ValueError('The main model changed; refresh before saving')
                if level is not None and level not in cap['levels']:
                    raise ValueError('This route does not support the requested level')
                if level is None:
                    data.pop(route, None)
                else:
                    data[route] = level
                private_dir(self.path.parent)
                atomic_write(self.path, json.dumps(data) + '\n')
            return {'capability': CAPABILITY, 'route': route, **cap,
                    'scope': 'Saved per-route default for future MAIN turns in this core and seat; other roles are unchanged.',
                    'seat': engine.agent, 'level': data.get(route),
                    'preferences_available': available, 'warning': '' if available else UNAVAILABLE,
                    'last_call': engine.store.latest_model_call_reasoning()}
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            raise CommandFailed('bad_reasoning', 'Reasoning setting refused; refresh and choose a supported route and level') from None
