"""The Command tab's approval card, run offline in node: allow always takes a separate Confirm always click, as on the
Chat card, and the card shows the whole call.

Production dom.js and command.js build the page. The harness only stands in for the DOM, focus, fetch,
sessionStorage and EventSource. As in a browser, focus falls to the body when the focused element leaves the page."""
import json
import re
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
  replaceChildren(...xs) { this.children.forEach(x=>{ x.parent=null; }); this.children=[]; xs.forEach(x=>this.appendChild(x)); }
  get textContent() { return this.text ?? this.children.map(x=>x.textContent).join(""); }
  all() { return [this, ...this.children.flatMap(x=>x.all ? x.all() : [])]; }
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
const streams=[];
globalThis.EventSource=class { constructor(url){ this.url=url; streams.push(this); } close(){} addEventListener(){} };
const LONG="deploy notes: "+"x".repeat(300)+" END-OF-ARGUMENT";
const ask={request_id:"a_1",summary:"send the release notes",risk:"high",session:"s_1",requested_at:0,
  options:["allow","deny","allow_always"],call:{name:"send_message",arguments:{target:"channel:ops",message:LONG}}};
let pending=[ask];
const posts=[];
const snap=()=>({connected:true,seq:5,error:null,approvals:{ok:true,pending,counts:{pending:pending.length},recent:[]},
  decisions:{ok:true,configured:false,waiting:[],counts:{}},
  sessions:{ok:true,active:[],past:[],counts:{},live_window_seconds:600},
  tasks:{ok:true,columns:[],open:0},projects:{ok:true,lanes:[],counts:{projects:0,sessions:0}},
  costs:{ok:true,top:[],by_seat:[],by_model:[],window_days:7}});
globalThis.fetch=async(path,opt)=>{
  if(opt.method==="POST") posts.push([path,JSON.parse(opt.body)]);
  return {ok:true,status:200,json:async()=>(path.startsWith("/api/snapshot")?snap():{ok:true})};
};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const root=document.body.appendChild(new El("div"));
const {mount}=await import("./command.mjs");
const unmount=mount(root);
await sleep(20);
const buttons=()=>root.all().filter(x=>x.tagName==="BUTTON");
const button=label=>buttons().find(b=>b.textContent===label);
// A click focuses its button first, as Chrome does.
const click=(b,detail=1)=>{ assert.ok(b,"no such button"); b.focus(); (b.listeners.click||[]).forEach(f=>f({target:b,detail})); };
const press=key=>{   // a keydown on the focused element, bubbling up to the document
  const e={key,target:document.activeElement,preventDefault(){},stopPropagation(){}};
  for(let x=document.activeElement; x; x=x.parent) (x.listeners.keydown||[]).forEach(f=>f(e));
};
const changed=async()=>{ streams[0].onmessage({data:"{}"}); await sleep(200); };   // the core published something
'''


def execute(tmp_path, scenario):
    (tmp_path / "dom.mjs").write_text((STATIC / "dom.js").read_text())
    (tmp_path / "command.mjs").write_text((STATIC / "command.js").read_text().replace('"./dom.js"', '"./dom.mjs"'))
    trailer = '\nunmount();\nconsole.log(JSON.stringify({ok:true,out:globalThis.out??null}));\n'
    (tmp_path / "test.mjs").write_text(HARNESS + scenario + trailer)
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    done = json.loads(result.stdout)
    assert done["ok"] is True
    return done["out"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_allow_always_waits_for_a_separate_confirm(tmp_path):
    execute(tmp_path, r'''
