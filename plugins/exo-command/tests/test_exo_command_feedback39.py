"""Issue 39 regressions: real chat/approval modules in the existing offline view fixture."""
from __future__ import annotations

import json

import pytest

import test_exo_command_chat_view as view

pytestmark = view.pytestmark
REPLY = {"id": "r1", "role": "assistant", "content": [{"type": "text", "text": "hello reply"}]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    # Extend the existing DOM double only with observable focus/tabIndex behavior.
    dom = view.DOM.replace('focus(){}', '''focus(){
      globalThis.document.activeElement=this;
      if(this.attrs.onfocus)this.attrs.onfocus();
    }
    set tabIndex(v){this.attrs.tabindex=v;} get tabIndex(){return this.attrs.tabindex ?? 0;}''')
    monkeypatch.setattr(view, "DOM", dom)

    def execute(script, messages=None, pending=None):
        setup = f"globalThis.messages={json.dumps([REPLY] if messages is None else messages)};\n"
        setup += f"globalThis.pending={json.dumps(pending or [])};\n"
        helpers = r'''
const group=()=>walk(log()).find(x=>x.attrs["aria-label"]==="Feedback on this reply");
const button=label=>walk(log()).find(x=>x.tagName==="BUTTON" && x.textContent===label);
const click=async label=>{button(label).attrs.onclick();await settle();};
const feedbackRequests=()=>requests.filter(x=>x.path==="/api/feedback");
const nonFeedbackPosts=()=>requests.filter(x=>x.method==="POST" && x.path!=="/api/feedback");
const startupPosts=nonFeedbackPosts().slice();
const originalApi=globalThis.mockApi;
globalThis.mockApi=async (method,path,body)=>{
  if(path!=="/api/feedback")return originalApi(method,path,body);
  requests.push({method,path,body});
  if(globalThis.holdFeedback)await new Promise((resolve,reject)=>{globalThis.resolveFeedback=resolve;globalThis.rejectFeedback=reject;});
  if(globalThis.failFeedback)throw Error("fixture save failure");
  return {};
};
'''
        view._run(tmp_path, helpers + script, setup=setup)
    return execute


def test_feedback_labels_and_group(run):
    run(r'''
assert.ok(group(), "group label missing");
assert.equal(group().attrs.role,"group");
assert.deepEqual(group().children.filter(x=>x.tagName==="BUTTON").map(x=>x.textContent),["Good reply","Correct it","Bad reply"]);
assert.equal(walk(group()).filter(x=>x.tagName==="BUTTON" && x.attrs.tabindex===0).length,1);
assert.ok(!/accept|approve|allow/.test(group().textContent));
button("Good reply").focus();
let prevented=false;
button("Good reply").attrs.onkeydown({key:"ArrowRight",preventDefault(){prevented=true;}});
assert.equal(prevented,true);assert.equal(document.activeElement,button("Correct it"));
assert.equal(button("Correct it").attrs.tabindex,0);assert.equal(button("Good reply").attrs.tabindex,-1);
button("Correct it").attrs.onkeydown({key:"End",preventDefault(){}});
assert.equal(document.activeElement,button("Bad reply"));
button("Bad reply").attrs.onkeydown({key:"Home",preventDefault(){}});
assert.equal(document.activeElement,button("Good reply"));
for(const key of ["o","a","d"]){button("Good reply").attrs.onkeydown({key,preventDefault(){throw Error("feedback shortcut");}});}
assert.equal(feedbackRequests().length,0);
''')


@pytest.mark.parametrize("label,verdict,text", [("Good reply", "accept", ""), ("Correct it", "edit", "corrected reply"), ("Bad reply", "reject", "")])
def test_feedback_posts_values_and_preserves_action_approval(run, label, verdict, text):
    run(f'const label={json.dumps(label)}, verdict={json.dumps(verdict)}, text={json.dumps(text)};\n' + r'''
const approvals=()=>byClass("xc-approvals").children;
const before=approvals().slice(), pendingBefore=JSON.stringify(globalThis.pending);
assert.equal(before.length,1);
await click(label);
if(verdict==="edit"){
  const box=walk(log()).find(x=>x.tagName==="TEXTAREA");
  assert.equal(document.activeElement,box);box.value=text;await click("Send edit");
}
assert.deepEqual(feedbackRequests(),[{method:"POST",path:"/api/feedback",body:{session:"s1",verdict,message_id:"r1",text}}]);
assert.equal(JSON.stringify(globalThis.pending),pendingBefore);
assert.deepEqual(approvals(),before);
assert.deepEqual(nonFeedbackPosts(),startupPosts); // feedback creates no unrelated POST
assert.equal(requests.filter(x=>x.path.startsWith("/api/approvals/")).length,0);
assert.equal(button(label).attrs["aria-pressed"],"true");
assert.ok(group().textContent.includes("sent: "+label));
assert.ok(!/accept|approve|allow/.test(group().textContent));
''', pending=[{"request_id": "action1", "session": "s1", "summary": "Run a command?", "risk": "low"}])


def test_streaming_and_no_id_are_excluded(run):
    run(r'''
assert.ok(log().textContent.includes("no id reply"));assert.equal(group(),undefined);
bus.emit({seq:11,session:"s1",type:"message.delta",body:{message_id:"stream1",text:"streaming"}});
assert.ok(log().textContent.includes("streaming"));assert.equal(group(),undefined);
bus.emit({seq:12,session:"s1",type:"message.completed",body:{message:{id:"stream1",role:"assistant",content:[{type:"text",text:"finished"}]}}});
assert.ok(group());await click("Good reply");
assert.equal(feedbackRequests()[0].body.message_id,"stream1");
''', messages=[{"role": "assistant", "content": [{"type": "text", "text": "no id reply"}]}])


def test_pending_duplicate_error_and_retry(run):
    run(r'''
globalThis.holdFeedback=true;
const stale=button("Bad reply");stale.attrs.onclick();await settle();
assert.equal(feedbackRequests().length,1);assert.ok(group().textContent.includes("Saving."));
assert.equal(button("Bad reply").attrs.disabled,true);
stale.attrs.onclick();button("Good reply").attrs.onclick();await settle();
assert.equal(feedbackRequests().length,1);
globalThis.rejectFeedback(Error("fixture pending failure"));await settle();
assert.ok(group().textContent.includes("Not saved. Try again."));
assert.equal(walk(group()).filter(x=>x.attrs["aria-pressed"]==="true").length,0);
assert.ok(!group().textContent.includes("sent:"));assert.equal(button("Bad reply").attrs.disabled,false);
globalThis.holdFeedback=false;await click("Bad reply");
assert.equal(feedbackRequests().length,2);assert.ok(group().textContent.includes("sent: Bad reply"));
assert.ok(!group().textContent.includes("Not saved"));
''')


def test_edit_cancel_escape_and_pending_save(run):
    run(r'''
await click("Correct it");let box=walk(log()).find(x=>x.tagName==="TEXTAREA");
assert.equal(document.activeElement,box);box.value="draft correction";
box.listeners.input();box.listeners.keydown({key:"Escape",preventDefault(){}});await settle();
assert.equal(document.activeElement,button("Correct it"));assert.equal(feedbackRequests().length,0);
await click("Correct it");box=walk(log()).find(x=>x.tagName==="TEXTAREA");assert.equal(box.value,"draft correction");
await click("Cancel");assert.equal(document.activeElement,button("Correct it"));
await click("Correct it");globalThis.holdFeedback=true;await click("Send edit");
assert.equal(button("Send edit").attrs.disabled,true);assert.equal(button("Cancel").attrs.disabled,true);
button("Send edit").attrs.onclick();await settle();assert.equal(feedbackRequests().length,1);
globalThis.rejectFeedback(Error("edit failed"));await settle();
box=walk(log()).find(x=>x.tagName==="TEXTAREA");assert.equal(box.value,"draft correction");
assert.equal(document.activeElement,box);assert.ok(group().textContent.includes("Not saved. Try again."));
globalThis.holdFeedback=false;await click("Send edit");
assert.equal(feedbackRequests().length,2);assert.equal(feedbackRequests()[1].body.text,"draft correction");
assert.equal(walk(log()).filter(x=>x.tagName==="TEXTAREA").length,0);
''')


def test_saved_id_that_matches_synthetic_display_identity_still_gets_feedback(run):
    reply = dict(REPLY, id="m0")
    run(r'''
assert.ok(group());await click("Good reply");
assert.equal(feedbackRequests()[0].body.message_id,"m0");
''', messages=[reply])
