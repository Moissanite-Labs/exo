"""Native display preferences survive disposable origins without persisting conversation text."""
import importlib.util
import json

import pytest
from browser import Browser, Served, find_chrome
from test_layout_web import ThreadSrc

pytestmark = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='Chrome/websockets missing')


def inject(b, values):
    b.call('Page.addScriptToEvaluateOnNewDocument', source="window.__exoDesktopPrefs=" + json.dumps(values) + ";window.savedPrefs=[];window.webkit={messageHandlers:{exoDesktop:{postMessage:p=>savedPrefs.push(p)}}};localStorage.setItem('xc-layout','inverse');localStorage.setItem('xc-appearance','dark');")


def test_native_values_override_browser_storage_and_explicit_view_wins():
    srv = Served(ThreadSrc(pairs=60))
    try:
        with Browser() as b:
            inject(b, {'view': 'providers', 'thread': 's1', 'layout': 'classic', 'appearance': 'light', 'scroll': .4})
            b.goto(srv.url('chat'))
            b.wait("document.querySelector('.xc-sess.on')?.dataset.id === 's1' || !!document.querySelector('.xc-log .xc-msg')")
            assert b.eval("document.querySelector('.xc-nav-link[aria-current]').textContent") == 'Chat'
            assert b.eval("document.documentElement.dataset.theme") == 'light'
            assert b.eval("localStorage.getItem('xc-layout')") == 'classic'
            b.wait("(()=>{const l=document.querySelector('.xc-log');return l.scrollHeight>l.clientHeight && Math.abs(l.scrollTop/(l.scrollHeight-l.clientHeight)-.4)<.03;})()")
            b.eval("document.querySelector('.xc-log').scrollTop=200")
            b.wait("savedPrefs.some(p=>p.thread==='s1' && typeof p.scroll==='number' && p.scroll>0)")
            assert b.eval("savedPrefs.every(p=>Object.keys(p).every(k=>['view','thread','layout','appearance','petMode','scroll'].includes(k)))")
    finally:
        srv.close()


def test_missing_native_thread_does_not_silently_open_latest():
    srv = Served(ThreadSrc(pairs=3))
    try:
        with Browser() as b:
            inject(b, {'view': 'chat', 'thread': 'missing', 'scroll': .6})
            b.goto(srv.url('chat'))
            b.wait("document.querySelectorAll('.xc-sess').length>0")
            b.wait("savedPrefs.some(p=>p.thread===null && p.scroll===null)")
            assert not b.eval("!!document.querySelector('.xc-sess.on')")
    finally:
        srv.close()


def test_native_view_restores_without_explicit_url_view_and_changes_are_forwarded():
    srv = Served(ThreadSrc(pairs=3))
    try:
        with Browser() as b:
            inject(b, {'view': 'tasks', 'appearance': 'light'})
            b.goto(srv.url().rsplit('&chat', 1)[0])
            b.wait("document.querySelector('.xc-nav-link[aria-current]')?.textContent==='Tasks'")
            b.eval("(()=>{const a=document.querySelector('.xc-appearance');a.value='system';a.dispatchEvent(new Event('change'));})()")
            b.wait("savedPrefs.some(p=>p.appearance==='system')")
            b.eval("document.querySelector('a[href=\"#schedule\"]').click()")
            b.wait("savedPrefs.some(p=>p.view==='schedule')")
    finally:
        srv.close()


