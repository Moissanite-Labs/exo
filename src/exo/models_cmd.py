"""`exo models`: configured providers, key found (yes or no), resolved route per role.

    exo models                      providers and roles, no secrets
    exo models --remote PROVIDER    ask the provider's /models endpoint (network, only when asked)
"""
from __future__ import annotations

import sys
from typing import Any, Callable

from .core.models.router import Router


def build_router(env: dict | None = None) -> Router:
    from .config import Config
    return Router.from_config(Config.load().data.get('models'), env=env)


def render(router: Router) -> str:
    lines = ['providers (key: yes or no, never the value):']
    for p in router.providers_status():
        env = ','.join(p['key_env']) or '-'
        tag = ' local' if p['local'] else ''
        only = f" roles={','.join(p['roles'])}" if p['roles'] else ''
        lines.append(f"  {p['provider']:<12} key={p['key']:<3} {env:<22} {p['base_url']}{tag}{only}")
    lines.append('roles (first route serves, the rest are fallbacks):')
    for role, refs in router.resolved_roles().items():
        lines.append(f"  {role:<9} {' > '.join(refs) or '-'}")
    return '\n'.join(lines)


def remote_models(router: Router, provider: str, *, get: Callable[..., Any] | None = None) -> list[str]:
    """Model ids from ``{base_url}/models`` of an OpenAI-compatible provider."""
    spec = router.providers.get(provider)
    if spec is None:
        raise ValueError(f'unknown provider {provider!r}')
    if spec.get('transport', 'openai') != 'openai':
        raise ValueError(f'provider {provider!r} has no OpenAI-style /models endpoint')
    envs = spec.get('key_env') or []
    envs = [envs] if isinstance(envs, str) else list(envs)
    key = next((k for e in envs if (k := router._key(e))), None)
    headers = {'authorization': f'Bearer {key}'} if key else {}
    if get is None:
        import httpx
        get = httpx.get
    try:
        resp = get(spec['base_url'].rstrip('/') + '/models', headers={**headers, **(spec.get('headers') or {})},
                   timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # never echo request headers
        raise ValueError(f'{provider}: could not list models ({type(e).__name__})') from None
    items = data.get('data') if isinstance(data, dict) else data
    return sorted(str(m.get('id', m)) if isinstance(m, dict) else str(m) for m in items or [])


def register(sp, common) -> None:
    p = sp.add_parser('models', help='configured providers, keys found, and the route for each role')
    p.add_argument('--remote', metavar='PROVIDER', help="list the provider's models over the network")


def cmd_models(a) -> int:
    router = build_router()
    if a.remote:
        try:
            for m in remote_models(router, a.remote):
                print(m)
        except ValueError as e:
            print(f'exo models: {e}', file=sys.stderr)
            return 1
        return 0
    print(render(router))
    return 0
