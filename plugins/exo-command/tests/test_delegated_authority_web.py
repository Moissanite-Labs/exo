"""Principal web entry uses a capability check, never a model turn or a client-side grant."""
import asyncio
from types import SimpleNamespace

import pytest

from browser import Browser, Served
from exo_command import api
from test_layout_web import ThreadSrc, needs_chrome


class AuthoritySource(ThreadSrc):
    def __init__(self, supported):
        super().__init__(0)
        self.supported, self.calls = supported, []
    async def call(self, body):
        self.calls.append(body)
        if type(body).TYPE == 'command.run' and body.name == 'approval-authority':
            return {'output': {'supports_delegated_approval': self.supported, 'enabled': body.args == 'grant'}}
        return await super().call(body)


@pytest.mark.parametrize('supported', [False, True])
def test_api_checks_old_core_before_forwarding_grant(supported):
    source = AuthoritySource(supported)
    status, body = asyncio.run(api.dispatch(SimpleNamespace(source=source), 'POST', '/api/approval-authority', {},
                                           {'session': 's1', 'action': 'grant'}))
    assert status == (200 if supported else 400)
    assert [b.args for b in source.calls] == (['status', 'grant'] if supported else ['status'])
    assert all(b.session == 's1' for b in source.calls)


@needs_chrome
def test_web_old_core_preserves_command_draft_without_turn_send():
    source = AuthoritySource(False)
    server = Served(source)
    try:
        with Browser() as browser:
            browser.goto(server.url())
            browser.wait("document.querySelector('.xc-sess.on[data-session=s1]')?.textContent.includes('demo')")
            browser.eval("(()=>{const input=document.querySelector('.xc-input');input.value='/approval-authority grant';input.dispatchEvent(new Event('input'));input.focus();})()")
            browser.press('Enter', 'Enter', 13)
            browser.wait("document.querySelector('.xc-strip').textContent.includes('does not support')")
            assert browser.eval("document.querySelector('.xc-input').value") == '/approval-authority grant'
            assert not any(type(body).TYPE == 'turn.send' for body in source.calls)
            assert [body.args for body in source.calls if type(body).TYPE == 'command.run' and body.name == 'approval-authority'] == ['status']
    finally:
        server.close()
