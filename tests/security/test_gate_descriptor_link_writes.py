"""Issue #324: a write back through a link the gate did not see, or a hardlink on stdout, reaches the store.

Follow-up to #217 (PR #305), which reads a descriptor path as written. A link that already exists, or one made
where the gate does not see it, hid the spelling: with a link to /dev in place, `printf X > <link>/fd/3` was an
ordinary local write, and a link straight to /proc/self/fd/3 was a plain path. A hardlink hid the store the
other way: `exec 1< <hardlink to the grants>` read as an unnamed file, so the write to /dev/stdout after it
was a read. And a named descriptor (`{fd}<`) was not read as a descriptor at all. The links and the hardlink
below are real files under tmp_path, because the gate now looks at them; every command string is data for
exo.gate.classify/decide and none is run."""
import os

import pytest

from exo import gate

GRANTS = "~/.exo/state/gate-grants.json"


@pytest.fixture(autouse=True)
def _rig(tmp_path, monkeypatch):
    home = tmp_path / "home"
    state = home / ".exo" / "state"
    state.mkdir(parents=True)
    (state / "gate-grants.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("EXO_HOME", raising=False)
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    links = tmp_path / "links"
    links.mkdir()
    os.symlink("/dev", links / "d")                    # a link to /dev, already on the machine
    os.symlink("/proc/self/fd/3", links / "l")          # a link straight to a descriptor path
    os.symlink("/proc/self", links / "p")               # a link to a process folder
    os.symlink(links / "d", links / "c")                # a chain: link to link to /dev
    os.symlink("d", links / "rel")                      # a relative link target
    os.symlink("/nonexistent-324", links / "gone")      # a dangling link resolves nowhere
    (links / "real" / "fd").mkdir(parents=True)         # a real folder called fd
    (links / "plain.txt").write_text("x")
    os.link(state / "gate-grants.json", links / "h")    # the grants under another name
    return links


def _sh(cmd):
    return {"tool": "bash", "args": {"command": cmd}}


def _decided(action):
    c = gate.classify(action)
    d = gate.decide(c)
    return c.category, d.outcome, "only the principal approves" in d.reason


@pytest.mark.parametrize("cmd", [
    # a write through a link to /dev, /proc/self or a descriptor path
    "printf FORGED > @L@/d/fd/3",
    'exec 3< "$F"; printf FORGED > @L@/d/fd/3',
    "printf FORGED > @L@/l",
    "exec 3< ~/.exo/sta*/gate-grants.json; printf FORGED > @L@/l",
    "printf FORGED > @L@/p/fd/3",
    "printf FORGED > @L@/c/fd/3",
    "printf FORGED > @L@/rel/fd/3",
    # a hardlink to a store file, opened on a descriptor or written through
    "exec 1< @L@/h; printf FORGED > /dev/stdout",
    "{ printf FORGED > /dev/stdout; } 1< @L@/h",
    "exec 3< @L@/h",
    "printf FORGED > @L@/h",
    # a named descriptor is a descriptor: the store file it opens counts, as a numbered one does
    f"exec {{fd}}< {GRANTS}",
    "exec {fd}< @L@/h",
])
def test_a_write_through_an_unseen_link_is_a_store_write(cmd, _rig):
    assert _decided(_sh(cmd.replace("@L@", str(_rig)))) == ("store_write", "ask", True), cmd


@pytest.mark.parametrize("path", ["@L@/d/fd/3", "@L@/l"])
def test_a_file_tool_write_through_an_unseen_link_is_a_store_write(path, _rig):
    action = {"tool": "write_file", "args": {"path": path.replace("@L@", str(_rig)), "content": "x"}}
    assert _decided(action) == ("store_write", "ask", True), path


@pytest.mark.parametrize("cmd, category", [
    ("exec 1< @L@/d/null; printf FORGED > /dev/stdout", "read"),   # /dev/null through a link is not a store file
    ("cat < @L@/h", "read"),                                       # reading through a hardlink is a read
    ("exec {fd}< @L@/plain.txt", "read"),                          # a named descriptor on an ordinary file
    ("printf x > @L@/real/fd/3", "local_write"),                   # a real folder called fd, no links on the way
    ("printf x > @L@/gone/fd/3", "local_write"),                   # a dangling link resolves nowhere
    ("printf x > @L@/plain.txt", "local_write"),
])
def test_links_that_lead_nowhere_near_the_store_keep_their_class(cmd, category, _rig):
    assert gate.classify(_sh(cmd.replace("@L@", str(_rig)))).category == category, cmd


@pytest.mark.parametrize("cmd", ["printf x > '/tmp/x\x00'", "printf x > '/tmp/x\ud800'"])
def test_an_odd_byte_in_a_redirection_target_never_raises(cmd):
    # The inode comparison stats the target: a NUL byte or a lone surrogate names no store file.
    assert gate.classify(_sh(cmd)).category == "local_write", cmd


def test_the_walk_never_resolves_through_the_gates_own_descriptors():
    # /dev/stdout and /proc/self name the writer's descriptors; resolving them here would read the gate's own.
    assert not gate._fd_link_path("/dev/stdout")
    assert not gate._fd_link_path("/proc/self/fd/1")
    assert gate._fd_link_path("/proc/self/fd/3")


def test_the_lexer_reads_a_named_descriptor():
    cmds = gate._parse("exec {fd}< a 3< b {out}> c")[0]
    assert [(op, t.text, t.fd) for op, t in cmds[0].redirs] == [
        ("<", "a", "{fd}"), ("<", "b", "3"), (">", "c", "{out}")]
