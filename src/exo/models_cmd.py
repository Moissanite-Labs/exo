"""`exo models`: configured providers, key found (yes or no), resolved route per role.

    exo models                      providers and roles, no secrets
    exo models --remote PROVIDER    ask the provider's /models endpoint (network, only when asked)
"""
from __future__ import annotations

from pathlib import Path

import sys
from typing import Any, Callable

from .core.models.router import Router


def build_router(env: dict | None = None) -> Router:
    from .config import Config
    return Router.from_config(Config.load().models(), env=env)


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
    from .core.tools.builtin.codefind import status as code_find_status
    lines.append('tools:')
    lines.append(f'  {code_find_status()}')
    return '\n'.join(lines)


def _remote_catalog(router: Router, provider: str, *, get=None) -> list[dict]:
    """Public model endpoint. SIWC catalog is account-specific and deliberately never cached."""
    import asyncio
    spec = router.providers.get(provider)
    if spec is None:
        raise ValueError(f'unknown provider {provider!r}')
    if spec.get('transport', 'openai') not in ('openai', 'responses', 'deepseek'):
        raise ValueError(f'provider {provider!r} has no OpenAI-style /models endpoint')
    subscription = spec.get('auth') == 'chatgpt'
    scope_callback = router.provider_auth.get(provider, (None, None, lambda: ''))
    scope_callback = scope_callback[2] if len(scope_callback) > 2 else lambda: ''
    scope = scope_callback() if subscription else ''
    if subscription:
        if spec.get('base_url') != 'https://api.openai.com/v1' or not router.auth_available(provider):
            raise ValueError('ChatGPT plan usage is not connected')
        try:
            headers = asyncio.run(router.provider_auth[provider][1]())
        except Exception:
            raise ValueError('ChatGPT authorization unavailable') from None
    else:
        envs = spec.get('key_env') or []
        envs = [envs] if isinstance(envs, str) else list(envs)
        key = next((k for e in envs if (k := router._key(e))), None)
        headers = {**({'authorization': f'Bearer {key}'} if key else {}), **(spec.get('headers') or {})}
    if get is None:
        import httpx
        get = httpx.get
    try:
        resp = get(spec['base_url'].rstrip('/') + '/models', headers=headers, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
        if subscription:
            if scope != scope_callback():
                raise ValueError('Selected account changed')
            items = data['models']
            if not isinstance(items, list):
                raise ValueError()
            return [{'id': item['slug'], 'display_name': item.get('display_name', item['slug'])}
                    for item in items if isinstance(item, dict) and item.get('visibility') == 'list'
                    and isinstance(item.get('slug'), str) and item['slug']]
        items = data.get('data') if isinstance(data, dict) else data
        return sorted([{'id': str(m.get('id', m)) if isinstance(m, dict) else str(m)} for m in items or []],
                      key=lambda item: item['id'])
    except Exception as exc:
        raise ValueError(f'{provider}: could not list models ({type(exc).__name__})') from None


def remote_models(router: Router, provider: str, *, get: Callable[..., Any] | None = None) -> list[str]:
    return [row['id'] for row in _remote_catalog(router, provider, get=get)]


CACHE_TTL = 6 * 3600.0


def available_models(router: Router, home: Path, *, get: Callable[..., Any] | None = None,
                     now: float | None = None) -> list[dict[str, Any]]:
    """Every model the user can pick: each keyed provider's /models list, cached for six hours in
    ``home/cache/models``. A provider that fails to answer is skipped (its stale cache is used if any).
    Models a provider lists but Exo cannot call yet (``hide_models`` substrings) are left out."""
    import json, time
    now = time.time() if now is None else now
    out: list[dict[str, Any]] = []
    cache_dir = Path(home) / 'cache' / 'models'
    for st in router.providers_status():
        name, spec = st['provider'], router.providers.get(st['provider'], {})
        if spec.get('auth') == 'chatgpt':
            if router.auth_available(name) and router._allows(name, 'main'):
                try:
                    out += [{'route': f"{name}:{row['id']}", 'provider': name,
                             'display_name': row['display_name']} for row in _remote_catalog(router, name, get=get)]
                except ValueError:
                    pass  # never reuse another account's catalog after an authorization failure
            continue
        if spec.get('transport') in ('claude-cli', 'muse-cli'):  # static list; offered only when the binary is on PATH
            if router._allows(name, "main") and router.credentials(name):
                out += [{'route': f'{name}:{m}', 'provider': name} for m in spec.get('models') or []]
            continue
        if (spec.get('transport', 'openai') not in ('openai', 'responses', 'deepseek') or spec.get('local') or st.get('key') != 'yes'
                or (st.get('roles') and 'main' not in st['roles'])):  # e.g. decision-only providers
            continue
        f = cache_dir / f'{name}.json'
        ids: list[str] | None = None
        try:
            cached = json.loads(f.read_text())
            if now - cached['at'] < CACHE_TTL:
                ids = cached['ids']
        except (OSError, ValueError, KeyError):
            cached = None
        if ids is None:
            try:
                ids = remote_models(router, name, get=get)
                cache_dir.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps({'at': now, 'ids': ids}))
            except ValueError:
                ids = cached['ids'] if cached else []
        hide = [h.lower() for h in spec.get('hide_models') or []]
        out += [{'route': f'{name}:{i}', 'provider': name} for i in ids if not any(h in i.lower() for h in hide)]
    return out


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
