"""Human intent stays with its thread through navigation and unconfirmed sends."""
import importlib.util

import pytest

from browser import Browser, Served, find_chrome
from test_layout_web import ThreadSrc

pytestmark = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"), reason="Chrome or websockets missing")


class DraftSrc(ThreadSrc):
    def __init__(self):
        super().__init__(pairs=0)

    async def call(self, body):
        if type(body).TYPE == "session.list":
            return {"sessions": [{"id": s, "title": s, "model": "m", "status": "idle"} for s in ("s1", "s2")]}
        return await super().call(body)


def put(b, text, start=None, end=None):
    import json
    start = len(text) if start is None else start
    end = start if end is None else end
    b.eval(f"(()=>{{const i=document.querySelector('.xc-input');i.focus();i.value={json.dumps(text)};i.setSelectionRange({start},{end});i.dispatchEvent(new Event('input'));}})()")


def switch(b, sid):
    b.eval(f"[...document.querySelectorAll('.xc-sess')].find(x=>x.textContent.includes('{sid}')).click()")
    b.wait(f"document.querySelector('.xc-sess.on')?.textContent.includes('{sid}')")


def hold_send(b):
    b.eval("""(()=>{const fetch0=window.fetch;window.sends=[];
      window.fetch=(url,options)=>String(url).endsWith('/send') ? new Promise((resolve,reject)=>{
        window.sends.push({url,options,resolve,reject});
      }) : fetch0(url,options);})()""")


def send(b):
    b.eval("document.querySelector('.xc-composer .xc-btnrow button').click()")
    b.wait("window.sends.length === 1")


def test_thread_drafts_caret_and_tab_remount():
    srv = Served(DraftSrc())
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            put(b, "first intention", 2, 8)
            switch(b, "s2")
            assert b.eval("document.querySelector('.xc-input').value") == ""
            put(b, "second intention", 4)
            switch(b, "s1")
            assert b.eval("[document.querySelector('.xc-input').value,document.querySelector('.xc-input').selectionStart,document.querySelector('.xc-input').selectionEnd]") == ["first intention", 2, 8]
            b.eval("location.hash='#tasks'"); b.wait("!document.querySelector('.xc-composer')")
            b.eval("location.hash='#chat'"); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            assert b.eval("document.querySelector('.xc-input').value") == "first intention"
            switch(b, "s2")
            assert b.eval("document.querySelector('.xc-input').value") == "second intention"
    finally:
        srv.close()


def test_delayed_failure_stays_with_origin_and_keeps_newer_edits():
    srv = Served(DraftSrc())
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            hold_send(b); put(b, "original instruction"); send(b)
            assert b.eval("document.querySelector('.xc-draft-recovery').textContent.includes('Sending')")
            put(b, "newer intention", 3)
            # Enter is guarded even though the composer still accepts the next draft.
            b.press('Enter', 'Enter', 13)
            assert b.eval("window.sends.length") == 1
            switch(b, "s2"); put(b, "other thread")
            b.eval("window.sends[0].reject(new Error('connection lost'))")
            b.wait("!document.querySelector('.xc-draft-recovery').textContent.includes('Sending')")
            assert b.eval("document.querySelector('.xc-input').value") == "other thread"
            switch(b, "s1")
            assert b.eval("document.querySelector('.xc-input').value") == "newer intention"
            assert b.eval("document.querySelector('.xc-input').selectionStart") == 3
            b.eval("[...document.querySelectorAll('.xc-draft-recovery button')].find(x=>x.textContent==='Restore text').click()")
            assert b.eval("document.querySelector('.xc-input').value") == "original instruction\nnewer intention"
            assert b.eval("window.sends.length") == 1
    finally:
        srv.close()


def test_failure_during_tab_absence_restores_empty_draft():
    srv = Served(DraftSrc())
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            hold_send(b); put(b, "keep this instruction"); send(b)
            b.eval("location.hash='#tasks'"); b.wait("!document.querySelector('.xc-composer')")
            b.eval("window.sends[0].reject(new Error('connection lost'))")
            b.eval("location.hash='#chat'"); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            b.wait("document.querySelector('.xc-input').value === 'keep this instruction'")
            assert b.eval("document.querySelector('.xc-draft-recovery').textContent.includes('not confirmed')")
            assert b.eval("window.sends.length") == 1
    finally:
        srv.close()


