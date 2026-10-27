"""Write-only HTTP edge for the selected core's principal-owned credential service."""
from __future__ import annotations

import json
import re

from exo.protocol import schema as s

CAPABILITY = 'secure-seat-credentials-v1'
ACTIONS = {'set', 'remove', 'test', 'github_set', 'github_remove', 'github_test'}


def _snapshot(data):
    if not isinstance(data, dict) or data.get('capability') != CAPABILITY:
        return None
    gh = data.get('github') or {}
    return {'capability': CAPABILITY, 'seat': data.get('seat'), 'home_id': data.get('home_id'),
            'store': 'keychain', 'store_available': data.get('store_available') is True,
            'github': {k: gh.get(k) for k in ('mode', 'host', 'store', 'configured', 'secure', 'status')},
            'credentials': [{k: row.get(k) for k in ('name', 'env', 'consumers', 'enabled', 'store', 'status',
                                                    'configured', 'cleanup_pending', 'policy_blocked')}
                            for row in data.get('credentials') or [] if isinstance(row, dict)],
            'consumers': ['bash', 'jobs'], 'variable_prefix': 'EXO_SECRET_'}


async def _call(centre, body):
    result = await centre.source.call(s.CommandRun(name='credentials', args=json.dumps(body)))
    return result.get('output')


async def handle(centre, method, body):
    """The normal provider loopback/origin/page-token guard runs before this function."""
    try:
        # Probe without a secret first. Older cores never receive a secret-bearing command.
        snapshot = _snapshot(await _call(centre, {'action': 'list'}))
        if snapshot is None:
            return 503, {'error': 'The selected core needs secure credential support before this view can save anything',
                         'upgrade_required': True}
        if method == 'GET':
            return 200, snapshot
        if method != 'POST':
            return 405, {'error': 'GET or POST only'}
        if not isinstance(body, dict) or body.get('action') not in ACTIONS:
            return 400, {'error': 'Invalid credential action'}
        request = {k: body[k] for k in ('action', 'name', 'env', 'consumers', 'host', 'value') if k in body}
        try:
            result = await _call(centre, request)
        finally:
            request.clear()
            body.clear()
        if not isinstance(result, dict):
            return 502, {'error': 'The core returned an invalid credential response'}
        # Do not forward arbitrary core data, exception text, stored values or subprocess output.
        public = {k: result[k] for k in ('ok', 'disabled', 'deleted', 'removed', 'cleanup_pending', 'storage_available',
                                        'remote_authentication_tested') if isinstance(result.get(k), bool)}
        if result.get('mode') == 'disabled':
            public['disabled'] = True
        login = result.get('login')
        if result.get('ok') is True and isinstance(login, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', login):
            public['login'] = login
        return 200, public
    except Exception as exc:
        if getattr(exc, 'code', '') in ('not_found', 'unknown_command', 'unsupported'):
            return 503, {'error': 'The selected core needs an upgrade before it can manage credentials', 'upgrade_required': True}
        if getattr(exc, 'code', '') == 'forbidden':
            return 403, {'error': 'Credential setup requires principal authority on the selected core'}
        return 400, {'error': 'Credential operation failed; check settings, policy and Keychain availability'}
    finally:
        if isinstance(body, dict):
            body.clear()