def test_delayed_saved_thread_cannot_restore_over_a_new_selection():
    class TwoThreads(ThreadSrc):
        async def call(self, body):
            if type(body).TYPE == 'session.list':
                return {'sessions': [{'id': sid, 'title': sid, 'agent': 'atlas', 'model': 'm', 'status': 'idle'} for sid in ['s1', 's2']]}
            return await super().call(body)
    srv = Served(TwoThreads(pairs=60))
    try:
        with Browser() as b:
            inject(b, {'view': 'chat', 'thread': 's1', 'layout': 'inverse', 'scroll': .6})
            b.call('Page.addScriptToEvaluateOnNewDocument', source="const realFetch=window.fetch;window.releaseSaved=[];window.fetch=(url,...args)=>String(url).endsWith('/api/chat/sessions/s1')?new Promise(resolve=>releaseSaved.push(()=>realFetch(url,...args).then(resolve))):realFetch(url,...args);")
            b.goto(srv.url())
            b.wait("releaseSaved.length>0 && document.querySelectorAll('.xc-sess').length===2")
            b.eval("[...document.querySelectorAll('.xc-sess')].find(x=>x.textContent.startsWith('s2')).click()")
            b.wait("document.querySelector('.xc-sess.on')?.textContent.startsWith('s2') && document.querySelectorAll('.xc-msg').length>100")
            b.eval("Promise.all(releaseSaved.map(f=>f()))")
            assert b.eval("document.querySelector('.xc-sess.on').textContent.startsWith('s2')")
            assert b.eval("document.querySelector('.xc-log').scrollTop") == 0
            assert b.eval("savedPrefs.filter(p=>p.thread).at(-1).thread") == 's2'
    finally:
        srv.close()


