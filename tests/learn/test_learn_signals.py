import json
import logging
import threading
import time

import pytest

from exo import capture, provenance
from exo.learn import handle_feedback, handle_ui_respond
from exo.learn.learner import Learner
from exo.learn.runtime import resolve_store

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


def test_ui_respond_on_a_panel_a_private_thread_updated_is_not_remembered(engine, learner):
    """D-028: `ui update` takes a known panel id from any thread. A panel a normal thread opened and a private
    thread (or its child) then updated may show the private thread's words, so a response to it is not noted."""
    normal, _ = new_session(engine, ("user", "Compare the ferries"), kind="thread")
    priv, _ = new_session(engine, ("user", "Compare the zebracorn offers"), kind="private")
    kid, _ = new_session(engine, ("user", "Check zebracorn"), kind="subagent", parent=priv)
    for pid, by in (("p_shared", priv), ("p_kid", kid)):
        engine.hub.publish(s.UiPanel(panel_id=pid, action="open", kind="choice", text="Ferry: A or B?"), normal)
        engine.hub.publish(s.UiPanel(panel_id=pid, action="update", kind="choice", text="Zebracorn: A or B?"), by)
        out = handle_ui_respond(engine, s.UiRespond(panel_id=pid, action="acted",
                                                    value={"choice": "b", "comment": "zebracorn at $50M"}))
        assert out["recorded"] is False and "private" in out["reason"], pid
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


def test_store_falls_back_to_the_active_principal_in_config(make_engine, template_store):
    """The installer and `exo init` write only config.json `principal`; learning finds that store, as exo-memory
    does, and the environment still wins over the file."""
    import shutil
    eng = make_engine(store=False)
    shutil.copytree(template_store.root, eng.home / "principals" / "pat")
    shutil.copytree(template_store.root, eng.home / "principals" / "env-pick")
    (eng.home / "config.json").write_text(json.dumps({"principal": "pat"}))
    lr = Learner(eng, env={})
    assert lr.runtime is not None and lr.runtime.root == eng.home / "principals" / "pat"
    assert lr.notices == []
    assert resolve_store(eng, {"EXO_PRINCIPAL": "env-pick"}) == eng.home / "principals" / "env-pick"
    (eng.home / "config.json").write_text(json.dumps({"principal": "../escape"}))
    assert resolve_store(eng, {}) is None  # a bad id in the file is ignored, not joined into a path
    shutil.copytree(template_store.root, eng.home / "principals" / "7")
    (eng.home / "config.json").write_text(json.dumps({"principal": 7}))
    assert resolve_store(eng, {}) is None  # only a string names a principal, as exo-memory reads it


def test_no_principal_at_all_is_a_quiet_notice_that_names_the_fix(make_engine, caplog):
    """No principal anywhere is a setup step, not a fault: the notice names `exo init` and is not a warning,
    so it is not printed over the terminal before the client draws (exo-memory says it there)."""
    eng = make_engine(store=False)
    with caplog.at_level(logging.DEBUG, logger="exo.learn"):
        lr = Learner(eng, env={})
        assert lr.runtime is None
    assert len(lr.notices) == 1 and "exo init" in lr.notices[0]
    assert not [r for r in caplog.records if r.name == "exo.learn" and r.levelno >= logging.WARNING]


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


CAPTURED = ("ruling", "verdict", "question")


def _full_scan(led):
    """The known set as record_chat_signals built it before #263: every ruling, verdict and question in the ledger."""
    return {(e["body"] or {}).get("capture_key") for e in led.iter(kind=CAPTURED)}


def _say(engine, sid, role, text):
    from exo.core.types import ContentPart, Message
    engine.store.append_message(sid, Message(role=role, content=[ContentPart.of_text(text)]), "t1")


def _captured_first(led, said, answer):
    """Write the correction `said` of `answer` the way exo-memory does when it hears it first."""
    (sig,) = capture.extract_signals([{"role": "assistant", "content": answer}, {"role": "user", "content": said}])
    kind, body = capture.to_event(sig, source="pre_compress")
    body["target"] = led.append("utterance", {"text": answer, "role": "assistant"}, "exo-memory")["id"]
    return led.append(kind, body, "principal")


def test_chat_capture_reads_only_the_new_ledger_rows(engine, learner, monkeypatch):
    """#263: the known capture keys stay on the runtime between calls. A call reads only the ruling, verdict and
    question rows after the last one read, and still sees a key another writer added in between."""
    from exo.learn.signals import record_chat_signals
    led = learner.runtime.ledger
    for i in range(40):
        led.append("ruling", {"text": f"rule {i}", "capture_key": f"old-{i}"}, "principal")
    read, real = [], led.iter

    def counted(*a, **kw):
        for ev in real(*a, **kw):
            if ev["kind"] in CAPTURED:
                read.append(ev["seq"])
            yield ev
    monkeypatch.setattr(led, "iter", counted)
    sid, _ = new_session(engine, ("user", "Summarise"), ("assistant", "- a\n- b"),
                         ("user", "Important correction: use a table, NOT bullet lists."))
    assert len(record_chat_signals(learner, sid)) == 1 and len(read) == 40  # the first call reads the ledger once
    read.clear()
    assert record_chat_signals(learner, sid) == [] and len(read) == 1  # then only the verdict it wrote
    read.clear()
    said, answer = "Important correction: the deploy target is staging, NOT prod.", "Deployed to prod."
    theirs = _captured_first(led, said, answer)
    _say(engine, sid, "assistant", answer)
    _say(engine, sid, "user", said)
    assert record_chat_signals(learner, sid) == [] and read == [theirs["seq"]]  # known: not written twice
    read.clear()
    assert record_chat_signals(learner, sid) == [] and read == []


