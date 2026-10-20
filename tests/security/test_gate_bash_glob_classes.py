"""#199: a secret-store glob spelt with bash's `[^x]` or a `[[:class:]]` is read as bash reads it.

Every check classifies; nothing is run."""
import pytest

from exo import gate

POL = gate.DEFAULT_POLICIES


def outcome(command):
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    return c.category, gate.decide(c, POL).outcome


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("EXO_HOME", raising=False)


@pytest.mark.parametrize("line", [
    "cat ~/.kube/[^x]onfig",
    "cat ~/.kube/conf[^x]g",
    "cat ~/.kube/[[:alpha:]]onfig",
    "cat ~/.docker/config.[^x]son",
    "cat ~/.config/gh/hosts.[^x]ml",
    "cat /proc/self/[!x]nviron",
    "cat /proc/1/[^x]nviron",
    "cat /proc/*/[!x]nviron",
    "cat /proc/self/environ*",
    "cat ~/.config/gh/hosts.[[:lower:]]ml",
    "cat ~/.kube/[[:bogus:]]onfig",             # a class bash does not know: fails closed
])
def test_a_bash_glob_of_a_store_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat ~/.kube/[!x]onfig",
    "cat ~/.config/gh/hosts.[!x]ml",
    "cat /proc/self/environ",
])
def test_controls_keep_asking(line):
    assert outcome(line) == ("credentials", "ask")


def test_the_file_tools_read_bash_globs_too():
    c = gate.classify({"tool": "read_file", "args": {"path": "~/.kube/[^x]onfig"}})
    assert (c.category, gate.decide(c, POL).outcome) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "ls src/[^_]*.py",
    "cat docs/[[:upper:]]*.md",
    "ls /proc/*/status",
    "cat /proc/self/[s]tatus",
])
def test_ordinary_globs_keep_their_class(line):
    assert outcome(line) == ("read", "allow")
