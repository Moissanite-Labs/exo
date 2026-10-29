"""Exopet is a read-only local projection, not a model turn or an invented activity feed."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from browser import Browser, Served, find_chrome
from test_chat import FakeSource, call, centre
from test_layout_web import ThreadSrc
from exo_command.source import SourceError

STATIC = Path(__file__).resolve().parents[1] / "exo_command" / "static"
STATUS = {"schema_version": 1, "state": "ready", "verification": {"state": "verified"},
          "personal": {"xp": 300, "level": 6, "earned_form": "cell"}, "form": "egg", "next_form": "cell",
          "progress": 1, "learned_count": 2, "community": {"state": "pending", "hatch_at": None}, "stale": False}


def test_pet_route_only_runs_the_existing_read_builtin():
    src = FakeSource({"command.run": {"output": STATUS}})
    assert call(centre(src), "GET", "/api/pet") == (200, STATUS)
    assert [(type(c).TYPE, c.name, c.args, c.session) for c in src.calls] == [("command.run", "pet", "status", None)]
    src.calls.clear()
    assert call(centre(src), "POST", "/api/pet", {"action": "update"})[0] == 405
    assert call(centre(src), "GET", "/api/pet", q={"args": ["update"]})[0] == 400
    assert call(centre(src), "GET", "/api/pet", {"action": "update"})[0] == 400
    assert not src.calls


@pytest.mark.parametrize("code,status", [("forbidden", 403), ("unsupported", 501), ("failed", 502)])
def test_pet_route_preserves_core_authority_and_failure(code, status):
    assert call(centre(FakeSource(fail={"command.run": code})), "GET", "/api/pet")[0] == status


class PetSource(ThreadSrc):
    def __init__(self):
        super().__init__(pairs=0)
        self.calls = []
        self.pet_failure = False
        self.pending = []
        self.status = STATUS

    async def call(self, body):
        kind = type(body).TYPE
        self.calls.append((kind, getattr(body, "name", "")))
        if kind == "session.list":
            return {"sessions": [{"id": sid, "title": sid, "agent": "atlas", "model": "m", "status": "idle"} for sid in ("s1", "s2")]}
        if kind == "approvals.list":
            return {"approvals": self.pending}
        if kind == "command.run" and body.name == "pet":
            if self.pet_failure:
                raise SourceError("failed", "verification service unavailable")
            return {"output": self.status}
        return await super().call(body)


needs_browser = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"), reason="Chrome or websockets missing")


def mode(b, choice):
    b.wait("!!document.querySelector('.xc-pet-mode')")
    b.eval(f"(()=>{{const s=document.querySelector('.xc-pet-mode');s.value='{choice}';s.dispatchEvent(new Event('change'));}})()")


def event(srv, seq, kind, body=None, session="s1"):
    srv.call(srv.centre.record, {"seq": seq, "type": kind, "session": session, "body": body or {}})


@needs_browser
def test_mini_full_off_and_local_status_never_unlock_or_call_a_model():
    src = PetSource(); srv = Served(src)
    try:
        with Browser(width=390, height=900) as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-pet-glyph svg') && !!document.querySelector('.xc-sess.on')")
            assert b.eval("document.querySelector('.xc-pet-mode').value") == "mini"
            assert b.eval("document.querySelector('.xc-pet-card').hidden")
            assert b.eval("getComputedStyle(document.querySelector('#xc-live')).display") == 'none'
            mode(b, "full")
            b.wait("document.querySelector('.xc-pet-growth').textContent.includes('300 XP banked')")
            assert b.eval("document.querySelector('.xc-pet-hero svg').getAttribute('data-form')") == "egg"
            assert 'community hatch pending' in b.eval("document.querySelector('.xc-pet-growth').textContent")
            assert 'recorded promotions' in b.eval("document.querySelector('.xc-pet-growth').textContent")
            assert b.eval("document.documentElement.scrollWidth <= innerWidth")
            assert not b.eval("document.querySelector('.xc-pet-card').hidden")
            assert not b.eval("document.querySelector('.xc-pet-card').open")
            assert b.eval("getComputedStyle(document.querySelector('.xc-side')).display") != 'none'
            assert b.eval("document.querySelector('.xc-pet-growth').textContent.includes('null')") is False
            assert b.eval("getComputedStyle(document.querySelector('#xc-live')).display") == 'none'
            mode(b, "off")
            b.wait("document.querySelectorAll('.xc-pet-glyph svg,.xc-pet-card svg,.xc-pet-hero svg').length === 0")
            assert b.eval("getComputedStyle(document.querySelector('#xc-live')).display") != 'none'
            reads = len([c for c in src.calls if c == ("command.run", "pet")])
            event(srv, 1, "turn.completed", {"turn_id": "t", "stop_reason": "end"})
            event(srv, 2, "taste.updated", {"rule_id": "x", "action": "add"})
            b.eval("new Promise(r=>setTimeout(r,350))")
            assert len([c for c in src.calls if c == ("command.run", "pet")]) == reads
            b.eval("(()=>{const i=document.querySelector('.xc-composer .xc-input');i.focus();i.value='/pet status';i.dispatchEvent(new Event('input'));})()")
            b.press('Enter', 'Enter', 13)
            b.wait("document.querySelector('.xc-strip').textContent.includes('Exopet is off')")
            assert b.eval("document.querySelector('.xc-composer .xc-input').value") == '/pet status'
            mode(b, "mini")
            b.wait("!!document.querySelector('.xc-pet-glyph svg')")
            b.press('Enter', 'Enter', 13)
            b.wait("document.querySelector('.xc-log').textContent.includes('Exopet status')")
            assert not any(kind == 'turn.send' for kind, _ in src.calls)
            assert b.eval("localStorage.getItem('xc-pet-mode')") == 'mini'
    finally:
        srv.close()


@needs_browser
def test_selected_activity_waiting_priority_error_retry_and_reconnect():
    src = PetSource(); srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url()); mode(b, "full")
            b.wait("document.querySelector('.xc-pet-activity').textContent.includes('Idle')")
            b.wait("document.querySelector('.xc-pet-growth').textContent.includes('Growth verified locally')")
            event(srv, 1, 'turn.started', {'turn_id': 't'}, 's2')
            assert not b.eval("document.querySelector('.xc-pet-activity').textContent.includes('Working')")
            event(srv, 2, 'turn.started', {'turn_id': 't'})
            b.wait("document.querySelector('.xc-pet-activity').textContent.includes('Working')")
            event(srv, 3, 'message.delta', {'message_id': 'a', 'text': 'answer'})
            b.wait("document.querySelector('.xc-pet-activity').textContent.includes('Answering')")
            for seq in (4, 5):
                event(srv, seq, 'tool.started', {'call': {'id': 'tool1', 'name': 'read_file', 'arguments': {}}})
            event(srv, 6, 'approval.requested', {'request_id': 'approval', 'summary': 'Review', 'options': ['allow', 'deny']})
            b.wait("document.querySelector('.xc-pet-activity').textContent.includes('Waiting for approval')")
            event(srv, 7, 'approval.resolved', {'request_id': 'approval', 'decision': 'deny'})
            b.wait("document.querySelector('.xc-pet-activity').textContent.includes('Running a tool')")
            event(srv, 8, 'tool.completed', {'result': {'call_id': 'tool1', 'content': [], 'is_error': False}})
            b.wait("document.querySelector('.xc-pet-activity').textContent.includes('Answering')")
            event(srv, 9, 'turn.completed', {'turn_id': 't', 'stop_reason': 'interrupted'})
            b.wait("document.querySelector('.xc-pet-activity').textContent.includes('Idle')")
            event(srv, 10, 'model.call', {'call_id': 'old', 'route': 'fake:x'})
            assert b.eval("document.querySelector('.xc-pet-activity').textContent.includes('Idle')")
            src.pet_failure = True
            event(srv, 11, 'taste.updated', {'rule_id': 'x', 'action': 'add'})
            b.wait("document.querySelector('.xc-pet-growth').textContent.includes('Local growth unavailable')")
            assert b.eval("document.querySelector('.xc-pet-growth').textContent.includes('frozen at the last verified')")
            src.pet_failure = False
            event(srv, 12, 'taste.updated', {'rule_id': 'y', 'action': 'add'})
            b.wait("!document.querySelector('.xc-pet-growth').textContent.includes('Local growth unavailable') && document.querySelector('.xc-pet-growth').textContent.includes('Growth verified locally')")
            # A fresh real page restores only the authoritative approval snapshot, without replay-derived tool activity.
            src.pending = [{'request_id': 'restored', 'session': 's1', 'summary': 'Pending review', 'options': ['allow', 'deny']}]
            b.eval("location.reload()")
            b.wait("document.querySelector('.xc-pet-activity')?.textContent.includes('Waiting for approval')")
            assert not any(kind == 'turn.send' for kind, _ in src.calls)
    finally:
        srv.close()


@pytest.mark.skipif(not shutil.which('node'), reason='Node is needed')
def test_off_aborts_reads_clears_own_lifecycle_and_reenable_coalesces(tmp_path):
    (tmp_path / 'pet.mjs').write_text((STATIC / 'pet.js').read_text().replace('.js"', '.mjs"'))
    (tmp_path / 'dom.mjs').write_text('''
export function h(tag,attrs,...children){return {tag,attrs:attrs||{},children,isConnected:true,hidden:false,
  append(...c){this.children.push(...c);},replaceChildren(...c){this.children=c;},setAttribute(k,v){this.attrs[k]=v;},
  addEventListener(k,f){this.listeners[k]=f;},removeEventListener(k){delete this.listeners[k];},listeners:{}};}
export const api=(...args)=>globalThis.mockApi(...args);
''')
    (tmp_path / 'bus.mjs').write_text('export const onState=f=>{globalThis.busCount++;f({connected:true});return()=>globalThis.busCount--;};')
    (tmp_path / 'pet-renderer.mjs').write_text('''export const createPetRenderer=()=>{globalThis.created++;return {
render(input){globalThis.rendered++;globalThis.lastRender=input;},pause(){globalThis.paused++;},destroy(){globalThis.destroyed++;}};};''')
    (tmp_path / 'test.mjs').write_text('''
import assert from 'node:assert/strict';
let now=1000,nextId=1;const timers=new Map();
globalThis.setTimeout=(fn,delay)=>{const id=nextId++;timers.set(id,{fn,at:now+delay});return id;};
globalThis.clearTimeout=id=>timers.delete(id);Date.now=()=>now;
const flush=async()=>{for(let n=0;n<12;n++)await Promise.resolve();};
async function advance(ms){now+=ms;for(const [id,t]of [...timers])if(t.at<=now){timers.delete(id);t.fn();}await flush();}
let documentListeners=0,mediaListeners=0;const documentHandlers={};
globalThis.document={hidden:false,addEventListener(k,f){documentListeners++;documentHandlers[k]=f;},removeEventListener(k){documentListeners=Math.max(0,documentListeners-1);delete documentHandlers[k];}};
const media={matches:false,addEventListener(k,f){mediaListeners++;this.change=f;},removeEventListener(){mediaListeners--;}};
globalThis.window={matchMedia:()=>media};
let observer;globalThis.IntersectionObserver=class {constructor(callback){this.callback=callback;observer=this;}observe(){}unobserve(){}disconnect(){this.disconnected=true;}};
const saved=[];globalThis.localStorage={getItem:()=>null,setItem:(k,v)=>saved.push([k,v])};
globalThis.busCount=0;globalThis.created=0;globalThis.rendered=0;globalThis.paused=0;globalThis.destroyed=0;
const requests=[];globalThis.mockApi=(method,path,body,signal)=>new Promise((resolve,reject)=>{
 requests.push({method,path,signal,resolve});signal.addEventListener('abort',()=>reject(Object.assign(Error('aborted'),{name:'AbortError'})));});
const {createPet,getPetMode,savePetMode}=await import('./pet.mjs');
assert.equal(getPetMode({getItem:()=>"arbitrary prompt"}),"mini");
const pet=createPet();pet.start();pet.selectSession('s1');pet.hydrate({session:'s1',running:false,tools:[],approvals:[]});
await advance(0);assert.equal(requests.length,1);assert.equal(busCount,1);
document.hidden=true;documentHandlers.visibilitychange();assert.ok(paused>0);
pet.event({type:'turn.started',session:'s1',body:{}});assert.equal(lastRender.active,false);
document.hidden=false;documentHandlers.visibilitychange();assert.equal(lastRender.active,true);
observer.callback([{target:pet.glyph,isIntersecting:false}]);assert.equal(lastRender.active,false);
media.matches=true;media.change();assert.equal(lastRender.reducedMotion,true);
for(let n=0;n<30;n++)pet.refresh();await advance(100);assert.equal(requests.length,1);
const control=pet.control.children.find(x=>x.tag==='select');control.value='off';control.attrs.onchange();await flush();
assert.equal(requests[0].signal.aborted,true);assert.equal(busCount,0);assert.equal(documentListeners,0);assert.equal(mediaListeners,0);
assert.equal(timers.size,0);assert.ok(destroyed>0);
const before=rendered;pet.event({type:'turn.started',session:'s1',body:{}});pet.refresh();await advance(10000);
assert.equal(requests.length,1);assert.equal(rendered,before);
control.value='mini';control.attrs.onchange();await advance(0);assert.equal(requests.length,2);assert.equal(busCount,1);
const inspection=pet.inspect();let finished=false;inspection.then(()=>finished=true);
await flush();assert.equal(finished,false);
requests[1].resolve({schema_version:1,form:'egg',community:{state:'pending'},personal:{xp:0,level:1},verification:{state:'verified'},stale:false});
await flush();assert.equal((await inspection).personal.xp,0);
assert.deepEqual(saved,[["xc-pet-mode","off"],["xc-pet-mode","mini"]]);
await advance(200);assert.equal(requests.length,3); // one follow-up coalesced while inspection was pending
for(let n=0;n<20;n++)pet.refresh();
await advance(60000);assert.equal(requests[2].signal.aborted,false); // allow the measured ~59s initial full verification
await advance(30000);assert.equal(requests[2].signal.aborted,true);assert.equal(requests.length,3);
await advance(100000);assert.equal(requests.length,3); // timeout never turns queued demand into a retry loop
pet.suspend();assert.equal(timers.size,0);assert.equal(busCount,0);assert.equal(documentListeners,0);assert.equal(mediaListeners,0);
''')
    result = subprocess.run([shutil.which('node'), str(tmp_path / 'test.mjs')], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@needs_browser
@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('width', [320, 390, 768, 1440])
def test_full_details_are_reachable_and_hero_never_overlaps_heading(theme, width):
    src = PetSource(); srv = Served(src)
    try:
        with Browser(width=width, height=900) as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-pet-mode')")
            b.eval(f"document.querySelector('.xc-appearance').value='{theme}';document.querySelector('.xc-appearance').dispatchEvent(new Event('change'))")
            mode(b, 'full')
            b.wait("document.querySelector('.xc-pet-growth').textContent.includes('300 XP banked')")
            assert b.eval("document.querySelector('.xc-pet-growth').textContent.includes('null')") is False
            result = b.eval("""(()=>{const p=document.querySelector('.xc-pet-hero').getBoundingClientRect(),t=document.querySelector('.xc-hero-title').getBoundingClientRect();return {page:document.documentElement.scrollWidth,overlap:p.left<t.right&&p.right>t.left&&p.top<t.bottom&&p.bottom>t.top,side:getComputedStyle(document.querySelector('.xc-side')).display};})()""")
            assert result['page'] <= width and not result['overlap'] and result['side'] != 'none', result
            if width <= 1100:
                assert not b.eval("document.querySelector('.xc-pet-card').open")
                b.eval("document.querySelector('.xc-pet-card-summary').focus()")
                b.call('Input.dispatchKeyEvent', type='keyDown', key='Enter', code='Enter', windowsVirtualKeyCode=13, text='\r')
                b.call('Input.dispatchKeyEvent', type='keyUp', key='Enter', code='Enter', windowsVirtualKeyCode=13)
                b.wait("document.querySelector('.xc-pet-card').open")
                assert b.eval("document.activeElement.classList.contains('xc-pet-card-summary')")
            # Refresh replaces only the content region, so the summary's keyboard focus survives.
            b.eval("document.querySelector('.xc-pet-card-summary').focus()")
            event(srv, 1, 'taste.updated', {'rule_id': 'new', 'action': 'add'})
            b.eval("new Promise(r=>setTimeout(r,300))")
            assert b.eval("document.activeElement.classList.contains('xc-pet-card-summary')")
            assert b.eval("document.querySelector('.xc-pet-growth').getClientRects().length > 0")
            assert not any(kind == 'turn.send' for kind, _ in src.calls)
    finally:
        srv.close()


def test_real_core_pet_builtin_payload_reaches_the_authenticated_read_bridge(tmp_path, monkeypatch):
    import asyncio

    from exo import compose
    from exo.config import Config
    from exo.server.app import Dispatcher
    from exo_command import api
    from exo_command.source import DispatcherSource

    monkeypatch.setenv('EXO_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('EXO_PLUGIN_DIRS', str(tmp_path / 'no-plugins'))
    Config({'harness': {'profile': 'lean'}, 'models': {'roles': {'main': ['fake:x'], 'aux': ['fake:x']}},
            'cwd': str(tmp_path)}, tmp_path / 'home').save()
    engine = compose.create()

    async def scenario():
        try:
            command_centre = centre(DispatcherSource(Dispatcher(engine)))
            code, result = await api.dispatch(command_centre, 'GET', '/api/pet', {}, {})
            assert code == 200 and result['schema_version'] == 1
            assert result['form'] == 'egg' and result['community']['state'] == 'pending'
            assert result['personal']['xp'] is None and result['verification']['state'] == 'unavailable'
            assert result['state'] == 'no_store'
            assert not engine.store._q("SELECT 1 FROM events WHERE type IN ('turn.started','model.call')")
        finally:
            await engine.close()
    asyncio.run(asyncio.wait_for(scenario(), 10))


@needs_browser
def test_full_companion_does_not_reveal_empty_hero_in_nonempty_conversation():
    src = PetSource(); src.pairs = 2
    srv = Served(src)
    try:
        with Browser(width=1440, height=900) as b:
            b.goto(srv.url()); mode(b, "full")
            b.wait("document.querySelector('.xc-log').textContent.length > 0")
            assert b.eval("document.querySelector('.xc-hero').hidden")
            assert b.eval("getComputedStyle(document.querySelector('.xc-hero')).display") == 'none'
    finally:
        srv.close()


@needs_browser
def test_pet_read_needs_page_token_and_aborted_old_selection_cannot_replace_status():
    src = PetSource(); srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url()); mode(b, 'full')
            b.wait("document.querySelector('.xc-pet-growth').textContent.includes('300 XP banked')")
            reads = len([c for c in src.calls if c == ('command.run', 'pet')])
            assert b.eval("fetch('/api/pet').then(response=>response.status)") == 403
            assert len([c for c in src.calls if c == ('command.run', 'pet')]) == reads
            b.eval("""(()=>{const original=window.fetch;window.petReads=[];
              window.fetch=(url,options)=>String(url)==='/api/pet'?new Promise(resolve=>petReads.push({resolve,signal:options.signal})):original(url,options);
            })()""")
            event(srv, 1, 'taste.updated', {'rule_id': 'new', 'action': 'add'})
            b.wait("window.petReads.length === 1")
            b.eval("[...document.querySelectorAll('.xc-sess')].find(x=>x.textContent.includes('s2')).click()")
            b.wait("window.petReads.length === 2")
            assert b.eval("window.petReads[0].signal.aborted")
            old = dict(STATUS, personal={'xp': 999, 'level': 10})
            fresh = dict(STATUS, personal={'xp': 20, 'level': 2})
            b.eval("window.petReads[1].resolve(new Response(" + json.dumps(json.dumps(fresh)) + "))")
            b.wait("document.querySelector('.xc-pet-growth').textContent.includes('20 XP banked')")
            b.eval("window.petReads[0].resolve(new Response(" + json.dumps(json.dumps(old)) + "))")
            b.eval("new Promise(resolve=>setTimeout(resolve,0))")
            assert b.eval("document.querySelector('.xc-pet-growth').textContent.includes('20 XP banked')")
            assert not b.eval("document.querySelector('.xc-pet-growth').textContent.includes('999 XP banked')")
            assert not any(kind == 'turn.send' for kind, _ in src.calls)
    finally:
        srv.close()


@needs_browser
@pytest.mark.parametrize('state,verification,personal,stale,expected', [
    ('no_store', {'state': 'unavailable', 'reason': 'no_principal_store'}, {'xp': None, 'level': None}, False, 'No principal store is configured'),
    ('empty', {'state': 'verified', 'reason': None}, {'xp': 0, 'level': 1}, False, 'Personal level 1 · 0 XP banked'),
    ('unverified', {'state': 'unverified', 'reason': 'integrity_or_trust_failed'}, {'xp': 300, 'level': 6}, True, 'Ledger integrity or trust verification failed'),
    ('unavailable', {'state': 'unavailable', 'reason': '/private/raw-backend-detail'}, {'xp': None, 'level': None}, False, 'Local growth could not be verified'),
])
def test_growth_unavailability_is_not_zero_xp_and_freeze_reasons_are_safe(state, verification, personal, stale, expected):
    src = PetSource()
    src.status = dict(STATUS, state=state, verification=verification, personal=personal, stale=stale,
                      progress=None, learned_count=None)
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url()); mode(b, 'full')
            b.wait("document.querySelector('.xc-pet-growth').textContent.includes(" + json.dumps(expected) + ")")
            text = b.eval("document.querySelector('.xc-pet-growth').textContent")
            assert '/private/raw-backend-detail' not in text and 'null' not in text
            if state == 'no_store':
                assert 'Personal growth unavailable' in text and '· 0 XP banked' not in text
            if stale:
                assert 'Growth frozen at the last verified read' in text and '300 XP banked' in text
            assert not any(kind == 'turn.send' for kind, _ in src.calls)
    finally:
        srv.close()


@needs_browser
@pytest.mark.parametrize('choice', ['mini', 'full'])
def test_pet_hosts_preserve_empty_hero_and_instrument_spacing(choice):
    srv = Served(PetSource())
    try:
        with Browser(width=1440, height=900) as b:
            b.goto(srv.url()); mode(b, choice)
            b.wait("!!document.querySelector('.xc-side-contents .xc-ins')")
            assert b.eval("getComputedStyle(document.querySelector('.xc-side-contents')).gap") == '20px'
            assert b.eval("(()=>{const a=[...document.querySelector('.xc-side-contents').children].map(x=>x.getBoundingClientRect());return a.length>1 && a[1].top-a[0].bottom>=19;})()")
            if choice == 'mini':
                assert b.eval("document.querySelector('.xc-pet-hero-host').getBoundingClientRect().height") == 0
                assert b.eval("document.querySelector('.xc-pet-card-host').getBoundingClientRect().height") == 0
            else:
                b.wait("!!document.querySelector('.xc-pet-hero svg')")
                assert b.eval("(()=>{const p=document.querySelector('.xc-pet-hero').getBoundingClientRect(),t=document.querySelector('.xc-hero-title').getBoundingClientRect();return p.height>0 && t.top>=p.bottom;})()")
    finally:
        srv.close()


@needs_browser
def test_changing_ledger_is_not_presented_as_an_integrity_failure():
    src = PetSource()
    src.status = {**STATUS, 'state': 'unavailable', 'stale': True,
                  'verification': {'state': 'unavailable', 'reason': 'ledger_busy'}}
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url()); mode(b, 'full')
            b.wait("document.querySelector('.xc-pet-card').textContent.includes('The local ledger is changing.')")
            assert 'integrity or trust verification failed' not in b.eval("document.querySelector('.xc-pet-card').textContent")
    finally:
        srv.close()
