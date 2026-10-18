"""Store-path checks match the lexical and the resolved form, never raise, and cover `cd` through an alias."""
import os
import shlex

import pytest

from exo import gate

STRICT = {"gates": {"categories": {"store_write": "allow"}}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    home = tmp_path / "store"
    (home / "state").mkdir(parents=True)
    (home / "state" / "custom.json").write_text("{}")
    (home / "ordinary").mkdir()
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    return home


def outcome(call):
    classified = gate.classify(call)
    return classified.category, gate.decide(classified, STRICT).outcome


def bash(command):
    return {"tool": "bash", "args": {"command": command}}


# --- resolution failures ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("odd", ["/tmp/x\x00", "/tmp/x\udcff", "/tmp/\x00/state/custom.json"])
def test_odd_bytes_in_a_path_never_raise(store, odd):
    assert gate.is_store_path(odd) is False
    assert outcome({"tool": "write_file", "args": {"path": odd, "content": "x"}}) == ("local_write", "allow")
    assert outcome(bash(f"printf x > {shlex.quote(odd)}"))[0] != "store_write"


def test_odd_sidecar_path_keeps_the_store_floor(store):
    patch = f"*** Begin Patch\n*** Add File: {store}/state/custom.json\n+x\n*** End Patch"
    call = {"tool": "apply_patch", "args": {"path": "/tmp/x\x00", "patch": patch}}
    assert outcome(call) == ("store_write", "ask")
    command = f"printf x > /tmp/x\x00 && printf x > {shlex.quote(str(store / 'state' / 'custom.json'))}"
    assert outcome(bash(command)) == ("store_write", "ask")


def test_resolution_failure_keeps_the_lexical_match(store, monkeypatch):
    real = os.path.realpath

    def failing(path, **kw):
        if os.fspath(path).rstrip("/") == str(store) or "/state" in os.fspath(path):
            raise OSError("no resolution")
        return real(path, **kw)
    monkeypatch.setattr(gate.os.path, "realpath", failing)
    assert gate.is_store_path(str(store / "state" / "custom.json"))
    assert gate.is_store_path(str(store), ancestors=True)
    assert gate._in_store_dir(str(store))
    assert outcome({"tool": "write_file", "args": {"path": str(store / "state" / "new.json"), "content": "x"}}) == \
        ("store_write", "ask")


def test_lexical_and_resolved_forms_both_count(store, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(store, target_is_directory=True)
    outbound = tmp_path / "ordinary.json"
    outbound.write_text("{}")
    (store / "state" / "outbound.json").symlink_to(outbound)
    assert gate.is_store_path(str(alias / "state" / "new.json"))          # resolved form is the store
    assert gate.is_store_path(str(store / "state" / "outbound.json"))    # lexical form is the store
    assert not gate.is_store_path(str(outbound))
    assert gate.is_store_path("/" + str(store / "state" / "new.json"))   # a leading `//` is one slash


# --- cd, pushd and git -C through an alias ---------------------------------------------------------------------

def test_cd_pushd_and_git_through_an_alias(store, tmp_path):
    alias = tmp_path / "e"
    alias.symlink_to(store, target_is_directory=True)
    q = shlex.quote(str(alias))
    assert outcome(bash(f"cd {q} && printf x > state/custom.json")) == ("store_write", "ask")
    assert outcome(bash(f"pushd {q} && rm -rf state")) == ("store_write", "ask")
    assert outcome(bash(f"git -C {q} checkout -- state")) == ("store_write", "ask")
    assert outcome(bash(f"git -C {q} status")) == ("read", "allow")
    assert outcome(bash(f"cd {q}/ordinary/.. && printf x > state/custom.json")) == ("store_write", "ask")


def test_cd_through_odd_spellings_of_the_store(store):
    assert outcome(bash(f"cd /{shlex.quote(str(store))} && printf x > state/custom.json")) == ("store_write", "ask")
    if os.path.exists("/proc/self/root"):
        command = f"cd /proc/self/root{shlex.quote(str(store))} && printf x > state/custom.json"
        assert outcome(bash(command)) == ("store_write", "ask")


def test_cd_into_an_ordinary_alias_stays_local(store, tmp_path):
    outside = tmp_path / "ordinary"
    outside.mkdir()
    alias = tmp_path / "plain"
    alias.symlink_to(outside, target_is_directory=True)
    assert outcome(bash(f"cd {shlex.quote(str(alias))} && printf x > state/custom.json")) == ("local_write", "allow")
    assert not gate._in_store_dir(str(alias))


# --- links made in the same command --------------------------------------------------------------------------

def test_link_making_copies_and_relative_link_targets(store, tmp_path):
    home, t = shlex.quote(str(store)), shlex.quote(str(tmp_path))
    assert outcome(bash(f"cp -as {home} {t}/s && printf x > {t}/s/state/custom.json")) == ("store_write", "ask")
    assert outcome(bash(f"cp -al {home} {t}/h")) == ("store_write", "ask")
    assert outcome(bash(f"cp --symbolic-link -r {home} {t}/s2")) == ("store_write", "ask")
    assert outcome(bash(f"ln -s store/state {t}/b && printf x > {t}/b/custom.json")) == ("store_write", "ask")
    assert outcome(bash(f"ln -s store {t}/root-link")) == ("store_write", "ask")
    assert outcome(bash(f"cp -a {home} {t}/backup"))[0] == "shell"             # a plain copy is not a link tree
    assert outcome(bash(f"cp -r {home}/ordinary {t}/copy"))[0] == "shell"
    assert outcome(bash(f"ln -s ordinary {t}/plain"))[0] == "shell"            # a relative link to nowhere special
