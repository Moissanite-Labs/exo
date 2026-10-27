"""Approval wait is a selected-thread phase, not an execution-time estimate."""
import importlib.util
import asyncio

import pytest

from browser import Browser, Served, find_chrome
from test_core_availability_web import Source as AvailabilitySource

pytestmark = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='headless Chrome unavailable')


class Source(AvailabilitySource):
    async def call(self, body):
        if self.available and type(body).TYPE == 'session.list':
            return {'sessions': [{'id': sid, 'title': sid, 'model': 'fake:x', 'status': 'live'} for sid in ('s1', 's2')]}
        if self.available and type(body).TYPE == 'session.get':
            if self.fail_gets:
                self.fail_gets -= 1
                raise ConnectionError('disposable snapshot unavailable')
            return {'session': {'id': body.session, 'model': 'fake:x', 'status': 'live'}, 'messages': []}
        return await super().call(body)


def approval(rid, session='s1'):
    return {'type': 'approval.requested', 'session': session,
            'body': {'request_id': rid, 'summary': 'Disposable approval', 'risk': 'medium',
                     'call': {'id': rid, 'name': 'bash', 'arguments': {'command': 'true'}}, 'options': ['allow', 'deny']}}


def emit(web, event):
    def record():
        event['seq'] = web.centre.state.seq + 1
        web.centre.state.apply(event)
        web.centre.record(event)
    web.call(record)


def resolve(web, rid, session='s1'):
    emit(web, {'type': 'approval.resolved', 'session': session, 'body': {'request_id': rid, 'decision': 'deny', 'by': 'fixture'}})


def test_event_phase_is_thread_scoped_counts_multiple_and_global_approvals():
    source = Source()
    web = Served(source)
    try:
        with Browser() as browser:
            browser.goto(web.url())
            browser.wait('document.querySelector(".xc-state").textContent === "working"')
            browser.type('.xc-input', 'keep approval draft')
            emit(web, approval('other', 's2'))
            browser.wait('!!document.querySelector(".xc-sessions").textContent.includes("waiting for approval")')
            assert browser.eval('document.querySelector(".xc-state").textContent') == 'working'
            emit(web, approval('one'))
            emit(web, approval('two'))
            browser.wait('document.querySelector(".xc-state").textContent === "waiting for approval"')
            assert not browser.eval('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Stop").disabled')
            assert not browser.eval('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Send").disabled')
            assert browser.eval('document.querySelector(".xc-input").value') == 'keep approval draft'
            resolve(web, 'one')
            assert browser.eval('document.querySelector(".xc-state").textContent') == 'waiting for approval'
            resolve(web, 'two')
            browser.wait('document.querySelector(".xc-state").textContent === "working"')
            emit(web, approval('global', None))
            browser.wait('document.querySelector(".xc-state").textContent === "waiting for approval"')
            resolve(web, 'global', None)
            browser.wait('document.querySelector(".xc-state").textContent === "working"')
            assert not source.of('approval.respond') and not source.of('turn.send')
    finally:
        web.close()


def test_snapshot_navigation_reconnect_and_last_known_qualifiers_preserve_wait_phase():
    source = Source()
    web = Served(source)
    try:
        # A pending request exists before this browser connects: snapshot, not a received browser event.
        emit(web, approval('snapshot'))
        with Browser() as browser:
            browser.goto(web.url())
            browser.wait('document.querySelector(".xc-state").textContent === "waiting for approval"')
            browser.eval('Array.from(document.querySelectorAll(".xc-sess")).find(b=>b.textContent.startsWith("s2")).click()')
            browser.wait('document.querySelector(".xc-state").textContent === "working"')
            browser.eval('Array.from(document.querySelectorAll(".xc-sess")).find(b=>b.textContent.startsWith("s1")).click()')
            browser.wait('document.querySelector(".xc-state").textContent === "waiting for approval"')
            source.available = False
            web.call(web.centre.set_core_state, 'unavailable')
            browser.wait('document.querySelector(".xc-state").textContent === "core unavailable · last known waiting for approval"')
            browser.wait('document.querySelector(".xc-sess.on .xc-dim").textContent.includes("last known waiting for approval")')
            source.available = True
            source.fail_gets = 1
            web.call(lambda: asyncio.create_task(web.centre.bootstrap()))
            browser.wait('document.querySelector(".xc-state").textContent === "activity unavailable · last known waiting for approval"')
            source.fail_gets = 0
            browser.eval('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Retry activity").click()')
            browser.wait('document.querySelector(".xc-state").textContent === "waiting for approval"')
            resolve(web, 'snapshot')
            browser.wait('document.querySelector(".xc-state").textContent === "working"')
            assert not source.of('approval.respond') and not source.of('turn.send')
    finally:
        web.close()
