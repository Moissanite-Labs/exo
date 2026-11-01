"""A failed refresh keeps the picker usable and lets the operator retry in place."""
import asyncio
import importlib.util
import threading
import time

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


PROVIDERS = {'providers': [{'provider': 'demo', 'needs': 'none', 'ready': True}], 'ready': True, 'store': 'environment'}
MODEL_REPLIES = {
    'list': {'models': [{'route': 'demo:first'}], 'available': []},
    'succeeds': {'models': [{'route': 'demo:first'}], 'available': [{'route': 'demo:recovered'}]},
    'fails': {'error': 'Synthetic model-list failure', 'retryable': True, 'error_class': 'transport'},
    'refused': {'error': 'Synthetic refusal', 'retryable': False, 'error_class': 'authority'},
    'empty': {'models': [], 'available': []},
}
RETRY = "[...document.querySelectorAll('button')].find(b=>b.textContent==='Try again')"
SELECT = "document.querySelector('select[aria-label=\"Default model\"]')"
# For each outcome of the retry: what shows the reply was drawn, and what should then hold focus.
DRAWN = {
    'succeeds': "!!document.querySelector('option[value=\"demo:recovered\"]')",
    'fails': f"(() => {{ const t = {RETRY}; return !!t && t !== window.oldRetry; }})()",
    'refused': "document.body.textContent.includes('Synthetic refusal')",
    'empty': "!document.querySelector('.xc-prov-def')",
}
FOCUSED = {
    'succeeds': SELECT,
    'fails': RETRY,
    'refused': SELECT,
    'empty': "document.querySelector('main')",
}


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
@pytest.mark.parametrize('cached,outcome', [
    (True, 'succeeds'), (True, 'fails'), (True, 'refused'), (True, 'empty'),
    (False, 'succeeds'), (False, 'fails'), (False, 'refused'),
], ids=lambda v: {True: 'cached-list', False: 'no-list'}.get(v, 'retry-' + str(v)))
def test_keyboard_retry_keeps_focus_in_the_picker(monkeypatch, cached, outcome):
    """Space on Try again redraws the picker. Focus lands on Try again when it is drawn again, else on the model
    select, else on a refusal's message when no list is left, else on the workspace."""
    reads = []
    reply = ['list' if cached else 'fails']

    async def handle(centre, method, path, body, router_factory=None):
        if path == '/api/providers':
            return 200, PROVIDERS
        if path == '/api/providers/models':
            reads.append(path)
            return 200, MODEL_REPLIES[reply[0]]
        return 200, {'ok': True}

    monkeypatch.setattr(providers, 'handle', handle)
    srv = Served(ThreadSrc(pairs=0))
    try:
        with Browser() as b:
            b.goto(srv.url('providers'))
            if cached:
                b.wait(f"{SELECT}?.value === 'demo:first'")
                reply[0] = 'fails'
                b.eval("[...document.querySelectorAll('button')].find(b=>b.textContent==='Test').click()")
            b.wait(f"!!{RETRY}")
            b.eval(f"window.oldRetry = {RETRY}; window.oldRetry.focus()")
            assert b.eval("document.activeElement === window.oldRetry")
            reply[0] = outcome
            before = len(reads)
            b.press(' ', 'Space', 32)
            b.wait(DRAWN[outcome])
            want = FOCUSED[outcome]
            if outcome == 'refused' and not cached:   # no list and no retry: the refusal's message
                want = "document.querySelector('.xc-prov-def .xc-warn')"
            # The tag name of whatever holds focus instead, so a failure says where focus went (BODY when lost).
            assert b.eval(f"document.activeElement === {want} || document.activeElement.tagName") is True
            assert len(reads) == before + 1
    finally:
        srv.close()


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
def test_retry_leaves_focus_moved_away_alone(monkeypatch):
    """Focus the person moves out of the panel while a retry runs stays there when the reply is drawn."""
    reads = []
    release = threading.Event()

    async def handle(centre, method, path, body, router_factory=None):
        if path == '/api/providers':
            return 200, PROVIDERS
        if path == '/api/providers/models':
            reads.append(path)
            if len(reads) > 1:   # hold the retry until the test has moved focus
                for _ in range(500):
                    if release.is_set():
                        break
                    await asyncio.sleep(0.02)
            return 200, MODEL_REPLIES['fails']
        return 200, {'ok': True}

    monkeypatch.setattr(providers, 'handle', handle)
    srv = Served(ThreadSrc(pairs=0))
    try:
        with Browser() as b:
            b.goto(srv.url('providers'))
            b.wait(f"!!{RETRY}")
            b.eval(f"window.oldRetry = {RETRY}; window.oldRetry.focus()")
            b.press(' ', 'Space', 32)
            end = time.monotonic() + 10
            while len(reads) < 2 and time.monotonic() < end:
                time.sleep(0.02)
            assert len(reads) == 2
            b.eval("document.getElementById('xc-skip').focus()")
            release.set()
            b.wait(DRAWN['fails'])
            assert b.eval("document.activeElement.id") == 'xc-skip'
    finally:
        release.set()
        srv.close()


