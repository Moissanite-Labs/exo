"""Execute the web approval DOM/event handlers offline, including focused-button and editable-input paths."""
import json
import shutil
import subprocess

import pytest

from exo_command.server import STATIC

NODE = shutil.which("node")

# A small DOM/event harness, not a replacement approval implementation. Production dom.js builds the elements,
# and production approvals.js installs the actual handlers. Button activation follows the browser default.
DOM = r'''
import assert from "node:assert/strict";
class El {
  constructor(tag) { this.tagName=tag.toUpperCase(); this.children=[]; this.attrs={}; this.listeners={}; this.style={}; this.value=""; }
  setAttribute(k,v) { this.attrs[k]=String(v); if(k==="contenteditable") this.isContentEditable=true; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(k,f) { (this.listeners[k] ||= []).push(f); }
  removeEventListener(k,f) { this.listeners[k]=(this.listeners[k]||[]).filter(x=>x!==f); }
  appendChild(c) { if(c.parent) c.remove(); this.children.push(c); c.parent=this; return c; }
  contains(c) { return c===this || this.children.some(x=>x.contains && x.contains(c)); }
  remove() {
    if(this.contains(document.activeElement)) { const old=document.activeElement; document.activeElement=document.body; fire(old,"focusout"); }
    if(this.parent) this.parent.children=this.parent.children.filter(x=>x!==this);
    this.parent=null;
  }
  replaceChildren(...xs) { for(const c of [...this.children]) c.remove(); xs.forEach(x=>this.appendChild(x)); }
  matches(s) { return s.startsWith("[") ? this.attrs[s.slice(1,-1)]!==undefined : this.tagName===s.toUpperCase(); }
  closest(s) { for(let x=this;x;x=x.parent) if(s.split(",").some(t=>x.matches(t))) return x; return null; }
  querySelector(s) { for(const c of this.children) { if(c.matches(s)) return c; const found=c.querySelector(s); if(found) return found; } return null; }
  focus() { const old=document.activeElement; document.activeElement=this; if(old!==this) fire(old,"focusout"); }
  get textContent() { return this.text || this.children.map(x=>x.textContent).join(""); }
}
globalThis.document=new El("document");
document.body=new El("body"); document.appendChild(document.body); document.activeElement=document.body;
document.createElement=tag=>new El(tag);
document.createTextNode=text=>Object.assign(new El("text"),{text});
function fire(target,type,extra={}) {
  const e={target,key:"",ctrlKey:false,metaKey:false,altKey:false,isComposing:false,defaultPrevented:false,
    preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...extra};
  for(let x=target;x;x=x.parent) { for(const f of x.listeners[type]||[]) f(e); if(e.stopped) break; }
  return e;
}
function key(target,k,extra={}) {
  const e=fire(target,"keydown",{key:k,...extra});
  if(!e.defaultPrevented && target.tagName==="BUTTON" && ["Enter"," "].includes(k)) fire(target,"click");
  return e;
}
const tick=()=>new Promise(resolve=>queueMicrotask(resolve));
const button=(card,label)=>{ const walk=x=>[x,...x.children.flatMap(walk)]; return walk(card).find(x=>x.tagName==="BUTTON"&&x.textContent===label); };
const {attachApprovals}=await import("./approvals.mjs");
function setup() {
  const root=document.createElement("div"), composer=document.createElement("textarea");
  document.body.replaceChildren(root,composer); composer.focus();
  const answers=[];
  const view=attachApprovals({root,composer,answer:async(a,decision,note)=>{answers.push({decision,note});view.draw([]);}});
  view.draw([{request_id:"r1",summary:"run echo?",risk:"medium",options:["allow","allow_always","deny"]}]);
  return {root,composer,answers,view,card:root.children[0]};
}
'''


