"""Issue #224: a brace set in an Exo store path is expanded as bash does before the store-write check.

Every command string here is data for exo.gate.classify/decide; none is run."""
import time

import pytest

from exo import gate


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo" / "policies").mkdir(parents=True)
    (home / ".exo" / "policies" / "default.yaml").write_text("")
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
    # local_write or shell, and allow, on main c937a85
    "echo x > ~/.exo/polic{i..i}es/default.yaml",
    "cp x ~/.exo/polic{i..i}es/default.yaml",
    "echo x > ~/.exo/{policies,zz}/default.yaml",
    "echo x > ~/.exo/{zz,policies}/default.yaml",
    "tee ~/.exo/polic{i..i}es/default.yaml",
    "sed -i s/a/b/ ~/.exo/polic{i..i}es/default.yaml",
    "echo x >> ~/.exo/st{a..a}te/gate-grants.json",
    "cp x ~/.exo/{state,zz}/approval-grants.json",
    "cp x ~/.exo/ledger.sql{i..i}te",
    "ln -sf /tmp/x ~/.exo/{seats,z}/a.toml",
    'echo x > "$HOME"/.exo/polic{i..i}es/default.yaml',
    "echo x > polic{i..i}es/gates.yaml",                         # a store name matches anywhere
    "cd ~/.e{x,}o && echo x > notes.txt",                        # a cd into the store
    "echo x > ~/.exo/principals/abc/polic{i..i}es/x.yaml",
    # a `,` or `=` inside the group: braces expand first, then the word is split
    "dd if=/dev/zero of=~/.ex{o,zzq}/policies/default.yaml",
    "dd if=/dev/zero of=~/.exo/polic{i,zzq}es/default.yaml",
    # an ancestor of the store under a delete or move
    "rm -rf ~/.e{x,}o",
    "cp x ~/.e{x,}o; rm -rf ~/.e{x,}o",
])
def test_brace_set_reaching_the_store_is_a_store_write(cmd):
    assert _outcome(_bash(cmd)) == ("store_write", "ask")


@pytest.mark.parametrize("tool,args", [
    ("write_file", {"path": "~/.exo/polic{i..i}es/default.yaml", "content": "x"}),
    ("edit_file", {"path": "~/.exo/{policies,x}/default.yaml"}),
    ("apply_patch", {"patch": "*** Update File: ~/.exo/polic{i..i}es/default.yaml\n"}),
])
def test_brace_set_in_a_file_tool_path_is_a_store_write(tool, args):
    assert _outcome({"tool": tool, "args": args}) == ("store_write", "ask")


@pytest.mark.parametrize("action", [
    _bash("echo x > ~/.exo/p{1..2000}"),
    {"tool": "write_file", "args": {"path": "~/.exo/p{1..2000}", "content": "x"}},
])
def test_over_cap_brace_store_path_asks(action):
    assert _outcome(action)[1] == "ask"


def test_over_cap_brace_word_counts_as_the_store():
    assert gate.is_store_path("~/x{1..2000}")
    assert _outcome(_bash("cd ~/x{1..2000} && touch y")) == ("credentials", "ask")


@pytest.mark.parametrize("cmd,category", [
    ("cp a{,.bak}", "shell"),
    ("mkdir -p src/{a,b}", "shell"),
    ("echo x > notes/{a,b}.txt", "local_write"),
    ("touch build/{1..20}.o", "shell"),
    ("echo hi > ~/.exo/{notes,x}.md", "local_write"),
    ("touch ~/.exo/plugins/{a,b}/x", "shell"),
    # bash expands only unquoted braces: these name a literal `polic{i..i}es` folder
    ("echo x > ~/.exo/polic'{i..i}'es/x", "local_write"),
    ('echo x > ~/.exo/polic"{i..i}"es/x', "local_write"),
    ("echo x > ~/.exo/polic\\{i..i\\}es/x", "local_write"),
    ("ln -s a '~/.exo/{policies,x}/a'", "shell"),
    # quoted braces in a value never count, however many
    ("echo '[" + ", ".join(['{"a":1,"b":2}'] * 11) + "]' > out.json", "local_write"),
])
def test_ordinary_brace_writes_keep_their_class(cmd, category):
    assert _outcome(_bash(cmd)) == (category, "allow")


@pytest.mark.parametrize("cmd,category,outcome", [
    ("touch a{1..1000} b{1..1000} c{1..700}", "shell", "allow"),     # the store checks have their own budget
    ("cp x{1..1000} y{1..1000} dest/", "shell", "allow"),
    ("touch " + " ".join(["a{1..1000}"] * 20), "shell", "allow"),   # one spelling, expanded once
    # a spelling checked under a narrow and then a wide command, or as a wrapper's inner command, is charged once
    ("touch x/{0001..1000} y/{0001..1000} && chmod 600 x/{0001..1000} y/{0001..1000} && touch z/{0001..0100}",
     "shell", "allow"),
    ("uv run chmod 644 f{0001..1000} g{0001..1000} h{0001..0100}", "shell", "allow"),
    ("ls | xargs -I{} chmod 644 f{0001..1000} g{0001..1000} h{0001..0100}", "opaque_exec", "ask"),
    ("mkdir -p a/{0001..1000} b/{0001..1000} c/{0001..1000} d/{001..100} && rm -rf a/{0001..1000}", "delete", "ask"),
])
def test_many_brace_words_keep_their_class(cmd, category, outcome):
    t = time.process_time()
    assert _outcome(_bash(cmd)) == (category, outcome)
    assert time.process_time() - t < 5


def test_a_spelling_checked_narrow_is_still_checked_wide():
    assert _outcome(_bash("cp x ~/.e{x,}o/ && rm -rf ~/.e{x,}o/")) == ("store_write", "ask")


@pytest.mark.parametrize("cmd", [
    "cd '/tmp/" + "A" * 500000 + "{1..1}" * 80000 + "'",
    "git -C '/tmp/" + "A" * 500000 + "{1..1}" * 80000 + "' commit -m x",
    ";".join(["cd '/tmp/" + "A" * 100000 + "{1..1}" * 8000 + "'"] * 10),
])
def test_quoted_braces_in_a_folder_target_are_not_expanded(cmd):
    t = time.process_time()
    assert _outcome(_bash(cmd))[0] != "store_write"
    assert time.process_time() - t < 5


@pytest.mark.parametrize("cmd", [
    "cp " + " ".join(f"~/x{i}/{{1..1000}}" for i in range(100)) + " d/",
    "cp " + " ".join(f"/srv/w{i}/{{a,b}}{{c,d}}{{e,f}}{{g,h}}{{i,j}}{{k,l}}" for i in range(300)) + " d/",
])
def test_store_checks_past_their_budget_ask_in_bounded_time(cmd):
    t = time.process_time()
    assert _outcome(_bash(cmd))[1] == "ask"
    assert time.process_time() - t < 10
