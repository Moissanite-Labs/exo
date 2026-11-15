"""Storage-only tests: temporary SQLite files, no engine or model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from exo.shared_pad import (
    MAX_BYTES, MAX_HISTORY, PadValidationError, RevisionConflict,
    RevisionNotFound, SharedPadStorage,
)


@pytest.fixture
def storage(tmp_path):
    return SharedPadStorage(tmp_path / "pads.sqlite", owner="principal:alice")


def test_restart_durability_and_history(tmp_path):
    path = tmp_path / "pads.sqlite"
    pad = SharedPadStorage(path, owner="alice")
    assert pad.read("draft") == {"owner": "alice", "pad": "draft", "revision": 0,
                                 "created": None, "document": None}
    assert pad.history("draft") == []
    first = pad.update("draft", 0, "Freeform notes\n- arbitrary structure\t界")
    second = pad.update("draft", 1, "")
    del pad
    reopened = SharedPadStorage(path, owner="alice")
    assert reopened.read("draft") == second
    assert reopened.read("draft", 1) == first
    assert reopened.history("draft") == [second, first]
    assert reopened.history("draft", 1) == [second]
    assert first["created"] and first["revision"] == 1


def test_fresh_process_reads_committed_snapshot(tmp_path):
    path = tmp_path / "pads.sqlite"
    pad = SharedPadStorage(path, owner="alice")
    first = pad.update("draft", 0, "durable across process restart")
    script = """
import json, sys
sys.path.insert(0, sys.argv[1])
from exo.shared_pad import SharedPadStorage
pad = SharedPadStorage(sys.argv[2], owner="alice")
print(json.dumps(pad.read("draft")))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script,
         str(Path(__file__).resolve().parents[1] / "src"), str(path)],
        cwd=tmp_path, capture_output=True, text=True, check=True, timeout=10,
    )
    assert json.loads(result.stdout) == first


def test_relative_path_remains_bound_to_original_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pad = SharedPadStorage("pads.sqlite", owner="alice")
    first = pad.update("draft", 0, "original directory")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert pad.read("draft") == first
    assert not (elsewhere / "pads.sqlite").exists()


@pytest.mark.parametrize("initial_revision", [0, 1])
def test_concurrent_stale_writes_return_current_document(tmp_path, initial_revision):
    path = tmp_path / "pads.sqlite"
    one = SharedPadStorage(path, owner="alice")
    two = SharedPadStorage(path, owner="alice")
    if initial_revision:
        one.update("draft", 0, "initial")
    barrier = threading.Barrier(2)

    def write(storage, text):
        barrier.wait(timeout=5)
        try:
            return storage.update("draft", initial_revision, text)
        except RevisionConflict as conflict:
            return conflict

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, storage, text)
                   for storage, text in [(one, "writer one"), (two, "writer two")]]
        results = [future.result(timeout=15) for future in futures]
    winners = [result for result in results if isinstance(result, dict)]
    losers = [result for result in results if isinstance(result, RevisionConflict)]
    assert len(winners) == len(losers) == 1
    assert losers[0].current == winners[0] == one.read("draft")
    assert winners[0]["revision"] == initial_revision + 1
    assert len(one.history("draft")) == initial_revision + 1
    # The conflict rolled back and released its lock; retry is explicit.
    assert two.update("draft", winners[0]["revision"], "retry")["document"] == "retry"


def test_cross_owner_and_pad_isolation(tmp_path):
    path = tmp_path / "pads.sqlite"
    alice = SharedPadStorage(path, owner="alice")
    bob = SharedPadStorage(path, owner="bob")
    private = alice.update("same", 0, "Alice's document")
    assert bob.read("same")["document"] is None
    assert bob.history("same") == []
    with pytest.raises(RevisionNotFound):
        bob.read("same", 1)
    with pytest.raises(RevisionConflict) as exc:
        bob.update("same", 1, "not a valid expected revision for Bob")
    assert exc.value.current == bob.read("same")
    assert "Alice" not in str(exc.value.current)
    public = bob.update("same", 0, "Bob's document")
    assert alice.read("same") == private
    assert bob.read("same") == public
    assert alice.read("other")["revision"] == 0
    assert alice.history("other") == []
    alice.update("other", 0, "separate pad")
    assert alice.history("same") == [private]
    with pytest.raises(AttributeError):
        alice.owner = "bob"


def test_conflict_snapshot_cannot_mutate_storage(storage):
    current = storage.update("draft", 0, "saved")
    with pytest.raises(RevisionConflict) as exc:
        storage.update("draft", 0, "stale")
    assert exc.value.current == current
    exc.value.current["document"] = "tampered"
    assert storage.read("draft") == current
    assert storage.history("draft") == [current]


@pytest.mark.parametrize("document", [None, True, 42, {}, [], b"bytes", "\ud800", "\x00", "\r", "\x1f"])
def test_invalid_documents_do_not_write(storage, document):
    with pytest.raises(PadValidationError):
        storage.update("draft", 0, document)
    assert storage.read("draft")["revision"] == 0


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "0", None, 2**63])
def test_invalid_expected_revision(storage, value):
    with pytest.raises(PadValidationError):
        storage.update("draft", value, "text")
    assert storage.history("draft") == []


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", 2**63])
def test_invalid_read_revision(storage, value):
    with pytest.raises(PadValidationError):
        storage.read("draft", value)


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", None, MAX_HISTORY + 1])
def test_invalid_history_limit(storage, value):
    with pytest.raises(PadValidationError):
        storage.history("draft", value)


@pytest.mark.parametrize("value", [None, True, 1, "", " ", "a b", "界", "x" * 161,
                                   "x\x00", "x\ud800", "x' OR 1=1 --"])
def test_invalid_identifiers(tmp_path, storage, value):
    with pytest.raises(PadValidationError):
        SharedPadStorage(tmp_path / "invalid.sqlite", owner=value)
    assert not (tmp_path / "invalid.sqlite").exists()
    for operation in (lambda: storage.read(value), lambda: storage.history(value),
                      lambda: storage.update(value, 0, "text")):
        with pytest.raises(PadValidationError):
            operation()


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, None, MAX_BYTES + 1])
def test_invalid_byte_limit(tmp_path, value):
    with pytest.raises(PadValidationError):
        SharedPadStorage(tmp_path / "invalid.sqlite", owner="alice", max_bytes=value)
    assert not (tmp_path / "invalid.sqlite").exists()


def test_utf8_byte_limit_is_atomic(tmp_path):
    pad = SharedPadStorage(tmp_path / "pads.sqlite", owner="alice", max_bytes=6)
    first = pad.update("draft", 0, "界界")
    with pytest.raises(PadValidationError, match="UTF-8"):
        pad.update("draft", 1, "界界a")
    assert pad.read("draft") == first
    assert pad.history("draft") == [first]


def test_missing_revision_does_not_fall_back(storage):
    storage.update("draft", 0, "first")
    with pytest.raises(RevisionNotFound):
        storage.read("draft", 2)


@pytest.mark.parametrize("path", ["", ":memory:", None, True])
def test_requires_durable_path(path):
    with pytest.raises(PadValidationError):
        SharedPadStorage(path, owner="alice")