"""Write-only credential HTTP/UI edge with fake core capability; no real credentials."""
from __future__ import annotations

import asyncio
import importlib.util
import json

import httpx
import pytest

from browser import Browser, Served, find_chrome
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre
from exo_command.source import SourceError
from test_model_command_web import ModelSrc

SECRET = 'fake-browser-credential-1234567890'


class CredentialSource(ModelSrc):
    def __init__(self, supported=True):
        super().__init__()
        self.supported = supported
        self.requests = []
        self.deleted = False

    async def call(self, body):
        if type(body).TYPE == 'command.run' and body.name == 'credentials':
            request = json.loads(body.args)
            self.requests.append(request)
            if not self.supported:
                raise SourceError('not_found', 'unsupported')
            if request['action'] == 'list':
                return {'output': {'capability': 'secure-seat-credentials-v1', 'seat': 'canary', 'home_id': 'fixture',
                                   'store_available': True, 'github': {'mode': 'disabled', 'host': 'github.com'},
                                   'credentials': [], 'unexpected_secret': SECRET}}
            return {'output': {'ok': True, 'value': SECRET, 'debug': SECRET}}
        return await super().call(body)


def test_http_guard_and_old_core_refuse_without_secret_forwarding():
    async def go():
        source = CredentialSource(supported=False)
        web = WebServer(CommandCentre(load_settings({}), source), token='fixture-page-token')
        port = await web.start('127.0.0.1', 0)
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}') as client:
                for headers in ({}, {'X-Exo-Token': 'wrong'}, {'X-Exo-Token': 'fixture-page-token', 'Origin': 'https://evil.example'},
                                {'X-Exo-Token': 'fixture-page-token', 'Host': 'evil.example'},
                                {'X-Exo-Token': 'fixture-page-token', 'Sec-Fetch-Site': 'cross-site'}):
                    response = await client.post('/api/credentials', headers=headers, json={'action': 'github_set', 'value': SECRET})
                    assert response.status_code == 403 and SECRET not in response.text
                assert not source.requests
                response = await client.post('/api/credentials', headers={'X-Exo-Token': 'fixture-page-token'},
                                             json={'action': 'github_set', 'value': SECRET})
                assert response.status_code == 503 and response.json()['upgrade_required']
                assert source.requests == [{'action': 'list'}]  # older core received no value
                assert SECRET not in response.text
        finally:
            await web.close()
    asyncio.run(go())


def test_http_write_only_responses_filter_arbitrary_core_fields():
    async def go():
        source = CredentialSource()
        web = WebServer(CommandCentre(load_settings({}), source), token='fixture-page-token')
        port = await web.start('127.0.0.1', 0)
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', headers={'X-Exo-Token': 'fixture-page-token'}) as client:
                response = await client.get('/api/credentials')
                assert response.status_code == 200 and SECRET not in response.text
                assert response.json()['seat'] == 'canary'
                response = await client.post('/api/credentials', json={'action': 'github_set', 'value': SECRET, 'host': 'github.com'})
                assert response.status_code == 200 and response.json() == {'ok': True}
                assert source.requests[-1]['value'] == SECRET and SECRET not in response.text
        finally:
            await web.close()
    asyncio.run(go())


needs_browser = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='headless Chrome unavailable')


@needs_browser
@pytest.mark.parametrize(('label', 'button', 'action'), [('GitHub token (write-only)', 'Save or rotate GitHub token', 'github_set'),
                                                        ('API key (write-only)', 'Save or rotate API key', 'set')])
def test_masked_user_entry_clears_field_no_storage_or_chat(label, button, action):
    source = CredentialSource()
    web = Served(source)
    try:
        with Browser() as browser:
            browser.goto(web.url('credentials'))
            browser.wait('!!document.querySelector("input[aria-label=\\"' + label + '\\"]")')
            assert browser.eval('document.body.textContent.includes("Selected core seat: canary")')
            browser.eval('(()=>{const field=document.querySelector(' + json.dumps('[aria-label="' + label + '"]') + ');field.value=' + json.dumps(SECRET) + ';field.dispatchEvent(new Event("input",{bubbles:true}))})()')
            assert browser.eval('document.querySelector(' + json.dumps('[aria-label="' + label + '"]') + ').type') == 'password'
            assert all(request['action'] == 'list' for request in source.requests)
            if action == 'set':
                browser.eval('document.querySelector("[aria-label=\\"Service name\\"]").value="weather";document.querySelector("[aria-label=\\"Allowed variable name\\"]").value="EXO_SECRET_WEATHER";document.querySelector("[aria-label=\\"Allow bash\\"]").checked=true')
            browser.eval('Array.from(document.querySelectorAll("button")).find(button=>button.textContent===' + json.dumps(button) + ').click()')
            browser.wait('document.querySelector(' + json.dumps('[aria-label="' + label + '"]') + ').value === ""')
            browser.wait('document.body.textContent.includes("Saved in Keychain")')
            written = [request for request in source.requests if request['action'] == action]
            assert len(written) == 1 and written[0]['value'] == SECRET
            assert not browser.eval('JSON.stringify({...sessionStorage,...localStorage}).includes(' + json.dumps(SECRET) + ')')
            assert SECRET not in browser.eval('document.body.innerText')
            assert not source.sent()  # secret was never a model message
    finally:
        web.close()


@needs_browser
def test_unsupported_core_has_no_credential_entry_form():
    source = CredentialSource(supported=False)
    web = Served(source)
    try:
        with Browser() as browser:
            browser.goto(web.url('credentials'))
            browser.wait('document.body.textContent.includes("Credential setup is unavailable")')
            assert not browser.eval('!!document.querySelector("input[type=password]")')
            assert all(request == {'action': 'list'} for request in source.requests)
    finally:
        web.close()


@needs_browser
def test_navigation_clears_unsaved_password_without_posting_it():
    source = CredentialSource()
    web = Served(source)
    try:
        with Browser() as browser:
            browser.goto(web.url('credentials'))
            browser.wait('!!document.querySelector("input[type=password]")')
            browser.eval('window.draft=document.querySelector("input[type=password]");draft.value=' + json.dumps(SECRET))
            assert browser.eval('draft.autocomplete') == 'off'
            browser.eval('Array.from(document.querySelectorAll(".xc-nav button")).find(b=>b.textContent==="Chat").click()')
            browser.wait('!document.querySelector(".xc-credentials")')
            assert browser.eval('draft.value') == ''
            assert all(request['action'] == 'list' for request in source.requests)
            assert SECRET not in browser.eval('JSON.stringify({...sessionStorage,...localStorage})')
    finally:
        web.close()


@needs_browser
def test_successful_save_is_not_denied_when_metadata_refresh_fails():
    class RefreshFailure(CredentialSource):
        written = False
        async def call(self, body):
            if type(body).TYPE == 'command.run' and body.name == 'credentials':
                request = json.loads(body.args)
                if request['action'] == 'list' and self.written:
                    raise SourceError('credential_error', 'fixture refresh failed')
                if request['action'] == 'github_set':
                    self.written = True
            return await super().call(body)
    source = RefreshFailure()
    web = Served(source)
    try:
        with Browser() as browser:
            browser.goto(web.url('credentials'))
            browser.wait('!!document.querySelector("input[type=password]")')
            browser.eval('document.querySelector("input[type=password]").value=' + json.dumps(SECRET)
                         + ';Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Save or rotate GitHub token").click()')
            browser.wait('document.body.textContent.includes("status list could not be refreshed")')
            text = browser.eval('document.body.innerText')
            assert 'Saved in Keychain' in text and 'not saved' not in text and SECRET not in text
    finally:
        web.close()
