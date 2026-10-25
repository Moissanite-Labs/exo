"""Malformed escalation policy must not silently retain defaults or use substring membership (#542)."""
import pytest

from exo import gate


@pytest.mark.parametrize("value", [[], ["read"], "read", "", False, 0, None])
def test_escalate_requires_a_mapping(value):
    with pytest.raises(gate.PolicyError, match=r"gates\.escalate must be a mapping"):
        gate.normalize_policies({"gates": {"escalate": value}})


@pytest.mark.parametrize("value", ["read", "", {"read": True}, False, 0, None])
def test_escalate_categories_requires_a_list(value):
    with pytest.raises(gate.PolicyError, match=r"gates\.escalate\.categories must be a list"):
        gate.normalize_policies({"gates": {"escalate": {"categories": value}}})


@pytest.mark.parametrize("yaml, error", [
    ("escalate: [read]\n", r"gates\.escalate must be a mapping"),
    ("escalate: []\n", r"gates\.escalate must be a mapping"),
    ("escalate: null\n", r"gates\.escalate must be a mapping"),
    ("escalate:\n  categories: read\n", r"gates\.escalate\.categories must be a list"),
    ("escalate:\n  categories: null\n", r"gates\.escalate\.categories must be a list"),
])
def test_store_policy_load_rejects_malformed_escalation(tmp_path, yaml, error):
    policies = tmp_path / "policies"
    policies.mkdir()
    (policies / "gates.yaml").write_text(yaml)
    with pytest.raises(gate.PolicyError, match=error):
        gate.load_policies(tmp_path)


@pytest.mark.parametrize("raw", [{}, {"gates": {}}, {"gates": {"escalate": {}}}])
def test_omitted_escalation_values_keep_defaults(raw):
    assert gate.normalize_policies(raw)["gates"]["escalate"] == gate.DEFAULT_POLICIES["gates"]["escalate"]


@pytest.mark.parametrize("categories, outcome", [([], "allow"), (["read"], "ask"), (["thread"], "allow")])
def test_categories_are_whole_names_and_an_empty_list_disables_category_escalation(tmp_path, categories, outcome):
    policies = tmp_path / "policies"
    policies.mkdir()
    (policies / "gates.yaml").write_text("escalate:\n  categories: [" + ", ".join(categories) + "]\n")
    policy = gate.load_policies(tmp_path)
    assert policy["gates"]["escalate"]["categories"] == categories
    assert gate.decide(gate.classify(("terminal", {"command": "ls"})), policy).outcome == outcome


def test_valid_partial_escalation_keeps_unspecified_defaults():
    result = gate.normalize_policies({"gates": {"escalate": {"third_party": False}}})["gates"]["escalate"]
    assert result == {**gate.DEFAULT_POLICIES["gates"]["escalate"], "third_party": False}
