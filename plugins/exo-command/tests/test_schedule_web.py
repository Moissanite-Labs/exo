"""The Schedule tab's remove, run offline in node (exo-desktop #55). Remove has no undo, so it takes a separate
Confirm remove click, as Allow always does on the Command card. The step is drawn in the page, not with
window.confirm, which a desktop web view without a confirm handler answers with Cancel. Production dom.js and
schedule.js build the page; the harness only stands in for the DOM, fetch and sessionStorage."""
import json
import shutil
import subprocess

import pytest

from exo_command.server import STATIC

NODE = shutil.which("node")

HARNESS = r'''
import assert from "node:assert/strict";
class El {
  constructor(tag) { this.tagName=tag.toUpperCase(); this.children=[]; this.attrs={}; this.listeners={}; this.style={}; this.className=""; }
  setAttribute(k,v) { this.attrs[k]=String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(k,f) { (this.listeners[k] ||= []).push(f); }
  appendChild(c) { this.children.push(c); c.parent=this; return c; }
  replaceChildren(...xs) { this.children=[]; xs.forEach(x=>this.appendChild(x)); }
  get textContent() { return this.text ?? this.children.map(x=>x.textContent).join(""); }
  all() { return [this, ...this.children.flatMap(x=>x.all ? x.all() : [])]; }
}
globalThis.document=Object.assign(new El("document"),{hidden:false});
document.createElement=tag=>new El(tag);
document.createTextNode=text=>Object.assign(new El("#text"),{text});
globalThis.sessionStorage={getItem:()=>"tok"};
globalThis.confirm=()=>false;   // a web view with no confirm handler answers Cancel
let jobs=[{job_id:"j1",title:"Morning brief",when:"0 9 * * *",prompt:"Summarise the inbox"},
  {job_id:"j2",title:"Weekly digest",when:"0 8 * * 1",prompt:"Digest the week"}];
const posts=[];
globalThis.fetch=async(path,opt)=>{
  if(opt.method==="POST") { const b=JSON.parse(opt.body); posts.push(b); if(b.action==="remove") jobs=jobs.filter(j=>j.job_id!==b.job_id); }
  return {ok:true,status:200,json:async()=>(opt.method==="GET"?{jobs}:{ok:true})};
};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const root=new El("div");
const {mount}=await import("./schedule.mjs");
const unmount=mount(root);
await sleep(20);
const buttons=label=>root.all().filter(x=>x.tagName==="BUTTON"&&x.textContent===label);
const button=label=>buttons(label)[0];   // the first card's, j1, while it shows that button
const click=async(b,detail=1)=>{ assert.ok(b,"no such button"); (b.listeners.click||[]).forEach(f=>f({target:b,detail})); await sleep(20); };
'''


def execute(tmp_path, scenario):
    (tmp_path / "dom.mjs").write_text((STATIC / "dom.js").read_text())
    (tmp_path / "schedule.mjs").write_text((STATIC / "schedule.js").read_text().replace('"./dom.js"', '"./dom.mjs"'))
    trailer = '\nunmount();\nconsole.log(JSON.stringify({ok:true}));\n'
    (tmp_path / "test.mjs").write_text(HARNESS + scenario + trailer)
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_remove_waits_for_a_separate_confirm(tmp_path):
    execute(tmp_path, r'''
assert.ok(root.textContent.includes("Morning brief"));
await click(button("remove"));
assert.deepEqual(posts,[]);                         // one click removes nothing
assert.ok(root.textContent.includes("Remove Morning brief? It cannot be undone."));
assert.equal(buttons("remove").length,1);          // only j2 still shows remove
await click(button("Cancel"));
assert.deepEqual(posts,[]);
assert.ok(buttons("remove").length===2 && !root.textContent.includes("cannot be undone"));
await click(button("remove"));
await click(button("Confirm remove"),2);           // the second click of a double click on "remove"
assert.deepEqual(posts,[]);
await click(button("Confirm remove"));
assert.deepEqual(posts,[{action:"remove",job_id:"j1"}]);
assert.ok(root.textContent.includes("Removed.") && !root.textContent.includes("Morning brief"));
assert.ok(root.textContent.includes("Weekly digest"));
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_other_job_actions_do_not_ask(tmp_path):
    execute(tmp_path, r'''
await click(button("remove"));                     // j1 asks
assert.equal(buttons("pause").length,1);
await click(button("pause"));                      // j2's pause runs at once and drops j1's pending remove
assert.deepEqual(posts,[{action:"pause",job_id:"j2"}]);
assert.ok(!root.textContent.includes("cannot be undone"));
await click(button("run now"));
assert.deepEqual(posts,[{action:"pause",job_id:"j2"},{action:"run_now",job_id:"j1"}]);
''')
