"""Explicit full mode is capability-gated and bound to a real selected session."""
import pytest

from exo.protocol import schema as s
from exo_command.source import SourceError
from test_permissions_web import PermSource, call


class FullSource(PermSource):
    def __init__(self, supported=True, available=True):
        super().__init__()
        self.state.update(supports_full_all=supported, supports_full=supported, full_available=available, full_session=None, fallback_mode='ask')
    async def call(self, body):
        if isinstance(body, s.SessionList):
            return {'sessions': [{'id': sid, 'title': sid, 'status': 'idle'} for sid in ('s1', 's2')]}
        if isinstance(body, s.SessionGet):
            self.calls.append(body)
            if body.session not in ('s1', 's2'):
                raise SourceError('not_found', 'Session not found')
            return {'session': {'id': body.session, 'status': 'idle'}, 'messages': []}
        if isinstance(body, s.CommandRun) and body.name == 'permissions' and body.args.startswith('full '):
            self.calls.append(body)
            target = body.args.split()[1]
            self.state.update(mode='full', full_session='*' if target == 'all' else target, scope='core-all-sessions' if target == 'all' else 'session-all-turns')
            return {'output': 'permissions: full only ' + self.state['full_session']}
        return await super().call(body)


def writes(source):
    return [body for body in source.calls if isinstance(body, s.CommandRun) and body.name == 'permissions' and body.args != 'json']


def test_full_requires_capability_and_real_session_before_any_mutation():
    source = FullSource()
    status, result = call(source, 'POST', {'mode': 'full', 'session': 's1'})
    assert status == 200 and result['permissions']['full_session'] == 's1'
    assert writes(source)[0].args == 'full s1' and writes(source)[0].session == 's1'
    assert isinstance(source.calls[1], s.SessionGet)


@pytest.mark.parametrize('body', [{'mode': 'full'}, {'mode': 'full', 'session': 's1 ask'}, {'mode': 'full', 'session': 'missing'}])
def test_invalid_or_missing_selected_session_cannot_enable_full(body):
    source = FullSource()
    status, _ = call(source, 'POST', body)
    assert status in (400, 404) and not writes(source) and source.state['mode'] == 'ask'


@pytest.mark.parametrize('supported,available', [(False, True), (True, False)])
def test_old_core_or_required_executor_refused_without_changing_mode(supported, available):
    source = FullSource(supported, available)
    source.state['mode'] = 'bypass'
    status, result = call(source, 'POST', {'mode': 'full', 'session': 's1'})
    assert status == 501 and 'error' in result
    assert not writes(source) and source.state['mode'] == 'bypass'


def test_browser_full_is_scoped_and_unsupported_core_keeps_draft():
    from browser import Browser, Served, find_chrome
    if not find_chrome():
        pytest.skip('headless Chrome unavailable')
    for supported in (True, False):
        source = FullSource(supported)
        web = Served(source)
        try:
            with Browser() as browser:
                browser.goto(web.url())
                browser.wait('document.querySelector(".xc-sess.on")?.textContent.includes("s1")')
                browser.wait('!Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Send").disabled')
                browser.type('.xc-input', '/permissions full')
                browser.press('Enter', 'Enter', 13)
                if supported:
                    browser.wait('document.querySelector(".xc-perm").textContent.includes("full only s1")')
                    assert 'other threads and calls without a thread still ask' in browser.eval('document.querySelector(".xc-perm").textContent')
                    browser.eval('Array.from(document.querySelectorAll(".xc-sess")).find(b=>b.textContent.startsWith("s2")).click()')
                    browser.wait('document.querySelector(".xc-sess.on").textContent.includes("s2")')
                    assert 'full only s1' in browser.eval('document.querySelector(".xc-perm").textContent')
                    browser.eval('Array.from(document.querySelectorAll(".xc-perm button")).find(b=>b.textContent==="Ask again").click()')
                    browser.wait('document.querySelector(".xc-perm").hidden')
                else:
                    browser.wait('document.body.textContent.includes("does not support session-scoped full")')
                    assert browser.eval('document.querySelector(".xc-input").value') == '/permissions full'
                    assert not writes(source)
                assert not any(isinstance(body, s.TurnSend) for body in source.calls)
        finally:
            web.close()


def test_all_sessions_requires_explicit_target_and_its_own_capability():
    source = FullSource()
    status, result = call(source, 'POST', {'mode': 'full', 'session': 'all'})
    assert status == 200 and result['permissions']['full_session'] == '*'
    assert result['permissions']['scope'] == 'core-all-sessions'
    assert writes(source)[0].args == 'full all' and writes(source)[0].session is None
    assert not any(isinstance(body, s.SessionGet) for body in source.calls)
    source = FullSource()
    source.state['supports_full_all'] = False
    assert call(source, 'POST', {'mode': 'full', 'session': 'all'})[0] == 501
    assert not writes(source)


def test_browser_explicit_full_all_labels_every_session_including_delegates():
    from browser import Browser, Served, find_chrome
    if not find_chrome():
        pytest.skip('headless Chrome unavailable')
    source = FullSource()
    web = Served(source)
    try:
        with Browser() as browser:
            browser.goto(web.url())
            browser.wait('document.querySelector(".xc-sess.on")?.textContent.includes("s1")')
            browser.wait('!Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Send").disabled')
            browser.type('.xc-input', '/permissions full all')
            browser.press('Enter', 'Enter', 13)
            browser.wait('document.querySelector(".xc-perm").textContent.includes("full for all sessions")')
            assert 'delegated and background sessions' in browser.eval('document.querySelector(".xc-perm").textContent')
            assert 'without a thread still ask' in browser.eval('document.querySelector(".xc-perm").textContent')
            assert writes(source)[0].args == 'full all'
    finally:
        web.close()
