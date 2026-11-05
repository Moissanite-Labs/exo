"""A malformed escalate keeps the principal's other sections (#557).

Given an error list, normalize_policies and load_policies record a malformed `gates.escalate` (or its `categories`)
with the PolicyError's message and repair only that section: the default escalation plus the names it gives. Every
other section is read as before, and any other policy error still raises. The exo-gate hook decides under the
repaired policy and the defaults and keeps the stricter (plugins/exo-gate/tests/test_hook.py).
"""
import pytest

from exo import gate

DEFAULT = gate.DEFAULT_POLICIES["gates"]["escalate"]
MAPPING, CATEGORIES = "gates.escalate must be a mapping", "gates.escalate.categories must be a list"

# value of `escalate`, the error, the categories the repaired section escalates
SHAPES = [
    (["payment"], MAPPING, DEFAULT["categories"]),
    (["read"], MAPPING, DEFAULT["categories"] + ["read"]),
    ([], MAPPING, DEFAULT["categories"]),
    (None, MAPPING, DEFAULT["categories"]),
    (False, MAPPING, DEFAULT["categories"]),
    (0, MAPPING, DEFAULT["categories"]),
    ("payment", MAPPING, DEFAULT["categories"]),
    ("read", MAPPING, DEFAULT["categories"] + ["read"]),        # a top-level string's names count too
    ({"categories": "payment"}, CATEGORIES, DEFAULT["categories"]),
    ({"categories": "read"}, CATEGORIES, DEFAULT["categories"] + ["read"]),
    ({"categories": None}, CATEGORIES, DEFAULT["categories"]),
    ({"categories": {"read": True}}, CATEGORIES, DEFAULT["categories"] + ["read"]),
]
TIGHTENINGS = [
    ({"deny": ["delete"]}, "rm -rf build", "deny"),
    ({"reserved": ["vcs_push"]}, "git push origin main", "ask"),
    ({"categories": {"read": "deny"}}, "ls", "deny"),
    ({"rules": [{"match": {"category": "read"}, "decision": "deny"}]}, "ls", "deny"),
    ({"matrix": {"reversible": {"private": "ask"}}}, "touch x", "ask"),
]


@pytest.mark.parametrize("value, error, categories", SHAPES)
def test_a_malformed_escalate_is_recorded_and_repaired(value, error, categories):
    errors: list[str] = []
    pol = gate.normalize_policies({"gates": {"escalate": value}}, errors)
    assert errors == [error]
    assert pol["gates"]["escalate"] == {**DEFAULT, "categories": categories}
    with pytest.raises(gate.PolicyError, match=error):     # #549: a caller without a list still gets the error
        gate.normalize_policies({"gates": {"escalate": value}})


def test_a_mapping_keeps_its_other_keys_beside_repaired_categories():
    errors: list[str] = []
    esc = gate.normalize_policies({"gates": {"escalate": {"third_party": False, "categories": "read"}}},
                                  errors)["gates"]["escalate"]
    assert errors == [CATEGORIES]
    assert esc == {**DEFAULT, "third_party": False, "categories": DEFAULT["categories"] + ["read"]}


@pytest.mark.parametrize("value, error, categories", SHAPES)
@pytest.mark.parametrize("tight, line, outcome", TIGHTENINGS)
def test_the_other_sections_stay_in_force(value, error, categories, tight, line, outcome):
    errors: list[str] = []
    pol = gate.normalize_policies({"gates": {**tight, "escalate": value}}, errors)
    assert errors == [error]
    d = gate.decide(gate.classify(("terminal", {"command": line})), pol)
    assert d.outcome == outcome
    if "reserved" in tight:
        assert d.reserved


@pytest.mark.parametrize("raw, error", [
    ({"gates": {"escalate": [], "rules": "deny"}}, "gates.rules must be a list"),
    ({"gates": {"escalate": None, "deny": "delete"}}, "gates.deny must be a list"),
    ({"gates": {"escalate": {"categories": None}, "matrix": {"costly": {"public": "maybe"}}}}, "unknown decision"),
    ({"gates": {"escalate": "x", "categories": ["read"]}}, "gates.categories must be a mapping"),
])
def test_any_other_policy_error_still_raises(raw, error):
    with pytest.raises(gate.PolicyError, match=error):
        gate.normalize_policies(raw, [])


@pytest.mark.parametrize("raw", [{}, {"gates": {"escalate": {}}}, {"gates": {"escalate": {"categories": []}}},
                                 {"gates": {"deny": ["delete"], "escalate": {"third_party": False}}}])
def test_a_valid_policy_records_nothing_and_reads_as_before(raw):
    errors: list[str] = []
    assert gate.normalize_policies(raw, errors) == gate.normalize_policies(raw)
    assert errors == []


@pytest.mark.parametrize("yaml, error", [
    ("deny: [delete]\nescalate: [payment]\n", MAPPING),
    ("deny: [delete]\nescalate:\n  # categories: [payment]\n", MAPPING),
    ("deny: [delete]\nescalate:\n  categories:\n", CATEGORIES),
])
def test_store_load_records_the_error_and_keeps_the_scrub_names(tmp_path, yaml, error):
    (tmp_path / "policies").mkdir()
    (tmp_path / "policies" / "gates.yaml").write_text(yaml)
    (tmp_path / "policies" / "internal-names.txt").write_text("zebrafinch\n")
    errors: list[str] = []
    pol = gate.load_policies(tmp_path, errors)
    assert errors == [error]
    assert pol["gates"]["deny"] == ["delete"]
    assert "zebrafinch" in repr(pol["scrub"])


def test_store_load_still_raises_a_yaml_error_with_a_list(tmp_path):
    (tmp_path / "policies").mkdir()
    (tmp_path / "policies" / "gates.yaml").write_text("escalate: [payment\n")
    with pytest.raises(gate.PolicyError, match="gates.yaml"):
        gate.load_policies(tmp_path, [])
