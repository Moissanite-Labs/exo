import asyncio
import json

import pytest

from exo.learn import attach, handle_feedback
from exo.learn.review import parse_json, review, run_review

from conftest import feedback, new_session


def test_disabled_runtime_has_no_signals_but_retains_skill_context():
    from types import SimpleNamespace
    from exo.learn.review import _touched, new_signals

    skills = SimpleNamespace(listing=lambda: [{"name": "writing", "description": "plain prose"}])
    lr = SimpleNamespace(runtime=None, engine=SimpleNamespace(skills=skills))
    assert new_signals(lr) == []
    assert _touched(lr, []) == "- skill writing: plain prose"


def test_parse_json_keeps_a_value_with_trailing_braces():
    text = 'Here is the review: {"proposals": []} } thanks }'
    assert parse_json(text) == {"proposals": []}
    assert parse_json('[{"a": 1}] trailing ]') == [{"a": 1}]
    assert parse_json('prefix {"objection": null} suffix') == {"objection": None}
    with pytest.raises(Exception):
        parse_json("this is not json at all")


def test_parse_json_skips_a_bracket_in_prose_that_is_not_json():
    # #835 review: a start that does not decode, such as "[draft]", no longer hides the value after it.
    assert parse_json('Note [draft]: {"objection": "one-off"}') == {"objection": "one-off"}
    assert parse_json('See {this} and [that]: {"proposals": []} }') == {"proposals": []}
    with pytest.raises(Exception):
        parse_json("[draft] {not json either}")


def reply(items):
    return json.dumps({"proposals": items})


def proposals(lr):
    return list(lr.runtime.ledger.iter(kind="belief_proposed"))


def edit_turn(engine, text="Status: all good"):
    sid, _ = new_session(engine, ("user", "Write the status line"), ("assistant", "Status: all good :)"))
    out = handle_feedback(engine, feedback(sid, "edit", text))
    return sid, out


def test_review_turns_an_edit_into_a_typed_taste_proposal(make_engine):
    eng = make_engine()
    lr = attach(eng, env={})
    sid, _ = edit_turn(eng)
    ev = next(lr.runtime.ledger.iter(kind="verdict"))
    eng.fake.script.append(reply([{
        "type": "taste", "claim": "The principal wants plain status lines, not emoticons, because they read as noise.",
        "wants": "plain status lines", "avoid": "emoticons", "because": "they read as noise",
        "implication": "Applies to all short status output.", "domain": "mind", "evidence": ["e1"]}]))
    out = asyncio.run(review(lr, sid, "signal", promote=False))
    assert out["status"] == "done" and out["reflector"] == "model" and len(out["proposals"]) == 1
    (pe,) = proposals(lr)
    b = pe["body"]
    assert b["ptype"] == "taste" and b["claim"].startswith("The principal wants plain status lines")
    assert b["evidence"][0]["id"] == ev["id"] and pe["author"] == "exo-learn"
    assert (lr.runtime.root / "proposals" / f"{b['proposal_id']}.json").exists()
    assert (lr.runtime.root / "proposals" / f"{b['proposal_id']}.md").exists()
    # the prompt carried the signal and the aux role served it
    prompt = eng.fake.calls[0]["messages"][-1].text()
    assert "e1 [verdict:edit]" in prompt and "Do not capture" in prompt


def test_all_four_types_and_invented_evidence(make_engine):
    eng = make_engine()
    lr = attach(eng, env={})
    sid, _ = edit_turn(eng)
    eng.fake.script.append(reply([
        {"type": "taste", "claim": "The principal wants tables, not bullets, because they scan faster.",
         "wants": "tables", "avoid": "bullets", "because": "they scan faster", "evidence": ["e1"]},
        {"type": "skill", "skill": "status-update", "patch": "Write one plain line. No emoticons.",
         "claim": "Status lines are plain.", "wants": "plain status lines", "evidence": ["e1"]},
        {"type": "fact", "claim": "The release branch is main.", "wants": "the release branch is main",
         "because": "they said so", "evidence": ["e1"]},
        {"type": "question", "question": "Should status lines ever carry links?", "claim": "links in status?"},
        {"type": "taste", "claim": "made up", "wants": "made up", "evidence": ["e99"]},   # invented evidence
        {"type": "nonsense", "claim": "x", "evidence": ["e1"]},
    ]))
    out = asyncio.run(review(lr, sid, "signal", promote=False))
    kinds = sorted(e["body"]["ptype"] for e in proposals(lr))
    assert kinds == ["fact", "question", "skill", "taste"] and len(out["proposals"]) == 4
    skill = next(e["body"] for e in proposals(lr) if e["body"]["ptype"] == "skill")
    assert skill["target"] == "status-update" and (lr.runtime.root / "proposals" / f"{skill['proposal_id']}.diff").exists()
    # the question also reached the queue
    assert "Should status lines ever carry links?" in (lr.runtime.root / "queues" / "questions.jsonl").read_text()


