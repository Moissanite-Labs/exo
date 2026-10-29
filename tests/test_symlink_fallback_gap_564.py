"""Read-only fallback side effects and freshness constraints for #564."""
import pytest

import exo.ledger as module
from exo.ledger import Ledger


def target_and_link(tmp_path, signer):
    target = tmp_path / 'target.sqlite'
    writer = Ledger(target, signer)
    writer.append('note', {'text': 'before'})
    writer.close()
    link = tmp_path / 'linked.sqlite'
    link.symlink_to(target)
    assert not target.with_name(target.name + '-wal').exists()
    return target, link


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="Failed snapshot falls back to live SQLite and leaves target sidecars (#564)")
def test_failed_snapshot_fallback_does_not_leave_target_sidecars(tmp_path, signer, monkeypatch):
    target, link = target_and_link(tmp_path, signer)
    monkeypatch.setattr(module, '_snapshot', lambda path: None)
    before = set(tmp_path.iterdir())
    with Ledger(link) as reader:
        assert [e['body']['text'] for e in reader.iter()] == ['before']
    assert set(tmp_path.iterdir()) == before


def test_fallback_reader_observes_writer_checkpoint(tmp_path, signer, monkeypatch):
    target, link = target_and_link(tmp_path, signer)
    monkeypatch.setattr(module, '_snapshot', lambda path: None)
    with Ledger(link) as reader:
        assert reader.head().seq == 1
        with Ledger(target, signer) as writer:
            writer.append('note', {'text': 'after'})
            writer.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            assert reader.head().seq == 2
        assert reader.head().seq == 2
