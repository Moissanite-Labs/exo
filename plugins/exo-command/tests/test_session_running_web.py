"""Running state survives navigation and reconnects in a disposable headless browser."""
from __future__ import annotations

import asyncio
import importlib.util
import threading

import pytest

from browser import Browser, Served, find_chrome
from exo_command.source import SourceError
from test_model_command_web import ModelSrc

pytestmark = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'),
                                reason='headless Chrome or websockets unavailable')


class RunningSource(ModelSrc):
    def __init__(self):
        super().__init__()
        self.status = {'s1': 'idle', 's2': 'idle'}
        self.fail = set()
        self.gates = {}
        self.started = set()
        self.entered = {sid: threading.Event() for sid in self.status}

    async def call(self, body):
        typ = type(body).TYPE
        if typ == 'approvals.list':
            return {'approvals': list(self.centre.state.approvals.values())}
        if typ == 'session.list':
            # A cached list can lag the following transcript response.
            return {'sessions': [{'id': sid, 'title': sid, 'model': 'fake:x', 'status': 'idle'} for sid in self.status]}
        if typ == 'session.get':
            sid = body.session
            status = self.status[sid]  # snapshot taken before a delayed response
            self.started.add(sid)
            self.entered[sid].set()
            if sid in self.gates:
                await self.gates[sid].wait()
            if sid in self.fail:
                raise SourceError('unavailable', 'snapshot unavailable')
            return {'session': {'id': sid, 'model': 'fake:x', 'status': status},
                    'messages': [{'id': 'u_' + sid, 'role': 'user', 'content': [{'type': 'text', 'text': 'fixture ' + sid}]}]}
        return await super().call(body)


# Track body completion too: fetch() resolves before api() consumes JSON and redraws.
STARTUP_TRACKER = r"""
window.startupRequests = new Set();
const originalFetch = window.fetch;
window.fetch = async (...args) => {
  const request = {}; window.startupRequests.add(request);
  try {
    const response = await originalFetch(...args);
    const originalJson = response.json.bind(response);
    response.json = async () => {
      try { return await originalJson(); }
      finally { window.startupRequests.delete(request); }
    };
    return response;
  } catch (error) { window.startupRequests.delete(request); throw error; }
};
"""
STARTUP_READY = r"""(async () => {
  const bus = await import("/static/bus.js");
  // Let API continuations and their follow-up requests run before checking the queue.
  await new Promise(requestAnimationFrame);
  let synced = 0; const off = bus.onState(state => { synced = state.synced; }); off();
  return synced > 0 && window.startupRequests.size === 0;
})()"""


def track_startup(browser):
    browser.call('Page.addScriptToEvaluateOnNewDocument', source=STARTUP_TRACKER)


@pytest.fixture
def page():
    src = RunningSource()
    srv = Served(src)
    with Browser() as browser:
        track_startup(browser)
        browser.goto(srv.url())
        browser.wait('document.querySelectorAll(".xc-sess").length === 2 && !!document.querySelector(".xc-msg")')
        browser.wait(STARTUP_READY)
        browser.eval('window.thread = id => Array.from(document.querySelectorAll(".xc-sess")).find(e => e.querySelector(".xc-sess-title").textContent === id)')
        yield src, srv, browser
    srv.close()


def click(browser, sid):
    browser.eval('Array.from(document.querySelectorAll(".xc-sess")).find(e=>e.querySelector(".xc-sess-title").textContent===' + repr(sid) + ').click()')


def expect(browser, sid, working, phase=None):
    browser.wait('document.querySelector(".xc-sess.on .xc-sess-title").textContent===' + repr(sid)
                 + ' && document.querySelector(".xc-state").textContent===' + repr(phase or ('working' if working else 'idle')))
    assert browser.eval('document.querySelector("#xc-live").classList.contains("on")') is working
    assert browser.eval('!!document.querySelector(".xc-strip .xc-state.on .xc-dot.on")') is working
    assert browser.eval('!!document.querySelector(".xc-wire")') is working
    assert browser.eval('document.querySelector(".xc-composer").classList.contains("xc-steer")') is working
    assert browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(e=>e.textContent==="Stop").disabled') is not working


def emit(srv, seq, sid, kind):
    event = {'seq': seq, 'type': kind, 'session': sid, 'ts': '2026-10-27T09:30:00Z',
             'body': {'session': {'id': sid}} if kind == 'session.updated' else {'turn_id': 't_fixture', 'model': 'fake:x'}}
    def record():
        srv.centre.state.apply(event)
        srv.centre.record(event)
    srv.call(record)


def test_switch_back_uses_fresh_running_snapshot_not_cached_idle(page):
    src, srv, browser = page
    src.status['s1'] = 'live'
    emit(srv, 1, 's1', 'turn.started')
    expect(browser, 's1', True)
    click(browser, 's2')
    expect(browser, 's2', False)
    click(browser, 's1')
    expect(browser, 's1', True)


