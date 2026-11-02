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


# A key typed into a provider row holds the rows back from a redraw (#1099, #1100). These tests type into the spare
# row's key field and check what is still drawn around it. Each holds at most one model-list read at a time, so
# Chrome's cache lock (a second GET for a URL waits for an unanswered first one) never holds a read the test waits on.
MODELS = '/api/providers/models'
KEY_FIELD = '[aria-label="API key for spare"]'
CUSTOM_KEY = '[aria-label="Custom provider API key"]'
CUSTOM_FORM = "document.querySelector('.xc-custom-form')"
NAME_FIELD = "document.querySelector('.xc-custom-form [aria-label=\"Provider name\"]')"
CANCEL = "[...document.querySelectorAll('.xc-custom-form button')].find(b => b.textContent === 'Cancel')"
EDIT_DEMO = "[...document.querySelectorAll('button')].find(b => b.textContent === 'Edit demo')"
REJECTIONS = "window.rejections = []; addEventListener('unhandledrejection', e => rejections.push(String(e.reason)))"
# Counts the model-list replies the page has read. The count goes up one task after a reply is read, so the code
# that awaited that reply has run by then, whether it drew anything or not.
COUNT_REPLIES = ("(() => { window.replies = 0; const json = Response.prototype.json;"
                 " Response.prototype.json = async function () { const d = await json.call(this);"
                 " if (this.url.includes('/api/providers/models')) setTimeout(() => window.replies++); return d; }; })()")
# The key field holds focus and its text, and it is the same node, so its row was not redrawn.
KEY_KEPT = "[window.key.isConnected, document.activeElement === window.key, window.key.value]"
SAVED = {'name': 'demo', 'endpoint': 'https://example.test/v1', 'protocol': 'openai', 'auth': 'key', 'model': 'model-one'}
READOUT = "[...document.querySelectorAll('.xc-phead-read .xc-rv')].map(e => e.textContent)"
# TWO_ROWS after a new default: spare is the default, and demo is no longer ready.
SPARE_DEFAULT = {'providers': [{'provider': 'demo', 'needs': 'none', 'ready': False},
                               {'provider': 'spare', 'needs': 'key', 'key': 'found', 'ready': True, 'default': True}],
                 'ready': True, 'store': 'environment'}


async def opened(gate):
    """Hold a request on the server's loop until the test opens its gate (at most 10 s). None does not hold."""
    for _ in range(500):
        if gate is None or gate.is_set():
            return
        await asyncio.sleep(0.02)


def arrived(seen, path, count):
    """Wait until the server has seen `count` requests for `path`."""
    end = time.monotonic() + 10
    while seen.count(path) < count and time.monotonic() < end:
        time.sleep(0.02)
    assert seen.count(path) == count


class Planned:
    """Serves the provider routes. `rows` is the provider list the next read returns. `models` holds (reply, gate)
    pairs for the next model-list reads, and a gate holds its read until the test opens it (None does not hold). A
    custom provider saved with POST is listed by the next read, and `posts` records each save's fields but its key."""

    def __init__(self, monkeypatch, rows=TWO_ROWS, saved=()):
        self.rows, self.seen, self.models, self.posts, self.saved = rows, [], [], [], list(saved)

        async def handle(centre, method, path, body, router_factory=None):
            self.seen.append(path)
            if path == '/api/providers':
                return 200, self.rows
            if path == MODELS:
                reply, gate = self.models.pop(0) if self.models else ('list', None)
                await opened(gate)
                return 200, MODEL_REPLIES[reply]
            if path == '/api/providers/custom':
                if method == 'POST':
                    # A copy without the key: the server clears the body once the handler returns.
                    self.posts.append({k: v for k, v in body.items() if k != 'key'})
                    self.saved[:] = [{k: body[k] for k in ('name', 'endpoint', 'protocol', 'auth', 'model')}]
                    return 200, {'provider': 'custom-' + body['name'], 'ok': True}
                return 200, {'providers': list(self.saved)}
            return 200, {'ok': True}

        monkeypatch.setattr(providers, 'handle', handle)
        self.srv = Served(ThreadSrc(pairs=0))


