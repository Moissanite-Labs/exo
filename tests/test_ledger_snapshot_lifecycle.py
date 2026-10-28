"""Symlink readers defer snapshots and reject damaged backup images (#535, #565)."""
import sqlite3

import pytest

from exo import ledger as module
from exo.ledger import Ledger


def make_target(tmp_path, signer):
    target = tmp_path / "target.sqlite"
    with Ledger(target, signer) as writer:
        writer.append("note", {"n": 1}, "agent")
    link = tmp_path / "reader.sqlite"
    link.symlink_to(target)
    return target, link


def test_first_read_includes_appends_after_open(tmp_path, signer):
    target, link = make_target(tmp_path, signer)
    reader = Ledger(link)
    with Ledger(target, signer) as writer:
        writer.append("note", {"n": 2}, "agent")
    assert len(reader) == 2
    with Ledger(target, signer) as writer:
        writer.append("note", {"n": 3}, "agent")
    assert len(reader) == 2
    reader.close()


def test_unread_close_never_copies_or_opens_target(tmp_path, signer, monkeypatch):
    _, link = make_target(tmp_path, signer)
    copied = []
    original = module._open_symlinked
    def open_target(path):
        copied.append(path)
        return original(path)
    monkeypatch.setattr(module, "_open_symlinked", open_target)
    reader = Ledger(link)
    reader.close()
    assert copied == []
    with pytest.raises(sqlite3.ProgrammingError):
        len(reader)


def test_a_backup_with_invalid_schema_is_not_accepted_as_a_snapshot(tmp_path, signer, monkeypatch):
    target, _ = make_target(tmp_path, signer)
    original = sqlite3.connect
    copied = []
    class DamagingCopy(sqlite3.Connection):
        def backup(self, destination, **kwargs):
            super().backup(destination, **kwargs)
            destination.execute("PRAGMA writable_schema=ON")
            destination.execute("UPDATE sqlite_master SET rootpage=999999 WHERE name='events'")
            destination.execute("PRAGMA writable_schema=RESET")
            copied.append(destination)
    monkeypatch.setattr(sqlite3, "connect", lambda database, *args, **kwargs: original(
        database, *args, **({**kwargs, "factory": DamagingCopy}
                           if isinstance(database, str) and "immutable=1" in database else kwargs)))
    snapshot = module._snapshot(target)
    if snapshot is not None:
        snapshot.close()
    assert snapshot is None
    assert copied
    with pytest.raises(sqlite3.ProgrammingError):
        copied[0].execute("SELECT 1")
