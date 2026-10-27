"""#293: the per-word secret-store check made a long line of short words slow (about 0.2 ms a word).

_shell_credential_hit met the secret-store check about twice a word — the expanded text's words and the
segments' tokens name most words twice — and each meeting ran the full check: expansion, a realpath (an
lstat per path part) and the dynamic stores' roots. A 100 KB line of 'x' words took 10.9 s to classify on
the machine that measured it, and classify runs in the gate hook, on the core's event loop. The check now
keeps each distinct expanded word's file-system verdict on the call's scope, so a word is read from the
file system once per classify call; the caller checks each distinct piece once; and a bare word that
cannot name a path into a store (no separator, expansion, glob or brace set in it, not a link, read from
folders outside the stores) skips the file system — only its name can name a store, and that is read
without the file system. Two cases keep the full check: the bare word `run`, which joined to a tool
folder can be a run folder, and any word read from a folder spelled with a leading `//`, which main
matches whole.

The speed pin counts `_real` calls, not seconds: main meets the check about twice a word, so a repeated
line costs about a thousand calls where the memo costs a handful. Timing bounds would pass on main too.

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


def _counted(monkeypatch):
    calls = []
    real = gate._real
    monkeypatch.setattr(gate, "_real", lambda p, memo: calls.append(p) or real(p, memo))
    return calls


def test_each_distinct_word_is_resolved_once(monkeypatch):
    """A repeated word costs the file-system walk once per call, not once per meeting: main makes about
    a thousand `_real` calls for the repeated line below, the memo makes a handful."""
    calls = _counted(monkeypatch)
    gate.classify({"tool": "bash", "args": {"command": "echo " + "plainword " * 500}})
    assert len(calls) <= 100, len(calls)
    calls.clear()
    gate.classify({"tool": "bash", "args": {"command": "echo " + " ".join(f"w{i}" for i in range(500))}})
    assert len(calls) <= 750, len(calls)


@pytest.fixture()
def _stores(_home):
    """A home with a secret store and a work folder holding links into it, for the boundary pins below."""
    ssh = _home / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("x\n")
    (_home / ".claude").mkdir()
    work = _home / "work"
    work.mkdir()
    (work / "notes.txt").write_text("x\n")
    (work / "innocent").symlink_to(ssh / "id_rsa")
    (work / "keylink.pub").symlink_to(ssh / "id_rsa")
    (work / "dirlink").symlink_to(ssh)
    return _home, work


def _classify(cmd, cwd=None):
    c = gate.classify({"tool": "bash", "args": {"command": cmd}}, cwd=str(cwd) if cwd else None)
    return c.category, gate.decide(c).outcome


@pytest.mark.parametrize("cmd", ["cat innocent", "cat keylink.pub", "cat dirlink"])
def test_a_bare_word_that_is_a_link_into_a_store_still_asks(_stores, cmd):
    """A bare word skips the file system only when it is not a link: a link's target decides, by name too,
    and a link named .pub is judged by where it leads."""
    _, work = _stores
    assert _classify(cmd, work) == ("credentials", "ask")


@pytest.mark.parametrize("cmd,cwd", [
    ("cat anything.txt", ".claude"),   # the folder itself sits in a dynamic store
    ("cat keys", ".exo"),              # a store's own name, spelled from the folder above it
    ("cat .claude", ""),               # the same from the home folder
])
def test_a_bare_word_that_reaches_a_store_from_its_folder_still_asks(_stores, cmd, cwd):
    home, _ = _stores
    assert _classify(cmd, home / cwd if cwd else home) == ("credentials", "ask")


@pytest.mark.parametrize("cmd", ["cat readme", "cat notes.txt", "cat ..", "cat ."])
def test_a_plain_bare_word_stays_a_read(_stores, cmd):
    _, work = _stores
    assert _classify(cmd, work) == ("read", "allow")


@pytest.fixture()
def _run_home(_home):
    """A home whose Exo home holds a run folder, a principal folder, and a link to the Exo home."""
    exo = _home / ".exo"
    (exo / "run").mkdir(parents=True)
    (exo / "principals" / "p").mkdir(parents=True)
    (_home / "exo-link").symlink_to(exo)
    return _home


@pytest.mark.parametrize("cmd,cwd", [
    ("ls run", ".exo"),
    ("cat run", ".exo"),
    ("tar czf /tmp/o.tgz run", ".exo"),
    ("ls run", ".exo/principals/p"),
    ("ls run", "exo-link"),
])
def test_a_bare_run_word_read_from_an_exo_folder_still_asks(_run_home, cmd, cwd):
    """`run` keeps the full check: joined to a tool folder it is a run folder, so reading it asks even
    though it is a bare word."""
    home = _run_home
    assert _classify(cmd, home / cwd) == ("credentials", "ask")


def test_a_bare_run_word_read_by_a_file_tool_still_asks(_run_home):
    home = _run_home
    c = gate.classify({"tool": "list_dir", "args": {"path": "run"}}, cwd=str(home / ".exo"))
    assert (c.category, gate.decide(c).outcome) == ("credentials", "ask")


def test_a_word_from_a_double_slash_folder_still_asks(_stores):
    """A folder spelled with a leading `//` keeps the full check: main matches such a path whole, so the
    bare-word skip would drop the ask. The call's own folder argument is the form that asks: the folder
    itself is a store by name, and the line's outcome asks through it."""
    home, _ = _stores
    c = gate.classify({"tool": "bash", "args": {"command": "cat x", "cwd": "//" + str(home / ".ssh")}})
    assert (c.category, gate.decide(c).outcome) == ("read", "ask")
