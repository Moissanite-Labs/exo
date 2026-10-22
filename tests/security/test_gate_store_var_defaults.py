"""#202: a store path written with a default (${EXO_HOME:-$HOME/.exo}/keys) is read as bash reads it.

Both the variable's value and its default count. Every check classifies; nothing is run."""
import time

import pytest

from exo import gate

POL = gate.DEFAULT_POLICIES
STORE = {"gates": {"categories": {"store_write": "allow"}}}


def outcome(command, policy=POL):
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    return c.category, gate.decide(c, policy).outcome


@pytest.fixture(params=[None, "/srv/exo-home"], ids=["EXO_HOME unset", "EXO_HOME set"])
def env(request, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    if request.param:
        monkeypatch.setenv("EXO_HOME", request.param)
    else:
        monkeypatch.delenv("EXO_HOME", raising=False)
    return request.param


@pytest.mark.parametrize("line", [
    "cat ${EXO_HOME:-$HOME/.exo}/keys/seat",
    "tar czf - ${EXO_HOME:-$HOME/.exo}/keys",
    "cp -r ${EXO_HOME:-$HOME/.exo}/keys /tmp/k",
    "cat ${EXO_HOME-$HOME/.exo}/keys/seat",
    "cat ${EXO_HOME:=$HOME/.exo}/keys/seat",
    "cat ${EXO_HOME=$HOME/.exo}/keys/seat",
    "cat ${EXO_HOME:?not set}/keys/seat",
    "cat ${EXO_HOME:-~/.exo}/keys/seat",
    "cat ${EXO_HOME:-${HOME}/.exo}/keys/seat",
    'cat "${EXO_HOME:-$HOME/.exo}/keys/seat"',
    'cat ${EXO_HOME:-"$HOME/.exo"}/keys/seat',
    "cat ${EXO_HOME:-${HOME:-/nowhere}/.exo}/keys/seat",
    "ls ${EXO_HOME:-$HOME/.exo}/run",
])
def test_a_default_spelling_of_the_store_asks(env, line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat $EXO_HOME/keys/seat",
    "tar czf - $EXO_HOME/keys",
    "cp -r ~/.exo/keys /tmp/k",
])
def test_controls_keep_asking(env, line):
    assert outcome(line) == ("credentials", "ask")


def test_the_file_tools_read_the_default_too(env):
    for path in ("${EXO_HOME:-$HOME/.exo}/keys/seat", "${EXO_HOME:-~/.exo}/keys"):
        c = gate.classify({"tool": "read_file", "args": {"path": path}})
        assert (c.category, gate.decide(c, POL).outcome) == ("credentials", "ask")
    c = gate.classify({"tool": "write_file", "args": {"path": "${EXO_HOME:-$HOME/.exo}/policies/x.yaml", "c": "x"}})
    assert c.category == "store_write"


def test_the_value_and_the_default_both_count(tmp_path, monkeypatch):
    """With EXO_HOME set, `${EXO_HOME:-/elsewhere}` reads EXO_HOME; when the tool's shell has no EXO_HOME it reads the
    default. Both ask. `:+` is the word when set and nothing when unset: the word counts."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo"))
    assert outcome(f"cat ${{EXO_HOME:-{tmp_path}/x}}/keys/seat") == ("credentials", "ask")       # the value
    assert outcome("cat ${EXO_HOME:-/srv/a/.exo}/keys/seat") == ("credentials", "ask")            # the default
    assert outcome("cat ${EXO_HOME:+$HOME/.aws}/config") == ("credentials", "ask")
    assert outcome("cat ${EXO_HOME%/}/keys/seat") == ("credentials", "ask")                       # other operators
    assert outcome("cat ${HOME:-/nowhere}/.aws/credentials") == ("credentials", "ask")
    assert outcome("rm -rf ${EXO_HOME:-$HOME/.exo}/policies", STORE)[1] == "ask"


def test_other_variables_and_plain_paths_are_unchanged(env):
    assert outcome("cat ${OUT:-/tmp/out}/report.txt") == ("read", "allow")
    assert outcome("cat ${HOME:-/nowhere}/notes.txt") == ("read", "allow")
    assert outcome("ls ${EXO_HOME:-$HOME/.exo}/skills") == ("read", "allow")


def test_deep_nesting_is_bounded():
    line = "cat " + "${HOME:-" * 3000 + "x" + "}" * 3000 + "/.ssh/id_rsa"
    start = time.process_time()
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    assert time.process_time() - start < 5
    assert c.category == "credentials"
