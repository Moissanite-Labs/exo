"""The Schedule tab's remove, run offline in node (exo-desktop #55). Remove has no undo, so it takes a separate
Confirm remove click, as Allow always does on the Command card. The step is drawn in the page, not with
window.confirm, which a desktop web view without a confirm handler answers with Cancel.

Production dom.js and schedule.js build the page. The harness only stands in for the DOM, focus, fetch and
sessionStorage. As in a browser, focus falls to the body when the focused element leaves the page. The last test
drives the real page in headless Chrome and skips without it."""
import asyncio
import importlib.util
import json
import shutil
import subprocess
import time

import pytest

from browser import Browser, Served, find_chrome
from exo_command.server import STATIC
from exo_command.source import SourceError

NODE = shutil.which("node")
needs_chrome = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"),
                                  reason="Chrome or websockets missing")

HARNESS = r'''
import assert from "node:assert/strict";
class El {
  constructor(tag) { this.tagName=tag.toUpperCase(); this.children=[]; this.attrs={}; this.listeners={}; this.style={}; this.className=""; }
  setAttribute(k,v) { this.attrs[k]=String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(k,f) { (this.listeners[k] ||= []).push(f); }
  appendChild(c) { this.children.push(c); c.parent=this; return c; }
  replaceChildren(...xs) { this.children.forEach(x=>{ x.parent=null; }); this.children=[]; xs.forEach(x=>this.appendChild(x)); }
  get textContent() { return this.text ?? this.children.map(x=>x.textContent).join(""); }
  all() { return [this, ...this.children.flatMap(x=>x.all ? x.all() : [])]; }
  contains(c) { for(let x=c; x; x=x.parent) if(x===this) return true; return false; }
  focus() { focused=this; }
}
let focused=null;
globalThis.document=Object.assign(new El("document"),{hidden:false});
document.body=document.appendChild(new El("body"));
Object.defineProperty(document,"activeElement",{get() {   // the body once the focused element has left the page
  let x=focused; while(x && x!==document) x=x.parent; return x ? focused : document.body; }});
document.createElement=tag=>new El(tag);
document.createTextNode=text=>Object.assign(new El("#text"),{text});
globalThis.sessionStorage={getItem:()=>"tok"};
globalThis.confirm=()=>false;   // a web view with no confirm handler answers Cancel
let jobs=[{job_id:"j1",title:"Morning brief",when:"0 9 * * *",prompt:"Summarise the inbox"},
  {job_id:"j2",title:"Weekly digest",when:"0 8 * * 1",prompt:"Digest the week"}];
const posts=[];
let hold=null, refuse=false;   // a POST waits for hold while it is set, and fails while refuse is set
const holds={};                // or waits for the hold of its job, when that job has one
let failGet=false;             // a GET fails while it is set
globalThis.fetch=async(path,opt)=>{
  if(opt.method==="GET"&&failGet) return {ok:false,status:502,json:async()=>({error:"core down"})};
  if(opt.method==="POST") {
    const b=JSON.parse(opt.body); posts.push(b); await (holds[b.job_id] ?? hold);
    if(refuse) return {ok:false,status:404,json:async()=>({error:"no such job"})};
    if(b.action==="remove") jobs=jobs.filter(j=>j.job_id!==b.job_id);
  }
  return {ok:true,status:200,json:async()=>(opt.method==="GET"?{jobs}:{ok:true})};
};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const root=document.body.appendChild(new El("div"));
const {mount}=await import("./schedule.mjs");
const unmount=mount(root);
await sleep(20);
const buttons=label=>root.all().filter(x=>x.tagName==="BUTTON"&&x.textContent===label);
const button=label=>buttons(label)[0];   // the first card's, j1, while it shows that button
const card=title=>root.all().find(x=>x.tagName==="ARTICLE"&&x.textContent.includes(title));
const onCard=(title,label)=>card(title).all().find(x=>x.tagName==="BUTTON"&&x.textContent===label);
// A card's buttons as "label" or "label (disabled)".
const shown=title=>card(title).all().filter(x=>x.tagName==="BUTTON").map(x=>x.textContent+(x.getAttribute("disabled")!==null?" (disabled)":""));
// A click focuses its button first, as Chrome does.
const click=async(b,detail=1)=>{ assert.ok(b,"no such button"); b.focus(); (b.listeners.click||[]).forEach(f=>f({target:b,detail})); await sleep(20); };
const press=async(key)=>{   // a keydown on the focused element, bubbling up to the document
  const e={key,target:document.activeElement,preventDefault(){},stopPropagation(){}};
  for(let x=document.activeElement; x; x=x.parent) (x.listeners.keydown||[]).forEach(f=>f(e));
  await sleep(20);
};
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


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_step_takes_focus_and_escape_or_cancel_gives_it_back(tmp_path):
    """Remove redraws the cards, so the focused button leaves the page. Focus goes to Cancel, the safe default for a
    step that cannot be undone: a second Enter cancels instead of removing. Cancel and Escape put it back on the
    remove button of that card."""
    execute(tmp_path, r'''
