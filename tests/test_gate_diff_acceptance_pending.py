"""Pending comparator/budget acceptance, not a runtime optimization or gate fix.

The mutant is confined to temporary source copies. No command string is run.
Known gaps are strict xfails reproduced on main 64bfee2c; ordinary guards show
that valid outcomes and an explicit long-line corpus still work.
"""
from __future__ import annotations

import json
import sys

import pytest

from exo import gate
from scripts import gate_diff
from test_gate_diff import ROOT, _d, _gate_diff, _stream, _tree


def url_action(count):
    return {"tool": "bash", "args": {"command": "gh pr comment " + " ".join(
        f"https://ghe.corp/o/r/pull/{i}" for i in range(count)) + " -b x"}}


def python_calls(count, phase):
    action = url_action(count)
    classification = gate.classify(action)
    policy = gate.normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}}})
    calls = 0

    def profile(frame, event, arg):
        nonlocal calls
        if event == "call" and frame.f_code.co_filename == gate.__file__:
            calls += 1

    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        gate.classify(action) if phase == "classify" else gate.decide(classification, policy)
    finally:
        sys.setprofile(previous)
    return calls


@pytest.mark.xfail(strict=True, reason="Open #550: deciding many URL operands has superlinear Python work")
def test_url_operand_decisions_have_a_bounded_work_growth():
    small, large = python_calls(8, "decide"), python_calls(32, "decide")
    # Four times the input allows six times the work plus fixed overhead. This
    # deterministic proxy is not a wall-clock or end-to-end performance claim.
    assert large <= 6 * small + 200, (small, large)


def test_url_classification_work_control():
    small, large = python_calls(8, "classify"), python_calls(32, "classify")
    assert large <= 6 * small + 200, (small, large)


def compare_pair(tmp_path, base, head):
    _stream(tmp_path / "base.gz", [["read", "", None, [_d(base)], [0], 0]], ["p"])
    _stream(tmp_path / "head.gz", [["read", "", None, [_d(head)], [0], 0]], ["p"])
    return gate_diff.compare(tmp_path / "base.gz", tmp_path / "head.gz", [url_action(1)])


@pytest.mark.parametrize("base,unknown", [("ask", "Ask"), ("allow", "block")])
def test_unknown_outcomes_are_not_reported_as_safe_or_tighter(tmp_path, base, unknown):
    try:
        result = compare_pair(tmp_path, base, unknown)
    except (ValueError, gate_diff.Failed) as error:
        assert any(word in str(error).lower() for word in ("outcome", "decision")), error
        return  # Refusing malformed output is an acceptable fail-closed response.
    counts = result["counts"]
    if counts.get("head_decide_errors"):
        return
    if base == "ask":
        assert counts["lowered_pairs"] == 1, counts  # The hook runs an unrecognized outcome.
    else:
        assert counts["tightened_pairs"] == 0, counts


@pytest.mark.parametrize("base,head,lowered,tightened", [("ask", "allow", 1, 0), ("allow", "deny", 0, 1)])
def test_valid_comparison_outcomes_keep_their_order(tmp_path, base, head, lowered, tightened):
    counts = compare_pair(tmp_path, base, head)["counts"]
    assert (counts["lowered_pairs"], counts["tightened_pairs"]) == (lowered, tightened)


def long_store_budget_line():
    return "; ".join(f"cd ~/.exo/d{i}" for i in range(70)) + "; cat seat.db"


def reaches_reported_store_cap(action):
    classification = gate.classify(action)
    return any("too many to resolve" in part.summary + part.target
               for reading in gate.readings(classification) for part in gate._each_part(reading))


def test_builtin_corpus_reaches_the_store_folder_budget_family():
    actions, _ = gate_diff.build_corpus([ROOT])
    candidates = [a for a in actions if isinstance(a.get("args"), dict) and any(
        isinstance(text, str) and text.count("cd ") >= 65 and text.count(".exo") >= 65
        for text in a["args"].values())]
    assert any(reaches_reported_store_cap(action) for action in candidates), (
        "No built-in action reaches the reported repeated-store-folder cap family")


def test_explicit_budget_seed_detects_the_fail_open_mutant(tmp_path):
    action = {"tool": "bash", "args": {"command": long_store_budget_line()}}
    assert reaches_reported_store_cap(action)
    classification = gate.classify(action)
    assert gate.decide(classification, gate.normalize_policies({})).outcome == "ask"
    assert gate.decide(classification, gate.normalize_policies({"gates": {"categories": {
        "credentials": "deny"}}})).outcome == "deny"
    base = _tree(tmp_path, "base")
    source = (base / "src/exo/gate.py").read_text()
    start = source.index("def _exo_relative(")
    end = source.index("\ndef ", start + 5)
    function = source[start:end]
    assert function.count("return _TOO_MANY") == 2
    mutant = _tree(tmp_path, "mutant", (function, function.replace("return _TOO_MANY", 'return ""')))
    corpus = tmp_path / "budget.json"
    corpus.write_text(json.dumps([long_store_budget_line()]))
    process, report = _gate_diff(base, mutant, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                                "--pick", "default", "--jobs", "1")
    assert process.returncode == 1, process.stdout + process.stderr
    assert report["counts"]["lowered_pairs"] >= 1, report["counts"]
