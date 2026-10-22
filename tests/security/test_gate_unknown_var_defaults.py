"""#341: the default word of an unknown variable is read, not dropped (cat ${x:-.env}).

#307 reads `${VAR:-word}` and the other operators for the four store variables, and #225 reads any other
`${..}` as empty. The word of `${name OP word}` is in the command text, so the gate reads it too, for OP
`-`, `:-`, `=`, `:=`, `+` and `:+`: quotes dropped and spelt out like a store variable's word, to 8 levels.
A legacy `$[...]` counts as an expansion, and a plain number reads as itself. Every check classifies;
nothing is run."""
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
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.mark.parametrize("line", [
    # the issue's rows: with x unset the -, :- and := forms give the word, with x set the + forms do
    "cat ${x-.env}",
    "cat ${x:-.env}",
    "cat ${x:=.env}",
    "cat ${x:+.env}",
    'cat "${x:-.env}"',
    "cat ${x:-.e}nv",
    "cat ${x:-.env.local}",
    "cat ${x:-~/.netrc}",
    "cat ${x:-~/.n}etrc",
    "cat ${x:-~/.kube}/config",
    "cat ${x:-~/.kube/config}",
    "cat ${x:-$HOME/.exo}/keys/seat",
    # the word spelt out: quotes dropped, ~ and $HOME resolved, nested forms read in turn
    "cat ${x:-'~/.netrc'}",
    'cat ${x:-"$HOME/.ssh/id_rsa"}',
    "cat ${x:-~/.aws}/credentials",
    "cat ${x:-${y:-.env}}",
    "cat ${x:-.e}${y:-nv}",
    # a store folder or a store path under one, handed to tar
    "tar czf - ${x:-$HOME/.claude}",
    "tar czf - ${x:-$HOME/.exo}/keys",
    "tar czf - ${x:-$HOME/.kube}/config",
    # legacy arithmetic: a plain number is what bash prints
    "cat id_ed$[25519]",
    "cat .e$[110]nv",
])
def test_a_store_name_in_a_default_word_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat ${EXO_HOME:-$HOME/.exo}/keys/seat",   # control: a store variable's word, read since #307
    "cat .e${x:-n}v",                           # control: read as .e*v since #225
    "cat ${x:-~/.ssh/id_rsa}",                  # control: matched by file name
    "cat id_ed25519",                           # the $[..] row's control
])
def test_controls_keep_asking(line):
    assert outcome(line) == ("credentials", "ask")


def test_a_store_write_through_a_default_word_asks():
    c = gate.classify({"tool": "bash", "args": {"command": "cp x ${x:-$HOME/.exo}/policies/a"}})
    assert (c.category, gate.decide(c, POL).outcome) == ("store_write", "ask")


@pytest.mark.parametrize("line", [
    "cat ${OUT:-/tmp/out}/report.txt",   # the issue's should-stay row
    "cat ${1:-README.md}",               # a positional parameter's word counts too, and is no store
    "cat ${x:-notes}.md",
    "cat ${x:?error}",                   # ? is not one of the word operators: read as empty, as before
    "echo ${x:-hello}",
    "ls ${x:-/tmp}",
    # A bare .kube folder is no store path in any spelling (only .kube/config is), so the default
    # spelling classifies exactly like the plain one; the expansion no longer hides anything.
    "tar czf - ${x:-$HOME/.kube}",
    "tar czf - $HOME/.kube",
])
def test_ordinary_defaults_keep_their_class(line):
    assert outcome(line)[1] == "allow"
