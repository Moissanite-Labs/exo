"""Issue #465: after `cd` into the store, a write inside a $( ), backticks or <( ) on the line is a store write.

The `cd` context (store_cwd, fd_cwd) lived in one line's classification only, and each $( ) was classified as a
fresh line, so `cd ~/.exo/policies && echo $(cp /tmp/evil gates.yaml)` was an allowed read while the unwrapped
`cp` asked. Every command string here is data for exo.gate.classify/decide; none is run."""
import pytest

from exo import gate


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo" / "policies").mkdir(parents=True)
    (home / ".exo" / "state").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_HOME", "EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _bash(cmd):
    return gate.classify({"tool": "bash", "args": {"command": cmd}})


# The issue's rows: read allow (local_write allow for the last) on main 3ddd89e9.
ROWS = [
    "cd ~/.exo/policies && echo $(cp /tmp/evil gates.yaml)",
    "cd ~/.exo/policies && x=$(cp /tmp/evil gates.yaml)",
    "cd ~/.exo/policies && cat <(cp /tmp/evil gates.yaml)",
    "cd ~/.exo/policies && echo `cp /tmp/evil gates.yaml`",
    'cd ~/.exo/state && echo "$(cp /tmp/g.json approval-grants.json)"',
    "cd ~/.exo/policies && echo $(echo x > gates.yaml)",
    # nested, and other places a substitution runs
    "cd ~/.exo/policies && echo $(echo $(cp /tmp/evil gates.yaml))",
    "cd ~/.exo/policies && diff <(cp /tmp/evil gates.yaml) x",
    "cd ~/.exo/policies; echo $(cp /tmp/evil gates.yaml)",
    "pushd ~/.exo/state && echo $(cp /tmp/g.json approval-grants.json)",
]


@pytest.mark.parametrize("line", ROWS)
def test_a_write_in_a_substitution_after_cd_into_the_store_asks_the_principal(line):
    c = _bash(line)
    assert c.category == "store_write", (line, c.category)
    d = gate.decide(c)
    assert (d.outcome, d.rule) == ("ask", "floor"), (line, d)
    lifted = gate.normalize_policies({"gates": {"categories": {"store_write": "allow", "local_write": "allow"}}})
    assert gate.decide(c, lifted).outcome == "ask", line             # no policy lowers the store floor
    assert gate.approvers_for(c.category, {"default": ["seat:lead"], "categories": {"store_write": ["seat:lead"]}}) \
        == ["principal"]
    assert gate.decide(c, gate.normalize_policies({"gates": {"deny": ["store_write"]}})).outcome == "deny", line


@pytest.mark.parametrize("line", ["cd /dev && echo $(cp /tmp/evil fd/3)", "cd /proc/self/fd && x=$(cp /tmp/evil 3)"])
def test_a_substitution_after_cd_into_a_descriptor_folder_is_a_store_write_there(line):
    c = _bash(line)
    assert c.category == "store_write", (line, c)
    assert any(p.category == "store_write" and p.target in ("/dev", "/proc/self/fd") for p in gate._each_part(c)), c
    d = gate.decide(c, gate.normalize_policies({"gates": {"deny": ["target:/dev*", "target:/proc*"]}}))
    assert d.outcome == "deny", (line, d)


@pytest.mark.parametrize("line,cat", [
    ("cd ~/.exo/policies && echo $(ls)", "read"),                     # reads in the store stay reads
    ("cd ~/.exo/policies && echo $(cat gates.yaml)", "read"),
    ("cd ~/.exo/policies && git log -n1 $(git rev-parse HEAD)", "read"),
    ("cd ~/project && echo $(cp a b)", "read"),                       # not a store folder
    ("echo $(cp a b)", "read"),
    ('git commit -m "$(date)"', "local_write"),
])
def test_controls_keep_their_class(line, cat):
    assert _bash(line).category == cat, line


def test_a_substitution_text_read_both_outside_and_inside_the_store_on_one_line():
    # within one call each text is classified once per depth (#334): the same $( ) at the same depth, first outside
    # the store and then in it, is read again there, and the first reading does not stand in for the second
    c = _bash("bash -c 'echo $(cp a b)'; bash -c 'cd ~/.exo/policies && echo $(cp a b)'")
    assert c.category == "store_write", c
    assert _bash("bash -c 'echo $(cp a b)'; bash -c 'cd ~/project && echo $(cp a b)'").category != "store_write"


def test_order_and_subshells_are_not_followed():
    # as for the line's own commands after `cd` (D-038), the gate does not follow the order or a subshell: it fails
    # closed, so a $( ) before the `cd` counts too
    for line in ("echo $(cp /tmp/evil gates.yaml); cd ~/.exo/policies",
                 "(cd ~/.exo/policies) && echo $(cp /tmp/evil gates.yaml)"):
        assert _bash(line).category == "store_write", line
