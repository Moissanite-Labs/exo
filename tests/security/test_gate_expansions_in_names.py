"""#225: an expansion inside a secret-store name (cat .e$(:)nv) is read as bash may expand it.

Each command substitution, backtick pair or unknown variable is read as empty and, between two literal parts, as
any text (`*`). Every check classifies; nothing is run."""
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
    # the issue's rows and the skeptic pass's
    "cat .e$(:)nv",
    "cat ~/.n$(:)etrc",
    "cat ~/.config/gh/ho$(:)sts.yml",
    "cat $HOME/.kube/c$(:)onfig",
    "cat $HOME/.docker/con$(:)fig.json",
    "cat ~/.config/gh/$(:)*",
    "cat .e``nv",
    # the same with other empty or quoted substitutions
    "cat .e$( : )nv",
    "cat .e$(true)nv",
    "cat .e`:`nv",
    "cat '.e'$(:)'nv'",
    'cat ".e$(:)nv"',
    'cat .e"$(:)"nv',
    "cat .e$(:)n$(:)v",
    "cat < .e$(:)nv",
    "cat ~/.ex$(:)o/keys/seat",
    # a substitution that prints the missing letters, read as any text
    "cat .e$(echo n)v",
    "cat ~/.ne$(printf t)rc",
    # an unset variable is empty too
    "cat .e${x}nv",
    "cat ${x}.env",
    "cat $x.env",
    "cat ~/.n${x}etrc",
    "cat ~/.n$1etrc",
])
def test_a_store_name_split_by_an_expansion_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", ["cat .env", "cat ~/.netrc", "cat ~/.config/gh/hosts.yml", "cat $(:).env"])
def test_controls_keep_asking(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat '.e$(:)nv'",                         # single quotes: a literal file name
    "cat notes-$(date +%F).md",
    "cat build-$(git rev-parse HEAD).log",
    "ls -la $(pwd)/src",
    "echo $(date) ${USER}",
    "cat ${OUT}/report-${N}.txt",
    'git commit -m "fix: ${x} creds and $(id -u) tokens"',
])
def test_ordinary_expansions_keep_their_class(line):
    assert outcome(line)[1] == "allow"
