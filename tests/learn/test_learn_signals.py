import json

import pytest

from exo import capture
from exo.learn import handle_feedback, handle_ui_respond
from exo.learn.learner import Learner

from conftest import feedback, new_session
from exo.protocol import schema as s


def verdicts(lr):
    return [e for e in lr.runtime.ledger.iter(kind="verdict")]


def pairs(lr):
    f = lr.runtime.root / "training" / "preference-pairs.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def test_accept_writes_verdict_with_answer_and_no_pair(engine, learner):
    sid, ids = new_session(engine, ("user", "Summarise the PR"), ("assistant", "Here is a long summary."))
    out = handle_feedback(engine, feedback(sid, "accept"))
    assert out["recorded"] and out["pair"] is False and out["review"] is False
    (ev,) = verdicts(learner)
    assert ev["body"]["verdict"] == "accept" and ev["body"]["answer"] == "Here is a long summary."
    assert ev["body"]["message_id"] == ids[1] and ev["session"] == sid
    assert learner.runtime.ledger.get(ev["body"]["target"])["body"]["text"] == "Here is a long summary."
    assert pairs(learner) == []


def test_edit_writes_verdict_and_a_preference_pair(engine, learner):
    sid, _ = new_session(engine, ("user", "Write the status line"), ("assistant", "Status: all good :)"))
    out = handle_feedback(engine, feedback(sid, "edit", "Status: all good"))
    assert out["pair"] is True
    (ev,) = verdicts(learner)
    assert ev["body"]["edit"] == "Status: all good" and ev["body"]["chosen"] == "Status: all good"
    (pair,) = pairs(learner)
    assert pair["rejected"] == "Status: all good :)" and pair["chosen"] == "Status: all good"
    assert pair["context"] == "Write the status line" and pair["evidence"] == [ev["id"]]


def test_should_have_been_pairs_and_reject_does_not(engine, learner):
    sid, _ = new_session(engine, ("user", "Name the file"), ("assistant", "report_final_v2.md"))
    handle_feedback(engine, feedback(sid, "should_have_been", "report.md"))
    handle_feedback(engine, feedback(sid, "reject", "too long"))
    assert [p["chosen"] for p in pairs(learner)] == ["report.md"]
    reject = verdicts(learner)[1]
    assert reject["body"]["reason"] == "too long" and "chosen" not in reject["body"]


def test_message_id_picks_an_older_answer(engine, learner):
    sid, ids = new_session(engine, ("user", "a"), ("assistant", "first answer"), ("user", "b"),
                           ("assistant", "second answer"))
    handle_feedback(engine, feedback(sid, "reject", message_id=ids[1]))
    assert verdicts(learner)[0]["body"]["answer"] == "first answer"


def test_same_edit_twice_makes_one_pair(engine, learner):
    sid, _ = new_session(engine, ("user", "q"), ("assistant", "bad"))
    handle_feedback(engine, feedback(sid, "edit", "good"))
    handle_feedback(engine, feedback(sid, "edit", "good"))
    assert len(verdicts(learner)) == 2 and len(pairs(learner)) == 2  # distinct verdict events, distinct evidence


def test_feedback_on_a_private_thread_is_not_remembered(engine, learner):
    """D-028: a verdict on a private thread's answer would put that answer and the question before it in the
    ledger and the preference pairs, so it is not recorded and says why."""
    sid, _ = new_session(engine, ("user", "Plan the zebracorn launch"), ("assistant", "Zebracorn ships Friday."),
                         kind="private")
    for verdict, text in (("accept", ""), ("edit", "Zebracorn ships Monday."), ("reject", "wrong day")):
        out = handle_feedback(engine, feedback(sid, verdict, text))
        assert out["recorded"] is False and "private" in out["reason"]
    assert list(learner.runtime.ledger.iter()) == [] and pairs(learner) == [] and learner.tasks == {}


def test_feedback_under_a_private_thread_is_not_remembered(engine, learner):
    """D-028: a private thread's delegate child holds the brief built from it and the child's result, and a client
    can open it (/tasks, /open) and send a verdict. Neither it nor its own child records one. A normal thread's
    child still does."""
    priv, _ = new_session(engine, ("user", "Plan the zebracorn deal"), ("assistant", "Delegating."), kind="private")
    kid, _ = new_session(engine, ("user", "Dig into zebracorn"), ("assistant", "Zebracorn: $40M, closes Friday."),
                         kind="subagent", parent=priv)
    grandkid, _ = new_session(engine, ("user", "Check the zebracorn terms"), ("assistant", "Zebracorn terms hold."),
                              kind="subagent", parent=kid)
    for sid in (kid, grandkid):
        for verdict, text in (("accept", ""), ("edit", "Zebracorn: $50M"), ("reject", "zebracorn is $50M")):
            out = handle_feedback(engine, feedback(sid, verdict, text))
            assert out["recorded"] is False and "private" in out["reason"]
    assert list(learner.runtime.ledger.iter()) == [] and pairs(learner) == [] and learner.tasks == {}
    normal, _ = new_session(engine, ("user", "Plan the trip"), ("assistant", "Delegating."), kind="thread")
    child, _ = new_session(engine, ("user", "Check the ferry"), ("assistant", "Ferry: nine."), kind="subagent",
                           parent=normal)
    assert handle_feedback(engine, feedback(child, "edit", "Ferry: ten."))["recorded"] is True