def test_watermark_stops_a_second_review_from_rereading(make_engine):
    eng = make_engine()
    lr = attach(eng, env={})
    sid, _ = edit_turn(eng)
    eng.fake.script.append(reply([]))
    assert asyncio.run(review(lr, sid, "signal", promote=False))["status"] == "done"
    assert asyncio.run(review(lr, sid, "signal", promote=False))["status"] == "nothing_new"
    assert len(eng.fake.calls) == 1


def test_bad_model_output_falls_back_to_the_rules(make_engine):
    eng = make_engine(["this is not json at all"])
    lr = attach(eng, env={})
    sid, _ = new_session(eng, ("user", "x"), ("assistant", "bad output"))
    handle_feedback(eng, feedback(sid, "should_have_been", "one line per item with the review link"))
    out = asyncio.run(review(lr, sid, "signal", promote=False))
    assert out["reflector"] == "rules" and len(out["proposals"]) == 1
    assert any("rules instead" in n for n in lr.notices)


def test_model_error_falls_back_to_the_rules(make_engine):
    eng = make_engine()            # script is empty: the fake provider errors
    lr = attach(eng, env={})
    sid, _ = edit_turn(eng)
    out = asyncio.run(review(lr, sid, "signal", promote=False))
    assert out["status"] == "done" and out["reflector"] == "rules"


def test_critic_objection_is_attached_for_a_skill_patch(make_engine):
    eng = make_engine()
    lr = attach(eng, env={})
    sid, _ = edit_turn(eng)
    eng.fake.script += [
        reply([{"type": "skill", "skill": "status-update", "patch": "No emoticons.", "claim": "No emoticons.",
                "wants": "no emoticons", "evidence": ["e1"]}]),
        json.dumps({"objection": "This looks like a one-off."})]
    asyncio.run(review(lr, sid, "signal", promote=False))
    (pe,) = proposals(lr)
    assert pe["body"]["critic_note"] == "This looks like a one-off."


def test_daily_cap(make_engine):
    eng = make_engine()
    lr = attach(eng, env={})
    lr.settings["fast_loop"]["daily_cap"] = 1
    sid, _ = edit_turn(eng)
    eng.fake.script.append(reply([]))
    asyncio.run(review(lr, sid, "signal", promote=False))
    handle_feedback(eng, feedback(sid, "reject", "no"))
    assert asyncio.run(review(lr, sid, "signal", promote=False))["status"] == "capped"


def test_hooks_run_the_loop_and_a_live_turn_cancels_it(make_engine):
    async def scenario():
        eng = make_engine()
        lr = attach(eng, env={})
        sid, _ = new_session(eng, ("user", "Summarise"), ("assistant", "- a\n- b"),
                             ("user", "Important correction: use a table, NOT bullet lists."))
        gate = asyncio.Event()

        async def slow(role, prompt, system):
            await gate.wait()
            return reply([])
        lr._complete = slow
        eng.plugins.hooks.fire("on_turn_end", session_id=sid)
        await asyncio.sleep(0.05)
        task = lr.tasks[sid]
        assert not task.done()
        eng.plugins.hooks.fire("on_turn_start", session_id=sid)        # a live turn starts
        await asyncio.sleep(0.05)
        assert task.cancelled() or task.done()
        assert list(lr.runtime.ledger.iter(kind="belief_proposed")) == []
        # the signal is still unread, so the next turn end reviews it
        lr._complete = lambda role, prompt, system: reply([])
        eng.plugins.hooks.fire("on_turn_end", session_id=sid)
        await asyncio.sleep(0.1)
        notes = [e for e in lr.runtime.ledger.iter(kind="note") if e["body"]["type"] == "learn_review"]
        assert len(notes) == 1
    asyncio.run(scenario())