assert.deepEqual(buttons().filter(b=>/allow|deny/.test(b.textContent)).map(b=>b.textContent),["allow","deny","allow always"]);
click(button("allow always"));
await sleep(20);
assert.equal(posts.length,0);                       // one click grants nothing
assert.ok(root.textContent.includes("Allow always? This saves a standing grant."));
assert.equal(button("allow always"),undefined);
click(button("Confirm always"),2);                 // the second click of a double click on "allow always"
await sleep(20);
assert.equal(posts.length,0);
await changed();                                    // a redraw while the confirm is open keeps it
assert.ok(button("Confirm always")); assert.equal(posts.length,0);
click(button("Confirm always"));
await sleep(20);
assert.deepEqual(posts,[["/api/approvals/a_1",{decision:"allow_always"}]]);
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_confirm_cancels_on_cancel_navigation_and_a_closed_request_and_once_still_answers(tmp_path):
    execute(tmp_path, r'''
click(button("allow always")); click(button("Cancel")); await sleep(20);
assert.ok(button("allow always")); assert.equal(button("Confirm always"),undefined);
click(button("allow always")); click(button("Sessions")); click(button("All")); await sleep(20);   // section tabs
assert.equal(button("Confirm always"),undefined);
click(button("allow always")); click(button("Approvals")); await sleep(20);   // even the tab that shows the card
assert.ok(button("allow always")); assert.equal(button("Confirm always"),undefined);
click(button("allow always")); click(buttons().find(b=>b.className.includes("xc-kpi")&&b.textContent.includes("Approvals")));
await sleep(20); assert.equal(button("Confirm always"),undefined);
click(button("allow always")); click(button("Refresh")); await sleep(20);
assert.equal(button("Confirm always"),undefined);
click(button("allow always")); pending=[]; await changed();   // answered in another client
assert.equal(button("Confirm always"),undefined);
pending=[ask]; await changed();                     // the same card again does not come back confirming
assert.ok(button("allow always")); assert.equal(button("Confirm always"),undefined);
assert.equal(posts.length,0);
click(button("allow")); await sleep(20);            // allow and deny still answer in one click
click(button("deny")); await sleep(20);
assert.deepEqual(posts,[["/api/approvals/a_1",{decision:"allow"}],["/api/approvals/a_1",{decision:"deny"}]]);
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_confirm_takes_focus_keeps_it_through_core_events_and_escape_or_cancel_gives_it_back(tmp_path):
    """Allow always redraws the page, so the focused button leaves it. Focus goes to Cancel, the safe default for a
    standing grant: a second Enter cancels instead of saving it. A core event redraws the page too, and focus stays on
    the same button. Cancel and Escape put it back on allow always."""
    execute(tmp_path, r'''
click(button("allow always")); await sleep(20);
assert.equal(document.activeElement,button("Cancel"));
await changed();
assert.equal(document.activeElement,button("Cancel"));
press("a"); await sleep(20);                        // only Escape cancels
assert.ok(button("Confirm always"));
press("Escape"); await sleep(20);
assert.equal(button("Confirm always"),undefined);
assert.equal(document.activeElement,button("allow always"));
await changed();
assert.equal(document.activeElement,button("allow always"));
click(button("allow always")); await sleep(20);
button("Confirm always").focus();
press("Escape"); await sleep(20);                   // Escape on Confirm always cancels too
assert.equal(button("Confirm always"),undefined);
assert.equal(document.activeElement,button("allow always"));
click(button("allow always")); click(button("Cancel")); await sleep(20);
assert.equal(document.activeElement,button("allow always"));
assert.equal(posts.length,0);
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_card_shows_the_whole_call(tmp_path):
    execute(tmp_path, r'''
const call=root.all().find(x=>x.tagName==="PRE"&&x.className.includes("xc-args"));
assert.ok(call,"no call block");
assert.equal(call.textContent,JSON.stringify(ask.call.arguments,null,2));
assert.ok(call.textContent.includes("END-OF-ARGUMENT") && call.textContent.includes("channel:ops"));
assert.ok(root.textContent.includes("send_message"));
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_card_with_no_asked_time_shows_no_asked_or_never_label(tmp_path):
    execute(tmp_path, r'''
pending=[{...ask,requested_at:null}];
await changed();
assert.ok(root.textContent.includes("send the release notes"));   // the card is there
assert.ok(!root.textContent.includes("asked"));
assert.ok(!root.textContent.includes("never"));
pending=[{...ask,requested_at:Date.now()/1000-120}];
await changed();
assert.ok(root.textContent.includes("asked 2m ago"));              // and a real time still shows
''')


def could_match(selector, chain):
    """True when a CSS selector could match chain[0], whose ancestors follow it up to <html>. Pseudo-classes and
    attribute tests are dropped and `>` reads as any ancestor, so the check errs toward matching."""
    parts = re.sub(r"::?[\w-]+(\([^)]*\))?|\[[^\]]*\]", "", selector).replace(">", " ").split()

    def fits(part, el):
        tag = re.match(r"[\w-]*", part).group(0)
        return (tag in ("", "*", el["tag"]) and set(re.findall(r"\.([\w-]+)", part)) <= set(el["cls"])
                and all(i == el.get("id") for i in re.findall(r"#([\w-]+)", part)))

    if not parts or not fits(parts[-1], chain[0]):
        return False
    at = 1
    for part in reversed(parts[:-1]):
        while at < len(chain) and not fits(part, chain[at]):
            at += 1
        if at == len(chain):
            return False
        at += 1
    return True


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_call_grows_with_the_card_and_never_scrolls_in_a_box_of_its_own(tmp_path):
    """render() replaces the whole page on every /api/stream message, and a new scroll box starts at its top: a capped
    call box threw the reader back to the start of a long call several times a second while anything ran. The call
    grows with the card, and the page scroll, which the redraw keeps, reaches its end."""
    chain = execute(tmp_path, r'''
const call=root.all().find(x=>x.tagName==="PRE"&&x.className.includes("xc-args"));
assert.ok(call,"no call block");
globalThis.out=[];
for(let el=call; el && el!==root; el=el.parent)
  globalThis.out.push({tag:el.tagName.toLowerCase(),cls:el.className.split(/\s+/).filter(Boolean),id:el.attrs.id??null});
''')
    chain += [{"tag": "div", "cls": ["xc-main"]}, {"tag": "div", "cls": ["xc-root"], "id": "app"},
              {"tag": "body", "cls": []}, {"tag": "html", "cls": []}]   # where app.js mounts the Command page
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "style.css").read_text(), flags=re.S)
    rules = [(sel, body) for sels, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
             for sel in sels.split(",") if could_match(sel.strip(), chain)]
    assert any(".xc-args" in sel for sel, _ in rules), rules
    boxed = re.compile(r"(^|[;\s])(max-height|overflow(-[xy])?)\s*:")
    capped = [(sel.strip(), body.strip()) for sel, body in rules if boxed.search(body)]
    assert capped == [], capped
    gridded = [sel.strip() for sel, body in rules if re.search(r"(^|[;\s])display\s*:", body)]
    assert gridded == [], gridded   # the chat's side-panel .xc-call rule once made it a two-column grid
