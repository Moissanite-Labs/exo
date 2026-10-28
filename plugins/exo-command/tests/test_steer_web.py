"""Queued steer visibility follows persistence, not turn completion or navigation."""
import pytest
from browser import Browser, Served, find_chrome
from test_layout_web import ThreadSrc, parts

needs_chrome = pytest.mark.skipif(not find_chrome() or not __import__("importlib").util.find_spec("websockets"), reason="Chrome or websockets missing")

class SteerSrc(ThreadSrc):
    def __init__(self):
        super().__init__(pairs=0)
        self.persisted = False
        self.queued = False  # the core still holds the steer
    async def call(self, body):
        kind = type(body).TYPE
        if kind == "session.list":
            return {"sessions": [{"id": sid, "title": sid, "model": "fake:x", "status": "live" if sid == "s1" else "idle"} for sid in ("s1", "s2")]}
        if kind == "session.get":
            s1 = body.session == "s1"
            return {"session": {"id": body.session, "model": "fake:x"}, "messages": [{"id": "queued", "role": "user", "content": parts("late note")}] if self.persisted and s1 else [],
                    "steers": ["queued"] if self.queued and not self.persisted and s1 else [], "seq": 10 if self.persisted else 0}
        if kind == "turn.send":
            self.queued = True
            return {"steered": True, "message_id": "queued"}
        return await super().call(body)

@needs_chrome
def test_queued_steer_survives_completion_and_thread_navigation_until_persisted():
    src = SteerSrc()
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            b.wait("document.querySelector('.xc-state')?.textContent === 'working'")  # the thread has loaded: Enter steers
            b.type('.xc-input', 'late note')
            b.press('Enter', 'Enter', 13)
            b.wait("!!document.querySelector('[data-steer=queued]')")
            srv.call(srv.centre.record, {"seq": 1, "type": "turn.completed", "session": "s1", "body": {"turn_id": "t1", "stop_reason": "end"}})
            b.wait("document.querySelector('.xc-state')?.textContent === 'idle'")
            assert b.eval("!!document.querySelector('[data-steer=queued]')")
            b.eval("document.querySelectorAll('.xc-sess')[1].click()")
            b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s2')")
            b.eval("document.querySelectorAll('.xc-sess')[0].click()")
            b.wait("!!document.querySelector('[data-steer=queued]')")
            src.persisted = True
            b.eval("document.querySelectorAll('.xc-sess')[1].click()")
            b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s2')")
            b.eval("document.querySelectorAll('.xc-sess')[0].click()")
            b.wait("document.querySelector('.xc-user')?.textContent.includes('late note')")
            assert not b.eval("!!document.querySelector('[data-steer=queued]')")
    finally:
        srv.close()


@needs_chrome
def test_steer_chip_goes_when_the_core_no_longer_queues_it():
    """A core restart loses a queued steer: reopening the thread drops its chip instead of showing it until reload."""
    src = SteerSrc()
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            b.wait("document.querySelector('.xc-state')?.textContent === 'working'")
            b.type('.xc-input', 'late note')
            b.press('Enter', 'Enter', 13)
            b.wait("!!document.querySelector('[data-steer=queued]')")
            src.queued = False  # the core forgot it and never stored it
            b.eval("document.querySelectorAll('.xc-sess')[1].click()")
            b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s2')")
            b.eval("document.querySelectorAll('.xc-sess')[0].click()")
            b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            b.wait("!document.querySelector('[data-steer=queued]')")
    finally:
        srv.close()


class SavedSteerSrc(SteerSrc):
    def __init__(self):
        super().__init__()
        self.queued = True
        self.sent = 0

    async def call(self, body):
        if type(body).TYPE == "session.get":
            out = await super().call(body)
            out["queued_steers"] = ([{"id": "queued", "role": "user", "content": parts("saved correction"),
                                     "meta": {"author": "delegate"}}]
                                    if body.session == "s1" and self.queued else [])
            return out
        if type(body).TYPE == "turn.send":
            self.sent += 1
        if type(body).TYPE == "command.run" and body.name == "unsteer":
            self.queued = False
            return {"output": {"cancelled": [{"id": "queued", "text": "saved correction"}]}}
        return await super().call(body)


@needs_chrome
def test_saved_pending_input_is_visible_cancellable_and_not_sent_on_reload():
    src = SavedSteerSrc()
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("!!document.querySelector('[data-steer=queued]')")
            assert b.eval("document.querySelector('[data-steer=queued]').textContent.includes('saved correction')")
            assert b.eval("document.querySelector('[data-steer=queued]').textContent.includes('delegate')")
            b.eval("window.beforeReload=true")
            b.eval("location.reload()")  # a fresh page has no local chip map
            b.wait("!window.beforeReload && !!document.querySelector('[data-steer=queued]')")
            assert src.sent == 0
            b.eval("document.querySelector('[data-steer=queued] button').click()")
            b.wait("document.querySelector('.xc-input').value === 'saved correction'")
            assert not src.queued and src.sent == 0
            src.queued = True  # a later explicit internal requeue can reuse its original ID
            b.eval("document.querySelector('.xc-sess.on').click()")
            b.wait("!!document.querySelector('[data-steer=queued]')")
            b.eval("document.querySelector('[data-steer=queued] button').click()")
            b.wait("!document.querySelector('[data-steer=queued]')")
            b.eval("window.beforeReload=true;location.reload()")
            b.wait("!window.beforeReload && document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            assert not b.eval("!!document.querySelector('[data-steer=queued]')")
    finally:
        srv.close()


@needs_chrome
def test_cancellation_during_snapshot_cannot_restore_stale_chip():
    src = SavedSteerSrc()
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('[data-steer=queued]')")
            b.eval("""(()=>{const f=window.fetch;window.fetch=async(u,o)=>{
              const response=await f(u,o);
              if(String(u)==='/api/chat/sessions/s1' && (!o?.method || o.method==='GET')) return new Promise(r=>window.releaseSnapshot=()=>r(response));
              return response;
            }})()""")
            b.eval("document.querySelectorAll('.xc-sess')[1].click()")
            b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s2')")
            b.eval("document.querySelectorAll('.xc-sess')[0].click()")
            b.wait("!!window.releaseSnapshot")
            src.queued = False
            srv.call(srv.centre.record, {"seq": 20, "type": "steer.cancelled", "session": "s1",
                                       "body": {"message_id": "queued", "text": "saved correction"}})
            b.eval("window.releaseSnapshot()")
            b.wait("!document.querySelector('[data-steer=queued]')")
            assert src.sent == 0
    finally:
        srv.close()


@needs_chrome
def test_cancel_ack_wins_over_older_snapshot_without_cancel_event():
    src = SavedSteerSrc()
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('[data-steer=queued]')")
            b.eval("""(()=>{const f=window.fetch;window.fetch=async(u,o)=>{
              const response=await f(u,o);
              if(String(u)==='/api/chat/sessions/s1' && (!o?.method || o.method==='GET')) return new Promise(r=>window.releaseSnapshot=()=>r(response));
              return response;
            }})()""")
            b.eval("document.querySelector('.xc-sess.on').click()")
            b.wait("!!window.releaseSnapshot")
            b.eval("document.querySelector('[data-steer=queued] button').click()")
            b.wait("document.querySelector('.xc-input').value === 'saved correction'")
            assert not src.queued
            # No event is published by this source. The successful cancel reply is sufficient.
            b.eval("window.releaseSnapshot()")
            b.eval("new Promise(r=>setTimeout(r,100))")
            assert not b.eval("!!document.querySelector('[data-steer=queued]')")
            assert src.sent == 0
    finally:
        srv.close()
