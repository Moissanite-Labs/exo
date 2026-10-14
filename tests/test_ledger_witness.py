"""An independent witness certifies durable checkpoint prefixes, not event contents or freshness."""
import concurrent.futures
import copy
import json
import sqlite3

import pytest

from exo import crypto
from exo.ledger_authority import AuthorityError, digest
from exo.ledger_authority.journal import AuthorityJournal
from exo.ledger_authority.witness import WitnessConflict, WitnessJournal, verify_receipt


@pytest.fixture
def setup(tmp_path):
    writer, checkpoint_key, witness_key = crypto.keygen(), crypto.keygen(), crypto.keygen()
    identity = {
        "version": 1, "stream_id": "test-stream", "checkpoint_pub": crypto.pubkey_b64(checkpoint_key),
        "writers": {"agent": {"pub": crypto.pubkey_b64(writer), "author": "agent", "authors": ["agent"]}},
    }
    authority = AuthorityJournal.provision(tmp_path / "authority.sqlite", identity, {"agent": writer}, checkpoint_key)
    witness = WitnessJournal.provision(tmp_path / "witness.sqlite", identity, witness_key, authority.head())
    return authority, witness, identity, writer, checkpoint_key, witness_key


def append(authority, operation="op-1", text="a private event body"):
    head = authority.head()
    return authority.append("agent", operation, {"seq": head["seq"], "chain": head["chain"]}, {
        "author": "agent", "kind": "note", "body": {"text": text}, "session": None, "refs": [],
    })


def test_witness_stores_only_commitments_and_replays_identical_receipt(setup):
    authority, witness, identity, _, _, witness_key = setup
    initial = witness.head()
    record = append(authority, text="UNIQUE_PRIVATE_BODY_NEVER_SENT_TO_WITNESS")
    receipt = witness.observe(record["checkpoint"])
    verify_receipt(identity, record["checkpoint"], receipt, crypto.pubkey_b64(witness_key), initial)
    assert receipt == witness.observe(record["checkpoint"]) == witness.lookup(1) == witness.head()
    assert witness.lookup(2) is None
    with sqlite3.connect(witness.path) as db:
        rows = db.execute("SELECT checkpoint,receipt FROM witnessed").fetchall()
    assert len(rows) == 2
    assert "UNIQUE_PRIVATE_BODY" not in repr(rows)
    assert "body" not in receipt


def test_rejects_gap_then_accepts_complete_sequence(setup):
    authority, witness, identity, _, _, witness_key = setup
    first, second = append(authority), append(authority, "op-2")
    with pytest.raises(WitnessConflict):
        witness.observe(second["checkpoint"])
    assert witness.head()["seq"] == 0
    one = witness.observe(first["checkpoint"])
    two = witness.observe(second["checkpoint"])
    verify_receipt(identity, second["checkpoint"], two, crypto.pubkey_b64(witness_key), one)
    assert two["prev_receipt"] == digest(one)


def test_rejects_valid_authority_equivocation_at_witnessed_sequence(setup, tmp_path):
    authority, witness, identity, writer, checkpoint_key, _ = setup
    original = append(authority)
    fork = AuthorityJournal.provision(tmp_path / "fork.sqlite", identity, {"agent": writer}, checkpoint_key)
    alternate = append(fork, text="different validly signed history")
    receipt = witness.observe(original["checkpoint"])
    with pytest.raises(WitnessConflict):
        witness.observe(alternate["checkpoint"])
    assert witness.head() == receipt


@pytest.mark.parametrize("change", [
    {"stream_id": "another-stream"}, {"seq": True}, {"chain": "f" * 64}, {"extra": "field"},
])
def test_untrusted_or_malformed_checkpoint_never_advances_witness(setup, change):
    authority, witness, _, _, _, _ = setup
    checkpoint = {**append(authority)["checkpoint"], **change}
    with pytest.raises(AuthorityError):
        witness.observe(checkpoint)
    assert witness.head()["seq"] == 0


def test_pinned_witness_identity_required_and_receipt_binds_exact_checkpoint(setup):
    authority, witness, identity, _, _, witness_key = setup
    record = append(authority)
    receipt = witness.observe(record["checkpoint"])
    attacker = crypto.keygen()
    forged = crypto.sign_doc({**receipt, "pub": crypto.pubkey_b64(attacker)}, attacker)
    with pytest.raises(AuthorityError):
        verify_receipt(identity, record["checkpoint"], forged, crypto.pubkey_b64(witness_key))
    second = append(authority, "op-2")
    with pytest.raises(AuthorityError):
        verify_receipt(identity, second["checkpoint"], receipt, crypto.pubkey_b64(witness_key))


