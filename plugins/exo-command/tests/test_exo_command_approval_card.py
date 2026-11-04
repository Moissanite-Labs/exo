"""The Command tab's approval card, run offline in node: allow always takes a separate Confirm always click, as on the
Chat card, the card shows the whole call, and a card whose answer is out takes no second answer (#1117).

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
const streams=[];
globalThis.EventSource=class { constructor(url){ this.url=url; streams.push(this); } close(){} addEventListener(){} };
const LONG="deploy notes: "+"x".repeat(300)+" END-OF-ARGUMENT";
const ask={request_id:"a_1",summary:"send the release notes",risk:"high",session:"s_1",requested_at:0,
  options:["allow","deny","allow_always"],call:{name:"send_message",arguments:{target:"channel:ops",message:LONG}}};
let pending=[ask];
const posts=[];
let hold=null, refuse=false;   // a POST waits for hold while it is set, and is refused while refuse is set
const holds={};                // or waits for the hold of its request, when that request has one
const snap=()=>({connected:true,seq:5,error:null,approvals:{ok:true,pending,counts:{pending:pending.length},recent:[]},
  decisions:{ok:true,configured:false,waiting:[],counts:{}},
  sessions:{ok:true,active:[],past:[],counts:{},live_window_seconds:600},
  tasks:{ok:true,columns:[],open:0},projects:{ok:true,lanes:[],counts:{projects:0,sessions:0}},
  costs:{ok:true,top:[],by_seat:[],by_model:[],window_days:7}});
globalThis.fetch=async(path,opt)=>{
  if(opt.method==="POST") {
    posts.push([path,JSON.parse(opt.body)]); await (holds[path.split("/").pop()] ?? hold);
    if(refuse) return {ok:false,status:502,json:async()=>({error:"the core refused the answer"})};
  }
  return {ok:true,status:200,json:async()=>(path.startsWith("/api/snapshot")?snap():{ok:true})};
};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const root=document.body.appendChild(new El("div"));
const {mount}=await import("./command.mjs");
const unmount=mount(root);
await sleep(20);
const buttons=()=>root.all().filter(x=>x.tagName==="BUTTON");
const button=label=>buttons().find(b=>b.textContent===label);
const card=summary=>root.all().find(x=>x.tagName==="ARTICLE"&&x.textContent.includes(summary));
const onCard=(summary,label)=>card(summary).all().find(x=>x.tagName==="BUTTON"&&x.textContent===label);
// A card's buttons as "label" or "label (disabled)".
const shown=summary=>card(summary).all().filter(x=>x.tagName==="BUTTON")
  .map(x=>x.textContent+(x.getAttribute("disabled")!==null?" (disabled)":""));
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
def test_cancel_or_escape_after_confirm_always_does_nothing_while_the_grant_is_out(tmp_path):
    """Cancel cannot stop a grant the page has sent. Before #1117 the step stayed with both buttons live, and Cancel
    drew allow, deny and allow always again, as if nothing was sent, while the grant still landed. Now the step stays
    with both buttons disabled until the POST settles. A browser sends no click to a disabled button. The harness
    does, so this also checks that Cancel, Escape and a slow second Confirm always do nothing then."""
    execute(tmp_path, r'''
const S="send the release notes", held=["Confirm always (disabled)","Cancel (disabled)"];
let land; hold=new Promise(r=>{ land=r; });
click(button("allow always")); click(button("Confirm always")); await sleep(20);
assert.deepEqual(posts,[["/api/approvals/a_1",{decision:"allow_always"}]]);
assert.ok(root.textContent.includes("Allow always? This saves a standing grant."));
assert.deepEqual(shown(S),held);
assert.equal(document.activeElement,document.body);   // Confirm always went disabled, so focus fell to the body
click(button("Cancel")); await sleep(20);
assert.deepEqual(shown(S),held);
button("Cancel").focus(); press("Escape"); await sleep(20);
assert.deepEqual(shown(S),held);
click(button("Confirm always")); await sleep(20);    // a slow second click: detail is 1
assert.equal(posts.length,1);
pending=[]; land(); hold=null; await sleep(20);     // the grant lands, and the core closes the request
assert.equal(card(S),undefined);
assert.equal(posts.length,1);
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_second_choice_while_an_answer_is_out_sends_nothing(tmp_path):
    """Allow and deny answer in one click, and the card goes disabled until the POST settles. Before #1117 the card
    stayed live, and a second click sent a second answer for the same request after the first."""
    execute(tmp_path, r'''
const S="send the release notes", held=["allow (disabled)","deny (disabled)","allow always (disabled)"];
for(const first of ["allow","deny"]) {
  posts.length=0; pending=[ask]; await changed();
  let land; hold=new Promise(r=>{ land=r; });
  click(button(first)); await sleep(20);
  assert.deepEqual(shown(S),held);
  for(const next of ["allow","deny","allow always"]) click(button(next));
  await sleep(20);
  assert.deepEqual(shown(S),held);                  // allow always opens no step either
  assert.deepEqual(posts,[["/api/approvals/a_1",{decision:first}]]);
  pending=[]; land(); hold=null; await sleep(20);
  assert.equal(card(S),undefined);
  assert.deepEqual(posts,[["/api/approvals/a_1",{decision:first}]]);
}
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_redraw_while_an_answer_is_out_keeps_the_card_disabled_and_a_request_closed_elsewhere_drops(tmp_path):
    """render() redraws the page on every core event. Before #1117 a redraw while a grant was out drew allow, deny and
    allow always live again. A request answered in another client while ours is out leaves the page, and our
    refused answer shows its error with no card to come back to."""
    execute(tmp_path, r'''
const S="send the release notes";
let land; hold=new Promise(r=>{ land=r; });
click(button("allow always")); click(button("Confirm always")); await sleep(20);
await changed();                                    // the core published something
assert.deepEqual(shown(S),["Confirm always (disabled)","Cancel (disabled)"]);
click(button("Refresh")); await sleep(20);
assert.deepEqual(shown(S),["Confirm always (disabled)","Cancel (disabled)"]);
pending=[]; await changed();                        // answered in another client
assert.equal(card(S),undefined);
refuse=true; land(); hold=null; await sleep(20);    // the core refuses ours
assert.ok(root.textContent.includes("the core refused the answer"));
assert.equal(card(S),undefined);
assert.equal(document.activeElement,document.body);
refuse=false; pending=[ask]; await changed();
assert.deepEqual(shown(S),["allow","deny","allow always"]);
hold=new Promise(r=>{ land=r; });
click(button("deny")); await changed();
assert.deepEqual(shown(S),["allow (disabled)","deny (disabled)","allow always (disabled)"]);
land(); hold=null; await sleep(20);
assert.deepEqual(posts.map(p=>p[1].decision),["allow_always","deny"]);
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_refused_answer_gives_the_card_its_buttons_back_with_the_error_and_focus(tmp_path):
    """A refused answer redraws the card live and keeps the error. The pressed button left the page when the card
    went disabled, so focus fell to the body. It goes to the card's button for that answer, allow always for a
    grant, as a failed Schedule remove puts focus on that card's remove."""
    execute(tmp_path, r'''
const S="send the release notes";
refuse=true;
for(const [answer,via,back] of [["allow",["allow"],"allow"],["deny",["deny"],"deny"],
    ["allow_always",["allow always","Confirm always"],"allow always"]]) {
  let land; hold=new Promise(r=>{ land=r; });
  for(const b of via) click(button(b));
  await sleep(20);
  assert.ok(shown(S).every(x=>x.endsWith(" (disabled)")),String(shown(S)));
  assert.equal(document.activeElement,document.body);
  land(); hold=null; await sleep(20);
  assert.ok(root.textContent.includes("the core refused the answer"));
  assert.deepEqual(shown(S),["allow","deny","allow always"]);
  assert.equal(document.activeElement,button(back));
  assert.deepEqual(posts.at(-1),["/api/approvals/a_1",{decision:answer}]);
}
assert.equal(posts.length,3);
''')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_two_answers_out_each_keep_their_own_card_and_focus_on_another_approval_stays(tmp_path):
    """Each card whose answer is out stays disabled until its own POST settles, and the other cards stay live. When an
    answer is refused while focus is on another approval's button, focus stays there: the redraw gives it back by
    key, as on any core event. Focus outside the page, such as on the app's nav, stays there too."""
    execute(tmp_path, r'''
const A="send the release notes", B="delete the old branch", C="restart the worker";
const ask2={...ask,request_id:"a_2",summary:B,call:{name:"git_push",arguments:{branch:"old"}}};
const ask3={...ask,request_id:"a_3",summary:C,call:{name:"restart",arguments:{unit:"worker"}}};
pending=[ask,ask2]; await changed();
let landA, landB; holds.a_1=new Promise(r=>{ landA=r; }); holds.a_2=new Promise(r=>{ landB=r; });
click(onCard(A,"allow always")); click(onCard(A,"Confirm always")); await sleep(20);
assert.deepEqual(shown(B),["allow","deny","allow always"]);
click(onCard(B,"deny")); await sleep(20);
assert.deepEqual(shown(A),["Confirm always (disabled)","Cancel (disabled)"]);
assert.deepEqual(shown(B),["allow (disabled)","deny (disabled)","allow always (disabled)"]);
pending=[ask,ask3]; landB(); await sleep(20);       // B lands first: A's card stays as it is
assert.equal(card(B),undefined);
assert.deepEqual(shown(A),["Confirm always (disabled)","Cancel (disabled)"]);
assert.deepEqual(shown(C),["allow","deny","allow always"]);
onCard(C,"deny").focus();
refuse=true; landA(); await sleep(20);
assert.ok(root.textContent.includes("the core refused the answer"));
assert.deepEqual(shown(A),["allow","deny","allow always"]);
assert.equal(document.activeElement,onCard(C,"deny"));
assert.deepEqual(posts.map(p=>p[1].decision),["allow_always","deny"]);
const nav=document.body.appendChild(new El("a")); nav.text="Chat";   // app.js draws the nav beside the page
let landC; holds.a_3=new Promise(r=>{ landC=r; });
click(onCard(C,"allow")); await sleep(20);
assert.ok(shown(C).every(x=>x.endsWith(" (disabled)")),String(shown(C)));
nav.focus(); landC(); await sleep(20);              // focus has moved out of the page when the refusal comes
assert.deepEqual(shown(C),["allow","deny","allow always"]);
assert.equal(document.activeElement,nav);
assert.deepEqual(posts.map(p=>p[1].decision),["allow_always","deny","allow"]);
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