await click(buttons("remove")[1]);                 // j2 asks
assert.equal(document.activeElement,button("Cancel"));
await press("a");                                   // only Escape cancels
assert.ok(root.textContent.includes("Remove Weekly digest? It cannot be undone."));
await press("Escape");
assert.ok(!root.textContent.includes("cannot be undone"));
assert.equal(document.activeElement,buttons("remove")[1]);
await click(button("remove"));                      // j1 asks
button("Confirm remove").focus();
await press("Escape");                              // Escape on Confirm remove cancels too
assert.ok(!root.textContent.includes("cannot be undone"));
assert.equal(document.activeElement,buttons("remove")[0]);
await click(button("remove"));
await click(button("Cancel"));
assert.equal(document.activeElement,buttons("remove")[0]);
assert.deepEqual(posts,[]);
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_remove_in_flight_keeps_its_step_with_both_buttons_disabled(tmp_path):
    """Cancel cannot stop a remove the page has sent, so it is disabled until the POST lands, and so is Confirm remove.
    The step stays, so the card's pause, run now and remove do not come back under the pointer for the second click
    of a double click. A failed remove gives the card its buttons back."""
    execute(tmp_path, r'''
const off=label=>button(label).getAttribute("disabled")!==null;
let land; hold=new Promise(r=>{ land=r; });
await click(button("remove"));
await click(button("Confirm remove"));
assert.deepEqual(posts,[{action:"remove",job_id:"j1"}]);
assert.ok(root.textContent.includes("Remove Morning brief? It cannot be undone."));
assert.ok(off("Confirm remove") && off("Cancel"));
assert.ok(buttons("pause").length===1 && buttons("remove").length===1);   // only j2's
land(); hold=null; await sleep(20);
assert.ok(root.textContent.includes("Removed.") && !root.textContent.includes("Morning brief"));
refuse=true;
await click(button("remove"));
await click(button("Confirm remove"));
assert.ok(root.textContent.includes("no such job") && !root.textContent.includes("cannot be undone"));
assert.ok(button("remove") && !off("remove"));
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_second_confirm_click_while_the_remove_is_in_flight_sends_nothing(tmp_path):
    """A second Confirm remove click while the POST is held sends no second remove. A browser sends no click to the
    disabled button. The harness does, so this checks the guard's `removing !== jid(j)` half, which stops it too."""
    execute(tmp_path, r'''
let land; hold=new Promise(r=>{ land=r; });
await click(button("remove"));
await click(button("Confirm remove"));
await click(button("Confirm remove"));              // a slow second click: detail is 1
assert.deepEqual(posts,[{action:"remove",job_id:"j1"}]);
land(); hold=null; await sleep(20);
assert.deepEqual(posts,[{action:"remove",job_id:"j1"}]);
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_two_removes_in_flight_each_keep_their_own_disabled_step(tmp_path):
    """Each job's remove in flight keeps its own step. The page held one job id, so a second job's remove gave the
    first card its pause, run now and remove back while the first remove was still out (review 5707362258)."""
    execute(tmp_path, r'''
let land1, land2; holds.j1=new Promise(r=>{ land1=r; }); holds.j2=new Promise(r=>{ land2=r; });
const held=["Confirm remove (disabled)","Cancel (disabled)"];
await click(onCard("Morning brief","remove")); await click(onCard("Morning brief","Confirm remove"));
await click(onCard("Weekly digest","remove")); await click(onCard("Weekly digest","Confirm remove"));
assert.deepEqual(posts,[{action:"remove",job_id:"j1"},{action:"remove",job_id:"j2"}]);
assert.deepEqual(shown("Morning brief"),held);
assert.deepEqual(shown("Weekly digest"),held);
land2(); await sleep(20);                            // j2 lands first: j1's step stays
assert.ok(root.textContent.includes("Removed.") && !card("Weekly digest"));
assert.deepEqual(shown("Morning brief"),held);
land1(); await sleep(20);
assert.equal(root.all().filter(x=>x.tagName==="ARTICLE").length,0);
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_failed_remove_puts_focus_on_its_remove_unless_focus_moved_on(tmp_path):
    """A failed remove puts focus on that card's remove, so a keyboard user can try again. Focus starts on the body,
    since Confirm remove left the page, or on a region around the list, where a click on the page puts it.

    Focus the person put on a control while the remove was out, such as the form's Title, stays there. A remove the
    core took leaves focus alone, even when the list could not be read again and still shows the card."""
    execute(tmp_path, r'''
refuse=true;
let land;
const title=root.all().find(x=>x.tagName==="INPUT"&&x.getAttribute("aria-label")==="Title");
for(const moved of [null,root,title]) {
  hold=new Promise(r=>{ land=r; });
  await click(onCard("Weekly digest","remove"));
  await click(onCard("Weekly digest","Confirm remove"));
  assert.equal(document.activeElement,document.body);
  if(moved) moved.focus();                          // while the remove is out
  land(); hold=null; await sleep(20);
  assert.ok(root.textContent.includes("no such job"));
  assert.deepEqual(shown("Weekly digest"),["pause","run now","remove"]);
  assert.equal(document.activeElement,moved===title ? title : onCard("Weekly digest","remove"));
}
refuse=false; failGet=true;
await click(onCard("Weekly digest","remove"));
await click(onCard("Weekly digest","Confirm remove"));
assert.deepEqual(posts.at(-1),{action:"remove",job_id:"j2"});
assert.ok(root.textContent.includes("core down"));
assert.deepEqual(shown("Weekly digest"),["pause","run now","remove"]);   // the list as last read
assert.equal(document.activeElement,document.body);
''')


class ScheduleSrc:
    """A core with two jobs. Each remove waits until the test lets it land, and fails if the test refused it."""
    centre = None

    def __init__(self):
        self.jobs = [{"job_id": "j1", "title": "Morning brief", "when": "0 9 * * *", "prompt": "Summarise the inbox"},
                     {"job_id": "j2", "title": "Weekly digest", "when": "0 8 * * 1", "prompt": "Digest the week"}]
        self.posts: list = []
        self.gates = {"j1": asyncio.Event(), "j2": asyncio.Event()}
        self.refused: set = set()

    async def call(self, body):
        kind = type(body).TYPE
        if kind == "hello":
            return {"seq": 0}
        if kind == "events.subscribe":
            return {"events": [], "seq": 0}
        if kind == "schedule.edit" and body.action == "list":
            return {"jobs": list(self.jobs)}
        if kind == "schedule.edit":
            self.posts.append([body.action, body.job_id])
            await self.gates[body.job_id].wait()
            if body.job_id in self.refused:
                raise SourceError("not_found", "no such job")
            self.jobs = [j for j in self.jobs if j["job_id"] != body.job_id]
            return {"ok": True}
        if kind == "config.get":
            return {"key": body.key, "value": None}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


def on_card(title):
    """A script expression for the job card with this title."""
    return "[...document.querySelectorAll('.xc-job')].find((c) => c.querySelector('h3').textContent === {})".format(
        json.dumps(title))


def shown(b, title):
    """The buttons on a job's card, as [label, disabled] pairs."""
    return b.eval("[...{}.querySelectorAll('button')].map((x) => [x.textContent, x.disabled])".format(on_card(title)))


def click(b, title, index):
    """A real mouse click on a job card's button, by its place on the card, as a person makes it."""
    x, y = b.eval("(() => {{ const e = {}.querySelectorAll('button')[{}]; e.scrollIntoView({{ block: 'center' }});"
                  " const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }})()".format(
                      on_card(title), index))
    for kind in ("mousePressed", "mouseReleased"):
        b.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)


def posts(srv, src, n):
    """The schedule edits the core has received, once there are n of them."""
    end = time.monotonic() + 10
    while time.monotonic() < end:
        got = srv.call(lambda: list(src.posts))
        if len(got) >= n:
            return got
        time.sleep(0.05)
    raise AssertionError("timed out waiting for {} posts (got {!r})".format(n, got))


@needs_chrome
def test_two_removes_in_flight_in_chrome_keep_their_steps_and_a_failed_one_gives_focus_to_its_remove():
    """The two-job case in the real page, with both remove POSTs held at the core. Before, a second job's remove
    gave the first card its pause, run now and remove back, and a click on that card's first button sent a pause
    for a job being removed (review 5707362258).

    Now each card keeps its disabled step until its own POST lands, and a click there sends nothing. A failed
    remove shows the error and puts focus on that card's remove."""
    src = ScheduleSrc()
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url("schedule"))
            b.wait("document.querySelectorAll('.xc-job').length === 2")
            b.settle()   # the start-up requests are answered, so the open requests below are the two removes
            held = [["Confirm remove", True], ["Cancel", True]]
            for n, title in enumerate(("Morning brief", "Weekly digest"), 1):
                click(b, title, 2)                   # remove
                b.wait("{}.textContent.includes('cannot be undone')".format(on_card(title)))
                click(b, title, 0)                   # Confirm remove
                assert posts(srv, src, n)[-1][0] == "remove"
            assert shown(b, "Morning brief") == held
            assert shown(b, "Weekly digest") == held
            # The disabled Confirm remove takes no click. Chrome puts focus on the page's main region instead.
            click(b, "Morning brief", 0)
            assert b.eval("window.__exoRequests") == 2 and srv.call(lambda: list(src.posts)) == [
                ["remove", "j1"], ["remove", "j2"]]
            srv.call(src.gates["j2"].set)            # j2 lands first: j1's step stays
            b.wait("document.querySelectorAll('.xc-job').length === 1")
            b.wait("document.querySelector('.xc-sched-note').textContent.includes('Removed.')")
            assert shown(b, "Morning brief") == held
            srv.call(src.refused.add, "j1")
            srv.call(src.gates["j1"].set)
            b.wait("!!document.querySelector('.xc-sched-note .xc-error')")
            assert "no such job" in b.eval("document.querySelector('.xc-sched-note').textContent")
            assert shown(b, "Morning brief") == [["pause", False], ["run now", False], ["remove", False]]
            assert b.eval("document.activeElement === {}.querySelectorAll('button')[2]".format(
                on_card("Morning brief")))
            assert srv.call(lambda: list(src.posts)) == [["remove", "j1"], ["remove", "j2"]]
    finally:
        srv.close()
