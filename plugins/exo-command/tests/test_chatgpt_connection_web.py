"""Guarded principal-owned setup and mounted browser controls, with no real authorization."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from browser import Browser, Served, find_chrome
from exo_command import providers
from exo_command.source import SourceError
from test_providers_routes import raw, serve


class ConnectionSource:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure
        self.state = {'supported': True, 'available': False, 'active': None, 'profiles': [],
                      'pending': False, 'message': '', 'store_available': True}

    async def call(self, body):
        self.calls.append(body)
        if body.TYPE == 'command.run' and body.name == 'chatgpt':
            if self.failure:
                raise SourceError(self.failure, 'not echoed raw upstream details')
            args = json.loads(body.args)
            if args['action'] == 'start':
                self.state.update(pending=True, message='Waiting for ChatGPT sign-in.')
            if args['action'] == 'cancel':
                self.state.update(pending=False, message='ChatGPT sign-in cancelled.')
            if args['action'] == 'disconnect':
                self.state.update(available=False, active=None, message='ChatGPT disconnected.')
            return {'output': dict(self.state)}
        if body.TYPE == 'models.list':
            return {'models': [], 'available': []}
        if body.TYPE == 'session.list':
            return {'sessions': []}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        pass


def test_chatgpt_routes_require_launch_guard_before_core_calls():
    src = ConnectionSource()
    async def run(web, port):
        status, _, _ = await raw(port, 'POST', '/api/providers/chatgpt', body={'action': 'start'})
        assert status == 403 and not src.calls
        status, _, _ = await raw(port, 'POST', '/api/providers/chatgpt',
            headers={'X-Exo-Token': web.token, 'Origin': 'https://untrusted.invalid'}, body={'action': 'start'})
        assert status == 403 and not src.calls
        status, result, _ = await raw(port, 'POST', '/api/providers/chatgpt',
            headers={'X-Exo-Token': web.token}, body={'action': 'start', 'access_token': 'must-not-forward'})
        assert status == 200 and result['pending']
        assert json.loads(src.calls[-1].args) == {'action': 'start'}
    serve(run, src)


@pytest.mark.parametrize('failure,expected', [('not_found', 409), ('forbidden', 403)])
def test_old_or_nonprincipal_core_refuses_without_local_storage(failure, expected):
    src = ConnectionSource(failure)
    result = asyncio.run(providers.handle(SimpleNamespace(source=src), 'POST', '/api/providers/chatgpt', {'action': 'start'}))
    assert result[0] == expected and 'not echoed' not in json.dumps(result)
    assert len(src.calls) == 1


@pytest.mark.skipif(not find_chrome(), reason='headless Chrome required')
def test_mounted_continue_cancel_and_usage_controls_do_not_change_default():
    src = ConnectionSource()
    web = Served(src)
    try:
        with Browser() as browser:
            browser.goto(web.url('providers'))
            browser.wait('document.body.textContent.includes("Continue with ChatGPT")')
            browser.eval('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Continue with ChatGPT").click()')
            browser.wait('document.body.textContent.includes("Cancel sign-in")')
            assert browser.eval('document.body.textContent.includes("does not change your default model")')
            assert browser.eval('document.querySelector("a[href=\\"https://chatgpt.com/settings/usage\\"]").textContent') == 'Manage ChatGPT usage'
            browser.eval('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Cancel sign-in").click()')
            browser.wait('document.body.textContent.includes("ChatGPT sign-in cancelled.")')
            assert not [b for b in src.calls if b.TYPE in ('models.set', 'config.set')]
            posted = [json.loads(b.args)['action'] for b in src.calls if b.TYPE == 'command.run' and b.name == 'chatgpt']
            assert posted.count('start') == 1 and posted.count('cancel') == 1
            assert browser.eval('Object.keys(localStorage).length') == 0

    finally:
        web.close()