def test_terminal_while_away_and_other_thread_terminal_do_not_revive_or_clear_working(page):
    src, srv, browser = page
    src.status.update(s1='live', s2='live')
    emit(srv, 1, 's1', 'turn.started')
    expect(browser, 's1', True)
    click(browser, 's2')
    expect(browser, 's2', True)
    src.status['s1'] = 'idle'
    emit(srv, 2, 's1', 'turn.completed')
    expect(browser, 's2', True)  # background completion cannot stop this thread's animation
    src.fail.add('s1')
    click(browser, 's1')
    expect(browser, 's1', False)  # failed snapshot falls back to the completed event, not old live cache
    browser.wait('document.querySelector(".xc-strip").textContent.includes("snapshot unavailable")')


def test_failed_snapshot_preserves_last_known_running_event(page):
    src, srv, browser = page
    src.status['s1'] = 'live'
    emit(srv, 1, 's1', 'turn.started')
    expect(browser, 's1', True)
    click(browser, 's2')
    expect(browser, 's2', False)
    src.fail.add('s1')
    click(browser, 's1')
    expect(browser, 's1', True)
    browser.wait('document.querySelector(".xc-strip").textContent.includes("snapshot unavailable")')


@pytest.mark.parametrize('fails', [False, True])
def test_late_snapshot_or_failure_cannot_overwrite_new_selection(page, fails):
    src, srv, browser = page
    src.status['s1'] = 'live'
    srv.call(lambda: src.gates.update(s1=asyncio.Event()))
    src.entered['s1'].clear()
    click(browser, 's1')
    assert src.entered['s1'].wait(2)
    click(browser, 's2')
    expect(browser, 's2', False)
    if fails:
        src.fail.add('s1')
    srv.call(src.gates['s1'].set)
    browser.wait('document.querySelector(".xc-log").textContent.includes("fixture s2")')
    expect(browser, 's2', False)
    assert not browser.eval('document.querySelector(".xc-strip").textContent.includes("snapshot unavailable")')


@pytest.mark.parametrize(('initial', 'event', 'working'), [('idle', 'turn.started', True), ('live', 'turn.completed', False)])
def test_turn_event_buffered_during_snapshot_wins_over_old_response(page, initial, event, working):
    src, srv, browser = page
    src.status['s2'] = initial
    srv.call(lambda: src.gates.update(s2=asyncio.Event()))
    src.entered['s2'].clear()
    click(browser, 's2')
    assert src.entered['s2'].wait(2)
    emit(srv, 1, 's2', event)
    srv.call(src.gates['s2'].set)
    expect(browser, 's2', working)


def test_reconnect_uses_transcript_status_newer_than_session_list(page):
    src, srv, browser = page
    expect(browser, 's1', False)
    src.status['s1'] = 'live'
    browser.eval('(async()=>{const bus=await import("/static/bus.js");await bus.restart()})()')
    expect(browser, 's1', True)


def test_stale_session_list_cannot_replace_a_newer_transcript_running_status(page):
    src, srv, browser = page
    list_entered, list_release = threading.Event(), None
    original = src.call
    async def delayed_list(body):
        if type(body).TYPE == 'session.list':
            snapshot = await original(body)
            list_entered.set()
            await list_release.wait()
            return snapshot
        return await original(body)
    def delay():
        nonlocal list_release
        list_release = asyncio.Event()
        src.call = delayed_list
    srv.call(delay)
    emit(srv, 1, 's1', 'session.updated')  # a list request snapshots idle, then waits
    assert list_entered.wait(2)
    src.status['s1'] = 'live'
    click(browser, 's1')
    expect(browser, 's1', True)  # a newer SessionGet supplies the running truth
    srv.call(list_release.set)
    browser.wait('document.querySelector(".xc-sess.on .xc-dim").textContent.includes("running")')
    click(browser, 's2')
    expect(browser, 's2', False)
    src.fail.add('s1')
    click(browser, 's1')
    expect(browser, 's1', True)  # stale list did not replace the valid snapshot fallback


def test_running_snapshot_with_pending_approval_keeps_working_indicators(page):
    src, srv, browser = page
    src.status['s1'] = 'live'
    event = {'seq': 1, 'type': 'approval.requested', 'session': 's1', 'ts': '2026-10-27T09:30:00Z',
             'body': {'request_id': 'a_fixture', 'summary': 'Fixture approval', 'risk': 'medium',
                      'call': {'id': 'c_fixture', 'name': 'bash', 'arguments': {'command': 'true'}},
                      'options': ['allow', 'deny']}}
    def record():
        srv.centre.state.apply(event)
        srv.centre.record(event)
    srv.call(record)
    click(browser, 's2')
    expect(browser, 's2', False)
    click(browser, 's1')
    expect(browser, 's1', True, phase='waiting for approval')
    browser.wait('!!document.querySelector("[data-approval=a_fixture]")')


