"""Guarded HTTP edge for the selected core's reasoning capability."""
import json

from exo.protocol import schema as s

CAPABILITY = 'main-route-reasoning-v1'


async def handle(centre, method, body):
    try:
        result = await centre.source.call(s.CommandRun(name='reasoning', args='{"action":"list"}'))
        data = result.get('output')
        if not isinstance(data, dict) or data.get('capability') != CAPABILITY:
            return 503, {'error': 'The selected core needs reasoning controls support', 'upgrade_required': True}
        if method == 'GET':
            return 200, data
        if method != 'POST':
            return 405, {'error': 'GET or POST only'}
        request = {key: body.get(key) for key in ('route', 'level')}
        request['action'] = 'reset' if body.get('action') == 'reset' else 'set'
        result = await centre.source.call(s.CommandRun(name='reasoning', args=json.dumps(request)))
        return 200, result['output']
    except Exception as exc:
        code = getattr(exc, 'code', '')
        if code in ('not_found', 'unknown_command', 'unsupported'):
            return 503, {'error': 'The selected core needs reasoning controls support', 'upgrade_required': True}
        if code == 'forbidden':
            return 403, {'error': 'Reasoning settings require principal authority'}
        return 400, {'error': 'Reasoning setting refused; refresh and choose a supported route and level'}
