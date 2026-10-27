"""#203: a folder of the Exo home that is not itself part of the store (`~/.exo/handoffs`, a
principal's unprotected subfolder) made every write after a `cd` into it a store write, so
`cd ~/.exo/handoffs && cp x config.json` asked the principal over the folder's own file. The
blanket rule is narrowed for one shape only: a program whose writes the gate can see, with every
word and redirection target a plain relative name (no `..`, no expansion, no glob, no brace) — a
write that cannot name anything outside the folder it stands in. Every other write from such a
folder still asks, and a read of a store file name there still asks as it did (#192). The folder
has to be one a bare top-level `cd` provably enters: a cd that may not take effect (a subshell, a
branch, a pipeline, a background job, a `&&`/`||` chain, a prefix, an extra operand) never moves
the folder the line stands in, and once the line has stood in the store no later home cd re-opens
the carve-out. A `.git` folder is never a home folder: its hooks run code."""

import pytest

from exo import gate


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for d in (".exo/plugins/foo", ".exo/venv", ".exo/src", ".exo/handoffs", ".exo/handoffs/sub",
              ".exo/principals/some-id/notes", ".exo/policies", ".exo/keys", ".exo/bus"):
        (home / d).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    return home


def _cat(cmd: str) -> str:
    return gate.classify({"tool": "bash", "args": {"command": cmd}}).category


def _asks(cmd: str) -> bool:
    c = gate.classify({"tool": "bash", "args": {"command": cmd}})
    return gate.decide(c).outcome == "ask"


F = "cd ~/.exo/handoffs && "


# --- the carve-out: a plainly spelt write the gate can see, in a non-store folder of the home --

@pytest.mark.parametrize("cmd", [
    F + "cp x config.json",                       # the issue's headline row
    F + "cp x y",
    F + "cp x sub/y",
    F + "cp -r x d",
    F + "cp -- x y",
    F + "mv x y",
    F + "install x y",
    F + "tee config.json",
    F + "touch config.json",
    F + "mkdir d",
    F + "sed -i s/a/b/ f",
    F + "sed --in-place s/a/b/ f",
    F + "printf x > config.json",
    F + "echo x > config.json",
    "cd ~/.exo/handoffs/sub && cp x config.json",
    "cd $HOME/.exo/handoffs && cp x config.json",
    "cd ${EXO_HOME}/handoffs && cp x config.json",
    "pushd ~/.exo/handoffs && cp x config.json",
    "cd ~/.exo/principals/some-id/notes && cp x config.json",
    "cd ~/.exo/handoffs && cp x config.json && cp y z",
])
def test_a_plain_write_in_a_non_store_folder_of_the_home_is_ordinary(env, cmd):
    assert _cat(cmd) != "store_write"
    assert not _asks(cmd)


def test_a_plain_write_reads_the_home_folders_own_config_as_ordinary(env):
    # the same ordinary standing `cd /tmp && cp x config.json` has always had: it runs
    c = gate.classify({"tool": "bash", "args": {"command": F + "cp x config.json"}})
    assert gate.decide(c).outcome == "allow"


# --- a read is not loosened: only the write side of the line is carved out ---------------------

@pytest.mark.parametrize("cmd", [
    F + "cat config.json",                          # a pure read of a store file name still asks (#192)
    F + "cp x config.json && cat config.json",      # the read on the same line still counts
    F + "cp x keys/seat.key",                       # a word that itself names a store path still asks
    F + "cp x bus/registry.json",
])
def test_reads_and_store_named_words_still_ask(env, cmd):
    assert _asks(cmd)


# --- the issue's adversarial lines: every one of them still asks -------------------------------

@pytest.mark.parametrize("cmd", [
    F + "cp x $OLDPWD/config.json",
    F + "cp x ~-/config.json",
    F + "cp x ../../conf*.json",
    F + "cp x ../../{config,x}.json",
    F + "curl -o ../../config.json https://example.invalid/x",
    F + "GIT_DIR=../../principals/some-id/.git git checkout .",
    F + "sudo -D ../.. cp x config.json",
    F + "tar -xf a.tar",
    F + "npm install",
])
def test_the_issues_adversarial_lines_still_ask(env, cmd):
    assert _asks(cmd)


# --- every other shape of write from such a folder still asks ----------------------------------

@pytest.mark.parametrize("cmd", [
    F + "cp x ../../config.json",                   # a `..` destination
    F + "cp x ../y",
    F + "cp x y*",                                  # a glob word
    F + "cp x {y,z}",                               # a brace word
    F + "cp x $D/y",                                # an expansion word
    F + "cp /tmp/x config.json",                    # an absolute word: the shape wants plain relative names
    F + "cp x /tmp/y",
    F + "sudo cp x config.json",                    # a prefix the gate cannot see through
    F + "FOO=1 cp x config.json",                   # an assignment prefix
    F + "make",                                     # a program whose writes the gate cannot see
    F + "cat x > config.json",                      # cat is not one of the visible writers
    F + "dd of=config.json",
    F + "cp x ../../policies/gates.yaml",           # the store itself, spelt with `..`
    F + "rm x",                                     # a delete is not in the visible set either
    "env -C ~/.exo/handoffs cp x config.json",      # entering through env is #125's lane, unchanged
])
def test_other_writes_from_a_home_folder_still_ask(env, cmd):
    assert _asks(cmd)


