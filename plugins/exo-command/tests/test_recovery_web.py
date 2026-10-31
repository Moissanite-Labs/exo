"""Exercise real web handlers in Node with an offline DOM/API stand-in, without a browser."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from exo_command.server import STATIC

NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_chat_reconnect_refresh_commands_and_structured_memory(tmp_path):
    for name in ("chat", "chat_state", "approvals", "markdown", "model_picker"):
        if name == "approvals" and not (STATIC / "approvals.js").exists():
            continue  # older chat embeds approval handling; newer chat imports the actual controller
        (tmp_path / f"{name}.mjs").write_text((STATIC / f"{name}.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "dom.mjs").write_text('''
export class El {
  constructor(tag, attrs={}, children=[]) { this.tagName=tag.toUpperCase(); this.attrs=attrs || {}; this.value=""; this.style={}; this.children=children.flat(Infinity).filter(x=>x!=null); this.listeners={}; this.classList={add(){},remove(){},toggle(){}}; this.scrollHeight=10; this.clientHeight=10; this.scrollTop=0; this.isConnected=false; globalThis.nodes.push(this); }
  contains(node){return node===this || this.children.some(x=>x instanceof El && x.contains(node));}
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
    (tmp_path / "pane_layout.mjs").write_text('export const attachPaneLayout=()=>()=>{};')
    (tmp_path / "hero.mjs").write_text('export const createHero=()=>({start(){},stop(){}});')
    (tmp_path / "composer.mjs").write_text('''import {h} from "./dom.mjs";
export const attachComposer=()=>({guideEl:h("div"),menuEl:h("div"),note(){},sent(){},stop(){},ready:()=>globalThis.namesLoaded!==false,
  elsewhere:n=>n==="grants"?"core":["help","quit","usage"].includes(n)?"client":"",
  typo(t){return t.startsWith("/contex ")?{guess:"context",line:"/context",here:true}:t.startsWith("/grant ")?{guess:"grants",line:"/grants list",here:false}:null;}});
''')
    (tmp_path / "layout.mjs").write_text('''export const getLayout=()=>"classic", setLayout=()=>{}, ordered=x=>x, homeScroll=()=>0, nearHome=()=>true, columnOrder=()=>["banner","stage","approvals","status","steer","guide","composer","menu"], LAYOUTS=["classic","inverse"];''')
    (tmp_path / "test.mjs").write_text(r'''
import assert from "node:assert/strict";
globalThis.nodes=[]; globalThis.window={matchMedia:()=>({matches:true})};
globalThis.document={getElementById:()=>null,addEventListener(){},removeEventListener(){}};
globalThis.sessionStorage={getItem:k=>k==="xc-token"?"tok":null,removeItem(){}}; globalThis.requestAnimationFrame=f=>f();
let messages=[{id:"u1",role:"user",content:[{text:"hello"}]},{id:"a1",role:"assistant",content:[{text:"original answer"}]}], status="idle", hold=null, snapshotSeq=100;
const requests=[];
let refuseModel=false, holdModel=null, refuseDefault=null, refuseList=false, holdList=null, failHeldList=false;
const report={schema_version:1,model:"fake:x",capacity:8192,text:"A report with no parseable token labels",tokens:{system:1,tree:2,notes:3,tail:4194,total:4200},tree:{summaries:1,nodes:2},nodes:{done:2,pending:0},log_entries:4,frontier:2};
globalThis.mockApi=async (method,path,body)=>{
  requests.push({method,path,body});
  if(path.endsWith("/model") && refuseModel)throw Error("principal required");
  if(path.endsWith("/model") && holdModel)await holdModel;
  if(path==="/api/providers/default" && refuseDefault)throw Object.assign(Error(refuseDefault[1]),{status:refuseDefault[0]});
  if(path==="/api/chat/models" && refuseList)throw Error("models list failed");
  if(path==="/api/chat/models" && holdList){const wait=holdList;holdList=null;await wait;if(failHeldList)throw Error("late list failed");}
  if(path==="/api/chat/sessions" && method==="GET"){if(hold) await hold; return {sessions:[{id:"s1",model:"fake:x",status}]};}
  if(path==="/api/chat/sessions" && method==="POST")return {session:{id:"s1"}};
  if(path.endsWith("/command"))return {output:body.name==="context"?report:{open:0}};
  if(path==="/api/chat/sessions/s1")return {session:{id:"s1",model:"fake:x"},messages,seq:snapshotSeq};
  if(path==="/api/section/approvals")return {pending:[]};
  if(path==="/api/providers/models")return {models:[{route:"fake:x",context:8192}]};
  if(path==="/api/chat/models")return {models:[{route:"fake:x",role:"main"}],available:[{route:"fake:y"},{route:"fake:x"}]};
  return {};
};
const {h}=await import("./dom.mjs"), chat=await import("./chat.mjs"), bus=await import("./bus.mjs");
const root=h("div"), off=chat.mount(root), settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
await settle();
const input=nodes.find(x=>x.tagName==="TEXTAREA"), send=nodes.find(x=>x.attrs.class==="xc-btn xc-primary" && x.textContent==="Send");
const log=nodes.find(x=>x.attrs.class==="xc-log"), strip=nodes.find(x=>x.attrs.class==="xc-strip");
assert.match(strip.textContent,/4.2k of 8.2k/); assert.ok(!strip.textContent.includes("200k"));
// Usage describes the completed call, not the independent next-call estimate.
bus.emit({seq:101,session:"s1",type:"model.call",body:{route:"fake:x",usage:{input_tokens:5000,cache_read_tokens:2000}}});
assert.match(strip.textContent,/Estimated next call: 4.2k of 8.2k/);
assert.match(strip.textContent,/last call7\.0k input tokens/);
const side=nodes.find(x=>x.attrs.class==="xc-side");
assert.match(side.textContent,/4.2kestimated tokens for next call/);
// A parent usage update can be the sum of a delegated child's calls.
bus.emit({seq:102,session:"s1",type:"usage.updated",body:{usage:{input_tokens:3000,cache_read_tokens:500}}});
assert.match(strip.textContent,/Estimated next call: 4.2k of 8.2k/);
assert.match(strip.textContent,/last call7\.0k input tokens/);
// A newer call with absent, empty, or zero usage must not retain the prior call's counts.
for (const [seq, usage] of [[104,undefined],[106,{}],[108,{input_tokens:0,cache_read_tokens:0}]]) {
  bus.emit({seq:seq-1,session:"s1",type:"model.call",body:{route:"fake:x",usage:{input_tokens:5000,cache_read_tokens:2000}}});
  assert.match(strip.textContent,/last call7\.0k input tokens/);
  bus.emit({seq,session:"s1",type:"model.call",body:{route:"fake:x",usage}});
  assert.match(strip.textContent,/Estimated next call: 4.2k of 8.2k/);
  assert.ok(!strip.textContent.includes("last call"));
  assert.ok(!strip.textContent.includes("cache29%"));
}
// Healthy call instruments become unknown at an accepted recovery baseline.
bus.emit({seq:109,session:"s1",type:"model.call",body:{route:"fake:x",usage:{input_tokens:12000,cache_read_tokens:3000}}});
assert.match(strip.textContent,/last call15k input tokens/);
assert.match(strip.textContent,/cache20%/);
input.value="draft survives";
bus.emit({seq:111,session:"s1",type:"message.delta",body:{message_id:"live",text:"one"}});
status="live"; let release; hold=new Promise(r=>release=r); bus.recover();
bus.emit({seq:112,session:"s1",type:"message.delta",body:{message_id:"live",text:" two"}});
// A new call received during the held snapshot must replay after the baseline clears usage.
bus.emit({seq:113,session:"s1",type:"model.call",body:{route:"fake:x",usage:{input_tokens:9000,cache_read_tokens:1000}}});
messages=messages.concat({id:"a2",role:"assistant",content:[{text:"missed durable answer"}]});
snapshotSeq=112;release();hold=null;await settle();
assert.match(log.textContent,/missed durable answer/); assert.match(log.textContent,/one two/);
assert.equal(input.value,"draft survives"); assert.match(strip.textContent,/working/);
assert.match(strip.textContent,/last call10k input tokens/);
assert.match(strip.textContent,/cache10%/);
assert.ok(!strip.textContent.includes("15k input tokens"));
// An already completed snapshot ignores an old delta arriving during a refresh.
bus.emit({seq:114,session:"s1",type:"message.delta",body:{message_id:"a1",text:" duplicated"}});
assert.ok(!log.textContent.includes("duplicated"));
status="idle"; report.capacity=null;bus.recover();await settle();
assert.match(strip.textContent,/idle/);assert.match(strip.textContent,/Limit unknown/);
assert.match(strip.textContent,/Estimated next call: about 4.2k/);
assert.ok(!strip.textContent.includes("last call"));
assert.ok(!strip.textContent.includes("cache10%"));
// A new report replaces only the estimate; subsequent usage cannot relabel it.
report.tokens.total=5800;report.capacity=16384;bus.recover();await settle();
assert.match(strip.textContent,/Estimated next call: 5.8k of 16k/);
assert.match(side.textContent,/5.8kestimated tokens for next call/);
assert.ok(!strip.textContent.includes("last call"));

// D-037: a /word that names no command, and is near none, is message text (#150 review: paths and /me are not held).
for(const line of ["/etc/hosts is broken", "/context/file is broken", "/layout/file is broken", "/new/file is broken",
                   "/tmp is full, what can I delete?", "/home is on another disk", "/var keeps growing", "/me waves", "/v2 is out",
                   "/frobnicate keep draft", "/xyzzy plugh"]){
  input.value=line;await send.attrs.onclick();await settle();
  assert.equal(requests.findLast(x=>x.path.endsWith("/send")).body.text,line);
}
let count=requests.filter(x=>x.path.endsWith("/send")).length;
for(const line of ["/grants", "/grant list", "/MODEL fake:y"]){
  input.value=line;await send.attrs.onclick();await settle();
  assert.equal(requests.filter(x=>x.path.endsWith("/send")).length,count);
}
assert.deepEqual(requests.findLast(x=>x.path.endsWith("/model")).body,{model:"fake:y"});
input.value="/model";await send.attrs.onclick();await settle();
const picker=nodes.find(x=>x.attrs.class==="xc-model-picker"); assert.equal(picker.hidden,false);
const select=nodes.findLast(x=>x.attrs["aria-label"]==="Main model search");
select.value="fake:x";select.listeners.input();
await nodes.findLast(x=>x.textContent==="Use model").attrs.onclick();await settle();
assert.equal(picker.hidden,true);assert.deepEqual(requests.findLast(x=>x.path.endsWith("/model")).body,{model:"fake:x"});
input.value="/model default fake:y";await send.attrs.onclick();await settle();
assert.deepEqual(requests.findLast(x=>x.path==="/api/providers/default").body,{ref:"fake:y",role:"main"});
input.value="/default";await send.attrs.onclick();await settle();
assert.equal(nodes.findLast(x=>x.attrs["aria-label"]==="Default model for new threads search").tagName,"INPUT");
const before=requests.length;input.value="/model -bad";await send.attrs.onclick();assert.equal(requests.length,before);
input.value="/grants revoke g1";await send.attrs.onclick();assert.equal(input.value,"/grants revoke g1");
assert.match(strip.textContent,/\/grants runs in exo tui/);
input.value="/grant list";await send.attrs.onclick();assert.equal(input.value,"/grant list");assert.match(strip.textContent,/did you mean \/grants/);
// The terminal client's own commands are held too, with the draft, rather than starting a turn (#146).
for(const line of ["/help", "/quit", "/usage today"]){
  input.value=line;await send.attrs.onclick();await settle();
  assert.equal(input.value,line);assert.match(strip.textContent,/runs in the terminal client/);
}
// Until the command names load, any /name line waits in the box; plain text still goes.
globalThis.namesLoaded=false;
input.value="/tmp is full";await send.attrs.onclick();await settle();
assert.equal(input.value,"/tmp is full");assert.match(strip.textContent,/commands are still loading/);
globalThis.namesLoaded=true;
assert.equal(requests.filter(x=>x.path.endsWith("/send")).length,count);
// A failed /default, a failing models list and the loopback-only provider routes (D-034) all keep the draft.
refuseDefault=[400,"unknown model nosuch:m"];input.value="/default nosuch:m";await send.attrs.onclick();await settle();
assert.equal(input.value,"/default nosuch:m");assert.match(strip.textContent,/unknown model/);
globalThis.location={hostname:"192.168.1.20"};refuseDefault=[403,"provider routes answer loopback clients only"];
input.value="/model default fake:y";await send.attrs.onclick();await settle();
assert.equal(input.value,"/model default fake:y");assert.match(strip.textContent,/only a page opened at 127.0.0.1 or localhost/);
globalThis.location={hostname:"127.0.0.1"};
input.value="/default fake:y";await send.attrs.onclick();await settle();
assert.equal(input.value,"/default fake:y");assert.match(strip.textContent,/loopback clients only/);refuseDefault=null;
refuseList=true;input.value="/model";await send.attrs.onclick();await settle();
assert.equal(input.value,"/model");assert.match(strip.textContent,/models list failed/);refuseList=false;
// A late /model list never replaces the /default picker opened after it.
let releaseList;holdList=new Promise(r=>releaseList=r);
input.value="/model";const slowList=send.attrs.onclick();await settle();
input.value="/default";await send.attrs.onclick();await settle();
releaseList();await slowList;await settle();
assert.equal(picker.hidden,false);assert.match(picker.textContent,/Set default/);assert.doesNotMatch(picker.textContent,/Use model/);
// Nor does a late failing one flash its error over it.
holdList=new Promise(r=>releaseList=r);failHeldList=true;
input.value="/model";const failingList=send.attrs.onclick();await settle();
input.value="/default";await send.attrs.onclick();await settle();
releaseList();await failingList;await settle();failHeldList=false;
assert.equal(picker.hidden,false);assert.match(picker.textContent,/Set default/);assert.doesNotMatch(strip.textContent,/late list failed/);
// Reopening the thread drops a list still loading: the late answer opens no picker.
holdList=new Promise(r=>releaseList=r);
input.value="/model";const reopened=send.attrs.onclick();await settle();
await nodes.findLast(x=>x.attrs.class==="xc-sess on").attrs.onclick();await settle();
releaseList();await reopened;await settle();assert.equal(picker.hidden,true);
// Esc closes the picker and gives the box its focus back; so does choosing a model.
let focused=0;input.focus=()=>{focused++;};
picker.listeners.keydown({key:"Escape",preventDefault(){}});assert.equal(picker.hidden,true);assert.equal(focused,1);
input.value="/model";await send.attrs.onclick();await settle();assert.equal(picker.hidden,false);
await nodes.findLast(x=>x.textContent==="Use model").attrs.onclick();await settle();
assert.equal(picker.hidden,true);assert.equal(focused,2);
// While a model change is in flight, Enter sends nothing: no second models.set, no message.
let releaseBusy;holdModel=new Promise(r=>releaseBusy=r);
const models=requests.filter(x=>x.path.endsWith("/model")).length, sends=requests.filter(x=>x.path.endsWith("/send")).length;
input.value="/model fake:y";const busyModel=send.attrs.onclick();await settle();
await send.attrs.onclick();input.value="hello while switching";await send.attrs.onclick();await settle();
assert.equal(requests.filter(x=>x.path.endsWith("/model")).length,models+1);
assert.equal(requests.filter(x=>x.path.endsWith("/send")).length,sends);assert.match(strip.textContent,/changing the model/);
assert.equal(input.value,"hello while switching");releaseBusy();await busyModel;holdModel=null;
// The picker's button waits for a change already in flight too.
input.value="/model";await send.attrs.onclick();await settle();assert.equal(picker.hidden,false);
holdModel=new Promise(r=>releaseBusy=r);
input.value="/model fake:x";const typedModel=send.attrs.onclick();await settle();
await nodes.findLast(x=>x.textContent==="Use model").attrs.onclick();await settle();
assert.equal(requests.filter(x=>x.path.endsWith("/model")).length,models+2);
releaseBusy();await typedModel;holdModel=null;
refuseModel=true;input.value="/model fake:y";await send.attrs.onclick();
assert.equal(input.value,"/model fake:y");assert.match(strip.textContent,/principal required/);refuseModel=false;
let releaseModel;holdModel=new Promise(r=>releaseModel=r);
input.value="/model fake:y";const slowModel=send.attrs.onclick();await settle();
input.value="a newer draft";releaseModel();await slowModel;holdModel=null;
assert.equal(input.value,"a newer draft");
bus.emit({seq:115,session:"s1",type:"turn.started",body:{model:"fake:x"}});
const interrupts=requests.filter(x=>x.path.endsWith("/interrupt")).length;
input.value="/model fake:y";await send.attrs.onclick();await settle();
assert.equal(requests.filter(x=>x.path.endsWith("/send")).length,count);
assert.equal(requests.filter(x=>x.path.endsWith("/interrupt")).length,interrupts);
bus.emit({seq:116,session:"s1",type:"turn.completed",body:{}});
input.value="/new private";await send.attrs.onclick();await settle();
assert.equal(requests.findLast(x=>x.path==="/api/chat/sessions" && x.method==="POST").body.kind,"private");
assert.equal(requests.filter(x=>x.path.endsWith("/send")).length,count);
input.value="/new private extra";await send.attrs.onclick();assert.match(strip.textContent,/usage: \/new/);
input.value="/context broken";await send.attrs.onclick();assert.match(strip.textContent,/usage: \/context/);
input.value="/contex help";await send.attrs.onclick();assert.equal(input.value,"/context");
assert.equal(requests.filter(x=>x.path.endsWith("/send")).length,count);
// Notices belong to a thread. Only the exact completed call clears a route warning.
input.value="draft survives";
let eventSeq=1000;
const emit=(type,body,session="s1")=>bus.emit({seq:eventSeq++,session,type,body});
const has=t=>strip.textContent.includes(t);
emit("notice",{text:"selected general"});assert.ok(has("selected general"));
emit("notice",{text:"foreign warning",source:"model_route",call_id:"foreign"},"other");
emit("notice",{text:"unscoped warning"},null);assert.ok(has("selected general"));assert.ok(!has("foreign warning"));
emit("notice",{text:"route retry",source:"model_route",call_id:"call1"});
emit("model.call",{call_id:"other-call",route:"fake:x"});assert.ok(has("route retry"));
emit("model.call",{call_id:"call1",route:"fake:x"},"other");assert.ok(has("route retry"));
emit("model.call",{call_id:"call1",error:"failed"});assert.ok(has("route retry"));
emit("model.call",{call_id:"call1",stop_reason:"interrupted"});assert.ok(has("route retry"));
emit("model.call",{call_id:"call1",route:"fake:x",stop_reason:"end"});assert.ok(!has("route retry"));
emit("notice",{text:"route retry2",source:"model_route",call_id:"call2"});
emit("notice",{text:"new general notice"});emit("model.call",{call_id:"call2",route:"fake:x"});
assert.ok(has("new general notice"));emit("turn.completed",{});assert.ok(has("new general notice"));
emit("error",{message:"persistent command failure"});emit("model.call",{call_id:"call2",route:"fake:x"});
assert.ok(has("persistent command failure"));
emit("notice",{text:"unfinished route",source:"model_route",call_id:"call3"});
emit("turn.completed",{},"other");assert.ok(has("unfinished route"));
emit("turn.completed",{});assert.ok(!has("unfinished route"));
emit("notice",{text:"legacy general warning",level:"warn"});
emit("model.call",{route:"fake:x"});emit("turn.completed",{});assert.ok(has("legacy general warning"));
assert.equal(input.value,"draft survives");
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
globalThis.EventSource=ES;globalThis.sessionStorage={getItem:k=>k==="xc-token"?"tok":null,removeItem(){}};
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
export const redeem=async()=>{throw Error("bare URL must not make requests");};
export const h=(tag,attrs,...children)=>({tag,attrs,children,classList:{remove(){}},replaceChildren(...c){this.children=c;}});
''')
    (tmp_path / "bus.mjs").write_text('''export const onState=f=>{globalThis.state=f;f({connected:false});};export const start=async()=>{globalThis.started++;};export const restart=async()=>{globalThis.restarted++;};''')
    for name in ("chat", "tasks", "schedule", "command", "providers", "credentials"):
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
