"""Browser behavior for worker drafts, search options, saves and concurrency refusals."""
import copy
import importlib.util
import json

import pytest
from browser import Browser, Served, find_chrome
from test_model_command_web import ModelSrc
from exo.core.models.router import Router
from exo_command.source import SourceError


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome required')
def test_worker_editor_keeps_failed_draft_and_applies_explicit_routes():
    class WorkerSrc(ModelSrc):
        def __init__(self):
            super().__init__()
            self.state = {'roles': {'fast': ['fake:old'], 'standard': [], 'deep': []},
                          'effective': {'fast': ['fake:old', 'fake:x'], 'standard': ['fake:x'], 'deep': ['fake:x']},
                          'revision': 'v1', 'editable': True, 'reason': '', 'scope': 'Connected core.',
                          'note': 'Later calls use the new routes.'}
            self.saved = []
            self.conflict = True
        async def call(self, body):
            if type(body).TYPE == 'command.run' and body.name == 'worker_models':
                if body.args:
                    payload = json.loads(body.args)
                    if self.conflict:
                        self.conflict = False
                        self.state['revision'] = 'v2'
                        raise SourceError('conflict', 'changed')
                    self.saved.append(payload)
                    self.state.update(roles=payload['roles'], revision='v3', saved=True, applied=True)
                return {'output': copy.deepcopy(self.state)}
            return await super().call(body)
    src = WorkerSrc()
    srv = Served(src)
    srv.web.router_factory = lambda: Router(env={}, key_sources=False)
    try:
        with Browser() as browser:
            browser.goto(srv.url('providers'))
            browser.wait("!!document.querySelector('[aria-label=\"fast preferred model\"]:not(:disabled)')")
            browser.wait("document.querySelectorAll('#worker-model-catalog option').length > 0")
            browser.eval("[...document.querySelectorAll('button')].find(b=>b.textContent==='Worker models').click()")
            browser.wait("document.activeElement.getAttribute('aria-label')==='fast preferred model'")
            browser.eval("document.querySelector('[aria-label=\"fast preferred model\"]').value='fake:new'")
            browser.eval("document.querySelector('[aria-label=\"fast fallback models\"]').value='fake:backup'")
            browser.eval("document.querySelector('[aria-label=\"Worker models\"] form').requestSubmit()")
            browser.wait("document.querySelector('[aria-label=\"Worker models\"]').textContent.includes('Reload before saving')")
            assert browser.eval("document.querySelector('[aria-label=\"fast preferred model\"]').value") == 'fake:new'
            browser.eval("[...document.querySelectorAll('[aria-label=\"Worker models\"] button')].find(b=>b.textContent==='Reload settings').click()")
            browser.wait("document.querySelector('[aria-label=\"fast preferred model\"]').value==='fake:old'")
            browser.eval("document.querySelector('[aria-label=\"fast preferred model\"]').value='fake:new'")
            browser.eval("document.querySelector('[aria-label=\"fast fallback models\"]').value='fake:backup'")
            browser.eval("document.querySelector('[aria-label=\"Worker models\"] form').requestSubmit()")
            browser.wait("document.querySelector('[aria-label=\"Worker models\"]').textContent.includes('Saved and applied')")
            assert src.saved == [{'roles': {'fast': ['fake:new', 'fake:backup'], 'standard': [], 'deep': []}, 'revision': 'v2'}]
            assert src.sent() == []
            browser.call('Emulation.setDeviceMetricsOverride', width=480, height=900, deviceScaleFactor=1, mobile=False)
            browser.wait("(()=>{const s=document.querySelector('.xc-workers');return s.scrollWidth<=s.clientWidth;})()")
            browser.call('Emulation.setDeviceMetricsOverride', width=1440, height=900, deviceScaleFactor=1, mobile=False)
    finally:
        srv.close()