@pytest.mark.parametrize("point,committed", [("before_commit", False), ("after_commit", True)])
def test_reply_loss_resolves_by_lookup_without_a_second_receipt(setup, point, committed):
    authority, witness, identity, _, _, witness_key = setup
    checkpoint = append(authority)["checkpoint"]

    def fault(current):
        if current == point:
            raise OSError("synthetic crash or lost reply")

    broken = WitnessJournal(witness.path, identity, witness_key, fault=fault)
    with pytest.raises(OSError):
        broken.observe(checkpoint)
    restarted = WitnessJournal(witness.path, identity, witness_key)
    assert (restarted.lookup(1) is not None) is committed
    receipt = restarted.observe(checkpoint)
    assert restarted.lookup(1) == receipt
    with sqlite3.connect(witness.path) as db:
        assert db.execute("SELECT count(*) FROM witnessed").fetchone()[0] == 2


def test_missing_established_state_does_not_reseed(setup):
    _, witness, identity, _, _, witness_key = setup
    witness.path.unlink()
    with pytest.raises(AuthorityError):
        WitnessJournal(witness.path, identity, witness_key)
    assert not witness.path.exists()


def test_provision_refuses_existing_state_or_non_genesis(setup, tmp_path):
    authority, witness, identity, _, checkpoint_key, witness_key = setup
    with pytest.raises(FileExistsError):
        WitnessJournal.provision(witness.path, identity, witness_key, authority.head())
    with pytest.raises(AuthorityError):
        WitnessJournal.provision(tmp_path / "bad.sqlite", identity, checkpoint_key, authority.head())
    append(authority)
    with pytest.raises(AuthorityError):
        WitnessJournal.provision(tmp_path / "bad.sqlite", identity, witness_key, authority.head())
    assert not (tmp_path / "bad.sqlite").exists()


def test_reopen_refuses_changed_identity_key_or_tampered_receipt(setup):
    authority, witness, identity, _, _, witness_key = setup
    receipt = witness.observe(append(authority)["checkpoint"])
    with pytest.raises(AuthorityError):
        WitnessJournal(witness.path, identity, crypto.keygen())
    changed = copy.deepcopy(identity)
    changed["stream_id"] = "another-stream"
    with pytest.raises(AuthorityError):
        WitnessJournal(witness.path, changed, witness_key)
    with sqlite3.connect(witness.path) as db:
        db.execute("UPDATE witnessed SET receipt=? WHERE seq=1", (json.dumps({**receipt, "chain": "f" * 64}),))
    with pytest.raises(AuthorityError):
        WitnessJournal(witness.path, identity, witness_key)


def test_two_instances_concurrently_observe_one_checkpoint_once(setup):
    authority, witness, identity, _, _, witness_key = setup
    checkpoint = append(authority)["checkpoint"]
    second = WitnessJournal(witness.path, identity, witness_key)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda w: w.observe(checkpoint), (witness, second)))
    assert replies[0] == replies[1] == witness.head()


@pytest.mark.parametrize("raw", ["[]", "null", "not-json"])
def test_corrupt_stored_document_fails_with_domain_error(setup, raw):
    _, witness, identity, _, _, witness_key = setup
    with sqlite3.connect(witness.path) as db:
        db.execute("UPDATE witnessed SET checkpoint=? WHERE seq=0", (raw,))
    with pytest.raises(AuthorityError):
        WitnessJournal(witness.path, identity, witness_key)


def test_changed_durability_mode_is_refused_on_reopen(setup):
    _, witness, identity, _, _, witness_key = setup
    db = sqlite3.connect(witness.path)
    try:
        assert db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    finally:
        db.close()
    with pytest.raises(AuthorityError, match="WAL"):
        WitnessJournal(witness.path, identity, witness_key)


def test_fresh_witness_head_exposes_authority_rollback_while_cached_receipt_proves_only_prefix(setup, tmp_path):
    authority, witness, identity, writer, checkpoint_key, witness_key = setup
    cached_checkpoint, cached_receipt = authority.head(), witness.head()
    witnessed = witness.observe(append(authority)["checkpoint"])
    restored = AuthorityJournal.provision(tmp_path / "restored.sqlite", identity, {"agent": writer}, checkpoint_key)
    # The old receipt remains cryptographically valid. It cannot prove current freshness.
    verify_receipt(identity, cached_checkpoint, cached_receipt, crypto.pubkey_b64(witness_key))
    assert witness.head() == witnessed
    assert witness.head()["seq"] > restored.head()["seq"]


@pytest.mark.parametrize("seq", [-1, True, "1", None])
def test_lookup_rejects_non_sequences(setup, seq):
    with pytest.raises(AuthorityError):
        setup[1].lookup(seq)