def test_no_review_without_a_signal_or_for_unattended_sessions(make_engine):
    async def scenario():
        eng = make_engine()
        lr = attach(eng, env={})
        calls = []
        lr._complete = lambda role, prompt, system: calls.append(role) or reply([])
        sid, _ = new_session(eng, ("user", "hello there"), ("assistant", "hi"))
        eng.plugins.hooks.fire("on_turn_end", session_id=sid)
        cron, _ = new_session(eng, ("user", "Important correction: use X, NOT Y."), ("assistant", "ok"),
                              title="cron: nightly")
        eng.plugins.hooks.fire("on_turn_end", session_id=cron)
        await asyncio.sleep(0.1)
        assert calls == [] and lr.tasks == {}
    asyncio.run(scenario())


def test_a_private_thread_gives_learning_nothing(make_engine):
    """D-028: a correction said in a private thread is not captured, starts no review (which would read the
    thread), and a review cannot be started for it; the same words in a normal thread are captured."""
    said = "Important correction: use a table, NOT bullet lists."

    async def scenario():
        eng = make_engine()
        lr = attach(eng, env={})
        calls = []
        lr._complete = lambda role, prompt, system: calls.append(prompt) or reply([])
        sid, _ = new_session(eng, ("user", "Summarise"), ("assistant", "- a\n- b"), ("user", said), kind="private")
        eng.plugins.hooks.fire("on_turn_start", session_id=sid)
        eng.plugins.hooks.fire("on_turn_end", session_id=sid)
        await asyncio.sleep(0.1)
        kid, _ = new_session(eng, ("user", "Summarise"), ("assistant", "- a\n- b"), ("user", said), kind="subagent",
                             parent=sid)  # its delegate child: a brief built from it
        eng.plugins.hooks.fire("on_turn_end", session_id=kid)
        await asyncio.sleep(0.1)
        assert list(lr.runtime.ledger.iter(kind=("ruling", "verdict", "utterance"))) == []
        assert calls == [] and lr.tasks == {} and lr.trigger(sid, "signal") is False
        assert lr.trigger(kid, "signal") is False
        normal, _ = new_session(eng, ("user", "Summarise"), ("assistant", "- a\n- b"), ("user", said))
        eng.plugins.hooks.fire("on_turn_end", session_id=normal)
        await asyncio.sleep(0.1)
        assert {e["session"] for e in lr.runtime.ledger.iter(kind=("ruling", "verdict"))} == {normal}
        assert len(calls) == 1  # the normal thread's review
    asyncio.run(scenario())


def test_every_n_turns_triggers_with_only_an_accept(make_engine):
    async def scenario():
        eng = make_engine()
        lr = attach(eng, env={})
        lr.settings["fast_loop"]["every_turns"] = 2
        calls = []
        lr._complete = lambda role, prompt, system: calls.append(role) or reply([])
        sid, _ = new_session(eng, ("user", "q"), ("assistant", "a"))
        handle_feedback(eng, feedback(sid, "accept"))
        eng.plugins.hooks.fire("on_turn_start", session_id=sid)
        eng.plugins.hooks.fire("on_turn_end", session_id=sid)
        await asyncio.sleep(0.05)
        assert calls == []
        eng.plugins.hooks.fire("on_turn_start", session_id=sid)
        eng.plugins.hooks.fire("on_turn_end", session_id=sid)
        await asyncio.sleep(0.1)
        assert calls == ["aux"]
    asyncio.run(scenario())


def test_deadline_drops_a_slow_review(make_engine):
    async def scenario():
        eng = make_engine()
        lr = attach(eng, env={})
        lr.settings["fast_loop"]["deadline"] = 0.05
        sid, _ = edit_turn(eng)

        async def slow(role, prompt, system):
            await asyncio.sleep(5)
        lr._complete = slow
        assert (await run_review(lr, sid, "signal"))["status"] == "timeout"
    asyncio.run(scenario())


def test_attach_without_a_store_is_quiet_and_says_so_once(make_engine):
    eng = make_engine(store=False)
    lr = attach(eng, env={})
    eng.plugins.hooks.fire("on_turn_end", session_id="s1")
    eng.plugins.hooks.fire("on_turn_end", session_id="s1")
    assert len(lr.notices) == 1 and "learning is off" in lr.notices[0]
