"""Guarded effort API and the real standalone UI, using a disposable fake core."""
import asyncio
import importlib.util
import json
import urllib.error
import urllib.request

import pytest

from browser import Browser, Served, find_chrome
from exo.core.models import Router
from exo_command import reasoning
from exo_command.source import SourceError


class Source:
    centre = None
    def __init__(self, old=False):
        self.old = old
        self.writes = []
        self.data = {'capability': reasoning.CAPABILITY, 'route': 'openai:gpt-5.1', 'seat': 'fake-seat',
                     'supported': True, 'levels': ['none', 'low', 'medium', 'high'], 'level': None,
                     'reason': 'Verified Chat Completions values', 'last_call': {}}
    async def call(self, body):
        if getattr(body, 'name', '') == 'reasoning':
            if self.old:
                raise SourceError('not_found', 'old core')
            request = json.loads(body.args)
            if request['action'] == 'set':
                self.writes.append(request)
                self.data['level'] = request['level']
            elif request['action'] == 'reset':
                self.writes.append(request)
                self.data['level'] = None
            return {'output': dict(self.data)}
        return {}
    async def stream(self, since=0):
        if False:
            yield {}
    async def aclose(self):
        pass


def request(srv, method='GET', body=None, **headers):
    req = urllib.request.Request('http://127.0.0.1:' + str(srv.port) + '/api/reasoning', method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'X-Exo-Token': srv.token, 'Content-Type': 'application/json', **headers})
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def test_old_core_is_checked_before_settings_are_forwarded():
    source = Source(old=True)
    result = asyncio.run(reasoning.handle(type('Centre', (), {'source': source})(), 'POST', {'level': 'high'}))
    assert result[0] == 503 and result[1]['upgrade_required']
    assert not source.writes


def test_http_guard_refuses_cross_origin_and_allows_guarded_setting():
    source = Source(); srv = Served(source)
    try:
        code, _ = request(srv, 'POST', {'route': source.data['route'], 'level': 'high'}, Origin='http://evil.example')
        assert code == 403 and not source.writes
        code, data = request(srv, 'POST', {'route': source.data['route'], 'level': 'high'})
        assert code == 200 and data['level'] == 'high'
        assert source.writes == [{'action': 'set', 'route': 'openai:gpt-5.1', 'level': 'high'}]
    finally:
        srv.close()


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='headless Chrome unavailable')
def test_browser_explicit_save_keyboard_button_inherit_and_unknown_gateway():
    source = Source(); srv = Served(source); browser = Browser()
    try:
        srv.web.router_factory = lambda: Router(catalog={}, env={}, key_sources=False)
        browser.goto(srv.url("providers"))
        browser.wait('!!(document.body && document.querySelector(".xc-root"))')
        browser.wait('sessionStorage.getItem("xc-token") === ' + json.dumps(srv.token))
        browser.eval('window.effort=()=>[...document.querySelectorAll("select")].find(e=>e.getAttribute("aria-label")==="Reasoning effort"); window.saveEffort=()=>[...document.querySelectorAll("button")].find(e=>e.textContent==="Save main-route default")')
        browser.wait('effort()?.options.length === 5')
        assert not source.writes
        assert 'Inherit existing settings' in browser.eval('document.body.textContent')
        assert 'future MAIN turns' in browser.eval('document.body.textContent')
        # Hold an older Refresh response while a newer Save succeeds.
        browser.eval('''window.originalFetch=window.fetch; window.holdNextReasoning=true;
            window.fetch=(url, opts)=>{const hold=url==='/api/reasoning' && opts?.method==='GET' && window.holdNextReasoning;
              if(hold)window.holdNextReasoning=false;
              const pending=window.originalFetch(url,opts);
              return hold ? pending.then(response=>new Promise(resolve=>{window.releaseOldReasoning=()=>resolve(response)})) : pending;};
            [...document.querySelectorAll('button')].find(x=>x.textContent==='Refresh reasoning').click();''')
        browser.wait('typeof window.releaseOldReasoning === "function"')
        browser.eval('effort().value="high"; saveEffort().focus()')
        browser.press(' ', 'Space', 32)
        browser.wait('document.body.textContent.includes("Saved for future MAIN turns")')
        assert source.writes[-1]['level'] == 'high'
        browser.eval('window.releaseOldReasoning(); new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
        assert browser.eval('effort().value') == 'high'
        browser.eval('effort().value=""; saveEffort().click()')
        browser.wait('effort().value === ""')
        assert source.writes[-1]['level'] is None
        before_reset = len(source.writes)
        browser.eval('document.querySelector("[data-reset-efforts]").click()')
        browser.wait('!!document.querySelector("[data-confirm-reset]")')
        assert len(source.writes) == before_reset
        assert browser.eval('document.activeElement.hasAttribute("data-confirm-reset")')
        browser.press('Escape', 'Escape', 27)
        assert browser.eval('document.querySelector("[data-confirm-reset]") === null')
        assert len(source.writes) == before_reset
        browser.eval('document.querySelector("[data-reset-efforts]").click()')
        browser.press(' ', 'Space', 32)
        browser.wait('document.querySelector("[data-confirm-reset]") === null && !saveEffort().disabled')
        assert source.writes[-1]['action'] == 'reset'
        source.data['last_call'] = {'requested': 'high', 'sent': None, 'route': 'backup:unknown',
                                    'status': 'unsupported; provider default'}
        browser.eval('[...document.querySelectorAll("button")].find(x=>x.textContent==="Refresh reasoning").click()')
        browser.wait('document.body.textContent.includes("provider default (no override)")')
        source.data['last_call'] = {'requested': 'high', 'sent': None, 'route': '', 'status': 'not sent'}
        browser.eval('[...document.querySelectorAll("button")].find(x=>x.textContent==="Refresh reasoning").click()')
        browser.wait('document.body.textContent.includes("sent to adapter not sent")')
        source.data.update(route='opencode-go:deepseek-v4-pro', supported=False, levels=[],
                           reason='OpenCode Go gateway effort forwarding is not verified.')
        browser.eval('[...document.querySelectorAll("button")].find(x=>x.textContent==="Refresh reasoning").click()')
        browser.wait('document.body.textContent.includes("gateway effort forwarding is not verified")')
        assert browser.eval('effort().disabled')
        assert browser.eval('saveEffort().disabled')
        browser.eval('location.hash="#chat"')
        browser.wait('!!document.querySelector(".xc-chat")')
    finally:
        browser.close(); srv.close()
