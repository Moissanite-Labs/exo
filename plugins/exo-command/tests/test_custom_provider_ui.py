"""Exercise the real Providers view with an offline DOM and fake API."""
import shutil
import subprocess

import pytest

from exo_command.server import STATIC


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed")
def test_custom_form_save_edit_cancel_and_error(tmp_path):
    (tmp_path / "providers.mjs").write_text((STATIC / "providers.js").read_text().replace('.js"', '.mjs"'))
    # A separate worker-model editor may be composed into Providers; it is outside this form's DOM fixture.
    (tmp_path / "worker_models.mjs").write_text('export const workerModels=()=>()=>{};')
    (tmp_path / "dom.mjs").write_text('''
export class El {
  constructor(tag, attrs={}, children=[]) {
    this.tagName=tag.toUpperCase(); this.attrs=attrs||{}; this.children=children.flat(Infinity).filter(x=>x!=null);
    this.value=this.attrs.value||""; this.disabled=!!this.attrs.disabled;
    if(tag==="select") this.value=(this.children.find(x=>x.attrs?.selected)||this.children[0]).attrs.value;
  }
  replaceChildren(...xs){this.children=xs.flat(Infinity).filter(x=>x!=null);}
  appendChild(x){this.children.push(x);}
  get textContent(){return this.children.map(x=>x instanceof El?x.textContent:String(x)).join("");}
}
export const h=(t,a,...c)=>new El(t,a,c);
export const api=(...args)=>globalThis.mockApi(...args);
export const errorBox=x=>h("div",null,x), pageHead=()=>h("header"), readout=()=>h("span"),
 pill=(s)=>h("span",null,s), emptyState=o=>h("div",null,o.title);
''')
    (tmp_path / "test.mjs").write_text('''
import assert from "node:assert/strict";
globalThis.sessionStorage={getItem:()=>"token"}; globalThis.document={activeElement:null};
let records=[], attempts=[], fail=false;
globalThis.mockApi=async (method,path,body)=>{
  if(path==="/api/providers") return {providers:[{provider:"gemini",needs:"key",key:"missing",key_env:["GEMINI_API_KEY"]}],ready:false};
  if(path==="/api/providers/models") return {models:[],available:[]};
  if(path==="/api/providers/custom" && method==="GET") return {providers:records};
  if(path==="/api/providers/custom" && method==="POST") {
    attempts.push({...body}); if(fail) throw Error("Synthetic save failure");
    records=[{name:body.name,endpoint:body.endpoint,protocol:body.protocol,auth:body.auth,model:body.model}];
    return {provider:"custom-"+body.name,ok:true};
  }
  throw Error("unexpected request");
};
const {h,El}=await import("./dom.mjs"), view=await import("./providers.mjs");
const root=h("div"); view.mount(root);
const settle=async()=>{for(let i=0;i<20;i++)await Promise.resolve();}; await settle();
const all=()=>{const out=[]; function walk(x){if(x instanceof El){out.push(x);x.children.forEach(walk);}} walk(root); return out;};
const button=t=>all().find(x=>x.tagName==="BUTTON" && x.textContent===t);
const field=t=>all().find(x=>x.attrs["aria-label"]===t);
assert.match(root.textContent,/Google Gemini \\(AI Studio\\)/);
button("Add custom provider").attrs.onclick();
field("Provider name").value="demo"; field("Custom provider API key").value="synthetic-key-0123456789";
button("Cancel").attrs.onclick(); assert.equal(attempts.length,0); assert.ok(!root.textContent.includes("synthetic-key"));
button("Add custom provider").attrs.onclick();
field("Provider name").value="demo"; field("Base URL").value="https://example.test/v1"; field("Model ID").value="model-one";
field("Custom provider API key").value="synthetic-key-0123456789"; fail=true;
await all().find(x=>x.tagName==="FORM" && x.attrs.class==="xc-custom-form").attrs.onsubmit({preventDefault(){}});
assert.equal(field("Custom provider API key").value,""); assert.match(root.textContent,/Synthetic save failure/);
assert.equal(field("Base URL").value,"https://example.test/v1");
fail=false; field("Custom provider API key").value="synthetic-key-0123456789";
await all().find(x=>x.tagName==="FORM" && x.attrs.class==="xc-custom-form").attrs.onsubmit({preventDefault(){}}); await settle();
assert.match(root.textContent,/Restart the core/); assert.ok(button("Edit demo")); assert.ok(!root.textContent.includes("synthetic-key"));
assert.equal(attempts.at(-1).replace,false);
button("Edit demo").attrs.onclick(); assert.equal(field("Model ID").value,"model-one");
field("Base URL").value="http://127.0.0.1:8000/v1"; field("Authentication").value="none"; field("Model ID").value="model-two";
await all().find(x=>x.tagName==="FORM" && x.attrs.class==="xc-custom-form").attrs.onsubmit({preventDefault(){}}); await settle();
assert.equal(attempts.at(-1).endpoint,"http://127.0.0.1:8000/v1"); assert.equal(attempts.at(-1).key,"");
assert.equal(attempts.at(-1).replace,true);
assert.match(root.textContent,/model-two/); assert.equal(records[0].auth,"none");
''')
    result = subprocess.run([shutil.which("node"), str(tmp_path / "test.mjs")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