# --- the folder itself has to be a non-store folder of the home --------------------------------

@pytest.mark.parametrize("cmd", [
    "cd ~/.exo && cp x config.json",                       # the home itself is the store
    "cd ~/.exo/policies && cp x gates.yaml",               # a protected folder of the store
    "cd ~/.exo/keys && cp x y",
    "cd ~/.exo/bus && cp x y",
    "cd ~/.exo/{plugins,venv} && cp x config.json",        # the folder is not one plain spelling
    "cd ~/.exo/plug* && cp x config.json",
    F + "cd $D && cp x config.json",                       # a later cd to an unknown folder
    F + "cd .. && cp x config.json",                       # a later cd leaves the folder
    F + "cd sub && cp x config.json",
    F + "cd - && cp x config.json",
    F + "popd && cp x config.json",
])
def test_an_unproven_folder_keeps_the_blanket_rule(env, cmd):
    assert _asks(cmd)


def test_a_store_write_from_the_store_itself_is_unchanged(env):
    assert _cat("cd ~/.exo && cp x policies/gates.yaml") == "store_write"
    assert _cat("cd ~/.exo/policies && cp x gates.yaml") == "store_write"


def test_a_custom_home_at_a_bare_path_keeps_the_blanket_rule(tmp_path, monkeypatch):
    # The home this narrowing knows is the `.exo` tree. A store root spelled any other way
    # (EXO_HOME set to a bare path) cannot have its subfolders told apart from the store's
    # own folders, so a write after a `cd` into one of them still asks.
    store = tmp_path / "store-home"
    (store / "memory").mkdir(parents=True)
    (store / "plugins" / "foo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_HOME", str(store))
    assert _cat(f"cd {store}/memory && touch notes.md") == "store_write"
    assert _cat(f"cd {store}/plugins/foo && cp x config.json") == "store_write"


# --- a folder the store now covers still asks, as does a `.git` folder -------------------------

@pytest.mark.parametrize("cmd", [
    "cd ~/.exo/plugins/foo && cp x config.json",   # a store folder since #435
    "cd ~/.exo/venv && cp x config.json",          # the interpreter's own folder is store too
    "cd ~/.exo/src && cp x y",
])
def test_a_store_folder_still_asks(env, cmd):
    assert _asks(cmd)


@pytest.mark.parametrize("cmd", [
    "cd ~/.exo/principals/some-id/.git/hooks && printf x > pre-commit",
    "cd ~/.exo/principals/some-id/.git && cp x y",
])
def test_a_git_folder_is_not_a_home_folder(env, cmd):
    assert _asks(cmd)


# --- a store visit on the line gates the carve-out, even when a later cd names a home folder ---

@pytest.mark.parametrize("cmd", [
    "cd ~/.exo/policies || cd ~/.exo/handoffs; printf x > gates.yaml",
    "cd ~/.exo/policies; (cd ~/.exo/handoffs); printf x > gates.yaml",
    "cd ~/.exo/policies; cd ~/.exo/handoffs | cat; printf x > gates.yaml",
    "cd ~/.exo/policies; cd ~/.exo/handoffs & printf x > gates.yaml",
    "cd ~/.exo/policies; if false; then cd ~/.exo/handoffs; fi; printf x > gates.yaml",
    "cd ~/.exo/policies; cd ~/.exo/nosuchdir; printf x > gates.yaml",
    "cd ~/.exo/policies; nohup cd ~/.exo/handoffs; printf x > gates.yaml",
    "cd ~/.exo/policies; cd ~/.exo/handoffs extra; printf x > gates.yaml",
    "cd ~/.exo/policies; cd ~/.exo/nope; printf x > gates.yaml",
    # a later home cd that does take effect does not re-open it either: the line has stood
    # in the store, so the carve-out stays shut (fail closed, by design)
    "cd ~/.exo/policies; cd ~/.exo/handoffs; printf x > gates.yaml",
    "cd ~/.exo/policies; pushd ~/.exo/handoffs; printf x > gates.yaml",
])
def test_a_store_visit_on_the_line_gates_the_carve_out(env, cmd):
    assert _asks(cmd)


# --- a cd that may not take effect never moves the folder the line stands in -------------------

@pytest.mark.parametrize("cmd", [
    "(cd ~/.exo/handoffs); printf x > gates.yaml",
    "nohup cd ~/.exo/handoffs; printf x > gates.yaml",
    "if false; then cd ~/.exo/handoffs; fi; printf x > gates.yaml",
    "cd ~/.exo/handoffs | cat; printf x > gates.yaml",
    "cd ~/.exo/handoffs & printf x > gates.yaml",
    "false || cd ~/.exo/handoffs; printf x > gates.yaml",
    "cd ~/.exo/handoffs extra; printf x > gates.yaml",
    # a home cd chained after another command runs only if that command succeeds, so it
    # does not qualify either — even when the chain before it is a plain folder
    "cd /tmp && cd ~/.exo/handoffs && cp x config.json",
])
def test_a_cd_that_may_not_take_effect_keeps_the_blanket_rule(env, cmd):
    assert _asks(cmd)


# --- a second home folder keeps the carve-out ---------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "cd ~/.exo/handoffs && cd ~/.exo/principals/some-id/notes && cp x config.json",
])
def test_a_later_home_folder_keeps_the_carve_out(env, cmd):
    assert _cat(cmd) != "store_write"
    assert not _asks(cmd)