TEST = "[...document.querySelectorAll('button')].find(b=>b.textContent==='Test')"
# Two rows with a Test button each, so focus has to come back to the row it was in, not to the first Test.
# The second row takes a key, so it also has Save and Remove: focus has to come back to Test, not to Save.
TWO_ROWS = {'providers': [{'provider': 'demo', 'needs': 'none', 'ready': True},
                          {'provider': 'spare', 'needs': 'key', 'key': 'found', 'ready': True}],
            'ready': True, 'store': 'environment'}
SPARE_TEST = ("[...[...document.querySelectorAll('.xc-prow')].find(r => r.querySelector('.xc-prow-name').textContent"
              " === 'spare').querySelectorAll('button')].find(b => b.textContent === 'Test')")
ADD_CUSTOM = "[...document.querySelectorAll('button')].find(b=>b.textContent==='Add custom provider')"
MOVED_TO = {'test': SPARE_TEST, 'select': SELECT, 'add-custom': ADD_CUSTOM}
# Where focus is, as tag and label, so a failure names it ("BUTTON:Try again", "SELECT:Default model", "BODY:...").
WHERE = "(e => e.tagName + ':' + (e.getAttribute('aria-label') || e.textContent))(document.activeElement)"


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
@pytest.mark.parametrize('outcome', ['fails', 'succeeds'])
@pytest.mark.parametrize('moved', ['test', 'select', 'add-custom'])
def test_held_retry_keeps_focus_moved_inside_the_panel(monkeypatch, moved, outcome):
    """Focus the person moves to another Providers control while a retry runs is on that control again once the
    reply has redrawn the panel (#1014). The reply replaces the control, so focus goes to its new copy. The
    controls are a provider's Test button, the model select and the custom providers' Add button."""
    reads = []
    reply = ['list']
    hold, release = threading.Event(), threading.Event()

    async def handle(centre, method, path, body, router_factory=None):
        if path == '/api/providers':
            return 200, TWO_ROWS
        if path == '/api/providers/models':
            reads.append(path)
            if hold.is_set():   # hold the retry until the test has moved focus
                for _ in range(500):
                    if release.is_set():
                        break
                    await asyncio.sleep(0.02)
            return 200, MODEL_REPLIES[reply[0]]
        return 200, {'ok': True}

    monkeypatch.setattr(providers, 'handle', handle)
    srv = Served(ThreadSrc(pairs=0))
    try:
        with Browser() as b:
            b.goto(srv.url('providers'))
            b.wait(f"{SELECT}?.value === 'demo:first'")
            reply[0] = 'fails'
            b.eval(f"{TEST}.click()")
            b.wait(f"!!{RETRY}")
            b.eval(f"window.oldRetry = {RETRY}; window.oldRetry.focus()")
            hold.set()
            reply[0] = outcome
            before = len(reads)
            b.press(' ', 'Space', 32)
            end = time.monotonic() + 10
            while len(reads) == before and time.monotonic() < end:
                time.sleep(0.02)
            assert len(reads) == before + 1
            b.eval(f"window.moved = {MOVED_TO[moved]}; window.moved.focus()")
            assert b.eval("document.activeElement === window.moved")
            release.set()
            b.wait(DRAWN[outcome])
            assert b.eval("window.moved.isConnected") is False   # the reply redrew the control
            assert b.eval(f"document.activeElement === {MOVED_TO[moved]} || {WHERE}") is True
            assert b.eval(f"{SELECT}.value") == 'demo:first'
    finally:
        release.set()
        srv.close()
