"""File tools write only the path they were given and read only regular files.

A write never follows a link planted at its temp name and never swaps a symlinked file for a regular one; a read
refuses FIFOs and devices instead of blocking on them or reading forever.
"""
import os
import stat
import threading
from pathlib import Path

import pytest

from exo.core.tools.builtin import fs

needs_links = pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="POSIX symlinks")
needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
DEVICE = "/dev/null"  # a character device that is safe to open even when a regression reads it


def P(*body):
    return "*** Begin Patch\n" + "\n".join(body) + "\n*** End Patch\n"


def _bounded(fn, fifos, timeout=5.0):
    """fn() in a thread; on a hang, keep opening each FIFO for writing so every blocked open ends, then fail."""
    out = []
    t = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    t.start()
    t.join(timeout)
    if not t.is_alive():
        return out[0]
    for _ in range(200):  # a reader may open the FIFO more than once
        if not t.is_alive():
            break
        for f in fifos:
            try:
                os.close(os.open(f, os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                pass
        t.join(0.05)
    pytest.fail("the tool blocked on a FIFO")


WRITES = {
    "write_file": lambda run: run("write_file", path="notes.txt", content="new\n"),
    "edit_file": lambda run: run("edit_file", path="notes.txt", old_string="old", new_string="new"),
    "apply_patch update": lambda run: run("apply_patch", patch=P("*** Update File: notes.txt", "-old", "+new")),
    "apply_patch add": lambda run: run("apply_patch", patch=P("*** Add File: notes.txt", "+new")),
}


@needs_links
@pytest.mark.parametrize("how", WRITES)
def test_write_never_follows_a_link_at_the_old_temp_name(run, tmp_path, tmp_path_factory, how):
    victim = tmp_path_factory.mktemp("outside") / "victim.txt"
    victim.write_text("precious\n")
    victim.chmod(0o600)
    notes, tmp = tmp_path / "notes.txt", tmp_path / ".notes.txt.exo-tmp"
    if how != "apply_patch add":
        notes.write_text("old\n")
        notes.chmod(0o750)
    os.symlink(victim, tmp)
    res = WRITES[how](run)
    assert not res.is_error, res.text()
    assert victim.read_text() == "precious\n" and stat.S_IMODE(victim.stat().st_mode) == 0o600
    assert not notes.is_symlink() and notes.read_text().strip() == "new"
    assert os.path.islink(tmp) and os.readlink(tmp) == str(victim)  # the old fixed name is never used (#273)
    assert sorted(f.name for f in victim.parent.iterdir()) == ["victim.txt"]  # nothing new beside the victim

    def leftovers():  # a temp file left beside the target by a write
        return [f.name for f in tmp_path.iterdir()
                if f != tmp and (f.name.endswith(".tmp") or f.name.startswith(".notes.txt."))]
    assert leftovers() == []
    if how != "apply_patch add":
        assert stat.S_IMODE(notes.stat().st_mode) == 0o750  # the file's own mode is kept
    # Writes go through a fresh mkstemp name, so a leftover file or a folder at the old name changes nothing.
    os.unlink(tmp)
    tmp.write_text("leftover")
    assert not run("write_file", path="notes.txt", content="again\n").is_error
    assert notes.read_text() == "again\n" and tmp.read_text() == "leftover" and leftovers() == []
    tmp.unlink()
    tmp.mkdir()
    assert not run("write_file", path="notes.txt", content="later\n").is_error and notes.read_text() == "later\n"
    assert leftovers() == [] and sorted(f.name for f in victim.parent.iterdir()) == ["victim.txt"]


@needs_links
def test_write_and_edit_refuse_a_symlinked_file(run, tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule: one\n")
    os.symlink("AGENTS.md", tmp_path / "CLAUDE.md")
    for res in (run("edit_file", path="CLAUDE.md", old_string="rule: one", new_string="rule: two"),
                run("write_file", path="CLAUDE.md", content="rule: three\n")):
        assert res.is_error and "symlink" in res.text() and "AGENTS.md" in res.text()
    assert (tmp_path / "CLAUDE.md").is_symlink() and (tmp_path / "AGENTS.md").read_text() == "rule: one\n"
    assert not run("edit_file", path="AGENTS.md", old_string="rule: one", new_string="rule: two").is_error
    assert (tmp_path / "CLAUDE.md").is_symlink() and (tmp_path / "CLAUDE.md").read_text() == "rule: two\n"
    assert "rule: two" in run("read_file", path="CLAUDE.md").text()  # reads still follow the link
    os.symlink("nowhere.md", tmp_path / "dangling.md")  # a write would have turned it into a regular file
    assert run("write_file", path="dangling.md", content="x").is_error
    assert (tmp_path / "dangling.md").is_symlink() and not (tmp_path / "nowhere.md").exists()


@needs_links
def test_apply_patch_refuses_symlinks_and_writes_nothing(run, tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule: one\n")
    (tmp_path / "plain.txt").write_text("a\n")
    os.symlink("AGENTS.md", tmp_path / "CLAUDE.md")
    os.symlink("gone.txt", tmp_path / "dangling.txt")
    res = run("apply_patch", patch=P("*** Update File: plain.txt", "-a", "+b",
                                     "*** Update File: CLAUDE.md", "-rule: one", "+rule: two"))
    assert res.is_error and "no files changed" in res.text() and "CLAUDE.md: is a symlink" in res.text()
    assert (tmp_path / "plain.txt").read_text() == "a\n" and (tmp_path / "AGENTS.md").read_text() == "rule: one\n"
    assert (tmp_path / "CLAUDE.md").is_symlink()
    res = run("apply_patch", patch=P("*** Add File: dangling.txt", "+x"))
    assert res.is_error and "symlink" in res.text()
    res = run("apply_patch", patch=P("*** Move File: plain.txt -> dangling.txt"))
    assert res.is_error and "destination already exists" in res.text()
    assert (tmp_path / "dangling.txt").is_symlink() and (tmp_path / "plain.txt").exists()
    # Delete File on a link removes the link only, as rm does.
    assert not run("apply_patch", patch=P("*** Delete File: CLAUDE.md")).is_error
    assert not os.path.lexists(tmp_path / "CLAUDE.md") and (tmp_path / "AGENTS.md").read_text() == "rule: one\n"


@needs_fifo
def test_read_and_edit_refuse_a_fifo_without_blocking(run, tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    for args in ({"path": "pipe"}, {"path": "pipe", "old_string": "a", "new_string": "b"}):
        name = "edit_file" if "old_string" in args else "read_file"
        res = _bounded(lambda: run(name, **args), [fifo])
        assert res.is_error and "not a regular file" in res.text()


@needs_links
def test_read_and_edit_refuse_devices_and_links_to_them(run, tmp_path):
    if not os.path.exists(DEVICE):
        pytest.skip(f"no {DEVICE}")
    os.symlink(DEVICE, tmp_path / "notes.md")
    for path in (DEVICE, "notes.md"):  # a repo can ship notes.md -> /dev/zero; /dev/null is the safe stand-in
        res = run("read_file", path=path)
        assert res.is_error and "not a regular file" in res.text()
    res = run("edit_file", path=DEVICE, old_string="a", new_string="b")
    assert res.is_error and "not a regular file" in res.text()


def test_read_refuses_a_file_over_the_cap(run, tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "MAX_READ_BYTES", 10)
    (tmp_path / "big.log").write_text("x" * 11)
    (tmp_path / "ok.log").write_text("x" * 10)
    res = run("read_file", path="big.log")
    assert res.is_error and "over the 10-byte limit" in res.text() and "grep" in res.text()
    assert run("read_file", path="ok.log").text() == "     1|xxxxxxxxxx"


def test_apply_patch_refuses_a_file_over_the_cap(run, tmp_path, monkeypatch):
    from exo.core.tools.builtin import patch
    monkeypatch.setattr(patch, "MAX_READ_BYTES", 10, raising=False)  # the message check catches a rename
    (tmp_path / "big.txt").write_text("old\n" + "x" * 10)
    res = run("apply_patch", patch=P("*** Update File: big.txt", "-old", "+new"))
    assert res.is_error and "over the 10-byte limit" in res.text()
    assert (tmp_path / "big.txt").read_text() == "old\n" + "x" * 10


GROW = {
    "read_file": lambda run: run("read_file", path="app.log"),
    "edit_file": lambda run: run("edit_file", path="app.log", old_string="TIMEOUT = 30", new_string="TIMEOUT = 60"),
    "apply_patch": lambda run: run("apply_patch", patch=P("*** Update File: app.log", "-TIMEOUT = 30", "+TIMEOUT = 60")),
}


@pytest.mark.parametrize("how", GROW)
def test_a_file_that_grows_past_the_cap_while_it_is_read_is_refused(run, tmp_path, monkeypatch, how):
    """fstat saw a file under the cap, then an appender pushed it past: a read cut at the cap would go back as the
    whole file. A file that grows but stays under the cap is read whole, appended bytes included."""
    from exo.core.tools.builtin import patch
    monkeypatch.setattr(fs, "MAX_READ_BYTES", 20)
    monkeypatch.setattr(patch, "MAX_READ_BYTES", 20)
    log, real_fstat, late = tmp_path / "app.log", os.fstat, []

    def fstat(fd):  # a concurrent appender, right after the size check
        st = real_fstat(fd)
        if late and os.path.samestat(st, os.stat(log)):
            with open(log, "a") as f:
                f.write(late.pop())
        return st
    monkeypatch.setattr(os, "fstat", fstat)
    log.write_text("TIMEOUT = 30\nEND\n")  # 17 bytes
    late.append("late: 1\n")  # 25 bytes once appended
    res = GROW[how](run)
    assert not late and res.is_error and "grew past the 20-byte limit" in res.text(), res.text()
    assert log.read_text() == "TIMEOUT = 30\nEND\nlate: 1\n"
    log.write_text("TIMEOUT = 30\nEND\n")
    late.append("z\n")  # 19 bytes: under the cap
    res = GROW[how](run)
    assert not late and not res.is_error, res.text()
    assert ("     3|z" in res.text()) if how == "read_file" else log.read_text() == "TIMEOUT = 60\nEND\nz\n"


@needs_fifo
def test_apply_patch_does_not_block_on_a_fifo_swapped_in_after_the_check(run, tmp_path, monkeypatch):
    # is_file() said "regular file", then the path became a FIFO: the open must not wait for a writer.
    fifo, is_file = tmp_path / "notes.txt", Path.is_file
    os.mkfifo(fifo)
    monkeypatch.setattr(Path, "is_file", lambda self: self == fifo or is_file(self))
    res = _bounded(lambda: run("apply_patch", patch=P("*** Update File: notes.txt", "-old", "+new")), [fifo])
    assert res.is_error and "not a regular file" in res.text()


@needs_fifo
@needs_links
def test_grep_skips_fifos_devices_and_big_files(run, tmp_path):
    from exo.core.tools.builtin import search
    (tmp_path / "a.txt").write_text("needle\n")
    (tmp_path / "big.txt").write_text("needle\n" * (search.MAX_FILE_BYTES // 7 + 1))
    os.mkfifo(tmp_path / "m_pipe")
    if os.path.exists(DEVICE):
        os.symlink(DEVICE, tmp_path / "n_dev.md")
    res = _bounded(lambda: run("grep", pattern="needle"), [tmp_path / "m_pipe"])
    assert res.text() == "a.txt:1:needle"