def test_new_thread_creation_preserves_source_and_late_reply_cannot_redirect():
    srv = Served(DraftSrc())
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            b.eval("""(()=>{const fetch0=window.fetch;window.creates=[];
              window.fetch=(url,options)=>String(url)==='/api/chat/sessions' && options?.method==='POST'
                ? new Promise(resolve=>window.creates.push(()=>resolve(new Response(JSON.stringify({session:{id:'s3'}}),{status:200}))))
                : fetch0(url,options);})()""")
            put(b, "source draft", 2)
            b.eval("[...document.querySelectorAll('button')].find(x=>x.textContent==='New thread').click()")
            b.wait("window.creates.length === 1")
            switch(b, "s2"); put(b, "stay here", 4)
            b.eval("window.creates[0]()")
            # Wait for a completed network roundtrip after the create resolution.
            b.eval("new Promise(r=>setTimeout(r,100))")
            assert b.eval("document.querySelector('.xc-sess.on').textContent.includes('s2')")
            assert b.eval("document.querySelector('.xc-input').value") == "stay here"
            switch(b, "s1")
            assert b.eval("document.querySelector('.xc-input').value") == "source draft"
            assert b.eval("document.querySelector('.xc-input').selectionStart") == 2
            # An intentional New opens an empty draft and leaves the source draft intact.
            b.eval("[...document.querySelectorAll('button')].find(x=>x.textContent==='New thread').click()")
            b.wait("window.creates.length === 2"); b.eval("window.creates[1]()")
            b.wait("document.querySelector('.xc-input').value === ''")
            put(b, "new thread draft")
            switch(b, "s1")
            assert b.eval("document.querySelector('.xc-input').value") == "source draft"
            put(b, "/new"); b.press('Enter', 'Enter', 13)
            b.wait("window.creates.length === 3"); b.eval("window.creates[2]()")
            b.wait("document.querySelector('.xc-input').value === 'new thread draft'")
    finally:
        srv.close()


class EmptySrc(DraftSrc):
    async def call(self, body):
        if type(body).TYPE == "session.list":
            return {"sessions": []}
        return await super().call(body)


def test_first_submission_recovery_belongs_to_created_thread_after_remount():
    srv = Served(EmptySrc())
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-input')")
            b.eval("""(()=>{const fetch0=window.fetch;window.sends=[];window.created=false;
              window.fetch=(url,options)=>{
                if(String(url)==='/api/chat/sessions' && options?.method==='POST') {
                  return new Promise(resolve=>window.releaseCreate=()=>{window.created=true;resolve(new Response(JSON.stringify({session:{id:'s3'}})));});
                }
                if(String(url)==='/api/chat/sessions' && window.created) return Promise.resolve(new Response(JSON.stringify({sessions:[{id:'s3',title:'s3',model:'m',status:'idle'}]})));
                if(String(url).endsWith('/send')) return new Promise((resolve,reject)=>window.sends.push({resolve,reject}));
                return fetch0(url,options);
              };})()""")
            put(b, "first instruction")
            b.eval("document.querySelector('.xc-composer .xc-btnrow button').click()")
            b.wait("!!window.releaseCreate")
            b.eval("location.hash='#tasks'"); b.wait("!document.querySelector('.xc-composer')")
            b.eval("location.hash='#chat'"); b.wait("!!document.querySelector('.xc-input')")
            put(b, "next intention", 4)
            b.eval("window.releaseCreate()")
            b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s3')")
            b.wait("window.sends.length === 1")
            assert b.eval("document.querySelector('.xc-input').value") == "next intention"
            b.eval("location.hash='#tasks'"); b.wait("!document.querySelector('.xc-composer')")
            b.eval("window.sends[0].reject(new Error('lost acknowledgement'))")
            b.eval("location.hash='#chat'"); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s3')")
            b.wait("document.querySelector('.xc-draft-recovery').textContent.includes('not confirmed')")
            assert b.eval("document.querySelector('.xc-input').value") == "next intention"
            assert b.eval("document.querySelector('.xc-input').selectionStart") == 4
            b.eval("[...document.querySelectorAll('.xc-draft-recovery button')].find(x=>x.textContent==='Restore text').click()")
            assert b.eval("document.querySelector('.xc-input').value") == "first instruction\nnext intention"
            assert b.eval("window.sends.length") == 1
    finally:
        srv.close()


