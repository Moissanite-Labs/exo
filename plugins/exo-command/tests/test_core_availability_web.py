"""Unavailable backend state is honest without deleting drafts or pending actions."""
from __future__ import annotations

import asyncio
import importlib.util
import threading

import pytest

from browser import Browser, Served, find_chrome
from test_model_command_web import ModelSrc

pytestmark = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='headless Chrome unavailable')


class Source(ModelSrc):
    def __init__(self, status='live'):
        super().__init__()
        self.available = True
        self.status = status
        self.queued = False
        self.get_gate = None
        self.fail_gets = 0
        self.get_started = threading.Event()
    async def call(self, body):
        if not self.available:
            raise ConnectionError('private-unreachable-fixture')
        kind = type(body).TYPE
        if kind == 'hello':
            return {'seq': self.centre.state.seq}
        if kind == 'session.list':
            return {'sessions': [{'id': 's1', 'title': 'Fixture', 'model': 'fake:x', 'status': self.status}]}
        if kind == 'session.get':
            if self.fail_gets:
                self.fail_gets -= 1
                raise ConnectionError('temporary snapshot failure')
            self.get_started.set()
            if self.get_gate is not None:
                await self.get_gate.wait()
            return {'session': {'id': 's1', 'model': 'fake:x', 'status': self.status},
                    'messages': [{'id': 'u1', 'role': 'user', 'content': [{'type': 'text', 'text': 'Fixture history'}]}],
                    'steers': ['queued'] if self.queued else []}
        if kind == 'turn.send':
            self.calls.append(body)
            self.queued = True
            return {'steered': True, 'message_id': 'queued'}
        if kind == 'approvals.list':
            return {'approvals': list(self.centre.state.approvals.values())}
        return await super().call(body)


def test_unavailable_core_marks_last_known_working_preserves_draft_pending_and_recovers_fresh_snapshot():
    source = Source()
    web = Served(source)
    try:
        with Browser() as browser:
            browser.goto(web.url())
            browser.wait('document.querySelector(".xc-state").textContent === "working"')
            browser.type('.xc-input', 'queued note')
            browser.press('Enter', 'Enter', 13)
            browser.wait('!!document.querySelector("[data-steer=queued]")')
            approval = {'seq': 1, 'type': 'approval.requested', 'session': 's1',
                        'body': {'request_id': 'a_fixture', 'summary': 'Fixture approval', 'risk': 'medium',
                                 'call': {'id': 'c', 'name': 'bash', 'arguments': {'command': 'true'}}, 'options': ['allow', 'deny']}}
            def record():
                web.centre.state.apply(approval)
                web.centre.record(approval)
            web.call(record)
            browser.wait('!!document.querySelector("[data-approval=a_fixture]")')
            browser.type('.xc-input', 'keep this draft')
            source.available = False
            web.call(web.centre.set_core_state, 'unavailable')
            browser.wait('document.querySelector(".xc-conn").textContent === "core unavailable"')
            browser.wait('document.querySelector(".xc-state").textContent.includes("last known waiting for approval")')
            assert browser.eval('document.querySelector(".xc-input").value') == 'keep this draft'
            assert not browser.eval('!!document.querySelector(".xc-wire") || document.querySelector("#xc-live").classList.contains("on")')
            assert browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(b=>["Stop", "Try stop"].includes(b.textContent)).disabled')
            assert browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(b=>b.textContent==="Send").disabled')
            assert browser.eval('!!document.querySelector("[data-steer=queued]") && !!document.querySelector("[data-approval=a_fixture]")')
            before = len(source.of('turn.send'))
            browser.press('Enter', 'Enter', 13)
            assert len(source.of('turn.send')) == before
            assert browser.eval('document.querySelector(".xc-input").value') == 'keep this draft'
            # A successful backend handshake alone must not revive the old WORKING flag.
            source.available = True
            source.status = 'idle'
            source.get_started.clear()
            def recover():
                source.get_gate = asyncio.Event()
                asyncio.create_task(web.centre.bootstrap())
            web.call(recover)
            assert source.get_started.wait(2)
            browser.wait('document.querySelector(".xc-state").textContent.includes("checking activity")')
            assert not browser.eval('!!document.querySelector(".xc-wire")')
            web.call(source.get_gate.set)
            browser.wait('document.querySelector(".xc-state").textContent === "idle"')
            assert browser.eval('document.querySelector(".xc-input").value') == 'keep this draft'
            assert browser.eval('!!document.querySelector("[data-steer=queued]") && !!document.querySelector("[data-approval=a_fixture]")')
            assert 'private-unreachable-fixture' not in browser.eval('document.body.innerText')
    finally:
        web.close()


