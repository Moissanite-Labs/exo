"""A failed refresh keeps the picker usable and lets the operator retry in place."""
import importlib.util

import pytest

from browser import Browser, Served, find_chrome
from test_layout_web import ThreadSrc
from exo_command import providers


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
@pytest.mark.parametrize('retryable', [True, False])
def test_cached_model_error_has_retry_only_when_allowed(monkeypatch, retryable):
    reads = []
    failing = [False]

    async def handle(centre, method, path, body, router_factory=None):
        if path == '/api/providers':
            return 200, {'providers': [{'provider': 'demo', 'needs': 'none', 'ready': True}], 'ready': True, 'store': 'environment'}
        if path == '/api/providers/models':
            reads.append(path)
            if failing[0]:
                return 200, {'error': 'Synthetic model-list failure', 'retryable': retryable, 'error_class': 'transport' if retryable else 'authority'}
            return 200, {'models': [{'route': 'demo:first'}], 'available': [{'route': 'demo:recovered'}] if len(reads) > 2 else []}
        return 200, {'ok': True}

    monkeypatch.setattr(providers, 'handle', handle)
    srv = Served(ThreadSrc(pairs=0))
    try:
        with Browser() as b:
            b.goto(srv.url('providers'))
            b.wait("document.querySelector('select[aria-label=\"Default model\"]')?.value === 'demo:first'")
            failing[0] = True
            b.eval("[...document.querySelectorAll('button')].find(b=>b.textContent==='Test').click()")
            b.wait("document.body.textContent.includes('This list is from the last good read.')")
            assert b.eval("document.querySelector('select[aria-label=\"Default model\"]').value") == 'demo:first'
            assert b.eval("[...document.querySelectorAll('button')].filter(b=>b.textContent==='Try again').length") == int(retryable)
            if retryable:
                failing[0] = False
                before = len(reads)
                b.eval("[...document.querySelectorAll('button')].find(b=>b.textContent==='Try again').click()")
                b.wait("!!document.querySelector('option[value=\"demo:recovered\"]')")
                assert not b.eval("document.body.textContent.includes('This list is from the last good read.')")
                b.wait("document.querySelector('select[aria-label=\"Default model\"]')?.value === 'demo:first'")
                assert len(reads) == before + 1
    finally:
        srv.close()
