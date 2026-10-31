"""Exercise real web handlers in Node with an offline DOM/API stand-in, without a browser."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from exo_command.server import STATIC

NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_context_estimate_and_capacity_have_distinct_owned_states(tmp_path):
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
    # Pane persistence is covered separately; this fixture exercises context state only.
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
globalThis.nodes=[];globalThis.window={matchMedia:()=>({matches:true})};
globalThis.document={getElementById:()=>null,addEventListener(){},removeEventListener(){}};
globalThis.sessionStorage={getItem:k=>k==="xc-token"?"tok":null,removeItem(){}};globalThis.requestAnimationFrame=f=>f();
let seq=1, reportModel="fake:x", reportCapacity=8192, failure=false, held=null;
const report=()=>({schema_version:1,model:reportModel,capacity:reportCapacity,tokens:{system:10,tree:20,notes:30,tail:4140,total:4200},tree:{},nodes:{},log_entries:0,frontier:0});
globalThis.mockApi=async(method,path,body)=>{
 if(path==="/api/providers/models")return{models:[{route:"fake:x",context:8192},{route:"fake:y",context:16384},{route:"fake:z",context:null},{route:"fake:invalid",context:"8192"},{route:"fake:zero",context:0},{route:"fake:negative",context:-5},{route:"fake:fraction",context:8192.5}]};
 if(path==="/api/chat/sessions"&&method==="POST")return{session:{id:"s2"}};
 if(path==="/api/chat/sessions")return{sessions:[{id:"s1",model:"fake:x",status:"idle"},{id:"s2",model:"fake:y",status:"idle"}]};
 if(path==="/api/chat/sessions/s1"||path==="/api/chat/sessions/s2")return{session:{id:path.endsWith("s2")?"s2":"s1",model:path.endsWith("s2")?"fake:y":"fake:x",status:"idle"},messages:[],seq:1};
 if(path.endsWith("/command")){
  if(body.name!=="context")return{output:{open:0}};
  const value=structuredClone(report()), fail=failure, wait=held;
  if(wait)await wait;
  if(fail)throw Error("fake unavailable");
  return{output:value};
 }
 if(path==="/api/section/approvals")return{pending:[]};
 return{};
};
const {h}=await import("./dom.mjs"),chat=await import("./chat.mjs"),bus=await import("./bus.mjs");
const root=h("div"),off=chat.mount(root),settle=async()=>{for(let n=0;n<30;n++)await Promise.resolve();};await settle();
const strip=nodes.find(x=>x.attrs.class==="xc-strip"),side=nodes.find(x=>x.attrs.class==="xc-side");
const text=()=>strip.textContent;
const gauge=()=>{const q=[strip];while(q.length){const e=q.pop();if(e&&e.attrs&&e.attrs.class&&e.attrs.class.includes("xc-gauge"))return true;if(e&&e.children)q.push(...e.children);}return false;};
const emit=(type,body={},session="s1")=>bus.emit({seq:++seq,session,type,body});
const unknown=(state,capacity)=>{
 assert.match(text(),new RegExp("Next-call estimate "+state));assert.match(side.textContent,new RegExp("Next-call estimate "+state));
 assert.ok(!text().includes("Estimated next call: 4.2k"));assert.ok(!side.textContent.includes("4.2kestimated"));assert.equal(gauge(),false);
 assert.ok(text().includes(capacity));assert.ok(side.textContent.includes(capacity));
};
assert.match(text(),/Estimated next call: 4.2k of 8.2k/);assert.equal(gauge(),true);
// Explicit /model changes invalidate and repaint both displays immediately.
const input=nodes.find(x=>x.tagName==="TEXTAREA"),send=nodes.find(x=>x.attrs.class==="xc-btn xc-primary"&&x.textContent==="Send");
input.value="/model fake:y";await send.attrs.onclick();await settle();unknown("unknown","Capacity 16k");
input.value="/model fake:x";await send.attrs.onclick();await settle();reportModel="fake:x";emit("memory.summarized");await settle();assert.equal(gauge(),true);
// A held failing report preserves route capacity, never the old numerator or utilization gauge.
let release;held=new Promise(r=>release=r);failure=true;emit("turn.completed");await settle();unknown("pending","Capacity 8.2k");
emit("model.call",{route:"fake:x",usage:{input_tokens:5000,cache_read_tokens:2000}});assert.match(text(),/last call7\.0k/);unknown("pending","Capacity 8.2k");
release();held=null;await settle();unknown("unavailable","Capacity 8.2k");assert.match(text(),/last call7\.0k/);
// A new turn invalidates even a successful report from the preceding turn.
failure=false;emit("memory.summarized");await settle();assert.equal(gauge(),true);
emit("turn.started",{model:"fake:x"});unknown("unknown","Capacity 8.2k");
// Changing model invalidates the report; known capacity alone cannot create an estimate.
emit("model.call",{route:"fake:y",usage:{input_tokens:6000}});unknown("unknown","Capacity 16k");
emit("model.call",{route:"fake:z"});unknown("unknown","Limit unknown");
for(const route of ["fake:invalid","fake:zero","fake:negative","fake:fraction"]){emit("model.call",{route});unknown("unknown","Limit unknown");}
// The planned next-call model may differ from the last-served fallback route.
emit("model.call",{route:"fake:y",requested:"fake:x",usage:{input_tokens:6000}});
reportModel="fake:x";reportCapacity=null;emit("memory.summarized");await settle();
assert.match(text(),/Estimated next call: about 4.2k. Limit unknown. Next model: fake:x/);
assert.match(text(),/modelfake:y/);assert.equal(gauge(),false);
assert.match(side.textContent,/estimated tokens for next call · fake:x/);
emit("model.call",{route:"fake:y"});reportModel="fake:y";reportCapacity=32768;emit("context.compacted");await settle();
assert.match(text(),/Estimated next call: 4.2k of 33k/);assert.equal(gauge(),true);
// Explicit unknown report capacity wins over a previously known route limit.
reportCapacity=null;emit("memory.summarized");await settle();assert.match(text(),/Estimated next call: about 4.2k. Limit unknown/);assert.equal(gauge(),false);
failure=true;emit("turn.completed");await settle();unknown("unavailable","Limit unknown");
// A delayed old-route report cannot revive its estimate after a model transition.
failure=false;reportModel="fake:y";reportCapacity=32768;held=new Promise(r=>release=r);emit("memory.summarized");await settle();
emit("model.call",{route:"fake:x"});unknown("unknown","Limit unknown");release();held=null;await settle();unknown("unknown","Limit unknown");
// Recovery clears reported call usage before replay, then gets a new estimate for its snapshot route.
emit("model.call",{route:"fake:x",usage:{input_tokens:7000}});reportModel="fake:x";reportCapacity=8192;bus.recover();await settle();
assert.match(text(),/Estimated next call: 4.2k of 8.2k/);assert.ok(!text().includes("last call"));
// A newer same-route report owns the display even if an earlier request resolves afterward.
reportCapacity=8192;held=new Promise(r=>release=r);emit("memory.summarized");await settle();
held=null;reportCapacity=32768;emit("memory.summarized");await settle();assert.match(text(),/4.2k of 33k/);
release();await settle();assert.match(text(),/4.2k of 33k/);
// A context request from the old session cannot paint the newly opened conversation.
held=new Promise(r=>release=r);emit("memory.summarized");await settle();
input.value="/new";reportModel="fake:y";reportCapacity=16384;const opening=send.attrs.onclick();await settle();
release();held=null;await opening;await settle();assert.match(text(),/Estimated next call: 4.2k of 16k/);assert.ok(!text().includes("of 8.2k"));
off();console.log("ok");
''')
    result = subprocess.run([NODE, str(tmp_path / 'test.mjs')], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
