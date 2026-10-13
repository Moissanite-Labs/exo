"""Exercise real web handlers in Node with an offline DOM/API stand-in, without a browser."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from exo_command.server import STATIC

NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_chat_reconnect_refresh_commands_and_structured_memory(tmp_path):
    for name in ("chat", "chat_state", "approvals"):
        if name == "approvals" and not (STATIC / "approvals.js").exists():
            continue  # older chat embeds approval handling; newer chat imports the actual controller
        (tmp_path / f"{name}.mjs").write_text((STATIC / f"{name}.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "dom.mjs").write_text('''
export class El {
  constructor(tag, attrs={}, children=[]) { this.tagName=tag.toUpperCase(); this.attrs=attrs || {}; this.value=""; this.style={}; this.children=children.flat(Infinity).filter(x=>x!=null); this.listeners={}; this.classList={add(){},remove(){},toggle(){}}; this.scrollHeight=10; this.clientHeight=10; this.scrollTop=0; this.isConnected=false; globalThis.nodes.push(this); }
  replaceChildren(...xs){this.children=xs.flat(Infinity).filter(x=>x!=null);}
  append(...xs){this.children.push(...xs.flat(Infinity).filter(x=>x!=null));}
  appendChild(x){this.append(x);} prepend(x){this.children.unshift(x);}
  setAttribute(k,v){this.attrs[k]=v;} addEventListener(k,f){this.listeners[k]=f;}
  dispatchEvent(){} setSelectionRange(){} focus(){} remove(){}
  set textContent(v){this.children=[v];} get textContent(){return this.children.map(x=>x instanceof El?x.textContent:String(x)).join("");}
}
export const h=(t,a,...c)=>new El(t,a,c);
export const api=(...args)=>globalThis.mockApi(...args);
export const short=(t)=>t || ""; export const when=()=>"";
export const partsText=(p)=>(p || []).map(x=>x.text || "").join("");
export const tag=(t)=>h("span",null,t); export const empty=tag;
''')
    (tmp_path / "bus.mjs").write_text('''
let seq=0, listener, stateListener, synced=0;
export const on=f=>(listener=f,()=>{}); export const onState=f=>(stateListener=f,f({synced}),()=>{});
export const openPanels=()=>[]; export const lastSeq=()=>seq;
export function emit(e){seq=e.seq;listener(e);} export function recover(){stateListener({synced:++synced});}
''')
    (tmp_path / "panels.mjs").write_text('export const renderPanel=()=>null;')
    (tmp_path / "permissions.mjs").write_text('export const set=async()=>"",refresh=async()=>({}),current=()=>({}),describe=()=>"";')
    (tmp_path / "hero.mjs").write_text('export const createHero=()=>({start(){},stop(){}});')
    (tmp_path / "composer.mjs").write_text('''import {h} from "./dom.mjs";
export const attachComposer=()=>({guideEl:h("div"),menuEl:h("div"),note(){},sent(){},stop(){},typo(t){return t.startsWith("/contex ")?{guess:"context",line:"/context"}:null;}});
''')
    (tmp_path / "layout.mjs").write_text('''export const getLayout=()=>"classic", setLayout=()=>{}, ordered=x=>x, homeScroll=()=>0, nearHome=()=>true, columnOrder=()=>["banner","stage","approvals","status","steer","guide","composer","menu"], LAYOUTS=["classic","inverse"];''')
    (tmp_path / "test.mjs").write_text(r'''
import assert from "node:assert/strict";
globalThis.nodes=[]; globalThis.window={matchMedia:()=>({matches:true})};
globalThis.document={getElementById:()=>null,addEventListener(){},removeEventListener(){}};
globalThis.sessionStorage={getItem:()=>"tok"}; globalThis.requestAnimationFrame=f=>f();
let messages=[{id:"u1",role:"user",content:[{text:"hello"}]},{id:"a1",role:"assistant",content:[{text:"original answer"}]}], status="idle", hold=null, snapshotSeq=100;
const requests=[];
const report={schema_version:1,model:"fake:x",capacity:8192,text:"A report with no parseable token labels",tokens:{system:1,tree:2,notes:3,tail:4194,total:4200},tree:{summaries:1,nodes:2},nodes:{done:2,pending:0},log_entries:4,frontier:2};
globalThis.mockApi=async (method,path,body)=>{
  requests.push({method,path,body});
  if(path==="/api/chat/sessions" && method==="GET"){if(hold) await hold; return {sessions:[{id:"s1",model:"fake:x",status}]};}
  if(path==="/api/chat/sessions" && method==="POST")return {session:{id:"s1"}};
  if(path.endsWith("/command"))return {output:body.name==="context"?report:{open:0}};
  if(path==="/api/chat/sessions/s1")return {session:{id:"s1",model:"fake:x"},messages,seq:snapshotSeq};
  if(path==="/api/section/approvals")return {pending:[]};
  if(path==="/api/providers/models")return {models:[{route:"fake:x",context:8192}]};
  return {};
};
const {h}=await import("./dom.mjs"), chat=await import("./chat.mjs"), bus=await import("./bus.mjs");
const root=h("div"), off=chat.mount(root), settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
await settle();
const input=nodes.find(x=>x.tagName==="TEXTAREA"), send=nodes.find(x=>x.attrs.class==="xc-btn xc-primary" && x.textContent==="Send");
const log=nodes.find(x=>x.attrs.class==="xc-log"), strip=nodes.find(x=>x.attrs.class==="xc-strip");
assert.match(strip.textContent,/4.2k \/ 8.2k/); assert.ok(!strip.textContent.includes("200k"));
input.value="draft survives";
bus.emit({seq:101,session:"s1",type:"message.delta",body:{message_id:"live",text:"one"}});
status="live"; let release; hold=new Promise(r=>release=r); bus.recover();
bus.emit({seq:102,session:"s1",type:"message.delta",body:{message_id:"live",text:" two"}});
messages=messages.concat({id:"a2",role:"assistant",content:[{text:"missed durable answer"}]});
snapshotSeq=102;release();hold=null;await settle();
assert.match(log.textContent,/missed durable answer/); assert.match(log.textContent,/one two/);
assert.equal(input.value,"draft survives"); assert.match(strip.textContent,/working/);
// An already completed snapshot ignores an old delta arriving during a refresh.
bus.emit({seq:103,session:"s1",type:"message.delta",body:{message_id:"a1",text:" duplicated"}});
assert.ok(!log.textContent.includes("duplicated"));
status="idle"; report.capacity=null;bus.recover();await settle();
assert.match(strip.textContent,/idle/);assert.match(strip.textContent,/capacity unknown/);
for(const line of ["/etc/hosts is broken", "/context/file is broken", "/layout/file is broken", "/new/file is broken", "/frobnicate is literal"]){
  input.value=line;await send.attrs.onclick();await settle();
  assert.equal(requests.findLast(x=>x.path.endsWith("/send")).body.text,line);
}
let count=requests.filter(x=>x.path.endsWith("/send")).length;
input.value="/new private";await send.attrs.onclick();await settle();
assert.equal(requests.findLast(x=>x.path==="/api/chat/sessions" && x.method==="POST").body.kind,"private");
assert.equal(requests.filter(x=>x.path.endsWith("/send")).length,count);
input.value="/new private extra";await send.attrs.onclick();assert.match(strip.textContent,/usage: \/new/);
input.value="/context broken";await send.attrs.onclick();assert.match(strip.textContent,/usage: \/context/);
input.value="/contex help";await send.attrs.onclick();assert.equal(input.value,"/context");
assert.equal(requests.filter(x=>x.path.endsWith("/send")).length,count);
off();console.log("ok");
''')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_real_bus_gap_panel_race_and_expired_token(tmp_path):
    for name in ("dom", "bus"):
        (tmp_path / f"{name}.mjs").write_text((STATIC / f"{name}.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "test.mjs").write_text('''
import assert from "node:assert/strict";
const made=[];
class ES { constructor(url){this.url=url;this.readyState=1;made.push(this);} close(){this.readyState=2;} addEventListener(t,f){this[t]=f;} }
globalThis.EventSource=ES;globalThis.sessionStorage={getItem:()=>"tok"};
let resolvePanels, delayed=false, expired=false;
globalThis.fetch=async path=>{
  if(expired)return {ok:false,status:403,json:async()=>({error:"expired"})};
  const d=path.includes("panels")?{panels:[{panel_id:"p",session:"s1",seq:10}],seq:10}:{ok:true};
  if(delayed && path.includes("panels"))await new Promise(r=>resolvePanels=r);
  return {ok:true,status:200,json:async()=>d};
};
const bus=await import("./bus.mjs"), got=[], states=[];
bus.on(e=>got.push(e.seq));bus.onState(s=>states.push(s));await bus.start();
const first=made[0];first.onopen();first.gap({data:'{"reset":true}'});
first.onmessage({data:JSON.stringify({seq:1,type:"notice",body:{}})});
assert.deepEqual(got,[1]);assert.equal(bus.lastSeq(),1);
delayed=true;const sync=first.synced();await Promise.resolve();
first.onmessage({data:JSON.stringify({seq:2,type:"ui.panel",body:{panel_id:"p",action:"close"}})});
resolvePanels();await sync;
assert.deepEqual(bus.openPanels(),[]);assert.equal(states.at(-1).synced,1);assert.equal(states.at(-1).gaps,1);
delayed=false;await bus.restart();first.onmessage({data:JSON.stringify({seq:100,type:"notice",body:{}})});
assert.deepEqual(got,[1,2]);assert.equal(made.length,2);
expired=true;made[1].readyState=2;made[1].onerror();await new Promise(r=>setTimeout(r,1200));
assert.equal(states.at(-1).denied,true);assert.equal(made.length,2); // no endless requests with an expired token
console.log("ok");
''')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_bare_link_guidance_and_fresh_fragment_in_same_tab(tmp_path):
    (tmp_path / "app.mjs").write_text((STATIC / "app.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "dom.mjs").write_text('''
export const token=()=>sessionStorage.getItem("xc-token") || "";
export const api=async()=>{throw Error("bare URL must not make requests");};
export const h=(tag,attrs,...children)=>({tag,attrs,children,classList:{remove(){}},replaceChildren(...c){this.children=c;}});
''')
    (tmp_path / "bus.mjs").write_text('''export const onState=f=>{globalThis.state=f;f({connected:false});};export const start=async()=>{globalThis.started++;};export const restart=async()=>{globalThis.restarted++;};''')
    for name in ("chat", "tasks", "schedule", "command", "providers"):
        (tmp_path / f"{name}.mjs").write_text('export const mount=()=>{globalThis.mounted++;return ()=>{};};')
    (tmp_path / "permissions.mjs").write_text('export const banner=()=>({hidden:true});export const start=()=>{globalThis.permStarted++;};')
    (tmp_path / "test.mjs").write_text('''
import assert from "node:assert/strict";
let stored={};globalThis.sessionStorage={getItem:k=>stored[k],setItem:(k,v)=>stored[k]=v};
globalThis.location={hash:"",pathname:"/",search:""};globalThis.history={replaceState:(a,b,u)=>{location.hash=u.slice(u.indexOf("#"));}};
globalThis.started=0;globalThis.restarted=0;globalThis.mounted=0;globalThis.permStarted=0;
const app={children:[],replaceChildren(...c){this.children=c;}}, handlers={};
globalThis.document={getElementById:()=>app};globalThis.window={matchMedia:()=>({matches:true}),addEventListener:(t,f)=>handlers[t]=f};
await import("./app.mjs");
const main=app.children.at(-1);assert.match(main.children[0].children.join(""),/bare address has no page token/);
assert.equal(started,0);assert.equal(mounted,0);assert.equal(permStarted,0);
location.hash="#token=fresh%2Dtoken&chat";await handlers.hashchange();
assert.equal(stored["xc-token"],"fresh-token");assert.equal(location.hash,"#chat");assert.equal(restarted,1);assert.equal(mounted,1);assert.equal(permStarted,1);
assert.deepEqual(Object.keys(stored),["xc-token"]); // no permanent storage or extra credentials
state({denied:true,connected:false});
const nav=app.children[0], conn=nav.children.at(-1);assert.match(conn.children[1].textContent,/latest complete link/);
console.log("ok");
''')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
