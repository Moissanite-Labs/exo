"""Verified reasoning controls for the current transport, not model-name guesses."""
from urllib.parse import urlsplit

OPENAI_SOURCE = 'https://developers.openai.com/api/docs/models/gpt-5.1'
DEEPSEEK_SOURCE = 'https://api-docs.deepseek.com/guides/thinking_mode/'


def capability(spec, model):
    try:
        url = urlsplit(spec.get('base_url', ''))
        port = url.port
    except ValueError:
        url = None
    if (url and url.scheme == 'https' and url.hostname == 'api.openai.com'
            and port in (None, 443) and url.path.rstrip('/') == '/v1'
            and url.username is None and url.password is None and not url.query and not url.fragment
            and spec.get('transport', 'openai') == 'openai'
            and model in ('gpt-5.1', 'gpt-5.1-2025-11-13')):
        return {'supported': True, 'levels': ['none', 'low', 'medium', 'high'], 'source': OPENAI_SOURCE,
                'reason': 'Verified Chat Completions effort values. Availability still depends on the provider.'}
    reason = 'This route has no verified effort contract for the installed transport.'
    if url and url.hostname == 'api.deepseek.com':
        reason = 'DeepSeek thinking with tools needs provider-safe reasoning replay (core issue #616 / PR #656).'
    elif url and url.hostname == 'opencode.ai':
        reason = 'OpenCode Go effort forwarding is not verified. Direct DeepSeek documentation does not establish gateway support.'
    elif model.startswith('gpt-6'):
        reason = 'This GPT-6 route is outside the verified Chat Completions effort subset.'
    return {'supported': False, 'levels': [], 'source': '', 'reason': reason}


def attempt_options(providers, route, opts):
    """Only explicitly selected controls are filtered. Inherit preserves legacy options."""
    out = dict(opts)
    request = out.pop('_exo_reasoning', None)
    if request is None:
        return out, {}
    requested = request['requested']
    cap = capability(providers.get(route.provider, {}), route.model)
    sent = requested if requested in cap['levels'] else None
    if sent is None:
        out.pop('reasoning', None)
    else:
        out['reasoning'] = sent
    return out, {'requested': requested, 'sent': sent, 'route': route.ref,
                 'requested_route': request['route'],
                 'status': 'sent' if sent is not None else 'unsupported; provider default'}
