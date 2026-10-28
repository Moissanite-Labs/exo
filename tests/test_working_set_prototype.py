"""Offline selection invariants; these tests make no inference calls."""
from dataclasses import replace
import json
from pathlib import Path
import runpy

import pytest

from exo.working_set import MAX_CANDIDATES, Record, WorkingSet, estimate


def record(ident, text="alpha build evidence", **kwargs):
    return Record(ident, "history", "agent", text, visibility="shared", thread_kind="thread", **kwargs)


def ids(selection):
    return [r.id for r in selection.records]


def test_private_protected_ancestry_and_other_agent_never_enter_even_when_pinned():
    public = record("public")
    blocked = [replace(public, id="private-secret", text="SECRET PRIVATE", visibility="private"),
               replace(public, id="protected-secret", text="SECRET PROTECTED", visibility="protected"),
               replace(public, id="child-secret", text="SECRET CHILD", private_ancestry=True),
               replace(public, id="other-agent-secret", text="SECRET OTHER", agent="other"),
               replace(public, id="subagent-secret", text="SECRET SUBAGENT", thread_kind="subagent"),
               replace(public, id="unknown-secret", text="SECRET UNKNOWN", visibility="unknown")]
    state = WorkingSet("current", "agent").adjust(pin=[r.id for r in blocked])
    selected = state.select("alpha", [public, *blocked])
    assert ids(selected) == ["public"]
    assert "SECRET" not in repr(selected) and "secret" not in repr(selected)


def test_unknown_visibility_is_excluded_by_default():
    source = Record("id", "history", "agent", "alpha")
    assert not WorkingSet("current", "agent").select("alpha", [source]).records


def test_query_change_refreshes_refs_without_reusing_stale_text_or_current_transcript():
    alpha, beta = record("alpha", "alpha build"), record("beta", "beta deploy")
    state = WorkingSet("current", "agent")
    first = state.select("alpha", [alpha, beta])
    second = state.select("beta", [alpha, beta], previous=first)
    assert ids(first) == ["alpha"] and ids(second) == ["beta"]
    assert not state.select("alpha", [alpha, beta], visible_ids=["alpha"]).records
    assert not state.select("alpha", [beta], previous=first).records
    changed = replace(alpha, text="alpha revised source")
    revised = state.select("alpha", [changed], previous=first)
    assert revised.records[0].text == changed.text
    assert revised.records[0].fingerprint != first.records[0].fingerprint


def test_adjustments_are_inspectable_reversible_and_conversation_local():
    alpha, beta = record("alpha", "alpha build"), record("beta", "beta deploy")
    original = WorkingSet("current", "agent")
    changed = original.adjust(pin=["beta"], exclude=["alpha"])
    assert ids(changed.select("alpha", [alpha, beta])) == ["beta"]
    assert ids(original.select("alpha", [alpha, beta])) == ["alpha"]
    restored = changed.adjust(unpin=["beta"], restore=["alpha"])
    assert restored == original
    previous = changed.select("alpha", [alpha, beta])
    with pytest.raises(ValueError, match="another conversation"):
        WorkingSet("different", "agent").select("alpha", [alpha, beta], previous=previous)


def test_relevant_principal_correction_gets_priority_without_changing_authorship():
    agent = record("agent-note", "alpha alpha evidence", ordinal=20, author="agent", correction=True)
    principal = record("correction", "alpha correction", ordinal=1, author="principal", correction=True)
    selected = WorkingSet("current", "agent", max_items=1).select("alpha", [agent, principal])
    assert ids(selected) == ["correction"] and selected.records[0].author == "principal"
    assert not WorkingSet("current", "agent").select("unrelated", [principal]).records


def test_budget_skips_oversized_entry_and_keeps_smaller_verbatim_reference():
    big, small = record("big", "alpha " * 400), record("small", "alpha exact quote")
    state = WorkingSet("current", "agent", token_budget=estimate(small))
    chosen = state.select("alpha", [big, small])
    assert ids(chosen) == ["small"] and chosen.records[0].text == "alpha exact quote"
    assert chosen.estimated_tokens <= state.token_budget and chosen.over_budget == 1


def test_conflicting_identity_is_not_silently_attributed_to_one_source():
    original = record("same")
    assert not WorkingSet("current", "agent").select("alpha", [original, replace(original, text="alpha changed")]).records
    assert ids(WorkingSet("current", "agent").select("alpha", [original, original])) == ["same"]


def test_iterable_input_is_bounded_and_output_order_deterministic():
    visited = []
    def candidates():
        for i in range(MAX_CANDIDATES + 1):
            visited.append(i)
            yield record(f"r{i}", ordinal=i)
        raise AssertionError("selector consumed beyond its advertised bound")
    state = WorkingSet("current", "agent", max_items=3)
    chosen = state.select("alpha", candidates())
    assert chosen.input_truncated and len(visited) == MAX_CANDIDATES + 1
    assert ids(chosen) == ["r255", "r254", "r253"]
    same = [record("b", ordinal=1), record("a", ordinal=1)]
    assert state.select("alpha", same) == state.select("alpha", reversed(same))


@pytest.mark.parametrize("kwargs", [{"token_budget": True}, {"token_budget": 0}, {"max_items": 33},
                                    {"pinned": ["x"] * 129}, {"session": "x" * 161}])
def test_invalid_budgets_and_adjustments_fail_explicitly(kwargs):
    args = {"session": "current", "agent": "agent", **kwargs}
    with pytest.raises(ValueError):
        WorkingSet(**args)


def test_source_size_and_unicode_cost_are_explicit():
    with pytest.raises(ValueError):
        record("oversize", "x" * 6001)
    ascii_source, unicode_source = record("same", "xxxx"), record("same", "😀😀😀😀")
    assert estimate(unicode_source) > estimate(ascii_source)


def test_offline_fixture_reports_task_changes_privacy_and_a_failure_case():
    root = Path(__file__).resolve().parents[1]
    evaluate = runpy.run_path(str(root / "research/working_set/evaluate.py"))["evaluate"]
    data = json.loads((root / "research/working_set/fixture.json").read_text())
    report = evaluate(data)
    assert report == evaluate(data)
    alpha, beta, synonym, private = report["tasks"]
    assert alpha["selector"]["recall"] == beta["selector"]["recall"] == 1
    assert beta["selector"]["selected"] == ["beta-proof"]
    assert synonym["selector"]["recall"] == 0
    assert private["selector"]["selected"] == []
    serialized = json.dumps(report)
    assert "private-bait" not in serialized and "PRIVATE_DATA" not in serialized and "PROTECTED_DATA" not in serialized
    assert all(task["selector"]["estimated_tokens"] <= data["token_budget"] for task in report["tasks"])
