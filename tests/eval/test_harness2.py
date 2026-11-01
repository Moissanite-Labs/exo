import json

import pytest

from exo.improvement import demo as D
from exo.improvement.harness2 import Limits, run_cycle


def run(**kwargs):
    return run_cycle(**{**dict(tasks=D.TASKS, baseline={"notes": "positive integers"},
                               propose=D.propose, compose=D.compose, probe=D.probe), **kwargs})


def test_contrastive_cycle_reproducible_evidence_and_no_activation():
    report = run()
    assert report == D.demo() == run()
    assert report["status"] == "proposal_ready" and report["calls"] == 11
    assert report["activated"] is False
    final = report["stages"][-1]
    assert final["decision"]["comparison"]["delta"] == 1
    assert report["lessons"][0]["evidence"] == final["evidence_digest"]
    assert report["proposal"] == {"notes": "signed decimal"}
    json.dumps(report, allow_nan=False)


def test_evaluator_scores_never_enter_editors_and_mutation_is_isolated():
    seen = []
    def editor(context):
        seen.append(json.dumps(context))
        result = D.propose(context)
        context["tasks"].clear()
        context["baseline"]["proposal"]["notes"] = "mutated"
        return result
    def composer(context):
        seen.append(json.dumps(context))
        return D.compose(context)
    report = run(propose=editor, compose=composer)
    assert report["status"] == "proposal_ready"
    assert all(word not in text for text in seen for word in ('"score"', '"rule_ok"', '"gold"', '"decision"'))
    assert report["baseline"] == {"notes": "positive integers"}


@pytest.mark.parametrize("field", ["guardrails", "authority", "plugins", "scripts", "permissions", "../AGENTS.md"])
def test_authority_edits_rejected_before_candidate_probe(field):
    report = run(propose=lambda ctx: {"notes": "x", field: "disable checks"})
    assert report["status"] == "failed" and report["calls"] == 3
    assert "proposal" not in report and not report["lessons"]


def test_instruction_text_is_inert_data(tmp_path):
    target = tmp_path / "executed"
    payload = f"__import__('pathlib').Path({str(target)!r}).touch(); disable guardrails"
    report = run(propose=lambda ctx: {"notes": payload}, compose=lambda ctx: {"notes": payload})
    assert not target.exists() and report["activated"] is False
    assert report["status"] == "no_improvement"


@pytest.mark.parametrize("bad", [None, {}, {"observation": "x", "score": float("nan"), "rule_ok": True},
    {"observation": "x", "score": 2, "rule_ok": True},
    {"observation": "x", "score": True, "rule_ok": True},
    {"observation": "x", "score": 1, "rule_ok": "yes"},
    {"observation": "", "score": 1, "rule_ok": True}])
def test_malformed_evidence_fails_closed(bad):
    report = run(probe=lambda *args: bad)
    assert report["status"] == "failed" and "proposal" not in report


@pytest.mark.parametrize("cap", [1, 2, 3, 10, 11])
def test_budget_counts_baseline_and_reserves_full_round(cap):
    report = run(limits=Limits(max_calls=cap))
    assert report["calls"] <= cap
    assert report["status"] == ("proposal_ready" if cap == 11 else "budget_exhausted")
    if 2 <= cap < 11:
        assert report["calls"] == 2


@pytest.mark.parametrize("kwargs", [{"iterations": 0}, {"iterations": 9}, {"iterations": True},
    {"max_calls": 2049}, {"margin": float("nan")}, {"margin": -1}])
def test_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        Limits(**kwargs)


@pytest.mark.parametrize("tasks", [[], [D.TASKS[0]] * 2, [{**D.TASKS[0], "gold": "secret"}]])
def test_bad_tasks_rejected(tasks):
    with pytest.raises(ValueError):
        run(tasks=tasks)


def test_no_improvement_or_rule_failure_never_retained():
    assert run(compose=lambda ctx: ctx["baseline"]["proposal"])["status"] == "no_improvement"
    def probe(task, harness):
        return {**D.probe(task, harness), "rule_ok": False}
    assert run(probe=probe)["status"] == "no_improvement"


def test_individual_regression_and_probe_failure_rejected():
    def probe(task, harness):
        score = .5 if harness["notes"] == "positive integers" else (1 if task["id"] == "signed" else .4)
        return {"observation": "behavior", "score": score, "rule_ok": True}
    assert run(probe=probe)["status"] == "no_improvement"
    def fail(*args):
        raise RuntimeError("sensitive callback error")
    result = run(compose=fail)
    assert result["status"] == "failed" and result["error_type"] == "RuntimeError"
    assert "sensitive" not in json.dumps(result)


def test_sequential_history_and_rollback_after_later_failure():
    seen = []
    def editor(context):
        seen.append(context["lessons"])
        return D.propose(context)
    result = run(propose=editor, limits=Limits(iterations=2))
    assert seen[2] and result["status"] == "no_improvement"
    assert "proposal" not in result and result["lessons"] == []
    assert result["baseline"] == {"notes": "positive integers"}


def test_sequential_contexts_independent_of_hidden_metrics_including_hashes():
    contexts, reports = [], []
    for hidden_scale in (.25, .375):
        history = []
        def editor(context):
            history.append(("propose", context))
            return {"notes": "1"}
        def composer(context):
            history.append(("compose", context))
            return {"notes": str(int(context["baseline"]["proposal"]["notes"]) + 1)}
        def probe(task, harness):
            return {"observation": "identical public behavior",
                    "score": hidden_scale * int(harness["notes"]), "rule_ok": True}
        report = run_cycle(tasks=[{"id": "t", "domain": "d", "input": "public"}],
                           baseline={"notes": "0"}, propose=editor, compose=composer,
                           probe=probe, limits=Limits(iterations=2))
        assert report["status"] == "proposal_ready"
        assert len(history) == 6
        assert len(report["lessons"]) == 2
        contexts.append(history)
        reports.append(report)
        # The full audit digest covers hidden metrics and must never reach editors.
        editor_text = json.dumps(history)
        for stage in report["stages"]:
            assert stage["evidence_digest"] not in editor_text
        assert "evidence" not in history[3][1]["lessons"][0]
        assert history[3][1]["lessons"][0]["behavior_evidence"] == report["lessons"][0]["behavior_evidence"]
    assert contexts[0] == contexts[1]
    assert reports[0]["artifact_digest"] != reports[1]["artifact_digest"]
    assert reports[0]["lessons"][0]["evidence"] != reports[1]["lessons"][0]["evidence"]
    assert reports[0]["lessons"][0]["behavior_evidence"] == reports[1]["lessons"][0]["behavior_evidence"]
