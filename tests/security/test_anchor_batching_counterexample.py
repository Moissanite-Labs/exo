"""A deferred-anchor prototype must not widen the undetectable truncation window (#256)."""
import os
import sqlite3
import subprocess
import sys

import pytest

from exo import crypto
from exo.ledger import Ledger


@pytest.mark.parametrize("defer", [False, True])
def test_crash_before_a_deferred_flush_exposes_lost_committed_events(tmp_path, monkeypatch, defer):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    path, key = tmp_path / "events.sqlite", tmp_path / "writer.key"
    crypto.save_key(crypto.keygen(), key)
    with Ledger(path, crypto.Signer.from_file("fixture", key)) as ledger:
        for i in range(3):
            ledger.append("note", {"fixture": i})
    snapshot = tmp_path / "before-batch.sqlite"
    with sqlite3.connect(path) as source, sqlite3.connect(snapshot) as copy:
        source.backup(copy)
    # This unsafe prototype exists only in the disposable child. It deliberately postpones both anchor
    # phases, then exits without cleanup, as a batched writer dying before its flush would.
    script = '''
import os, sys
from exo import crypto
from exo.ledger import Ledger
ledger = Ledger(sys.argv[1], crypto.Signer.from_file("fixture", sys.argv[2]))
if sys.argv[3] == "defer":
    ledger._catch_up = lambda *args: None
    ledger._anchor_committed = lambda *args: None
for i in range(3, 5):
    ledger.append("note", {"fixture": i})
os._exit(73)
'''
    result = subprocess.run([sys.executable, "-c", script, str(path), str(key),
                             "defer" if defer else "normal"], env=dict(os.environ),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 73, result.stderr
    with Ledger(path) as ledger:
        assert len(ledger) == 5 and ledger.verify()["ok"]
        assert {a["seq"] for _, a in ledger.anchors()} == ({3} if defer else {5})
    # Restore a consistent pre-batch database image, including its original triggers; anchors stay untouched.
    with sqlite3.connect(snapshot) as source, sqlite3.connect(path) as destination:
        source.backup(destination)
    with Ledger(path) as ledger:
        check = ledger.verify()
        assert len(ledger) == 3
        if defer:
            assert check["ok"]  # the unflushed prototype cannot distinguish this truncation from the old head
        else:
            assert not check["ok"] and "events were removed" in str(check)