def test_background_status_changes_preserve_focused_thread_and_keyboard_navigation(page):
    _, srv, browser = page
    browser.eval('window.thread("s1").focus()')
    emit(srv, 1, 's2', 'turn.started')
    browser.wait('window.thread("s2").querySelector(".xc-dim").textContent.includes("running")')
    assert browser.eval('document.activeElement.matches(".xc-sess") ? document.activeElement.querySelector(".xc-sess-title").textContent : null') == 's1'
    emit(srv, 2, 's2', 'turn.completed')
    browser.wait('!window.thread("s2").querySelector(".xc-dim").textContent.includes("running")')
    assert browser.eval('document.activeElement.matches(".xc-sess") ? document.activeElement.querySelector(".xc-sess-title").textContent : null') == 's1'
    browser.press('Tab')
    assert browser.eval('document.activeElement.matches(".xc-sess") ? document.activeElement.querySelector(".xc-sess-title").textContent : null') == 's2'
    browser.press(' ')
    expect(browser, 's2', False)


def test_repeated_or_unlisted_turn_events_do_not_rebuild_list_or_steal_composer_focus(page):
    _, srv, browser = page
    browser.eval('window.originalThread = window.thread("s1"); document.querySelector("textarea").focus(); document.querySelector("textarea").value="draft stays"')
    emit(srv, 1, 's2', 'turn.completed')
    emit(srv, 2, 's9', 'turn.started')
    browser.wait('(async()=> (await import("/static/bus.js")).lastSeq() >= 2)()')
    assert browser.eval('window.originalThread === window.thread("s1")')
    emit(srv, 3, 's1', 'turn.started')
    expect(browser, 's1', True)
    assert browser.eval('document.activeElement.tagName') == 'TEXTAREA'
    assert browser.eval('document.activeElement.value') == 'draft stays'
    # Current-thread status changed, so capture again before a duplicate event.
    browser.eval('window.originalThread = window.thread("s1")')
    emit(srv, 4, 's1', 'turn.started')
    browser.wait('(async()=> (await import("/static/bus.js")).lastSeq() >= 4)()')
    assert browser.eval('window.originalThread === window.thread("s1")')


def test_session_refresh_preserves_focus_by_id_after_reordering(page):
    src, srv, browser = page
    browser.eval('window.thread("s2").focus()')
    src.status = dict(reversed(list(src.status.items())))
    emit(srv, 1, 's2', 'session.updated')
    browser.wait('document.querySelector(".xc-sess .xc-sess-title").textContent === "s2"')
    assert browser.eval('document.activeElement.matches(".xc-sess") ? document.activeElement.querySelector(".xc-sess-title").textContent : null') == 's2'


def test_unknown_thread_event_during_list_request_is_merged(page):
    src, srv, browser = page
    original = src.call
    entered = threading.Event()
    release = None
    src.status['s3'] = 'idle'
    async def delayed_list(body):
        result = await original(body)
        if type(body).TYPE == 'session.list':
            entered.set()
            await release.wait()
        return result
    def delay():
        nonlocal release
        release = asyncio.Event()
        src.call = delayed_list
    srv.call(delay)
    emit(srv, 1, 's3', 'session.updated')
    assert entered.wait(2)
    emit(srv, 2, 's3', 'turn.started')
    emit(srv, 3, 's1', 'turn.started')
    expect(browser, 's1', True)
    srv.call(release.set)
    browser.wait('window.thread("s3").querySelector(".xc-dim").textContent.includes("running")')



def test_startup_barrier_waits_for_initial_approval_response():
    src = RunningSource()
    srv = Served(src)
    entered = threading.Event()
    release = None
    original = src.call
    async def held_approvals(body):
        if type(body).TYPE == 'approvals.list':
            entered.set()
            await release.wait()
        return await original(body)
    def hold():
        nonlocal release
        release = asyncio.Event()
        src.call = held_approvals
    srv.call(hold)
    try:
        with Browser() as browser:
            track_startup(browser)
            browser.goto(srv.url())
            # The old fixture was ready here, although approval sync still redraws later.
            browser.wait('document.querySelectorAll(".xc-sess").length === 2 && !!document.querySelector(".xc-msg")')
            assert entered.wait(2)
            assert not browser.eval(STARTUP_READY)
            srv.call(release.set)
            browser.wait(STARTUP_READY)
            browser.eval('window.originalThread = document.querySelector(".xc-sess")')
            emit(srv, 1, 's2', 'turn.completed')
            browser.wait('(async()=> (await import("/static/bus.js")).lastSeq() >= 1)()')
            assert browser.eval('window.originalThread === document.querySelector(".xc-sess")')
    finally:
        srv.call(release.set)
        srv.close()