def fill_custom_form(b):
    b.eval(f"{ADD_CUSTOM}.click()")
    b.type('[aria-label="Provider name"]', 'demo')
    b.type('[aria-label="Base URL"]', 'https://example.test/v1')
    b.type('[aria-label="Model ID"]', 'model-one')


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
def test_a_custom_provider_saved_while_a_key_is_typed_closes_its_form(monkeypatch):
    """A custom provider saved while a key is typed into a provider row closes its form and shows the saved entry
    when the reload after the save lands. The key's row is not redrawn. The form used to stay open after the save
    had cleared its entry, and the next Save threw before it sent anything (#1099)."""
    s = Planned(monkeypatch)
    gate = threading.Event()
    try:
        with Browser() as b:
            b.goto(s.srv.url('providers'))
            b.wait(f"{SELECT}?.value === 'demo:first'")
            b.eval(REJECTIONS)
            fill_custom_form(b)
            b.eval(COUNT_REPLIES)
            s.models.append(('list', gate))
            count = s.seen.count(MODELS) + 1
            b.eval(f"{CUSTOM_FORM}.requestSubmit()")
            arrived(s.seen, MODELS, count)   # the save is answered, and its reload waits on the model list
            assert len(s.posts) == 1
            b.type(KEY_FIELD, 'synthetic-not-a-key')
            b.eval("window.key = document.activeElement")
            gate.set()
            b.wait("window.replies === 1")
            assert b.eval(f"!!{CUSTOM_FORM}") is False
            assert b.eval("document.body.textContent.includes('Saved custom-demo. Restart the core to use these settings.')")
            assert b.eval(f"!!{EDIT_DEMO}") is True
            assert b.eval(KEY_KEPT) == [True, True, 'synthetic-not-a-key']
            assert b.eval("window.rejections") == []
            assert len(s.posts) == 1
    finally:
        gate.set()
        s.srv.close()


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
def test_add_edit_and_cancel_work_while_a_key_is_typed(monkeypatch):
    """Add custom provider, Edit and Cancel open and close the custom form while a key is typed into a provider row.
    A click that leaves focus where it was (Safari's buttons, a script) used to do nothing, because the redraw
    returned early (#1099). Focus and the key stay in the key field."""
    s = Planned(monkeypatch, saved=[SAVED])
    try:
        with Browser() as b:
            b.goto(s.srv.url('providers'))
            b.wait(f"{SELECT}?.value === 'demo:first' && !!{EDIT_DEMO}")
            b.type(KEY_FIELD, 'synthetic-not-a-key')
            b.eval("window.key = document.activeElement")
            for button, name, disabled in ((ADD_CUSTOM, '', False), (EDIT_DEMO, 'demo', True)):
                b.eval(f"{button}.click()")
                assert b.eval(f"[{NAME_FIELD}?.value, {NAME_FIELD}?.disabled]") == [name, disabled]
                b.eval(f"{CANCEL}.click()")
                assert b.eval(f"!{CUSTOM_FORM} && !!{ADD_CUSTOM}") is True
                assert b.eval(KEY_KEPT) == [True, True, 'synthetic-not-a-key']
    finally:
        s.srv.close()


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
def test_an_open_custom_form_survives_a_reload_while_a_key_is_typed(monkeypatch):
    """A Test's reload while a key is typed into a provider row leaves an open custom form alone: the same node, its
    fields filled in and the key typed into it. Only a save, Add, Edit or Cancel redraws that section."""
    s = Planned(monkeypatch)
    try:
        with Browser() as b:
            b.goto(s.srv.url('providers'))
            b.wait(f"{SELECT}?.value === 'demo:first'")
            fill_custom_form(b)
            b.type(CUSTOM_KEY, 'custom-synthetic-value')
            b.type(KEY_FIELD, 'synthetic-not-a-key')
            b.eval(COUNT_REPLIES)
            b.eval(f"window.form = {CUSTOM_FORM}; window.key = document.activeElement; {TEST}.click()")
            b.wait("window.replies === 1")
            assert b.eval(f"{CUSTOM_FORM} === window.form") is True
            assert b.eval("[...window.form.querySelectorAll('input')].map(e => e.value)") == [
                'demo', 'https://example.test/v1', 'model-one', 'custom-synthetic-value']
            assert b.eval(KEY_KEPT) == [True, True, 'synthetic-not-a-key']
    finally:
        s.srv.close()


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
@pytest.mark.parametrize('outcome', ['fails', 'succeeds'])
@pytest.mark.parametrize('cached', [False, True], ids=['no-list', 'cached-list'])
def test_a_retry_reply_is_drawn_while_a_key_is_typed(monkeypatch, cached, outcome):
    """A key typed into a provider row while Try again waits does not hold back the reply: the model section draws
    it, a new Try again or the new list. The key's row is not redrawn, and the key field keeps focus and its text.
    The model section used to stay as the retry left it, with no list and no way to retry (#1100)."""
    s = Planned(monkeypatch)
    gate = threading.Event()
    try:
        with Browser() as b:
            if not cached:
                s.models.append(('fails', None))
            b.goto(s.srv.url('providers'))
            if cached:
                b.wait(f"{SELECT}?.value === 'demo:first'")
                s.models.append(('fails', None))
                b.eval(f"{TEST}.click()")
            b.wait(f"!!{RETRY}")
            b.eval(REJECTIONS)
            b.eval(COUNT_REPLIES)
            s.models.append((outcome, gate))
            count = s.seen.count(MODELS) + 1
            b.eval(f"window.oldRetry = {RETRY}; window.oldRetry.click()")
            arrived(s.seen, MODELS, count)
            b.type(KEY_FIELD, 'synthetic-not-a-key')
            b.eval("window.key = document.activeElement")
            gate.set()
            b.wait("window.replies === 1")
            assert b.eval(DRAWN[outcome]) is True
            if outcome == 'succeeds':
                assert b.eval(f"{SELECT}.value") == 'demo:first'
            assert b.eval(KEY_KEPT) == [True, True, 'synthetic-not-a-key']
            assert b.eval("window.rejections") == []
    finally:
        gate.set()
        s.srv.close()


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
def test_the_page_head_readout_is_drawn_while_a_key_is_typed(monkeypatch):
    """A reload while a key is typed into a provider row still draws the readout in the page head, so a new default
    and ready count show there at once. Only the provider rows wait: the key's row is not redrawn, and the key field
    keeps focus and its text. The readout used to wait with the rows."""
    s = Planned(monkeypatch)
    try:
        with Browser() as b:
            b.goto(s.srv.url('providers'))
            b.wait(f"{SELECT}?.value === 'demo:first'")
            assert b.eval(READOUT) == ['2 / 2', 'not set']
            b.type(KEY_FIELD, 'synthetic-not-a-key')
            b.eval("window.key = document.activeElement")
            b.eval(COUNT_REPLIES)
            s.rows = SPARE_DEFAULT
            b.eval(f"{TEST}.click()")
            b.wait("window.replies === 1")
            assert b.eval(READOUT) == ['1 / 2', 'spare']
            assert b.eval(KEY_KEPT) == [True, True, 'synthetic-not-a-key']
    finally:
        s.srv.close()


@pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')
def test_a_late_submit_of_a_saved_entry_sends_and_throws_nothing(monkeypatch):
    """The custom form reads whether its entry exists once, when it is built. A submit that reaches an edited entry's
    form after its save cleared `editing`, while the save's reload waits, sends the save again as a replace and
    throws nothing. Only a script can do this, since Save is disabled while the save runs. The submit used to throw
    on `editing.existing` before it sent anything (#1099)."""
    s = Planned(monkeypatch, saved=[SAVED])
    gate = threading.Event()
    try:
        with Browser() as b:
            b.goto(s.srv.url('providers'))
            b.wait(f"{SELECT}?.value === 'demo:first' && !!{EDIT_DEMO}")
            b.eval(REJECTIONS)
            b.eval(f"{EDIT_DEMO}.click()")
            s.models.append(('list', gate))
            count = s.seen.count(MODELS) + 1
            b.eval(f"window.form = {CUSTOM_FORM}; window.form.requestSubmit()")
            arrived(s.seen, MODELS, count)   # the save is answered and has cleared its entry, and its reload waits
            assert [p['replace'] for p in s.posts] == [True]
            b.eval("window.form.requestSubmit()")
            end = time.monotonic() + 10
            while len(s.posts) < 2 and time.monotonic() < end:
                time.sleep(0.02)
            assert [p['replace'] for p in s.posts] == [True, True]
            assert b.eval("window.rejections") == []
    finally:
        gate.set()
        s.srv.close()
