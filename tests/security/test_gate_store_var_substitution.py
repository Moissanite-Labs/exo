"""#342: a pattern substitution on a store variable can add a store folder the plain value does not spell.

`${HOME/%//.kube}` is `$HOME/.kube` in bash: the `%` pattern matches at the end of the value and the
replacement is appended. The replacement counts — appended for a `%` pattern, prepended for a `#` pattern,
and on its own for an unanchored pattern, which may match the whole value. Every check classifies; nothing
is run."""
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
    "cat ${HOME/%//.kube}/config",
    "cat ${HOME/%//.config}/gh/hosts.yml",
    "cat ${HOME/%//.n}etrc",
    "cat ${HOME/%//.exo}/keys/seat",
    "cat ${EXO_HOME/%//keys}/seat",
    "cd ${HOME/%//.kube} && cat config",
    "cat ${HOME/$HOME/.kube/config}",          # an unanchored pattern matching the whole value
    "cat ${HOME/$HOME/.kube}/config",
    "cat ${HOME/#$HOME/~}/.kube/config",       # a `#` pattern: the replacement is prepended
    "ls ${EXO_HOME/%//run}",
])
def test_a_substitution_spelling_of_the_store_asks(env, line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat ${HOME}/.kube/config",
    "cat ${HOME%/}/.kube/config",
    "cat ${HOME//x/y}/.kube/config",
    "cat ${HOME/%//.ssh}/id_rsa",
    "cat ${HOME:0:2}/.kube/config",            # operators that only remove text still read as the value
    "cat ${HOME##*/}/.kube/config",
])
def test_controls_keep_asking(env, line):
    assert outcome(line) == ("credentials", "ask")


def test_a_substituted_store_write_asks(env):
    assert outcome("cp x ${HOME/%//.exo}/policies/default.yaml") == ("store_write", "ask")
    assert outcome("rm -rf ${HOME/%//.exo}/policies", STORE)[1] == "ask"


def test_the_file_tools_read_the_substitution_too(env):
    c = gate.classify({"tool": "read_file", "args": {"path": "${HOME/%//.kube}/config"}})
    assert (c.category, gate.decide(c, POL).outcome) == ("credentials", "ask")
    c = gate.classify({"tool": "write_file",
                       "args": {"path": "${HOME/%//.exo}/policies/x.yaml", "c": "x"}})
    assert c.category == "store_write"


def test_non_store_substitutions_are_unchanged(env):
    assert outcome("cp x ${HOME/%//backup}/a") == ("shell", "allow")
    assert outcome("echo ${PWD/#$HOME/~}") == ("read", "allow")
    assert outcome("cat ${HOME//x/y}/notes.txt") == ("read", "allow")
    assert outcome("cat ${HOME/%//notes}/x.txt") == ("read", "allow")


def test_many_substitutions_are_bounded():
    line = "cat " + "${HOME/%//.kube}" * 3000 + "/config"
    start = time.process_time()
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    assert time.process_time() - start < 5
    assert c.category == "credentials"