def test_known_capture_keys_match_a_full_scan_through_every_change(engine, learner, monkeypatch, tmp_path):
    """#263: the kept set equals a full scan of the ledger at every call: after rows the learner writes, rows another
    writer adds, the same file opened again, and another ledger put in its place, with a different history that is
    longer or shorter than the rows read so far."""
    from exo.ledger import Ledger
    from exo.learn import signals
    rt, real, calls = learner.runtime, signals.known_keys, []

    def checked(r):
        keys = real(r)
        assert keys == _full_scan(r.ledger)
        calls.append(len(keys))
        return keys
    monkeypatch.setattr(signals, "known_keys", checked)
    fix = "Important correction: use a table, NOT bullet lists."
    sid, _ = new_session(engine, ("user", "Summarise"), ("assistant", "- a\n- b"), ("user", fix))
    rt.ledger.append("ruling", {"text": "Always cite the source.", "capture_key": "k-cite"}, "principal")
    assert [e["kind"] for e in signals.record_chat_signals(learner, sid)] == ["verdict"]
    _captured_first(rt.ledger, "Important correction: it is staging, NOT prod.", "Deployed to prod.")
    _say(engine, sid, "assistant", "Deployed to prod.")
    _say(engine, sid, "user", "Important correction: it is staging, NOT prod.")
    assert signals.record_chat_signals(learner, sid) == []
    rt.ledger.close()  # the same file opened again keeps the set
    rt.ledger = Ledger(rt.store.ledger_path, rt.signer)
    _say(engine, sid, "user", "From now on always run the tests before a push.")
    assert [e["kind"] for e in signals.record_chat_signals(learner, sid)] == ["ruling"]
    assert real(rt) == _full_scan(rt.ledger)
    first = rt.ledger
    longer = Ledger(tmp_path / "longer" / "ledger.sqlite", rt.signer)  # more rows than read so far, none of ours
    for i in range(rt.known[0] + 5):
        longer.append("note", {"text": f"n{i}"}, "principal")
    rt.ledger = longer
    assert sorted(e["kind"] for e in signals.record_chat_signals(learner, sid)) == ["ruling", "verdict", "verdict"]
    shorter = Ledger(tmp_path / "shorter" / "ledger.sqlite", rt.signer)  # fewer rows: it holds just one of the keys
    _captured_first(shorter, fix, "- a\n- b")
    rt.ledger = shorter
    assert sorted(e["kind"] for e in signals.record_chat_signals(learner, sid)) == ["ruling", "verdict"]
    assert len(calls) == 5
    first.close()
    longer.close()


def test_threads_capturing_at_once_write_each_key_once(engine, learner):
    """#263: `record_chat_signals` reads its known keys under `rt.lock`. Eight threads capture the same chat on one
    runtime, and each key is written once. The threads reach the lock one at a time and wait there until all eight
    have come, so a read made before taking the lock would find no key in any thread, and each would write every
    key again."""
    from exo.learn.signals import record_chat_signals
    rt, threads, errors = learner.runtime, 8, []
    sid, _ = new_session(engine, ("user", "Summarise"), ("assistant", "- a\n- b"),
                         ("user", "Important correction: use a table, NOT bullet lists."),
                         ("user", "From now on always run the tests before a push."))
    messages = [provenance.flat(m) for m in learner.messages(sid)]
    gate, real, waiting, arrived = threading.Barrier(threads), rt.lock, set(), []

    class HeldLock:
        def __enter__(self):
            if threading.current_thread() in waiting:
                waiting.discard(threading.current_thread())
                arrived.append(threading.current_thread())
                gate.wait(timeout=30)
            return real.__enter__()

        def __exit__(self, *exc):
            return real.__exit__(*exc)

    def capture_once():
        try:
            record_chat_signals(learner, sid, messages)
        except Exception as e:
            errors.append(e)
    workers = [threading.Thread(target=capture_once) for _ in range(threads)]
    waiting.update(workers)
    rt.lock = HeldLock()
    try:
        for i, t in enumerate(workers, 1):  # the next thread starts once this one is at the lock
            t.start()
            while len(arrived) < i and t.is_alive():
                time.sleep(0.001)
        for t in workers:
            t.join(timeout=60)
    finally:
        rt.lock = real
    assert errors == [] and not any(t.is_alive() for t in workers)
    assert len(arrived) == threads  # every thread was held at the lock
    keys = [(e["body"] or {}).get("capture_key") for e in rt.ledger.iter(kind=CAPTURED)]
    assert len(keys) == 2 and len(set(keys)) == 2


def test_an_accept_whose_answer_is_not_in_the_transcript_is_no_replay_gold(engine, learner):
    """exo-desktop #54: the backfilled target of such an accept holds a placeholder, not the agent's words."""
    from exo.eval import build_replays
    led = learner.runtime.ledger
    found, _ = new_session(engine, ("user", "Summarise the PR"), ("assistant", "Here is a long summary."))
    lost, _ = new_session(engine, ("user", "Summarise the issue"))   # the answer is not in this transcript
    for sid, said in ((found, "Summarise the PR"), (lost, "Summarise the issue")):
        led.append("utterance", {"text": said, "role": "user"}, provenance.PRINCIPAL, session=sid)  # as exo-memory logs it
        assert handle_feedback(engine, feedback(sid, "accept"))["recorded"]
    targets = [led.get(e["body"]["target"])["body"] for e in verdicts(learner)]
    assert [t["source"] for t in targets] == ["learn_backfill", "learn_backfill"]
    golds = {i["session"]: i["gold"] for i in build_replays(led)}
    assert golds == {found: "Here is a long summary."}   # a backfill that holds the answer still counts
