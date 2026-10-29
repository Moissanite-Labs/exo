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
    for name in ("chat", "chat_state", "approvals", "desktop_prefs"):
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
const thread2=nodes.findLast(x=>x.attrs.class==="xc-sess" && x.textContent.startsWith("s2"));
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
const side=()=>nodes.filter(x=>x.attrs.class==="xc-side-contents").at(-1).children.map(x=>x.attrs["data-panel"]).filter(Boolean);
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