def execute(tmp_path, scenario):
    (tmp_path / "dom.mjs").write_text((STATIC / "dom.js").read_text())
    (tmp_path / "approvals.mjs").write_text((STATIC / "approvals.js").read_text().replace('"./dom.js"', '"./dom.mjs"'))
    (tmp_path / "test.mjs").write_text(DOM + scenario + '\nconsole.log(JSON.stringify({ok:true}));\n')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True}


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_web_typing_focus_navigation_and_editable_guards(tmp_path):
    execute(tmp_path, r'''
let s=setup();
assert.equal(document.activeElement,s.composer); // a just-sent empty composer keeps focus
for(const ch of "Act now ok do it") key(s.composer,ch);
key(s.composer,"Enter"); assert.equal(s.answers.length,0);
key(document.body,"o"); key(document.body,"A"); assert.equal(s.answers.length,0); // arbitrary page focus cannot authorize
key(s.composer,"f",{ctrlKey:true}); assert.equal(document.activeElement,s.card);
key(s.card,"O"); key(s.card,"D"); assert.equal(s.answers.length,0); assert.equal(s.card.querySelector("input"),null);
assert.equal(s.card.getAttribute("tabindex"),"0");
assert.equal(button(s.card,"o once").getAttribute("aria-keyshortcuts"),"o");
key(s.card,"o"); await tick(); assert.equal(s.answers[0].decision,"allow");
assert.equal(document.activeElement,s.composer); s.view.stop();
s=setup(); fire(s.card,"click"); assert.equal(document.activeElement,s.card);
for(const tag of ["input","textarea","select","div"]) {
  const edit=document.createElement(tag); if(tag==="div") edit.setAttribute("contenteditable","true");
  s.card.appendChild(edit); edit.focus();
  for(const ch of ["o","a","A","d","Enter"]) key(edit,ch);
  assert.equal(s.answers.length,0);
}
s.view.stop();
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_web_every_always_path_confirms_and_cancels_safely(tmp_path):
    execute(tmp_path, r'''
for(const begin of ["a","A","click","button-shortcut","button-uppercase","button-enter"]) {
  const s=setup(); s.card.focus();
  if(begin==="click") fire(button(s.card,"a always"),"click");
  else if(begin==="button-shortcut"||begin==="button-uppercase") { const b=button(s.card,"o once"); b.focus(); key(b,begin==="button-uppercase"?"A":"a"); }
  else if(begin==="button-enter") { const b=button(s.card,"a always"); b.focus(); key(b,"Enter"); }
  else key(s.card,begin);
  assert.equal(s.answers.length,0,begin); assert.ok(button(s.card,"Confirm always"));
  key(document.activeElement,"Enter",{repeat:true}); assert.equal(s.answers.length,0);
  key(document.activeElement,"Enter"); await tick();
  assert.equal(s.answers[0].decision,"allow_always",begin); assert.equal(document.activeElement,s.composer); s.view.stop();
}
let s=setup(); s.card.focus(); key(s.card,"A"); key(document.activeElement,"Escape");
key(s.card,"Enter"); assert.equal(s.answers.length,0); s.view.stop();
s=setup(); s.card.focus(); key(s.card,"a"); const cancel=button(s.card,"Cancel"); cancel.focus(); key(cancel,"Enter");
assert.equal(s.answers.length,0); assert.ok(button(s.card,"a always")); s.view.stop();
s=setup(); s.card.focus(); key(s.card,"a"); s.composer.focus(); await tick();
assert.ok(button(s.card,"a always")); key(s.composer,"A"); key(s.composer,"Enter"); assert.equal(s.answers.length,0); s.view.stop();
s=setup(); s.card.focus(); key(s.card,"d"); const note=s.card.querySelector("input"); note.value="not now";
key(note,"A"); assert.equal(s.answers.length,0); key(note,"Enter"); await tick();
assert.deepEqual(s.answers,[{decision:"deny",note:"not now"}]); assert.equal(document.activeElement,s.composer); s.view.stop();
s=setup(); s.card.focus(); key(s.card,"d"); key(s.card.querySelector("input"),"Escape");
assert.equal(s.answers.length,0); assert.equal(document.activeElement,s.card); s.view.stop();
''')


def test_chat_wires_the_approval_controller_and_sends_deny_notes():
    chat = (STATIC / "chat.js").read_text()
    assert 'from "./approvals.js"' in chat and "approvalView.draw(mine)" in chat and "approvalView.stop()" in chat
    assert "{ decision, note }" in chat


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_web_approval_keyboard_hint_multiple_cards_draft_and_cleanup(tmp_path):
    execute(tmp_path, r'''
const s=setup(); s.composer.value="draft o a d";
const req=id=>({request_id:id,summary:id,risk:"medium",options:["allow","deny"]});
s.view.draw([req("r1"),req("r2")]);
assert.ok(s.card.textContent.includes("Ctrl+F to focus the first approval."));
assert.ok(s.card.textContent.includes("Then o once; d, Enter deny. Esc returns to your message."));
assert.ok(!s.card.textContent.includes("always"));
assert.equal(document.activeElement,s.composer);
key(s.composer,"f",{ctrlKey:true}); assert.equal(document.activeElement,s.card);
key(s.card,"Escape"); assert.equal(document.activeElement,s.composer);
key(s.composer,"f",{ctrlKey:true}); key(s.card,"d");
assert.ok(s.card.textContent.includes("Enter sends denial; Esc cancels."));
assert.ok(!s.card.textContent.includes("Esc returns to your message."));
key(document.activeElement,"Escape"); assert.equal(document.activeElement,s.card);
s.view.draw([{...req("r1"),options:["allow","allow_always","deny"]},req("r2")]);
key(s.card,"a");
assert.ok(s.card.textContent.includes("Enter confirms always; Esc cancels."));
assert.ok(!s.card.textContent.includes("Esc returns to your message."));
key(document.activeElement,"Escape"); assert.equal(document.activeElement,s.card);
assert.ok(s.card.textContent.includes("Then o once; a, Enter always; d, Enter deny."));
key(s.card,"Escape"); assert.equal(document.activeElement,s.composer);
s.view.draw([req("r2")]);
key(s.composer,"f",{ctrlKey:true}); assert.equal(document.activeElement,s.root.children[0]);
key(document.activeElement,"o"); await tick(); assert.equal(s.answers[0].decision,"allow");
assert.equal(document.activeElement,s.composer);
assert.equal(s.composer.value,"draft o a d");
s.view.draw([req("r3")]); s.view.stop();
const e=key(s.composer,"f",{ctrlKey:true});
assert.equal(e.defaultPrevented,false); assert.equal(document.activeElement,s.composer);
''')