def test_a_session_list_asked_before_the_thread_existed_cannot_replace_a_newer_one():
    """#859: session lists can answer out of order. The remounted page asks for the list before the first send's
    thread exists; when those answers come back after the list that shows the new thread, the thread stays shown."""
    srv = Served(EmptySrc())
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-input')")
            b.eval("""(()=>{const fetch0=window.fetch;window.sends=[];window.created=false;window.held=[];window.staleRead=0;
              window.fetch=(url,options)=>{
                if(String(url)==='/api/chat/sessions' && options?.method==='POST') {
                  return new Promise(resolve=>window.releaseCreate=()=>{window.created=true;resolve(new Response(JSON.stringify({session:{id:'s3'}})));});
                }
                if(String(url)==='/api/chat/sessions' && window.created) return Promise.resolve(new Response(JSON.stringify({sessions:[{id:'s3',title:'s3',model:'m',status:'idle'}]})));
                if(String(url)==='/api/chat/sessions' && window.holdLists) return new Promise(resolve=>window.held.push(()=>{
                  const r=new Response(JSON.stringify({sessions:[]})), json=r.json.bind(r);
                  r.json=()=>json().finally(()=>{window.staleRead++;});
                  resolve(r);
                }));
                if(String(url).endsWith('/send')) return new Promise((resolve,reject)=>window.sends.push({resolve,reject}));
                return fetch0(url,options);
              };})()""")
            put(b, "first instruction")
            b.eval("document.querySelector('.xc-composer .xc-btnrow button').click()")
            b.wait("!!window.releaseCreate")
            b.eval("window.holdLists=true; location.hash='#tasks'"); b.wait("!document.querySelector('.xc-composer')")
            b.eval("location.hash='#chat'"); b.wait("!!document.querySelector('.xc-input')")
            b.wait("window.held.length >= 2")  # recover() and the startup refresh, both asked before s3 exists
            b.eval("window.releaseCreate()")
            b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s3')")
            held = b.eval("window.held.length")
            b.eval("window.held.forEach(release=>release())")
            b.wait(f"window.staleRead === {held}")
            b.eval("new Promise(resolve=>setTimeout(resolve, 0))")  # the page has applied what it read
            assert b.eval("document.querySelector('.xc-sess.on')?.textContent.includes('s3')"), \
                b.eval("document.querySelector('.xc-sessions')?.textContent")
    finally:
        srv.close()


def test_a_list_recover_asked_first_cannot_replace_a_newer_one():
    """#859: recover() writes the list too. An unsent draft keeps the startup refresh from opening a thread, so
    recover() reaches its own write; its older answer, read after the startup refresh's, leaves s3 listed."""
    srv = Served(EmptySrc())
    listed = "[...document.querySelectorAll('.xc-sess')].some(x=>x.textContent.includes('s3'))"
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-input')")
            # Once the event stream has synced, a remount's recover() asks before its startup refresh does.
            b.eval("import('/static/bus.js').then(bus=>bus.onState(st=>{window.synced=st.synced;}))")
            b.wait("window.synced >= 1")
            put(b, "unsent draft")
            b.eval("""(()=>{const fetch0=window.fetch;window.held=[];window.read=0;
              window.fetch=(url,options)=>{
                if(String(url)==='/api/chat/sessions' && (options?.method || 'GET')==='GET' && window.holdLists) {
                  const recover=/\\brecover\\b/.test(new Error().stack);
                  return new Promise(resolve=>window.held.push({recover, release:sessions=>{
                    const r=new Response(JSON.stringify({sessions})), json=r.json.bind(r);
                    r.json=()=>json().finally(()=>{window.read++;});
                    resolve(r);
                  }}));
                }
                return fetch0(url,options);
              };})()""")
            b.eval("window.holdLists=true; location.hash='#tasks'"); b.wait("!document.querySelector('.xc-composer')")
            b.eval("location.hash='#chat'"); b.wait("!!document.querySelector('.xc-input')")
            b.wait("window.held.length >= 2")
            assert b.eval("window.held[0].recover && !window.held[1].recover")  # recover() asked first
            n = b.eval("window.held.length")
            b.eval(f"window.held.slice(1, {n}).forEach(x=>x.release([{{id:'s3',title:'s3',model:'m',status:'idle'}}]))")
            b.wait(listed)
            b.eval("window.held[0].release([])")
            b.wait(f"window.read >= {n}")
            b.eval("new Promise(resolve=>setTimeout(resolve, 0))")  # the page has applied what it read
            assert b.eval(listed), b.eval("document.querySelector('.xc-sessions')?.textContent")
            assert b.eval("document.querySelector('.xc-input').value") == "unsent draft"
    finally:
        srv.close()