def test_ui_respond_on_a_private_threads_panel_is_not_remembered(engine, learner):
    """D-028: what the user does with a panel a private thread opened (a choice, a comment) stays out of the
    ledger; a panel from a normal thread, or one the event log does not know, is still noted."""
    priv, _ = new_session(engine, ("user", "Compare the zebracorn offers"), kind="private")
    normal, _ = new_session(engine, ("user", "Compare the ferries"), kind="thread")
    engine.hub.publish(s.UiPanel(panel_id="p_priv", action="open", kind="choice", text="Zebracorn: A or B?"), priv)
    engine.hub.publish(s.UiPanel(panel_id="p_norm", action="open", kind="choice", text="Ferry: A or B?"), normal)
    out = handle_ui_respond(engine, s.UiRespond(panel_id="p_priv", action="acted",
                                                value={"choice": "b", "comment": "zebracorn at $50M"}))
    assert out["recorded"] is False and "private" in out["reason"]
    for pid in ("p_norm", "p_unknown"):
        assert handle_ui_respond(engine, s.UiRespond(panel_id=pid, action="acted", value={"choice": "a"}))["recorded"]
    notes = [e["body"] for e in learner.runtime.ledger.iter(kind="note") if e["body"]["type"] == "ui_signal"]
    assert [n["panel_id"] for n in notes] == ["p_norm", "p_unknown"]
    assert "zebracorn" not in json.dumps(list(learner.runtime.ledger.iter())).lower()


def test_ui_respond_logs_a_note(engine, learner):
    out = handle_ui_respond(engine, s.UiRespond(panel_id="p1", action="dismissed", value={"why": "noise"}))
    assert out["recorded"]
    (ev,) = [e for e in learner.runtime.ledger.iter(kind="note") if e["body"]["type"] == "ui_signal"]
    assert ev["body"]["panel_id"] == "p1" and ev["body"]["action"] == "dismissed" and ev["body"]["value"] == {"why": "noise"}


def test_learning_off_without_a_store_says_so_once(make_engine):
    eng = make_engine(store=False)
    sid, _ = new_session(eng, ("user", "q"), ("assistant", "a"))
    out = handle_feedback(eng, feedback(sid, "edit", "b"))
    assert out["recorded"] is False and "learning is off" in out["reason"]
    assert handle_ui_respond(eng, s.UiRespond(panel_id="p", action="opened"))["recorded"] is False
    lr = eng._learn
    assert len(lr.notices) == 1


def test_store_falls_back_to_the_exo_memory_plugin_setting(make_engine, template_store, tmp_path):
    eng = make_engine(store=False)
    eng.plugins.config["exo-memory"] = {"store_root": str(template_store.root)}
    lr = Learner(eng, env={})
    assert lr.runtime is not None and lr.runtime.root == template_store.root


def test_store_from_environment_principal(make_engine, template_store, tmp_path):
    import shutil
    eng = make_engine(store=False)
    shutil.copytree(template_store.root, eng.home / "principals" / "pat")
    lr = Learner(eng, env={"EXO_PRINCIPAL": "pat"})
    assert lr.runtime is not None and lr.runtime.root == eng.home / "principals" / "pat"


@pytest.mark.parametrize("text,cue", [
    ("Important correction: use tables for the summary, NOT bullet lists.", "label"),
    ("Important correction: the deploy target is staging, NOT prod.", "label"),
    ("Not a PR, a draft issue.", "not-x-y"),
    ("Actually, send it to the team channel.", "actually"),
])
def test_capture_catches_the_missed_correction_shapes(text, cue):
    msgs = [{"role": "assistant", "content": "Done."}, {"role": "user", "content": text}]
    (sig,) = capture.extract_signals(msgs)
    assert sig.kind == capture.CORRECTION and cue in sig.cues and sig.target_index == 0


def test_capture_label_works_on_the_first_message_and_prose_is_left_alone():
    (sig,) = capture.extract_signals([{"role": "user", "content": "Important correction: it is staging, NOT prod."}])
    assert sig.kind == capture.CORRECTION
    quiet = capture.extract_signals([{"role": "assistant", "content": "Done."},
                                     {"role": "user", "content": "Not sure, I will read it tomorrow."}])
    assert [x for x in quiet if x.kind == capture.CORRECTION] == []


def test_chat_corrections_are_recorded_once(engine, learner):
    from exo.learn.signals import record_chat_signals
    sid, _ = new_session(engine, ("user", "Summarise"), ("assistant", "- a\n- b"),
                         ("user", "Important correction: use a table, NOT bullet lists."))
    first = record_chat_signals(learner, sid)
    again = record_chat_signals(learner, sid)
    assert len(first) == 1 and first[0]["kind"] == "verdict" and again == []
    body = first[0]["body"]
    assert body["verdict"] == "edit" and "NOT bullet" in body["text"]
    assert learner.runtime.ledger.get(body["target"])["body"]["text"] == "- a\n- b"
