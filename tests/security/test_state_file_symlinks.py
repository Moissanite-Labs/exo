"""State files are written through mkstemp, not a fixed temporary name (#273, the class of #221's anchor finding).

Each site used to write `<file>.tmp` (or a name with the pid in it) with a plain open, which follows a symlink. A
process that can create files in the folder could plant one there and turn the next write into a write to any file
the user can write. Each test plants that symlink at the old name, writes, and checks that the file it points at is
untouched and that the real file is a regular file with the content and mode the site writes.

The export site is covered in tests/baseline/test_export.py, the gate's grants and the bus cursor in their plugin
suites, and the ledger anchors in tests/test_ledger.py."""
import json
import os
import stat
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

VICTIM = "do not touch\n"


def _umask_default() -> int:
    """The mode a plain open gives a new file: what the sites that used `write_text` keep."""
    old = os.umask(0o022)
    os.umask(old)
    return 0o666 & ~old


@pytest.fixture
def victim(tmp_path):
    v = tmp_path / "victim.txt"
    v.write_text(VICTIM)
    return v


def _plant(link, victim):
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(victim)
    return link


def _check(path, victim, mode, planted):
    assert victim.read_text() == VICTIM
    st = os.lstat(path)
    assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == mode
    left = [p.name for p in path.parent.iterdir() if p != planted and p.name.startswith(f".{path.name}.")]
    assert left == []  # no temporary file left behind


def test_the_key_registry(victim):
    from exo.keyring import KeyRegistry
    path = KeyRegistry.path_for()
    planted = _plant(path.with_name(f".registry.json.{os.getpid()}.tmp"), victim)
    KeyRegistry.load()  # no registry yet: one is made and written
    assert json.loads(path.read_text())["v"] == 1
    _check(path, victim, 0o600, planted)


def test_the_update_pin(victim, tmp_path, capsys):
    from exo import update_verify
    path = update_verify.pin_file()
    planted = _plant(path.with_name(path.name + ".tmp"), victim)
    trust = "pat ssh-ed25519 AAAA\n"

    def git(cmd):
        return SimpleNamespace(returncode=0, stdout=trust)
    assert update_verify.pin_trust(tmp_path, "HEAD", read=git) == 0
    assert path.read_text() == trust
    _check(path, victim, _umask_default(), planted)


def test_the_composer_history(victim, tmp_path):
    from exo.complete import History
    path = tmp_path / "history" / "default.jsonl"
    planted = _plant(path.with_suffix(".tmp"), victim)
    History(path).add("hello")
    assert path.read_text() == '"hello"\n'
    _check(path, victim, 0o600, planted)


def test_a_handoff(victim, tmp_path):
    from exo.handoff import write_handoff
    out = tmp_path / "handoffs"
    planted = _plant(out / "release-plan-20261019.md.tmp", victim)
    path = write_handoff("release plan", [{"role": "user", "content": "ship it"}], out_dir=out,
                         now=datetime(2026, 10, 19, tzinfo=timezone.utc))
    assert path == out / "release-plan-20261019.md" and "release plan" in path.read_text()
    _check(path, victim, _umask_default(), planted)


def test_the_principal_token(victim, tmp_path):
    """Not redirectable before either (the old code unlinked the name and opened it with O_EXCL). It now goes
    through the same helper, so a link planted between the unlink and the open no longer stops the core."""
    from exo import principal
    path = principal.token_path(tmp_path / "run" / "exod.sock")
    planted = _plant(path.with_name(path.name + f".{os.getpid()}.tmp"), victim)
    token = principal.issue(tmp_path / "run" / "exod.sock")
    assert json.loads(path.read_text())["token"] == token
    _check(path, victim, 0o600, planted)


@pytest.mark.parametrize("existing", [None, 0o750], ids=["new", "existing-0750"])
def test_the_agent_file_tools(victim, tmp_path, existing):
    """write_file, edit_file and patch write through `write_atomic`: a link at `.<name>.exo-tmp` in the workspace
    sent the agent's text to a file the gate never saw. A new file keeps the umask default, an old one its mode."""
    from exo.core.tools.builtin.patch import write_atomic
    path = tmp_path / "work" / "notes.md"
    path.parent.mkdir()
    if existing is not None:
        path.write_text("old\n")
        path.chmod(existing)
    planted = _plant(path.with_name(".notes.md.exo-tmp"), victim)
    write_atomic(path, "new\r\n")
    assert path.read_bytes() == b"new\r\n"
    _check(path, victim, existing or _umask_default(), planted)


def test_a_failed_write_leaves_no_temporary_file(tmp_path):
    from exo.core.private import atomic_write
    target = tmp_path / "state.json"
    target.write_text("before")
    with pytest.raises(UnicodeEncodeError):
        atomic_write(target, "bad \udcff")  # fails while writing
    (tmp_path / "folder.json").mkdir()
    with pytest.raises(OSError):
        atomic_write(tmp_path / "folder.json", "{}")  # fails at the rename
    assert sorted(p.name for p in tmp_path.iterdir()) == ["folder.json", "state.json"]
    assert target.read_text() == "before"


def test_a_failed_history_write_leaves_no_temporary_file(tmp_path):
    from exo.complete import History
    path = tmp_path / "history" / "default.jsonl"
    path.mkdir(parents=True)  # a folder in the file's place: the rename fails, and history swallows the error
    History(path).add("hello")
    assert [p.name for p in path.parent.iterdir()] == ["default.jsonl"]


def test_a_symlink_at_the_path_itself_is_replaced_not_followed(victim, tmp_path):
    from exo.core.private import atomic_write
    link = _plant(tmp_path / "state.json", victim)
    atomic_write(link, b"{}", mode=0o640)
    assert victim.read_text() == VICTIM and not link.is_symlink() and link.read_bytes() == b"{}"
    assert stat.S_IMODE(link.stat().st_mode) == 0o640
