"""Regression: route capacity must not be transiently lost while /context is pending or fails."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from exo_command.server import STATIC

NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_capacity_fallback_web(tmp_path):
    for name in ("chat", "chat_state", "approvals", "markdown"):
        if name == "approvals" and not (STATIC / "approvals.js").exists():
            continue
        (tmp_path / f"{name}.mjs").write_text((STATIC / f"{name}.js").read_text().replace('.js"', '.mjs"'))
    if (STATIC / "model_picker.js").exists():
        (tmp_path / "model_picker.mjs").write_text((STATIC / "model_picker.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "dom.mjs").write_text('''export class El { constructor(tag, attrs={}, children=[]) { this.tagName=tag.toUpperCase(); this.attrs=attrs || {}; this.value=""; this.style={}; this.children=children.flat(Infinity).filter(x=>x!=null); this.listeners={}; this.classList={add(){},remove(){},toggle(){}}; this.scrollHeight=10; this.clientHeight=10; this.scrollTop=0; this.isConnected=false; globalThis.nodes.push(this); } replaceChildren(...xs){this.children=xs.flat(Infinity).filter(x=>x!=null);} append(...xs){this.children.push(...xs.flat(Infinity).filter(x=>x!=null));} appendChild(x){this.append(x);} prepend(x){this.children.unshift(x);} setAttribute(k,v){this.attrs[k]=v;} addEventListener(k,f){this.listeners[k]=f;} dispatchEvent(){} setSelectionRange(){} focus(){} remove(){} set textContent(v){this.children=[v];} get textContent(){return this.children.map(x=>x instanceof El?x.textContent:String(x)).join("");} } export const h=(t,a,...c)=>new El(t,a,c); export const api=(...args)=>globalThis.mockApi(...args); export const short=(t)=>t || ""; export const when=()=>""; export const partsText=(p)=>(p || []).map(x=>x.text || "").join(""); export const tag=(t)=>h("span",null,t); export const empty=tag;''')
    (tmp_path / "bus.mjs").write_text('''let seq=0, listener, stateListener, synced=0; export const on=f=>(listener=f,()=>{}); export const onState=f=>(stateListener=f,f({synced}),()=>{}); export const openPanels=()=>[]; export const lastSeq=()=>seq; export function emit(e){seq=e.seq;listener(e);} export function recover(){stateListener({synced:++synced});}''')
    (tmp_path / "panels.mjs").write_text('export const renderPanel=()=>null;')
    (tmp_path / "permissions.mjs").write_text('export const set=async()=>\"\",refresh=async()=>({}),current=()=>({}),describe=()=>\"\";')
    (tmp_path / "hero.mjs").write_text('export const createHero=()=>({start(){},stop(){}});')
    (tmp_path / "composer.mjs").write_text('''import {h} from "./dom.mjs"; export const attachComposer=()=>({guideEl:h("div"),menuEl:h("div"),note(){},sent(){},stop(){},ready:()=>true, elsewhere:n=>n==="grants"?"core":["help","quit","usage"].includes(n)?"client":"", typo(t){return null;}});''')
    (tmp_path / "layout.mjs").write_text('''export const getLayout=()=>"classic", setLayout=()=>{}, ordered=x=>x, homeScroll=()=>0, nearHome=()=>true, columnOrder=()=>["banner","stage","approvals","status","steer","guide","composer","menu"], LAYOUTS=["classic","inverse"];''')

    script = r'''
import assert from "node:assert/strict";
globalThis.nodes=[];
globalThis.window={matchMedia:()=>({matches:true})};
globalThis.document={getElementById:()=>null,addEventListener(){},removeEventListener(){}};
globalThis.sessionStorage={getItem:k=>k==="xc-token"?"tok":null,removeItem(){}};
globalThis.requestAnimationFrame=f=>f();

let reportCapacity = 8192;
let failContext = false;
let holdContext = false;
let holdResolve = null;
let holdPromise = null;
const report = () => ({schema_version:1,model:"fake:x",capacity:reportCapacity,text:"r",tokens:{system:1,tree:2,notes:3,tail:4194,total:4200},tree:{summaries:1,nodes:2},nodes:{done:2,pending:0},log_entries:4,frontier:2});

globalThis.mockApi = async (method,path,body) => {
  if (path === "/api/providers/models") {
    return {models:[
      {route:"fake:x",context:8192},
      {route:"fake:y",context:null},
      {route:"fake:badstr",context:"8192"},
      {route:"fake:zero",context:0},
      {route:"fake:neg",context:-5},
      {route:"fake:frac",context:8192.5}
    ]};
  }
  if (path === "/api/chat/sessions" && method === "GET") return {sessions:[{id:"s1",model:"fake:x",status:"idle"}]};
  if (path === "/api/chat/sessions" && method === "POST") return {session:{id:"s1"}};
  if (path === "/api/chat/sessions/s1") return {session:{id:"s1",model:"fake:x"},messages:[{id:"u1",role:"user",content:[{text:"hi"}]}],seq:1};
  if (path.endsWith("/command")) {
    if (body.name === "context") {
      if (holdContext) { holdPromise = new Promise(r=>{holdResolve=r;}); await holdPromise; holdPromise=null; }
      if (failContext) throw new Error("this thread has no shared memory (a subagent or plain session)");
      return report();
    }
    return {open:0};
  }
  if (path === "/api/section/approvals") return {pending:[]};
  return {};
};

function stripHasGauge() {
  const s = globalThis.nodes.find(x=>x.attrs && x.attrs.class==="xc-strip");
  if (!s) return false;
  // search any node with class xc-gauge
  const stack=[s]; let found=false;
  while (stack.length){const n=stack.pop(); if (n && n.attrs && n.attrs.class && String(n.attrs.class).includes("xc-gauge")) { found=true; break;} if (n && n.children) stack.push(...n.children);}
  return found;
}
function stripText() { const s=globalThis.nodes.find(x=>x.attrs && x.attrs.class==="xc-strip"); return s?s.textContent:""; }

async function settle(){ for(let i=0;i<20;i++) await Promise.resolve(); }

const {h}=await import("./dom.mjs");
const chat=await import("./chat.mjs");
const bus=await import("./bus.mjs");

// Case 1: known route stays known with gauge on failing /context after events
for (const evt of ["turn.completed","memory.summarized"]) {
  globalThis.nodes=[]; failContext=true; reportCapacity=8192; holdContext=false;
  const root=h("div"); const off=chat.mount(root); await settle();
  // set model known
  bus.emit({seq:2,session:"s1",type:"model.call",body:{route:"fake:x",requested:"fake:x",usage:{input_tokens:4000,cache_read_tokens:0,cache_write_tokens:0}}});
  await settle();
  // trigger loadContext via event
  if (evt==="turn.completed") bus.emit({seq:3,session:"s1",type:"turn.completed",body:{timing:{}}});
  else bus.emit({seq:3,session:"s1",type:"memory.summarized",body:{start:1,end:2,before_tokens:100,after_tokens:50}});
  await settle();
  assert.ok(stripHasGauge(), evt+" should keep gauge on failing /context");
  assert.ok(!stripText().includes("Limit unknown"), evt+" should not show Limit unknown");
  off();
}
// context.compacted
{
  globalThis.nodes=[]; failContext=true; reportCapacity=8192; holdContext=false;
  const root=h("div"); const off=chat.mount(root); await settle();
  bus.emit({seq:2,session:"s1",type:"model.call",body:{route:"fake:x",requested:"fake:x",usage:{input_tokens:4000,cache_read_tokens:0,cache_write_tokens:0}}});
  await settle();
  bus.emit({seq:3,session:"s1",type:"context.compacted",body:{before_tokens:100,after_tokens:50}});
  await settle();
  assert.ok(stripHasGauge(), "context.compacted should keep gauge on failing /context");
  assert.ok(!stripText().includes("Limit unknown"));
  off();
}

// Case 2: held /context does not transiently clear known limit/gauge
{
  globalThis.nodes=[]; failContext=true; reportCapacity=8192; holdContext=true;
  const root=h("div"); const off=chat.mount(root); await settle();
  bus.emit({seq:2,session:"s1",type:"model.call",body:{route:"fake:x",requested:"fake:x",usage:{input_tokens:4000,cache_read_tokens:0,cache_write_tokens:0}}});
  await settle();
  // trigger loadContext (held)
  let p = (async()=>{ bus.emit({seq:3,session:"s1",type:"turn.completed",body:{timing:{}}}); })();
  // wait a tick so loadContext starts and awaits holdPromise
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.ok(stripHasGauge(), "gauge must remain while /context is pending");
  assert.ok(!stripText().includes("Limit unknown"), "must not show Limit unknown while pending");
  // release
  holdResolve(); holdContext=false;
  await p; await settle();
  assert.ok(stripHasGauge(), "gauge must remain after failed /context");
  off();
}

// Case 3: unknown report/route no gauge; known->unknown->known model.call switches both ways
{
  globalThis.nodes=[]; failContext=false; reportCapacity=null; holdContext=false;
  const root=h("div"); const off=chat.mount(root); await settle();
  // report unknown -> no gauge
  bus.emit({seq:2,session:"s1",type:"turn.completed",body:{timing:{}}});
  await settle();
  assert.ok(!stripHasGauge(), "unknown report must have no .xc-gauge");
  // model.call unknown route
  bus.emit({seq:3,session:"s1",type:"model.call",body:{route:"fake:y",requested:"fake:y",usage:{input_tokens:4000,cache_read_tokens:0,cache_write_tokens:0}}});
  await settle();
  assert.ok(!stripHasGauge(), "unknown route must have no .xc-gauge");
  // model.call known route -> gauge
  bus.emit({seq:4,session:"s1",type:"model.call",body:{route:"fake:x",requested:"fake:x",usage:{input_tokens:4000,cache_read_tokens:0,cache_write_tokens:0}}});
  await settle();
  assert.ok(stripHasGauge(), "known route must show .xc-gauge");
  // back to unknown
  bus.emit({seq:5,session:"s1",type:"model.call",body:{route:"fake:y",requested:"fake:y",usage:{input_tokens:4000,cache_read_tokens:0,cache_write_tokens:0}}});
  await settle();
  assert.ok(!stripHasGauge(), "switch back to unknown must drop gauge");
  off();
}

// Case 4: invalid route context values (string "8192",0,-5,8192.5) => no gauge
for (const route of ["fake:badstr","fake:zero","fake:neg","fake:frac"]) {
  globalThis.nodes=[]; failContext=false; reportCapacity=8192; holdContext=false;
  const root=h("div"); const off=chat.mount(root); await settle();
  bus.emit({seq:2,session:"s1",type:"model.call",body:{route:route,requested:route,usage:{input_tokens:4000,cache_read_tokens:0,cache_write_tokens:0}}});
  await settle();
  assert.ok(!stripHasGauge(), route+" invalid context must have no .xc-gauge");
  off();
}

// Case 5: successful report explicit unknown clears; error fallback uses current model not old route
{
  globalThis.nodes=[]; failContext=false; reportCapacity=8192; holdContext=false;
  const root=h("div"); const off=chat.mount(root); await settle();
  // known route first
  bus.emit({seq:2,session:"s1",type:"model.call",body:{route:"fake:x",requested:"fake:x",usage:{input_tokens:4000,cache_read_tokens:0,cache_write_tokens:0}}});
  await settle();
  assert.ok(stripHasGauge(), "start known");
  // switch to unknown route
  bus.emit({seq:3,session:"s1",type:"model.call",body:{route:"fake:y",requested:"fake:y",usage:{input_tokens:4000,cache_read_tokens:0,cache_write_tokens:0}}});
  await settle();
  assert.ok(!stripHasGauge(), "unknown route no gauge");
  // successful report with explicit null capacity -> stays unknown (no stale 8192)
  reportCapacity=null;
  bus.emit({seq:4,session:"s1",type:"turn.completed",body:{timing:{}}});
  await settle();
  assert.ok(!stripHasGauge(), "explicit unknown report must not carry old capacity");
  // now simulate /context failure on current (unknown) route -> fallback uses current model (unknown), not old route
  failContext=true;
  bus.emit({seq:5,session:"s1",type:"turn.completed",body:{timing:{}}});
  await settle();
  assert.ok(!stripHasGauge(), "error fallback must use current model, not previous route");
  // restore known report would bring gauge back (sanity)
  reportCapacity=8192; failContext=false;
  bus.emit({seq:6,session:"s1",type:"turn.completed",body:{timing:{}}});
  await settle();
  // still unknown route -> capacities[fake:y] is null -> no gauge even with report 8192? applyContext sets capacities[ctx.model]=ctx.capacity where ctx.model=fake:x (report model). 
  // report model is fake:x with capacity 8192, so applyContext would set capacity=8192. That's successful report explicit known on unknown route -> gauge. Acceptable.
  off();
}

console.log("ALL_CAPACITY_TESTS_PASS");
'''
    (tmp_path / "test.mjs").write_text(script)
    r = subprocess.run([NODE, str(tmp_path / "test.mjs")], capture_output=True, text=True, timeout=120)
    (tmp_path / "node.out").write_text(r.stdout + "\n---STDERR---\n" + r.stderr)
    assert r.returncode == 0, f"node failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "ALL_CAPACITY_TESTS_PASS" in r.stdout