def test_native_bridge_rejects_text_unknown_fields_and_invalid_numbers(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if not node:
        pytest.skip('Node missing')
    static = Path(__file__).resolve().parents[1] / 'exo_command' / 'static'
    (tmp_path / 'prefs.mjs').write_text((static / 'desktop_prefs.js').read_text())
    (tmp_path / 'test.mjs').write_text('''
import assert from 'node:assert/strict';
const sent=[],stored=[];
globalThis.window={__exoDesktopPrefs:JSON.parse('{"__proto__":{},"constructor":"bad","view":"tasks","thread":"../../private text","scroll":2,"appearance":"light","token":"SECRET"}'),webkit:{messageHandlers:{exoDesktop:{postMessage:p=>sent.push(p)}}}};
globalThis.localStorage={setItem:(k,v)=>stored.push([k,v])};
const p=await import('./prefs.mjs');
assert.equal(p.desktopPreference('view'),'tasks');assert.equal(p.desktopPreference('thread'),undefined);
assert.equal(p.desktopPreference('scroll'),undefined);assert.equal(p.desktopPreference('token'),undefined);
p.saveDesktopPreferences({token:'SECRET',thread:'a/b',scroll:NaN,view:'invalid',constructor:'bad'});
assert.deepEqual(sent,[]);
p.saveDesktopPreferences({thread:'session_1-a',scroll:.5,layout:'classic'});
assert.deepEqual(sent,[{thread:'session_1-a',scroll:.5,layout:'classic'}]);
p.saveDesktopPreferences({thread:null,scroll:null});assert.deepEqual(sent.at(-1),{thread:null,scroll:null});
assert.ok(!JSON.stringify([sent,stored]).includes('SECRET'));
''')
    result = subprocess.run([node, str(tmp_path / 'test.mjs')], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_saved_reading_survives_new_origin_and_reload():
    first = Served(ThreadSrc(pairs=60))
    second = None
    try:
        with Browser() as b:
            inject(b, {'view': 'chat', 'thread': 's1', 'layout': 'classic', 'scroll': .35})
            b.goto(first.url())
            b.wait("(()=>{const l=document.querySelector('.xc-log');return l && Math.abs(l.scrollTop/(l.scrollHeight-l.clientHeight)-.35)<.02;})()")
            b.eval("document.querySelector('.xc-log').scrollTop=700")
            b.wait("savedPrefs.some(p=>p.thread==='s1' && p.scroll>0 && p.scroll<.2)")
            stored = b.eval("Object.assign({},__exoDesktopPrefs,...savedPrefs)")
            second = Served(ThreadSrc(pairs=60))
            assert first.port != second.port
            inject(b, stored)
            b.goto(second.url())
            fraction = stored['scroll']
            check = f"(()=>{{const l=document.querySelector('.xc-log');return l && Math.abs(l.scrollTop/(l.scrollHeight-l.clientHeight)-{fraction})<.02;}})()"
            b.wait(check)
            b.call('Page.reload')
            b.wait(check)
    finally:
        if second:
            second.close()
        first.close()


@pytest.mark.parametrize('layout', ['inverse', 'classic'])
def test_layout_change_cancels_held_native_reading_restore(layout):
    srv = Served(ThreadSrc(pairs=60))
    try:
        with Browser() as b:
            inject(b, {'view': 'chat', 'thread': 's1', 'layout': layout, 'scroll': .45})
            b.call('Page.addScriptToEvaluateOnNewDocument', source="const realFetch=window.fetch;window.releaseSaved=[];window.fetch=(url,...args)=>String(url).endsWith('/api/chat/sessions/s1')?new Promise(resolve=>releaseSaved.push(()=>realFetch(url,...args).then(resolve))):realFetch(url,...args);")
            b.goto(srv.url())
            b.wait("releaseSaved.length>0 && !!document.querySelector('.xc-layout')")
            b.eval("document.querySelector('.xc-layout').click();releaseSaved.forEach(f=>f())")
            b.wait("document.querySelectorAll('.xc-msg').length>100")
            target = 'classic' if layout == 'inverse' else 'inverse'
            b.wait(f"(()=>{{const l=document.querySelector('.xc-log');return Math.abs(l.scrollTop-({'l.scrollHeight-l.clientHeight' if target == 'classic' else '0'}))<2;}})()")
            assert b.eval("savedPrefs.some(p=>p.layout==='" + target + "' && p.scroll===null)")
    finally:
        srv.close()


def test_unmount_rejects_pending_saved_reading_response():
    srv = Served(ThreadSrc(pairs=60))
    try:
        with Browser() as b:
            inject(b, {'view': 'chat', 'thread': 's1', 'layout': 'classic', 'scroll': .6})
            b.call('Page.addScriptToEvaluateOnNewDocument', source="const realFetch=window.fetch;window.releaseSaved=[];window.fetch=(url,...args)=>String(url).endsWith('/api/chat/sessions/s1')?new Promise(resolve=>releaseSaved.push(()=>realFetch(url,...args).then(resolve))):realFetch(url,...args);")
            b.goto(srv.url())
            b.wait("releaseSaved.length>0")
            b.eval("document.querySelector('a[href=\"#tasks\"]').click()")
            b.wait("document.querySelector('.xc-nav-link[aria-current]')?.textContent==='Tasks'")
            b.eval("Promise.all(releaseSaved.map(f=>f()))")
            assert not b.eval("!!document.querySelector('.xc-log')")
            assert b.eval("savedPrefs.filter(p=>p.view).at(-1).view") == 'tasks'
            assert not b.eval("savedPrefs.some(p=>p.scroll===.6)")
    finally:
        srv.close()


def test_failed_saved_thread_read_does_not_erase_last_good_position():
    srv = Served(ThreadSrc(pairs=60))
    try:
        with Browser() as b:
            inject(b, {'view': 'chat', 'thread': 's1', 'layout': 'classic', 'scroll': .6})
            failing_script = b.call('Page.addScriptToEvaluateOnNewDocument', source="const realFetch=window.fetch;window.failedSaved=0;window.fetch=(url,...args)=>String(url).endsWith('/api/chat/sessions/s1')?(failedSaved++,Promise.resolve(new Response(JSON.stringify({error:'Synthetic unavailable'}),{status:503,headers:{'Content-Type':'application/json'}}))):realFetch(url,...args);")
            b.goto(srv.url())
            b.wait("failedSaved>0 && document.querySelector('.xc-strip').textContent.includes('Synthetic unavailable')")
            assert not b.eval("savedPrefs.some(p=>Object.hasOwn(p,'scroll'))")
            b.eval("document.querySelector('a[href=\"#tasks\"]').click()")
            b.wait("document.querySelector('.xc-nav-link[aria-current]')?.textContent==='Tasks'")
            assert not b.eval("savedPrefs.some(p=>Object.hasOwn(p,'scroll'))")
            # A reload with the same host preference must still restore the last successful position.
            b.call('Page.removeScriptToEvaluateOnNewDocument', identifier=failing_script['identifier'])
            b.goto(srv.url('chat').replace('boot=0', 'boot=retry'))
            b.wait("(()=>{const l=document.querySelector('.xc-log');return l && Math.abs(l.scrollTop/(l.scrollHeight-l.clientHeight)-.6)<.02;})()")
    finally:
        srv.close()
