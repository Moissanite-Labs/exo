"""The guarded HTTP editor reaches a real principal-stamped core without inference."""

from test_providers_routes import Src, ok_headers, raw, serve
from exo.config import Config
from exo.core.engine import CoreEngine
from exo_tui.conn import LocalClient


def test_worker_routes_apply_and_stale_http_write_is_refused(tmp_path):
    eng = CoreEngine(tmp_path / 'engine', {'roles': {'main': ['fake:main']}}, env={})
    client = LocalClient(eng)

    class CoreSrc(Src):
        async def call(self, body):
            return await client.call(body)

    async def scenario(web, port):
        try:
            status, before, _ = await raw(port, 'GET', '/api/providers/workers', ok_headers(port))
            assert status == 200 and before['editable']
            roles = {'fast': ['fake:quick'], 'standard': ['fake:normal', 'fake:backup'], 'deep': []}
            payload = {'roles': roles, 'revision': before['revision']}
            status, out, _ = await raw(port, 'POST', '/api/providers/workers', ok_headers(port), payload)
            assert status == 200 and out['saved'] and out['applied'] and out['roles'] == roles
            assert eng.router.roles['main'] == ['fake:main']
            assert Config.load(eng.home).get('models.roles.standard') == roles['standard']
            status, error, _ = await raw(port, 'POST', '/api/providers/workers', ok_headers(port), payload)
            assert status == 409 and 'Reload' in error['error']
            status, _, _ = await raw(port, 'POST', '/api/providers/workers', {}, payload)
            assert status == 403
        finally:
            await client.aclose()
    serve(scenario, CoreSrc())


def test_worker_route_reports_older_core_without_disk_fallback():
    from exo_command.source import SourceError
    class OldSrc(Src):
        async def call(self, body):
            raise SourceError('not_found', 'no worker_models')
    async def scenario(web, port):
        status, body, _ = await raw(port, 'GET', '/api/providers/workers', ok_headers(port))
        assert status == 501 and 'newer core' in body['error']
    serve(scenario, OldSrc())
