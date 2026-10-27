"""Command view stream recovery, run offline in node (mirrors the bus view's D-045 behaviour)."""
import json
import shutil
import subprocess

import pytest

from exo_command.server import STATIC

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")

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
globalThis.sessionStorage={getItem:()=> "tok"};
'''

SNAP = '''{connected:true,seq:1,error:null,approvals:{ok:true,pending:[],counts:{pending:0},recent:[]},
  decisions:{ok:true,configured:false,waiting:[],counts:{}},
  sessions:{ok:true,active:[],past:[],counts:{},live_window_seconds:600},
  tasks:{ok:true,columns:[],open:0},projects:{ok:true,lanes:[],counts:{projects:0,sessions:0}},
  costs:{ok:true,top:[],by_seat:[],by_model:[],window_days:7}}'''


def _write(tmp_path):
    (tmp_path / "dom.mjs").write_text((STATIC / "dom.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "command.mjs").write_text((STATIC / "command.js").read_text().replace('"./dom.js"', '"./dom.mjs"'))


def _run(tmp_path, body):
    _write(tmp_path)
    (tmp_path / "test.mjs").write_text(HARNESS + body)
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout.strip()


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_command_stream_reopens_after_a_closed_connection(tmp_path):
    scenario = r'''
const streams=[];
globalThis.EventSource=class { constructor(url){ this.url=url; this.readyState=1; streams.push(this); } close(){ this.readyState=2; } addEventListener(){} };
let loads=0;
globalThis.fetch=async(path)=>{ if(path.startsWith("/api/snapshot")) loads++; return {ok:true,status:200,json:async()=>(''' + SNAP + r''')}; };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const root=new El("div");
const {mount}=await import("./command.mjs");
const unmount=mount(root);
await sleep(50);
assert.equal(streams.length,1);
const first=streams[0];
first.readyState=2; first.onerror();          // HTTP-style fatal close: the browser will not retry
await sleep(1300);
assert.equal(streams.length,2,"stream did not reopen");
const second=streams[1];
const beforeStale=loads;
first.onmessage({data:"{}"});                 // a stale handle must not refresh the view
await sleep(100);
assert.equal(loads,beforeStale,"stale handle triggered a load");
const beforeRecovery=loads;
second.onmessage({data:"{}"});                // recovery refreshes from the new stream
await sleep(250);
assert.ok(loads>beforeRecovery,"recovery did not refresh");
unmount();
const count=streams.length;
streams[streams.length-1].readyState=2;
try { streams[streams.length-1].onerror(); } catch (e) {}
await sleep(1300);
assert.equal(streams.length,count,"reconnected after unmount");
console.log(JSON.stringify({ok:true,streams:streams.length,loads}));
'''
    out = _run(tmp_path, scenario)
    assert '"ok":true' in out


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_command_stream_no_eventsource_fallback_does_not_throw(tmp_path):
    scenario = r'''
delete globalThis.EventSource;
let loads=0;
globalThis.fetch=async(path)=>{ if(path.startsWith("/api/snapshot")) loads++; return {ok:true,status:200,json:async()=>(''' + SNAP + r''')}; };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const root=new El("div");
const {mount}=await import("./command.mjs");
const unmount=mount(root);
await sleep(50);
assert.equal(streams.length,0);
assert.ok(loads>=1);
unmount();
console.log(JSON.stringify({ok:true,loads}));
'''
    # streams is not defined in this scenario; declare it first
    scenario = "const streams=[];\n" + scenario
    out = _run(tmp_path, scenario)
    assert '"ok":true' in out
