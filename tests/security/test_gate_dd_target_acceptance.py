"""#568 regression contracts: dd operand prefixes never hide path-target policy matches.

All documented shapes already pass on main f28934a3. These are ordinary guards,
not strict-xfail gaps or a claim this acceptance draft implements a runtime fix.
No shell command string executes.
"""
import shlex

import pytest

from exo import gate

OPERANDS = [
    "dd if=~/.exo/keys/seat.key of=/tmp/a",
    "dd if=~/.exo//keys/seat.key of=/tmp/a",
    "dd if=/tmp/a of=~/.exo/policies/gates.yaml",
    "dd if=/tmp/a of=~/.exo/./plugins/p/x.py",
]


def inspect(line, policy):
    return gate.decide(gate.classify({"tool": "bash", "args": {"command": line}}),
                       gate.normalize_policies({"gates": policy}))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    (tmp_path / ".exo").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EXO_HOME", str(tmp_path / ".exo"))
    for name in ("EXO_PRINCIPAL_STORE", "EXO_KEYS_FILE", "EXO_VENV", "EXO_PLUGIN_DIRS"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("operand", OPERANDS)
@pytest.mark.parametrize("wrapper", ["direct", "sudo", "bash"])
@pytest.mark.parametrize("policy_kind", ["deny", "reserved", "rule"])
def test_568_dd_operand_paths_match_target_policy(operand, wrapper, policy_kind):
    line = operand if wrapper == "direct" else "sudo " + operand if wrapper == "sudo" else "bash -c " + shlex.quote(operand)
    policy = ({"rules": [{"match": {"target": "~/.exo/*"}, "decision": "deny"}]} if policy_kind == "rule"
              else {policy_kind: ["target:~/.exo/*"]})
    result = inspect(line, policy)
    assert result.outcome == ("ask" if policy_kind == "reserved" else "deny"), result
    if policy_kind == "reserved":
        assert result.reserved and any("/.exo/" in target and not target.startswith(("if=", "of="))
                                       for _, target in result.reserved), result


@pytest.mark.parametrize("kind", ["deny", "reserved", "rule"])
def test_568_any_store_on_multistore_read_keeps_its_target_match(kind):
    policy = ({"rules": [{"match": {"target": "*/.ssh/*"}, "decision": "deny"}]} if kind == "rule"
              else {kind: ["target:*/.ssh/*"]})
    result = inspect("cat ~/.kube/config ~/.ssh/id_rsa", policy)
    assert result.outcome == ("ask" if kind == "reserved" else "deny"), result
    if kind == "reserved":
        assert any("/.ssh/" in target for _, target in result.reserved), result


def test_568_matching_copy_control_is_denied():
    assert inspect("cp ~/.exo/keys/seat.key /tmp/a", {"deny": ["target:~/.exo/*"]}).outcome == "deny"


def test_568_unrelated_dd_does_not_acquire_a_store_target():
    result = inspect("dd if=/tmp/a of=/tmp/b", {"reserved": ["target:~/.exo/*"]})
    assert result.reserved == [], result
