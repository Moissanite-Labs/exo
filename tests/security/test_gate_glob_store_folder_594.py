"""#594: a glob in a secret store's folder with a bare file part reads the store without asking.

Before this fix, `_glob_word` gave up whenever the folder part held a glob, because `d_abs`
is set only for a literal folder, so `cat ~/.ss?/*` read as an ordinary read. Every check
classifies; nothing is run."""
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
    "cat ~/.ss?/*",
    "cat ~/.s*/*",
    "cat ~/.aw?/*",
    "cat ~/.*/*",
    "cat ~/.exo/key?/*",
    "cat ~/.exo/k*/*",
    "cat ~/.exo/k*/l*",
    "cat ~/.e*/k*/*",
    "cp ~/.exo/key?/le* /tmp/o",
    "base64 ~/.exo/[k]eys/[l]ead*",
])
def test_a_globbed_store_folder_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "sudo cat ~/.ss?/*",
    "bash -c 'cat ~/.ss?/*'",
    "ssh h 'cat ~/.ss?/*'",
    "sudo cat ~/.exo/k*/l*",
])
def test_a_globbed_store_folder_asks_under_wrappers(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("path", [
    "~/.ss?/*",
    "~/.exo/k*/l*",
    "~/.aw?/*",
])
def test_the_file_tools_read_globbed_store_folders_too(path):
    c = gate.classify({"tool": "read_file", "args": {"path": path}})
    assert (c.category, gate.decide(c, POL).outcome) == ("credentials", "ask")


def test_a_globbed_store_folder_denies_under_deny_credentials():
    deny = gate.normalize_policies({"gates": {"deny": ["credentials"]}})
    c = gate.classify({"tool": "bash", "args": {"command": "cat ~/.ss?/*"}})
    assert gate.decide(c, deny).outcome == "deny"


@pytest.mark.parametrize("line", [
    "cat ~/.ssh/*",
    "cat ~/.aws/*",
    "cat ~/.exo/keys/*",
    "cat ~/.ss?/id_rsa",
    "cat ~/.a*/cred*",
    "cat ~/.exo/k*s/lead.key",
])
def test_controls_keep_asking(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat src/*/*.py",
    "cat ~/*/*",
    "cat ~/.ssh/*.pub",
    "cat ~/.ss?/*.pub",
])
def test_ordinary_and_public_globs_keep_their_class(line):
    assert outcome(line) == ("read", "allow")


def test_a_literal_file_part_under_a_star_folder_stays_a_store_write():
    # The distinction the direct secret-folder hit keeps: a write whose target is only reached
    # by a substitution's `*` reading is store_write, not credentials (test_gate_store_name_forms).
    c = gate.classify({"tool": "bash", "args": {"command": "cp x ~/.exo/$(printf keys)/a"}})
    assert c.category == "store_write"
