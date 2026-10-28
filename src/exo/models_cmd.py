"""`exo models`: configured providers, key found (yes or no), resolved route per role.

    exo models                      providers and roles, no secrets
    exo models --remote PROVIDER    ask the provider's /models endpoint (network, only when asked)
    exo models --prices             the price table: rates, window in force, source and age (D-090)
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


CACHE_TTL = 6 * 3600.0


def render_prices(router: Router, *, today=None, at=None) -> str:
    """The price table (D-090): the rates charged right now, where they were read, and how old that is.
    A route with no price is listed as ``unknown``, never as free."""
    from .core.models import prices
    today = today or prices.utc_now().date()
    catalog = {'models': router.models}
    rows: list[tuple[str, str, str]] = []
    for ref in sorted(router.models):
        info = router.models.get(ref) or {}
        rates = prices.rates_at(info, at)
        if rates is None:
            rows.append((ref, 'unknown', 'no price in the catalog: cost reads unknown, never $0'))
            continue
        hit = next((w for w in prices.windows(info) if prices.matches(w, at)), None)
        marked = 'peak rate' if hit is None and prices.windows(info) else ''
        if hit is not None:
            marked = 'window in force'
        where = prices.in_force(info, at) or {}
        rate_text = ' '.join(f'{k} {v:g}' for k, v in rates.items())
        age = prices.age_days(where, today)
        source = where.get('source') or 'no source'
        rows.append((ref, rate_text, f"{marked + ', ' if marked else ''}{source}, "
                                     f"{'undated' if age is None else f'read {age}d ago'}"))
    width = max((len(r[0]) for r in rows), default=10)
    lines = ['prices (USD per million tokens; a window replaces the rates it names, D-090):']
    for ref, rates, note in rows:
        lines.append(f'  {ref:<{width}}  {rates}')
        lines.append(f'  {"":<{width}}  {note}')
    problems = prices.problems(catalog)
    stale_rows = prices.stale(catalog, today)
    if problems:
        lines.append('problems:')
        lines += [f'  {p}' for p in problems]
    if stale_rows:
        lines.append(f'not re-read in over {prices.STALE_PRICE_DAYS} days (or undated):')
        lines += [f"  {r['route']} (read {r['age_days']}d ago)" if r['age_days'] is not None
                  else f"  {r['route']} (no date)" for r in stale_rows]
    return '\n'.join(lines)


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
        if spec.get('transport') in ('claude-cli', 'muse-cli'):  # static list; offered only when the binary is on PATH
            if router._allows(name, "main") and router.credentials(name):
                out += [{'route': f'{name}:{m}', 'provider': name} for m in spec.get('models') or []]
            continue
        if (spec.get('transport', 'openai') != 'openai' or spec.get('local') or st.get('key') != 'yes'
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
    p.add_argument('--prices', action='store_true',
                   help='the price table: rates in force now, where they were read, and how old (D-090)')


def cmd_models(a) -> int:
    router = build_router()
    if getattr(a, 'prices', False):
        print(render_prices(router))
        return 0
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
