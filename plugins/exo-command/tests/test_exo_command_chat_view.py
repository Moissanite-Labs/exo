"""The Chat view in Node with an offline DOM and API: peer, bus and job messages show their real author, an approval
with no session shows in every thread, and a dismissed panel stays dismissed. The real chat.js, chat_state.js,
approvals.js and bus.js run; the rest are small stand-ins."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from exo_command.server import STATIC

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")

DOM = '''
export class El {
  constructor(tag, attrs={}, children=[]) { this.tagName=tag.toUpperCase(); this.attrs=attrs || {}; this.value=""; this.style={}; this.children=[]; this.listeners={}; this.classList={add(){},remove(){},toggle(){}}; this.scrollHeight=10; this.clientHeight=10; this.scrollTop=0; this.isConnected=false; this.append(children); globalThis.nodes.push(this); }
  append(...xs){for(const x of xs.flat(Infinity).filter(x=>x!=null)){if(x instanceof El){if(x.parent)x.remove();x.parent=this;}this.children.push(x);}}
  replaceChildren(...xs){for(const c of this.children)if(c instanceof El)c.parent=null;this.children=[];this.append(xs);}
  appendChild(x){this.append(x);} prepend(x){this.append(x);this.children.unshift(this.children.pop());}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.parent=null;}
  contains(x){return x===this||this.children.some(c=>c instanceof El&&c.contains(x));}
  setAttribute(k,v){this.attrs[k]=v;} addEventListener(k,f){this.listeners[k]=f;}
  dispatchEvent(){} setSelectionRange(){} focus(){}
  set textContent(v){this.children=[v];} get textContent(){return this.children.map(x=>x instanceof El?x.textContent:String(x)).join("");}
}
export const h=(t,a,...c)=>new El(t,a,c);
export const api=(...args)=>globalThis.mockApi(...args);
export const short=(t)=>t || ""; export const when=()=>"";
export const partsText=(p)=>(p || []).filter(x=>x.type==="text").map(x=>x.text || "").join("");
export const tag=(t)=>h("span",{class:"xc-tag"},t); export const empty=tag;
'''

# The page's bus as chat.js sees it: the open panels, one listener, and forget(), which the real bus.js has.
BUS = '''
let seq=0, listener;
globalThis.panels=globalThis.panels||[]; globalThis.forgot=[];
export const on=f=>(listener=f,()=>{}); export const onState=f=>(f({synced:0}),()=>{});
export const openPanels=()=>globalThis.panels.slice(); export const lastSeq=()=>seq;
export function forget(id){globalThis.forgot.push(id);globalThis.panels=globalThis.panels.filter(p=>p.panel_id!==id);}
export function emit(e){seq=e.seq;if(e.type==="ui.panel")globalThis.panels.push({...e.body,session:e.session});listener(e);}
'''

PRELUDE = r'''
import assert from "node:assert/strict";
globalThis.nodes=[]; globalThis.window={matchMedia:()=>({matches:true})};
globalThis.document={getElementById:()=>null,addEventListener(){},removeEventListener(){}};
globalThis.sessionStorage={getItem:()=>null,removeItem(){}}; globalThis.requestAnimationFrame=f=>f();
const requests=[];
globalThis.messages=globalThis.messages||[]; globalThis.pending=globalThis.pending||[];
globalThis.mockApi=async (method,path,body)=>{
  requests.push({method,path,body});
  if(path==="/api/chat/sessions" && method==="GET")return {sessions:[{id:"s1",model:"fake:x",status:"idle"},{id:"s2",model:"fake:x",status:"idle"}]};
  if(path.endsWith("/command"))throw Error("no reports here");
  if(path.startsWith("/api/chat/sessions/"))return {session:{id:path.split("/").pop(),model:"fake:x"},messages:path.endsWith("/s1")?globalThis.messages:[],seq:10};
  if(path==="/api/section/approvals")return {pending:globalThis.pending};
  return {};
};
const {h}=await import("./dom.mjs"), chat=await import("./chat.mjs"), bus=await import("./bus.mjs");
const settle=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const walk=x=>[x,...x.children.filter(c=>c.children).flatMap(walk)];
const byClass=c=>nodes.find(x=>x.attrs.class===c);
let root=h("div"), off=chat.mount(root);
await settle();
const log=()=>byClass("xc-log"), cards=()=>log().children.filter(x=>/xc-peer/.test(x.attrs.class));
const userRows=()=>log().children.filter(x=>/xc-user/.test(x.attrs.class));
const PEER=(mid,id)=>({id,role:"user",content:[{type:"text",text:"[peer message from other-session | id "+mid+"]\nShip it."}],
  meta:{peer:{from:"other-session",trust:"same-principal",msg_id:mid,session:null},author:"peer:other-session"}});
'''


def _run(tmp_path, script: str, setup: str = "", panels: str = "") -> None:
    for name in ("chat", "chat_state", "approvals"):
        (tmp_path / f"{name}.mjs").write_text((STATIC / f"{name}.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "dom.mjs").write_text(DOM)
    (tmp_path / "bus.mjs").write_text(BUS)
    (tmp_path / "panels.mjs").write_text(panels or 'export const renderPanel=()=>null;')
    (tmp_path / "markdown.mjs").write_text('export const renderMarkdown=t=>t;')  # for a chat.js that renders replies
    (tmp_path / "hero.mjs").write_text('export const createHero=()=>({start(){},stop(){}});')
    (tmp_path / "permissions.mjs").write_text('export const set=async()=>"",refresh=async()=>({}),current=()=>({}),describe=()=>"";')
    (tmp_path / "composer.mjs").write_text('''import {h} from "./dom.mjs";
export const attachComposer=()=>({guideEl:h("div"),menuEl:h("div"),note(){},sent(){},stop(){},typo(){return null;},ready:()=>true,elsewhere:()=>""});
''')
    (tmp_path / "layout.mjs").write_text('export const getLayout=()=>"classic", setLayout=()=>{}, ordered=x=>x, homeScroll=()=>0, '
                                         'nearHome=()=>true, columnOrder=()=>["banner","stage","approvals","status","steer","guide",'
                                         '"composer","menu"], LAYOUTS=["classic","inverse"];')
    (tmp_path / "test.mjs").write_text(setup + PRELUDE + script + '\noff();console.log("ok");\n')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")



REAL_PRELUDE = r"""
import assert from "node:assert/strict";
// A faithful document shim: createElement returns an element whose setAttribute /
// getAttribute behave like a browser's, so the real dom.js helper's Boolean
// handling (true -> "", false -> dropped) is visible instead of being hidden
// by the fake DOM's verbatim attrs.
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.attrs = {}; this.children = []; this.listeners = {}; this.className = ""; this.value = ""; this.style = {}; this.hidden = false; this.scrollHeight = 10; this.clientHeight = 10; this.scrollTop = 0; this.isConnected = false; this.parent = null; }
  append(...xs) { for (const x of xs.flat(Infinity).filter((x) => x != null && x !== false)) { if (x instanceof El) { if (x.parent) x.remove(); x.parent = this; } this.children.push(x); } }
  appendChild(x) { this.append(x); return x; }
  prepend(x) { this.append(x); this.children.unshift(this.children.pop()); }
  replaceChildren(...xs) { for (const c of this.children) if (c instanceof El) c.parent = null; this.children = []; this.append(xs); }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter((x) => x !== this); this.parent = null; } }
  contains(x) { return x === this || this.children.some((c) => c instanceof El && c.contains(x)); }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(k, f) { this.listeners[k] = f; }
  dispatchEvent() {} focus() {} select() {} setSelectionRange() {}
  get textContent() { return this.children.map((x) => x instanceof El ? x.textContent : (x && typeof x === "object" ? (x.textContent ?? "") : String(x))).join(""); }
  set textContent(v) { this.children = [{textContent: String(v)}]; }
  get classList() { const self = this; return { add(c) { const s = new Set(self.className.split(" ").filter(Boolean)); s.add(c); self.className = [...s].join(" "); }, remove(c) { self.className = self.className.split(" ").filter((x) => x !== c).join(" "); }, toggle(c, force) { const has = self.className.split(" ").includes(c); const on = force === undefined ? !has : !!force; if (on) this.add(c); else this.remove(c); } }; }
}
globalThis.document = { createElement: (t) => new El(t), createTextNode: (t) => ({textContent: String(t)}), getElementById: () => null, activeElement: null, addEventListener() {}, removeEventListener() {} };
globalThis.window = {matchMedia: () => ({matches: true})};
globalThis.sessionStorage = {getItem: () => null, removeItem() {}};
globalThis.requestAnimationFrame = (f) => f();
const requests = [];
globalThis.messages = globalThis.messages || []; globalThis.pending = globalThis.pending || [];
globalThis.mockApi = async (method, path, body) => {
  requests.push({method, path, body});
  if (path === "/api/chat/sessions" && method === "GET") return {sessions: [{id: "s1", model: "fake:x", status: "idle"}, {id: "s2", model: "fake:x", status: "idle"}]};
  if (path.endsWith("/command")) throw Error("no reports here");
  if (path.startsWith("/api/chat/sessions/")) return {session: {id: path.split("/").pop(), model: "fake:x"}, messages: path.endsWith("/s1") ? globalThis.messages : [], seq: 10};
  if (path === "/api/section/approvals") return {pending: globalThis.pending};
  return {};
};
// The real dom.js api() talks fetch, not the fake DOM's mockApi shortcut:
// route fetch through the mock so the mounted chat loads sessions.
globalThis.fetch = async (path, opt = {}) => { let body; try { body = opt.body ? JSON.parse(opt.body) : undefined; } catch { body = undefined; } const data = await globalThis.mockApi(opt.method || "GET", typeof path === "string" ? path : path.url, body); return {ok: true, status: 200, json: async () => data}; };
const {h} = await import("./dom.mjs"), chat = await import("./chat.mjs"), bus = await import("./bus.mjs");
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const walk = (x) => [x, ...x.children.filter((c) => c instanceof El).flatMap(walk)];
let root = h("div"), off = chat.mount(root);
await settle();
const toggles = () => walk(root).filter((x) => x instanceof El && x.className.split(" ").includes("xc-sess-toggle"));
const childButtons = () => walk(root).filter((x) => x instanceof El && x.className.split(" ").includes("xc-child"));
"""


def _run_real_dom(tmp_path, script: str, setup: str = "") -> None:
    # Like _run, but dom.mjs is the real dom.js helper, not the fake DOM stand-in,
    # so attribute rendering (Boolean true -> "", false -> dropped) is real.
    for name in ("chat", "chat_state", "approvals"):
        (tmp_path / f"{name}.mjs").write_text((STATIC / f"{name}.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "dom.mjs").write_text((STATIC / "dom.js").read_text())
    (tmp_path / "bus.mjs").write_text(BUS)
    (tmp_path / "panels.mjs").write_text('export const renderPanel=()=>null;')
    (tmp_path / "markdown.mjs").write_text('export const renderMarkdown=t=>t;')
    (tmp_path / "hero.mjs").write_text('export const createHero=()=>({start(){},stop(){}});')
    (tmp_path / "permissions.mjs").write_text('export const set=async()=>"",refresh=async()=>({}),current=()=>({}),describe=()=>"";')
    (tmp_path / "composer.mjs").write_text('import {h} from "./dom.mjs";\nexport const attachComposer=()=>({guideEl:h("div"),menuEl:h("div"),note(){},sent(){},stop(){},typo(){return null;},ready:()=>true,elsewhere:()=>""});\n')
    (tmp_path / "layout.mjs").write_text('export const getLayout=()=>"classic", setLayout=()=>{}, ordered=x=>x, homeScroll=()=>0, '
                                         'nearHome=()=>true, columnOrder=()=>["banner","stage","approvals","status","steer","guide",'
                                         '"composer","menu"], LAYOUTS=["classic","inverse"];')
    (tmp_path / "test.mjs").write_text(setup + REAL_PRELUDE + script + '\noff();console.log("ok");\n')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")

def test_ingress_item_maps_peer_bus_and_job_messages(tmp_path):
    (tmp_path / "chat_state.mjs").write_text((STATIC / "chat_state.js").read_text())
    (tmp_path / "test.mjs").write_text(r'''
import assert from "node:assert/strict";
const {ingressItem}=await import("./chat_state.mjs");
const text=t=>[{type:"text",text:t}];
const peer=ingressItem({id:"m_1",role:"user",content:text("framed"),meta:{peer:{from:"other-session",trust:"same-principal",msg_id:"pm_1"}}});
assert.deepEqual(peer,{kind:"peer",id:"pm_1",label:"Peer",sender:"other-session",trust:"same-principal",text:"framed"});
const bus=ingressItem({id:"m_2",role:"user",content:text("hi"),meta:{bus:{from:"seat-b",trust:"remote",msg_id:"pm_2",thread:"t"}}});
assert.equal(bus.label,"Bus");assert.equal(bus.sender,"seat-b");assert.equal(bus.trust,"remote");assert.equal(bus.id,"pm_2");
const job=ingressItem({id:"m_3",role:"user",content:text("[job notice | done]"),meta:{job:{id:"j_1",state:"done",msg_id:"jn_1"},author:"job"}});
assert.equal(job.label,"Job");assert.equal(job.sender,"j_1");assert.equal(job.trust,"");assert.equal(job.id,"jn_1");
// an older row with a bare peer name and no msg_id keys on the message id
const old=ingressItem({id:"m_4",role:"user",content:text("x"),meta:{peer:"razi"}});
assert.equal(old.sender,"razi");assert.equal(old.id,"m_4");
assert.equal(ingressItem({id:"m_5",role:"user",content:text("mine"),meta:{author:"principal"}}),null);
assert.equal(ingressItem({id:"m_6",role:"user",content:text("mine")}),null);
assert.equal(ingressItem({id:"m_7",role:"assistant",content:text("reply"),meta:{peer:{from:"x"}}}),null);
assert.equal(ingressItem(undefined),null);
console.log("ok");
''')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_a_reloaded_thread_draws_peer_bus_and_job_cards_not_user_rows(tmp_path):
    msgs = [{"id": "m_1", "role": "user", "content": [{"type": "text", "text": "[peer message from other-session]\nShip it."}],
             "meta": {"peer": {"from": "other-session", "trust": "same-principal", "msg_id": "pm_1"}, "author": "peer:other-session"}},
            {"id": "m_2", "role": "user", "content": [{"type": "text", "text": "[bus message from seat-b]\nhello"}],
             "meta": {"bus": {"from": "seat-b", "trust": "remote", "msg_id": "pm_2"}, "author": "bus:seat-b"}},
            {"id": "m_3", "role": "user", "content": [{"type": "text", "text": "[job notice | done | id j_1]\nno output"}],
             "meta": {"job": {"id": "j_1", "state": "done", "msg_id": "jn_1"}, "author": "job"}},
            {"id": "m_4", "role": "user", "content": [{"type": "text", "text": "what I typed"}], "meta": {"author": "principal"}},
            {"id": "m_5", "role": "assistant", "content": [{"type": "text", "text": "ok"}]}]
    _run(tmp_path, r'''
assert.equal(cards().length,3);
const heads=cards().map(c=>c.children[0].textContent);
assert.match(heads[0],/^Peer · other-session/);assert.match(heads[0],/same-principal/);
assert.match(heads[1],/^Bus · seat-b/);assert.match(heads[2],/^Job · j_1/);
assert.match(cards()[0].textContent,/Ship it\.from another session, not from you$/);
assert.match(cards()[1].textContent,/from another agent seat, not from you$/);
assert.match(cards()[2].textContent,/an automatic notice about a background job, not from you$/);
assert.equal(userRows().length,1);assert.match(userRows()[0].textContent,/what I typed/);
assert.ok(!log().textContent.includes("›[peer message"));
''', setup=f"globalThis.messages={json.dumps(msgs)};\n")


def test_a_live_peer_message_and_its_stored_message_are_one_card(tmp_path):
    _run(tmp_path, r'''
// peer.message first (a steer or a wake), then the stored message lands
bus.emit({seq:11,session:"s1",type:"peer.message",body:{msg_id:"pm_1",sender:"other-session",text:"Ship it.",trust:"same-principal",mode:"steered"}});
bus.emit({seq:12,session:"s1",type:"message.completed",body:{message:PEER("pm_1","m_9")}});
assert.equal(cards().length,1);assert.equal(userRows().length,0);
assert.match(cards()[0].children[0].textContent,/steered/);
// the stored message first (a queued message), then peer.message: one card, with the delivery mode
bus.emit({seq:13,session:"s1",type:"message.completed",body:{message:PEER("pm_2","m_10")}});
bus.emit({seq:14,session:"s1",type:"peer.message",body:{msg_id:"pm_2",sender:"other-session",text:"Ship it.",trust:"same-principal",mode:"queued"}});
assert.equal(cards().length,2);assert.equal(userRows().length,0);
assert.match(cards()[1].children[0].textContent,/queued/);
// a bus message: the live event says nothing of the bus, the stored message does
bus.emit({seq:15,session:"s1",type:"peer.message",body:{msg_id:"pm_3",sender:"seat-b",text:"hello",trust:"remote",mode:"started"}});
assert.match(cards()[2].children[0].textContent,/^Peer · seat-b/);
bus.emit({seq:16,session:"s1",type:"message.completed",body:{message:{id:"m_11",role:"user",content:[{type:"text",text:"[bus message from seat-b]\nhello"}],meta:{bus:{from:"seat-b",trust:"remote",msg_id:"pm_3"}}}}});
assert.equal(cards().length,3);assert.match(cards()[2].children[0].textContent,/^Bus · seat-b/);
// a job notice has no peer.message: it is still a card, not the user's line
bus.emit({seq:17,session:"s1",type:"message.completed",body:{message:{id:"m_12",role:"user",content:[{type:"text",text:"[job notice | failed, exit 1]"}],meta:{job:{id:"j_7",state:"failed",msg_id:"jn_7"},author:"job"}}}});
assert.equal(cards().length,4);assert.match(cards()[3].children[0].textContent,/^Job · j_7/);
assert.equal(userRows().length,0);
// the user's own message is still the user's line, and another thread's peer message draws nothing here
bus.emit({seq:18,session:"s1",type:"message.completed",body:{message:{id:"m_13",role:"user",content:[{type:"text",text:"thanks"}],meta:{author:"principal"}}}});
bus.emit({seq:19,session:"s2",type:"message.completed",body:{message:PEER("pm_4","m_14")}});
assert.equal(userRows().length,1);assert.equal(cards().length,4);
''')


def test_an_approval_with_no_session_shows_in_every_thread(tmp_path):
    _run(tmp_path, r'''
const appr=()=>byClass("xc-approvals").children.map(x=>x.attrs["data-approval"]).sort();
assert.deepEqual(appr(),["h1"]);   // the held peer message with no session, not the other thread's request
const h2={request_id:"h2",summary:"Peer message from bob is held",risk:"medium",options:["allow","deny"]}, o2={request_id:"o2",summary:"run ls?",risk:"low"};
globalThis.pending.push({...h2,session:null},{...o2,session:"s2"});   // the server's list has them too
bus.emit({seq:11,session:null,type:"approval.requested",body:h2});
bus.emit({seq:12,session:"s2",type:"approval.requested",body:o2});
assert.deepEqual(appr(),["h1","h2"]);
const thread2=nodes.findLast(x=>x.attrs.class==="xc-sess" && x.textContent.includes("s2"));   // untitled rows now read "Untitled thread · s2" (canonical port of exo-desktop #83), not just the id
await thread2.attrs.onclick();await settle();
assert.deepEqual(appr(),["h1","h2","o1","o2"]);   // in s2: its own requests and the ones with no session
globalThis.pending=globalThis.pending.filter(x=>x.request_id!=="h2");
bus.emit({seq:13,session:null,type:"approval.resolved",body:{request_id:"h2",decision:"deny",by:"tui"}});
assert.deepEqual(appr(),["h1","o1","o2"]);
// it is answered from here like any card
const h1=byClass("xc-approvals").children.find(x=>x.attrs["data-approval"]==="h1");
const allow=walk(h1).find(x=>x.tagName==="BUTTON" && x.textContent==="o once");
await allow.attrs.onclick();await settle();
assert.deepEqual(requests.findLast(x=>x.path.startsWith("/api/approvals/")),{method:"POST",path:"/api/approvals/h1",body:{decision:"allow",note:""}});
assert.deepEqual(appr(),["o1","o2"]);
''', setup='globalThis.pending=[{request_id:"h1",session:null,summary:"Peer message from bob (other) is held",risk:"medium",'
           'options:["allow","deny"]},{request_id:"o1",session:"s2",summary:"run ls?",risk:"low"}];\n')


def test_a_dismissed_panel_stays_dismissed(tmp_path):
    panels = '''import {h} from "./dom.mjs";
export const renderPanel=(p,on)=>h("section",{class:"panel","data-panel":p.panel_id,dismiss:()=>on.dismiss(p)},p.panel_id);'''
    _run(tmp_path, r'''
const side=()=>nodes.filter(x=>x.attrs.class==="xc-side").at(-1).children.map(x=>x.attrs["data-panel"]).filter(Boolean);
const opened=id=>requests.filter(x=>x.path==="/api/ui/respond" && x.body.panel_id===id && x.body.action==="opened").length;
assert.deepEqual(side(),["p_A"]);
await nodes.find(x=>x.attrs["data-panel"]==="p_A").attrs.dismiss();await settle();
assert.deepEqual(globalThis.forgot,["p_A"]);
assert.deepEqual(requests.filter(x=>x.path==="/api/ui/respond").map(x=>x.body.panel_id+" "+x.body.action),["p_A opened","p_A dismissed"]);
assert.deepEqual(side(),[]);
// the next panel event redraws from the page's list: the dismissed one stays gone
bus.emit({seq:11,session:null,type:"ui.panel",body:{panel_id:"p_B",action:"open",kind:"markdown",title:"second"}});
assert.deepEqual(side(),["p_B"]);
// a tab switch mounts the chat again: still gone, and no second "opened" for it
off();root=h("div");off=chat.mount(root);await settle();
assert.deepEqual(side(),["p_B"]);
assert.equal(opened("p_A"),1);
''', setup='globalThis.panels=[{panel_id:"p_A",kind:"markdown",title:"first",session:null}];\n', panels=panels)


def test_real_bus_forgets_a_dismissed_panel(tmp_path):
    for name in ("dom", "bus"):
        (tmp_path / f"{name}.mjs").write_text((STATIC / f"{name}.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "test.mjs").write_text('''
import assert from "node:assert/strict";
const made=[];
class ES { constructor(url){this.url=url;this.readyState=1;made.push(this);} close(){this.readyState=2;} addEventListener(t,f){this[t]=f;} }
globalThis.EventSource=ES;globalThis.sessionStorage={getItem:k=>k==="xc-token"?"tok":null,removeItem(){}};
globalThis.fetch=async path=>({ok:true,status:200,json:async()=>path.includes("panels")?{panels:[{panel_id:"p_A",seq:3}],seq:3}:{ok:true}});
const bus=await import("./bus.mjs");await bus.start();
assert.deepEqual(bus.openPanels().map(p=>p.panel_id),["p_A"]);
bus.forget("p_A");bus.forget("unknown");
assert.deepEqual(bus.openPanels(),[]);
made[0].onmessage({data:JSON.stringify({seq:4,type:"ui.panel",session:null,body:{panel_id:"p_B",action:"open"}})});
assert.deepEqual(bus.openPanels().map(p=>p.panel_id),["p_B"]);
console.log("ok");
''')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_group_threads_uses_parent_metadata_not_title_prefix(tmp_path):
    (tmp_path / "chat_state.mjs").write_text((STATIC / "chat_state.js").read_text())
    (tmp_path / "test.mjs").write_text(r'''
import assert from "node:assert/strict";
const {groupThreads, threadLabel, isScoutChild} = await import("./chat_state.mjs");
const s = (id, o) => ({id, title: "", kind: "", parent: null, status: "idle", updated: "", ...o});
const p1 = s("p1", {title: "Research"});
const p2 = s("p2", {title: "Research"});  // identical title, different parent
const a1 = s("a1", {title: "scout: database search", kind: "subagent", parent: "p1", status: "live"});
const a2 = s("a2", {kind: "subagent", parent: "p1"});  // untitled scout
const b1 = s("b1", {title: "scout: database search", kind: "subagent", parent: "p2"});  // identical title, other parent
const orphan = s("o1", {title: "scout: lost", kind: "subagent", parent: "missing"});
const fork = s("f1", {title: "copy", kind: "thread", parent: "p1"});  // a parent link, but not a scout: stays top-level
const old = s("old1", {title: "Old notes"});  // no metadata at all: stays top-level
const titled = s("t1", {title: "scout: not a child"});  // title prefix alone groups nothing
const groups = groupThreads([p1, a1, p2, a2, b1, orphan, fork, old, titled]);
assert.deepEqual(groups.map((g) => g.session.id), ["p1", "p2", "o1", "f1", "old1", "t1"]);
assert.deepEqual(groups[0].children.map((c) => c.session.id), ["a1", "a2"]);
assert.deepEqual(groups[1].children.map((c) => c.session.id), ["b1"]);
assert.equal(groups[2].orphan, true);
assert.equal(groups[0].orphan, false);
assert.equal(isScoutChild(a1), true);
assert.equal(isScoutChild(fork), false);
assert.equal(isScoutChild(titled), false);
assert.equal(threadLabel(a2), "Untitled scout · a2");
assert.equal(threadLabel(old), "Old notes");
assert.equal(threadLabel(s("u1", {})), "Untitled thread · u1");
// a parent cycle cannot drop a session or loop forever
const cyc = groupThreads([s("x1", {kind: "subagent", parent: "x2"}), s("x2", {kind: "subagent", parent: "x1"})]);
assert.deepEqual(cyc.map((g) => g.session.id).sort(), ["x1", "x2"]);
assert.deepEqual(groupThreads([]), []);
assert.deepEqual(groupThreads(null), []);
console.log("ok");
''')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_chat_sidebar_groups_scouts_under_parent(tmp_path):
    sessions = [
        {"id": "p1", "title": "Research", "status": "idle", "updated": ""},
        {"id": "a1", "title": "scout: a very long database search goal that keeps going past forty characters", "kind": "subagent", "parent": "p1", "status": "live", "updated": ""},
        {"id": "a2", "kind": "subagent", "parent": "p1", "status": "idle", "updated": ""},
        {"id": "p2", "title": "Research", "status": "idle", "updated": ""},
        {"id": "b1", "title": "scout: database search", "kind": "subagent", "parent": "p2", "status": "idle", "updated": ""},
        {"id": "o1", "title": "scout: lost", "kind": "subagent", "parent": "missing", "status": "idle", "updated": ""},
        {"id": "old1", "title": "Old notes", "status": "idle", "updated": ""},
    ]
    _run(tmp_path, r'''
// point the chat at the grouped session list, then redraw it via a session.created event
globalThis.mockApi = async (method, path, body) => {
  requests.push({method, path, body});
  if (path === "/api/chat/sessions" && method === "GET") return {sessions: globalThis.grouped};
  if (path.startsWith("/api/chat/sessions/")) return {session: {id: path.split("/").pop(), model: "fake:x"}, messages: [], seq: 10};
  if (path === "/api/section/approvals") return {pending: []};
  return {};
};
bus.emit({seq: 20, session: "p1", type: "session.created", body: {}});
await settle();
const list = byClass("xc-sessions");
// main wraps each conversation button in a div.xc-session-row beside its Rename button;
// the sidebar items are the conversation buttons plus the group toggles, in order.
const items = () => list.children.flatMap((x) => x.attrs.class === "xc-session-row" ? [x.children[0]] : [x]);
const rows = () => items().map((x) => x.textContent);
const classes = () => items().map((x) => x.attrs.class);
assert.deepEqual(classes(), ["xc-sess", "xc-sess-toggle", "xc-sess xc-child", "xc-sess xc-child", "xc-sess", "xc-sess-toggle", "xc-sess xc-child", "xc-sess", "xc-sess"]);
assert.match(rows()[0], /^Research/); assert.match(rows()[0], /2 scouts/);
assert.match(rows()[1], /2 scouts · 1 running/);
assert.match(rows()[2], /a very long database search goal that keeps going past forty characters/);  // child title is not truncated
assert.match(rows()[3], /Untitled scout · a2/);
assert.match(rows()[4], /^Research/);  // identical title, second parent, its own group
assert.match(rows()[6], /scout: database search/);
assert.match(rows()[7], /orphan scout/);
// collapsing the first group folds only its scouts, and expanding brings them back
await list.children[1].attrs.onclick(); await settle();
assert.deepEqual(classes(), ["xc-sess", "xc-sess-toggle", "xc-sess", "xc-sess-toggle", "xc-sess xc-child", "xc-sess", "xc-sess"]);
await list.children[1].attrs.onclick(); await settle();
assert.equal(list.children.length, 9);
// opening a scout still opens that session, not its parent
const scout = items().find((x) => x.attrs.class === "xc-sess xc-child" && x.textContent.includes("database search goal"));
await scout.attrs.onclick(); await settle();
assert.ok(requests.some((x) => x.path === "/api/chat/sessions/a1"));
''', setup=f"globalThis.grouped={json.dumps(sessions)};\n")


def test_scout_toggle_aria_expanded_is_string_in_both_states(tmp_path):
    # Orda review 5678722103 on exo #755: the scout disclosure passed a Boolean
    # aria-expanded to h(), and the real dom.js helper renders true as "" and
    # drops false, so assistive technology got neither valid state. The existing
    # fake-DOM sidebar test stores attrs verbatim and misses this; this mounted
    # test uses the real dom.js helper and asserts the rendered strings.
    sessions = [
        {"id": "p1", "title": "Research", "status": "idle", "updated": ""},
        {"id": "a1", "title": "scout: database search", "kind": "subagent", "parent": "p1", "status": "live", "updated": ""},
        {"id": "a2", "kind": "subagent", "parent": "p1", "status": "idle", "updated": ""},
    ]
    _run_real_dom(tmp_path, r"""
globalThis.mockApi = async (method, path, body) => {
  requests.push({method, path, body});
  if (path === "/api/chat/sessions" && method === "GET") return {sessions: globalThis.grouped};
  if (path.startsWith("/api/chat/sessions/")) return {session: {id: path.split("/").pop(), model: "fake:x"}, messages: [], seq: 10};
  if (path === "/api/section/approvals") return {pending: []};
  return {};
};
bus.emit({seq: 20, session: "p1", type: "session.created", body: {}});
await settle();
assert.equal(toggles().length, 1);
assert.equal(toggles()[0].getAttribute("aria-expanded"), "true");
assert.equal(childButtons().length, 2);
await toggles()[0].listeners["click"](); await settle();
assert.equal(toggles()[0].getAttribute("aria-expanded"), "false");
assert.equal(childButtons().length, 0);
await toggles()[0].listeners["click"](); await settle();
assert.equal(toggles()[0].getAttribute("aria-expanded"), "true");
assert.equal(childButtons().length, 2);
""", setup=f"globalThis.grouped={json.dumps(sessions)};\n")