def test_quiet_idle_core_does_not_wait_for_model_event_to_enable_send():
    source = Source(status='idle')
    web = Served(source)
    try:
        with Browser() as browser:
            browser.goto(web.url())
            browser.wait('document.querySelector(".xc-conn").textContent === "live"')
            browser.wait('document.querySelector(".xc-state").textContent === "idle"')
            assert not browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(b=>b.textContent==="Send").disabled')
            assert not source.of('turn.send') and web.centre.state.seq == 0
    finally:
        web.close()


def test_failed_recovery_can_retry_activity_without_losing_draft_or_pending_actions():
    source = Source()
    web = Served(source)
    try:
        with Browser() as browser:
            browser.goto(web.url())
            browser.wait('document.querySelector(".xc-state").textContent === "working"')
            browser.type('.xc-input', 'queued note')
            browser.press('Enter', 'Enter', 13)
            browser.wait('!!document.querySelector("[data-steer=queued]")')
            browser.type('.xc-input', 'keep retry draft')
            web.call(web.centre.set_core_state, 'unavailable')
            browser.wait('document.querySelector(".xc-state").textContent.includes("last known working")')
            source.fail_gets = 1
            web.call(web.centre.set_core_state, 'connected')
            browser.wait('document.querySelector(".xc-state").textContent.includes("activity unavailable")')
            assert browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(b=>b.textContent==="Send").disabled')
            assert browser.eval('document.querySelector(".xc-input").value') == 'keep retry draft'
            assert browser.eval('!!document.querySelector("[data-steer=queued]")')
            before = len(source.of('turn.send'))
            browser.eval('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Retry activity").click()')
            browser.wait('document.querySelector(".xc-state").textContent === "working"')
            assert not browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(b=>b.textContent==="Send").disabled')
            assert not browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(b=>["Stop", "Try stop"].includes(b.textContent)).disabled')
            assert browser.eval('!!document.querySelector(".xc-wire") && !!document.querySelector("[data-steer=queued]")')
            assert browser.eval('document.querySelector(".xc-input").value') == 'keep retry draft'
            assert len(source.of('turn.send')) == before
    finally:
        web.close()


def test_web_stream_loss_keeps_deliberate_http_stop_for_last_known_busy_session():
    source = Source()
    web = Served(source)
    try:
        with Browser() as browser:
            browser.call('Page.addScriptToEvaluateOnNewDocument', source='''
globalThis.EventSource=class extends EventSource { constructor(...args){super(...args);globalThis.fixtureStream=this;} };
''')
            browser.goto(web.url())
            browser.wait('document.querySelector(".xc-state").textContent === "working"')
            browser.type('.xc-input', 'keep stop draft')
            browser.eval('fixtureStream.onerror()')
            browser.wait('document.querySelector(".xc-state").textContent.includes("web reconnecting")')
            assert browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(b=>b.textContent==="Send").disabled')
            assert not browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(b=>b.textContent==="Try stop").disabled')
            browser.eval('Array.from(document.querySelectorAll(".xc-btn")).find(b=>b.textContent==="Try stop").click()')
            browser.wait('document.querySelector(".xc-input").value === "keep stop draft"')
            import time
            for _ in range(100):
                if source.of('turn.interrupt'):
                    break
                time.sleep(.01)
            assert len(source.of('turn.interrupt')) == 1
            assert source.of('turn.interrupt')[0].session == 's1'
            assert not source.of('turn.send')
            assert browser.eval('document.querySelector(".xc-input").value') == 'keep stop draft'
    finally:
        web.close()
