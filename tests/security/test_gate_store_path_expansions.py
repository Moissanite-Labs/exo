"""Issue #309: an expansion inside an Exo store path hides the store from the write check.

Bash drops an empty substitution and expands an unset variable to nothing, so
`cp x ~/.exo/polic$(:)ies/gates.yaml` writes the policy file while the word, read literally,
names no store path. The store checks now also match each word with an expansion it cannot
spell out read as empty (and as `*`), the reading #307 added for the secret-store checks.

Every command string here is data for exo.gate.classify/decide; none is run."""
import pytest

from exo import gate


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo" / "policies").mkdir(parents=True)
    (home / ".exo" / "policies" / "gates.yaml").write_text("")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _outcome(action):
    c = gate.classify(action)
    return c.category, gate.decide(c).outcome


def _bash(cmd):
    return {"tool": "bash", "args": {"command": cmd}}


@pytest.mark.parametrize("cmd", [
    # shell, and allow, on main 18b229b3
    "cp x ~/.exo/polic$(:)ies/gates.yaml",
    "cp x ~/.exo/polic``ies/gates.yaml",
    "mv x ~/.exo/polic${u}ies/gates.yaml",
    "tee ~/.exo/polic$(:)ies/gates.yaml < x",
    "cp x $HOME/.exo/polic$(:)ies/gates.yaml",
    "cp x ${EXO_HOME}/polic$(:)ies/gates.yaml",
    "cp x ~/.exo/polic$(:)ies/gates.yaml && echo done",
    "sed -i s/a/b/ ~/.exo/st$(:)ate/gate-grants.json",
    "echo x > ~/.exo/polic$(:)ies/gates.yaml",   # a redirection target: asked before, as opaque
])
def test_expansion_inside_a_store_path_is_a_store_write(cmd):
    assert _outcome(_bash(cmd)) == ("store_write", "ask")


@pytest.mark.parametrize("cmd,expected", [
    # an expansion outside a store name is not the store's business
    ("cp x out/$(date +%F).yaml", ("shell", "allow")),
    ("cp x out/f$(:)ile.yaml", ("shell", "allow")),
    ("echo $(date) > out.txt", ("local_write", "allow")),
    # a read of the store is fine, expansion or not
    ("cat ~/.exo/polic$(:)ies/gates.yaml", ("read", "allow")),
    ("cat ~/.exo/policies/gates.yaml", ("read", "allow")),
])
def test_expansion_outside_a_store_path_is_unchanged(cmd, expected):
    assert _outcome(_bash(cmd)) == expected


def test_the_plain_store_path_still_asks():
    assert _outcome(_bash("cp x ~/.exo/policies/gates.yaml")) == ("store_write", "ask")
