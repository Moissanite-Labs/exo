"""Issue #329: `unset PATH` makes later commands on the line run from the working folder.

With PATH unset, bash looks a command up in the working folder, so `ls` runs `./ls`, which an
earlier local write can plant. #231 already asks on the assignment forms (`PATH= ls`,
`PATH=$PATH: ls`); `unset` of PATH amounts to the same assignment and is read the same way,
`opaque_exec` named in the summary. `unset -f` names functions, not variables, and stays read.

Every command string here is data for exo.gate.classify/decide; none is run."""
import pytest

from exo import gate


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _bash(cmd: str):
    return gate.classify({"tool": "bash", "args": {"command": cmd}})


@pytest.mark.parametrize("cmd", [
    # read/allow on main 18b229b3
    "unset PATH; ls",
    "unset -v PATH; rg x",
    "declare +x PATH; unset PATH; ls",
    "unset PATH",
    "unset -- PATH; ls",
    "unset PATH HOME; ls",
    "unset -v PATH FOO; rg x",
    "unset 'PATH'; ls",
    "unset -n PATH; ls",
    "unset PATH && ls",
    "builtin unset PATH; ls",
    "command unset PATH; ls",
])
def test_unset_path_asks_as_the_empty_assignment_does(cmd):
    c = _bash(cmd)
    assert c.category == "opaque_exec" and "PATH" in c.summary, c
    assert gate.decide(c).outcome == "ask", c


@pytest.mark.parametrize("cmd", [
    "unset FOO; ls",          # an unlisted variable changes nothing the gate relies on
    "unset PATHX; ls",        # names that merely contain PATH are not PATH
    "unset XPATH; ls",
    "unset -f PATH; ls",      # -f unsets a function named PATH, not the variable
    "echo unset PATH",        # words, not the builtin
    "ls",
])
def test_other_unsets_stay_read(cmd):
    c = _bash(cmd)
    assert (c.category, gate.decide(c).outcome) == ("read", "allow"), c
