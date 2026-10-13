"""Synthetic keys and disposable stores only; no production provisioning or network."""
import concurrent.futures
import sqlite3

import pytest

from exo import crypto
from exo.ledger import Ledger
from exo.ledger_authority import (AppendOutcomeUnknown, AuthorityError, AuthorityUnavailable, HeadConflict,
                                 ProjectionPending, canonical, verify_record)
from exo.ledger_authority.factory import open_writable_ledger
from exo.ledger_authority.journal import AuthorityJournal, JournalClient
from exo.ledger_authority.projection import verify_projection


@pytest.fixture
def provisioned(tmp_path):
    keys = {"memory": crypto.keygen(), "gate": crypto.keygen()}
    checkpoint = crypto.keygen()
    identity = {"version": 1, "stream_id": "TEST-stream", "checkpoint_pub": crypto.pubkey_b64(checkpoint),
                "writers": {writer: {"pub": crypto.pubkey_b64(key), "author": writer,
                                      "authors": [writer, "principal"]} for writer, key in keys.items()}}
    path = tmp_path / "authority.sqlite"
    journal = AuthorityJournal.provision(path, identity, keys, checkpoint)
    return journal, identity, keys, checkpoint


def intent(text="one", author="memory", refs=()):
    return {"author": author, "kind": "note", "body": {"text": text}, "session": None, "refs": list(refs)}


def head(journal):
    cp = journal.head()
    return {"seq": cp["seq"], "chain": cp["chain"]}


def test_idempotent_portable_event_and_independent_writer_attribution(provisioned, tmp_path):
    journal, identity, _, _ = provisioned
    before = journal.head()
    record = journal.append("memory", "op1", head(journal), intent(author="principal"))
    verify_record(identity, record, before)
    assert journal.append("memory", "op1", head(journal), intent(author="principal")) == record
    assert journal.lookup("op1") == record and journal.head()["seq"] == 1
    assert record["event"]["pub"] == identity["writers"]["memory"]["pub"]
    client = JournalClient(journal, "memory")
    with Ledger(tmp_path / "copy.sqlite", authority=client) as ledger:
        ledger.sync_authority()
        assert ledger.verify()["ok"]  # unchanged portable event verification
        assert verify_projection(ledger, identity, [before, journal.head()])["state"] == "verified_prefix"


@pytest.mark.parametrize("change", ["body", "writer", "author"])
def test_operation_id_cannot_change_meaning(provisioned, change):
    journal, *_ = provisioned
    journal.append("memory", "op1", head(journal), intent())
    with pytest.raises(AuthorityError):
        journal.append("gate" if change == "writer" else "memory", "op1", head(journal),
                       intent("different" if change == "body" else "one",
                              author="principal" if change == "author" else "memory"))


def test_all_writers_share_one_head_and_competing_append_loses(provisioned):
    journal, identity, keys, checkpoint = provisioned
    other = AuthorityJournal(journal.path, identity, keys, checkpoint)
    expected = head(journal)
    def run(pair):
        j, writer = pair
        try:
            return j.append(writer, writer + "1", expected, intent(author=writer))
        except HeadConflict:
            return "conflict"
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = list(pool.map(run, [(journal, "memory"), (other, "gate")]))
    assert result.count("conflict") == 1 and journal.head()["seq"] == 1


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
def test_crash_or_commit_failure_reconciles_without_a_second_signature(provisioned, point):
    journal, identity, keys, checkpoint = provisioned
    expected = head(journal)
    def fail(where):
        if where == point:
            raise sqlite3.OperationalError("injected process/commit interruption")
    journal.fault = fail
    with pytest.raises(sqlite3.OperationalError):
        journal.append("memory", "op1", expected, intent())
    reopened = AuthorityJournal(journal.path, identity, keys, checkpoint)
    prior = reopened.lookup("op1")
    assert (prior is not None) == (point == "after_commit")
    record = reopened.append("memory", "op1", expected, intent())
    assert reopened.head()["seq"] == 1
    if prior:
        assert record == prior


def test_lost_reply_carries_operation_id_then_recovery_copies_only(provisioned, tmp_path, monkeypatch):
    journal, *_ = provisioned
    client = JournalClient(journal, "memory")
    original = client.append
    def lost(*args):
        original(*args)
        raise AuthorityUnavailable("reply lost")
    monkeypatch.setattr(client, "append", lost)
    with Ledger(tmp_path / "copy.sqlite", authority=client) as ledger:
        with pytest.raises(AppendOutcomeUnknown) as error:
            ledger.append("note", {"text": "one"}, operation_id="stable")
        assert error.value.operation_id == "stable" and journal.head()["seq"] == 1 and len(ledger) == 0
        ledger.sync_authority()
        assert len(ledger) == 1
        monkeypatch.setattr(client, "append", original)
        ev = ledger.append("note", {"text": "one"}, operation_id="stable")
        assert ev["seq"] == 1 and len(ledger) == 1


def test_local_commit_failure_never_rewinds_authority(provisioned, tmp_path, monkeypatch):
    journal, *_ = provisioned
    with Ledger(tmp_path / "copy.sqlite", authority=JournalClient(journal, "memory")) as ledger:
        def deny_copy_commit(action, argument, *rest):
            if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT" and journal.head()["seq"] == 1:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        original_append = ledger.authority.append
        def append_then_deny(*args):
            record = original_append(*args)
            ledger.db.set_authorizer(deny_copy_commit)  # invalidate SQLite's prepared COMMIT cache
            return record
        monkeypatch.setattr(ledger.authority, "append", append_then_deny)
        with pytest.raises(ProjectionPending) as error:
            ledger.append("note", {"text": "one"}, operation_id="stable")
        assert error.value.operation_id == "stable" and journal.head()["seq"] == 1 and len(ledger) == 0
        ledger.db.set_authorizer(None)
        ledger.sync_authority()
        assert len(ledger) == 1 and ledger.verify()["ok"]


def test_missing_tail_distinguished_from_tampered_prefix(provisioned, tmp_path):
    journal, identity, *_ = provisioned
    first = journal.append("memory", "op1", head(journal), intent())
    with Ledger(tmp_path / "copy.sqlite", authority=JournalClient(journal, "memory")) as ledger:
        cps = [journal.head(0), first["checkpoint"]]
        assert verify_projection(ledger, identity, cps)["state"] == "missing_tail"
        ledger.sync_authority()
        ledger.db.execute("DROP TRIGGER events_no_update")
        ledger.db.execute("UPDATE events SET body='{}'")
        assert verify_projection(ledger, identity, cps)["state"] == "divergent"
        with pytest.raises(AuthorityError, match="diverges"):
            ledger.sync_authority()


def test_missing_established_state_or_changed_key_never_regenerates(provisioned, tmp_path):
    journal, identity, keys, checkpoint = provisioned
    with pytest.raises(AuthorityError):
        AuthorityJournal(tmp_path / "absent.sqlite", identity, keys, checkpoint)
    assert not (tmp_path / "absent.sqlite").exists()
    with pytest.raises(FileExistsError):
        AuthorityJournal.provision(journal.path, identity, keys, checkpoint)
    with pytest.raises(AuthorityError, match="keys"):
        AuthorityJournal(journal.path, identity, {**keys, "memory": crypto.keygen()}, checkpoint)
    journal.append("memory", "op1", head(journal), intent())
    with sqlite3.connect(journal.path) as db:
        db.execute("DELETE FROM operations")
    with pytest.raises(AuthorityError, match="mapping"):
        AuthorityJournal(journal.path, identity, keys, checkpoint)


def test_replay_is_byte_bounded_and_short_batch_is_not_end(provisioned, tmp_path):
    journal, *_ = provisioned
    for n in range(3):
        journal.append("memory", f"op{n}", head(journal), intent("x" * 600000))
    assert len(journal.read_since(0)) == 1
    assert len(canonical(journal.read_since(0))) < 1024 * 1024
    with Ledger(tmp_path / "copy.sqlite", authority=JournalClient(journal, "memory")) as ledger:
        ledger.sync_authority()
        assert len(ledger) == 3
        from exo.ledger_authority.verify import verify_authority
        assert verify_authority(ledger.path, JournalClient(journal, "memory"))["authority_seq"] == 3


@pytest.mark.parametrize("bad", [{"seq": False, "chain": crypto.GENESIS}, {"seq": 0.0, "chain": crypto.GENESIS},
                                 {"seq": 0, "chain": crypto.GENESIS, "extra": True}, {"seq": 1 << 64, "chain": crypto.GENESIS}, []])
def test_expected_head_has_strict_types(provisioned, bad):
    with pytest.raises(AuthorityError, match="expected head"):
        provisioned[0].append("memory", "op1", bad, intent())


def test_required_factory_refuses_before_key_loading_or_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    monkeypatch.delenv("EXO_LEDGER_AUTHORITY", raising=False)
    monkeypatch.setattr(crypto, "load_key", lambda *a: pytest.fail("must not load a private key"))
    monkeypatch.setattr(crypto, "keygen", lambda: pytest.fail("must not generate a private key"))
    with pytest.raises(AuthorityUnavailable):
        open_writable_ledger(tmp_path / "copy", "memory", tmp_path / "key", auto_keygen=True)
    assert not (tmp_path / "key").exists() and not (tmp_path / "copy").exists()


def test_installed_backend_factory_does_not_touch_local_keys(provisioned, tmp_path, monkeypatch):
    from exo.ledger_authority import factory
    journal, *_ = provisioned
    class Entry:
        name = "fixture"
        def load(self):
            return lambda path, writer: JournalClient(journal, writer)
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "fixture")
    monkeypatch.setattr(factory.importlib.metadata, "entry_points", lambda **kw: [Entry()])
    monkeypatch.setattr(crypto, "load_key", lambda *a: pytest.fail("must not load a private key"))
    with open_writable_ledger(tmp_path / "copy.sqlite", "memory", tmp_path / "unused") as ledger:
        assert ledger.append("note", {"test": True})["seq"] == 1


@pytest.mark.parametrize("bad", [[], {}, True, None])
def test_malformed_writer_and_verdict_use_authority_error(provisioned, bad):
    journal, *_ = provisioned
    with pytest.raises(AuthorityError):
        journal.append(bad, "operation", head(journal), intent())
    with pytest.raises(AuthorityError):
        journal.append("memory", "operation", head(journal),
                       {**intent(), "kind": "verdict", "body": {"verdict": bad}})


def test_missing_local_tail_still_checks_entire_checkpoint_chain(provisioned, tmp_path):
    from exo.ledger_authority import digest
    journal, identity, _, checkpoint_key = provisioned
    first = journal.append("memory", "op1", head(journal), intent())
    second = journal.append("memory", "op2", head(journal), intent("two"))
    bad = crypto.sign_doc({**second["checkpoint"], "prev_checkpoint": digest(journal.head(0))}, checkpoint_key)
    with Ledger(tmp_path / "empty.sqlite") as ledger:
        with pytest.raises(AuthorityError, match="extend"):
            verify_projection(ledger, identity, [journal.head(0), first["checkpoint"], bad])


def test_required_mode_blocks_mutated_local_signer(tmp_path, monkeypatch):
    signer = crypto.Signer("test", crypto.keygen())
    with Ledger(tmp_path / "copy.sqlite") as ledger:
        monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
        ledger.signer = signer
        with pytest.raises(ValueError, match="direct-key"):
            ledger.append("note", {})


def test_projection_rejects_a_valid_but_different_operation_reply(provisioned, tmp_path, monkeypatch):
    journal, *_ = provisioned
    client = JournalClient(journal, "memory")
    original = client.append
    monkeypatch.setattr(client, "append", lambda op, h, doc: original(op, h, intent("changed")))
    with Ledger(tmp_path / "copy.sqlite", authority=client) as ledger:
        with pytest.raises(AppendOutcomeUnknown) as error:
            ledger.append("note", {"text": "one"}, operation_id="wrong-reply")
        assert error.value.operation_id == "wrong-reply"


@pytest.fixture
def installed_runtime_authority(tmp_path, monkeypatch):
    from exo.ledger_authority import factory
    authors = {"exo-gate": ["exo-gate"], "exo-memory": ["agent", "principal"],
               "exo-learn": ["exo-learn", "principal"], "exo-onboard": ["exo-onboard"],
               "principal": ["principal"], "exo-consolidate": ["exo-consolidate"],
               "exo-ingest": ["exo-ingest", "principal"], "agent": ["agent", "principal"]}
    keys = {writer: crypto.keygen() for writer in authors}
    ck = crypto.keygen()
    identity = {"version": 1, "stream_id": "runtime-fixture", "checkpoint_pub": crypto.pubkey_b64(ck),
                "writers": {w: {"pub": crypto.pubkey_b64(keys[w]), "author": names[0], "authors": names}
                            for w, names in authors.items()}}
    journal = AuthorityJournal.provision(tmp_path / "runtime-authority.sqlite", identity, keys, ck)
    class Entry:
        name = "fixture"
        def load(self):
            return lambda path, writer: JournalClient(journal, writer)
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "fixture")
    monkeypatch.setattr(factory.importlib.metadata, "entry_points", lambda **kw: [Entry()])
    monkeypatch.setattr(crypto, "load_key", lambda *a: pytest.fail("runtime must not load a private ledger key"))
    monkeypatch.setattr(crypto, "keygen", lambda: pytest.fail("runtime must not generate a private ledger key"))
    return journal


def test_required_runtime_writers_share_authority_without_private_keys(installed_runtime_authority, template_store,
                                                                     tmp_path, monkeypatch):
    from pathlib import Path
    for plugin in ("exo-gate", "exo-memory"):
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "plugins" / plugin))
    from exo_gate.hook import LedgerSink, Settings
    from exo_memory.provider import _Runtime
    from exo_memory.config import DEFAULTS
    from exo.learn.runtime import Runtime
    from exo.learn.signals import ensure_utterance
    journal = installed_runtime_authority
    gate = LedgerSink(Settings(template_store.root, key_path=tmp_path / "no-gate-key"))
    assert gate.append({"phase": "decision"})
    memory = _Runtime(template_store.root, {**DEFAULTS, "exo_home": str(tmp_path / "home")}, "agent", episodic=False)
    memory.ledger.append("utterance", {"text": "hello"}, "principal")
    learn = Runtime(template_store.root, tmp_path / "home")
    ensure_utterance(learn, "session", "user", "synthetic answer")
    ensure_utterance(learn, "session", "assistant", "synthetic reply")
    assert journal.head()["seq"] == 4
    assert [r["checkpoint"]["writer"] for r in journal.read_since(0)] == ["exo-gate", "exo-memory", "exo-learn", "exo-learn"]
    assert memory.ledger.signer is None and learn.ledger.signer is None
    gate._ledger.close()
    memory.ledger.close()
    learn.close()


def test_required_cli_writers_do_not_load_keys(installed_runtime_authority, template_store, tmp_path):
    from types import SimpleNamespace
    from exo import ask, consolidate, onboard, cli
    from exo.ingest.cli import cmd_ingest
    common = {"key": None, "seat": "exo-consolidate"}
    with consolidate._open_ledger(SimpleNamespace(**common), template_store, True) as ledger:
        ledger.append("note", {"test": "consolidate"})
    with ask._open_ledger(template_store, "agent", None, required=True) as ledger:
        assert ledger.writable and ledger.signer is None
        ledger.append("note", {"test": "ask"})
    bank = onboard.load_bank()
    onboard.record(template_store, "principal", bank, [onboard.Turn(bank.questions[0], 1, "synthetic answer")])
    args = SimpleNamespace(log_cmd="append", key=None, author="principal", body="{}", kind="note", session=None, ref=[])
    assert cli.cmd_log(args, template_store) == 0
    source = tmp_path / "chat.json"
    source.write_text('[]')
    args = SimpleNamespace(key=None, seat="exo-ingest", ledger=None, author="principal", principal="principal",
                           path=str(source), kind="chatgpt", since=None)
    assert cmd_ingest(args, template_store) == 0
    journal = installed_runtime_authority
    assert journal.head()["seq"] >= 5
    assert {r["checkpoint"]["writer"] for r in journal.read_since(0)} >= {"exo-consolidate", "agent", "exo-onboard", "principal"}


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
def test_real_process_exit_recovers_journal_operation(provisioned, point):
    import json
    import subprocess
    import sys
    journal, identity, keys, checkpoint = provisioned
    payload = {"path": str(journal.path), "identity": identity,
               "keys": {w: bytes(k).hex() for w, k in keys.items()}, "checkpoint": bytes(checkpoint).hex(),
               "point": point}
    script = """
import json, os, sys
from exo import crypto
from exo.ledger_authority.journal import AuthorityJournal
p = json.load(sys.stdin)
keys = {w: crypto.SigningKey(bytes.fromhex(k)) for w, k in p['keys'].items()}
def crash(point):
    if point == p['point']:
        os._exit(79)
j = AuthorityJournal(p['path'], p['identity'], keys, crypto.SigningKey(bytes.fromhex(p['checkpoint'])), fault=crash)
h = j.head()
j.append('memory', 'crashed-operation', {'seq': h['seq'], 'chain': h['chain']},
         {'author': 'memory', 'kind': 'note', 'body': {'text': 'one'}, 'session': None, 'refs': []})
"""
    result = subprocess.run([sys.executable, "-c", script], input=json.dumps(payload), text=True, capture_output=True)
    assert result.returncode == 79, result.stderr
    recovered = AuthorityJournal(journal.path, identity, keys, checkpoint)
    saved = recovered.lookup("crashed-operation")
    assert (saved is not None) == (point == "after_commit")
    record = recovered.append("memory", "crashed-operation", head(recovered), intent())
    assert recovered.head()["seq"] == 1
    if saved:
        assert saved == record


def test_actual_authority_sqlite_commit_failure_releases_no_record(provisioned, monkeypatch):
    import contextlib
    journal, identity, keys, checkpoint = provisioned
    original = journal._db
    @contextlib.contextmanager
    def failing_database():
        with original() as db:
            db.set_authorizer(lambda op, arg, *rest: sqlite3.SQLITE_DENY
                              if op == sqlite3.SQLITE_TRANSACTION and arg == "COMMIT" else sqlite3.SQLITE_OK)
            yield db
    monkeypatch.setattr(journal, "_db", failing_database)
    with pytest.raises(sqlite3.DatabaseError):
        journal.append("memory", "commit-failed", head(journal), intent())
    recovered = AuthorityJournal(journal.path, identity, keys, checkpoint)
    assert recovered.lookup("commit-failed") is None and recovered.head()["seq"] == 0
    assert recovered.append("memory", "commit-failed", head(recovered), intent())["event"]["seq"] == 1


@pytest.mark.parametrize("damage", ["invalid_json", "nonobject", "wal_disabled"])
def test_established_corruption_or_lost_durability_refused(provisioned, damage):
    journal, identity, keys, checkpoint = provisioned
    with sqlite3.connect(journal.path) as db:
        if damage == "wal_disabled":
            db.execute("PRAGMA journal_mode=DELETE")
        else:
            db.execute("UPDATE checkpoints SET document=?", ("not json" if damage == "invalid_json" else "[]",))
    with pytest.raises(AuthorityError):
        AuthorityJournal(journal.path, identity, keys, checkpoint)


def test_portable_verifier_is_read_only_and_pins_independent_identity(provisioned, tmp_path):
    from exo.ledger_authority.verify import verify_files
    journal, identity, *_ = provisioned
    journal.append("memory", "op1", head(journal), intent())
    manifest, checkpoints, copy = (tmp_path / name for name in ("identity.json", "checkpoints.jsonl", "copy.sqlite"))
    manifest.write_bytes(canonical(identity))
    checkpoints.write_bytes(b"\n".join(canonical(journal.head(n)) for n in range(2)) + b"\n")
    with pytest.raises(sqlite3.OperationalError):
        verify_files(copy, manifest, checkpoints)
    assert not copy.exists()
    with Ledger(copy, authority=JournalClient(journal, "memory")) as ledger:
        assert verify_files(copy, manifest, checkpoints)["state"] == "missing_tail"
        ledger.sync_authority()
        ledger.db.execute("DROP TRIGGER events_no_update")
        assert verify_files(copy, manifest, checkpoints)["state"] == "verified_prefix"
        assert not ledger.db.execute("SELECT 1 FROM sqlite_master WHERE name='events_no_update'").fetchone()
    manifest.write_bytes(canonical({**identity, "checkpoint_pub": crypto.pubkey_b64(crypto.keygen())}))
    with pytest.raises(AuthorityError):
        verify_files(copy, manifest, checkpoints)


def test_canonical_log_verify_detects_deleted_authoritative_tail_without_repair(provisioned, tmp_path, monkeypatch, capsys):
    import json
    from types import SimpleNamespace
    from exo import cli
    from exo.ledger import SCHEMA
    from exo.ledger_authority import factory
    journal, *_ = provisioned
    copy = tmp_path / "copy.sqlite"
    with Ledger(copy, authority=JournalClient(journal, "memory")) as ledger:
        ledger.append("note", {"text": "one"})
        ledger.db.execute("DROP TRIGGER events_no_delete")
        ledger.db.execute("DELETE FROM events")
        ledger.db.executescript(SCHEMA)
    class Entry:
        name = "fixture"
        def load(self):
            return lambda path, writer: JournalClient(journal, "memory")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "fixture")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    monkeypatch.setattr(factory.importlib.metadata, "entry_points", lambda **kw: [Entry()])
    assert cli.cmd_log(SimpleNamespace(log_cmd="verify"), SimpleNamespace(ledger_path=copy)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "missing_tail" and result["authority_seq"] == 1 and result["local_seq"] == 0
    from exo import memory_cmd
    monkeypatch.setattr(memory_cmd, "_stores", lambda args: [SimpleNamespace(root=tmp_path, ledger_path=copy)])
    assert memory_cmd.verify_ledgers(None)[tmp_path.name]["state"] == "missing_tail"
    with sqlite3.connect(copy) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0


@pytest.mark.parametrize("damage, expected", [("none", "verified_prefix"), ("body", "divergent"),
                                             ("extra", "unconfirmed_tail"), ("missing_copy", "unverifiable"),
                                             ("offline", "unverifiable")])
def test_configured_cli_verification_is_pinned_read_only_and_fail_closed(provisioned, tmp_path, monkeypatch,
                                                                       capsys, damage, expected):
    import json
    from types import SimpleNamespace
    from exo import cli
    from exo.ledger_authority import factory
    journal, _, keys, _ = provisioned
    path = tmp_path / "verify.sqlite"
    with Ledger(path, authority=JournalClient(journal, "memory")) as ledger:
        ledger.append("note", {"original": True})
        if damage == "body":
            ledger.db.execute("DROP TRIGGER events_no_update")
            ledger.db.execute("UPDATE events SET body='{}'")
    if damage == "extra":
        with Ledger(path, crypto.Signer("memory", keys["memory"])) as ledger:
            ledger.append("note", {"unconfirmed": True})
    if damage == "missing_copy":
        path.unlink()
    calls = []
    class Entry:
        name = "fixture"
        def load(self):
            def reader(copy, writer):
                assert writer is None
                calls.append("read-only")
                if damage == "offline":
                    raise AuthorityUnavailable("service unavailable")
                return JournalClient(journal, "memory")
            return reader
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "fixture")
    monkeypatch.setattr(factory.importlib.metadata, "entry_points", lambda **kw: [Entry()])
    monkeypatch.setattr(Ledger, "sync_authority", lambda *a: pytest.fail("verification repaired the local copy"))
    monkeypatch.setattr(Ledger, "__init__", lambda *a, **kw: pytest.fail("verification opened writable Ledger"))
    code = cli.cmd_log(SimpleNamespace(log_cmd="verify"), SimpleNamespace(ledger_path=path))
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == expected and result["fresh"] is False
    assert code == (0 if damage == "none" else 1) and calls == ["read-only"]
    from exo import memory_cmd
    monkeypatch.setattr(memory_cmd, "_stores", lambda args: [SimpleNamespace(root=tmp_path, ledger_path=path)])
    assert memory_cmd.verify_ledgers(None)[tmp_path.name]["state"] == expected
    assert memory_cmd.cmd_memory(SimpleNamespace(action="verify", agent=None, json=False)) == code
    if damage == "missing_copy":
        assert not path.exists()
    if damage == "body":
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT body FROM events").fetchone()[0] == "{}"
            assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='events_no_update'").fetchone()


def test_required_cli_verify_without_backend_never_accepts_legacy_copy(tmp_path, monkeypatch, capsys):
    import json
    from types import SimpleNamespace
    from exo import cli
    path = tmp_path / "absent.sqlite"
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    monkeypatch.delenv("EXO_LEDGER_AUTHORITY", raising=False)
    assert cli.cmd_log(SimpleNamespace(log_cmd="verify"), SimpleNamespace(ledger_path=path)) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "unverifiable"
    assert not path.exists()


def test_live_verifier_retries_head_change_instead_of_false_unconfirmed_tail(provisioned, tmp_path, monkeypatch):
    from exo.ledger_authority.verify import verify_authority
    journal, *_ = provisioned
    base = JournalClient(journal, "memory")
    with Ledger(tmp_path / "copy.sqlite", authority=base) as ledger:
        ledger.append("note", {"text": "one"})
        reader = JournalClient(journal, "memory")
        original = reader.read_since
        advanced = False
        def concurrent_append(seq, limit):
            nonlocal advanced
            records = original(seq, limit)
            if not advanced:
                advanced = True
                journal.append("memory", "concurrent", head(journal), intent("two"))
                ledger.sync_authority()  # the other writer, not the verifier, updates its copy
            return records
        monkeypatch.setattr(reader, "read_since", concurrent_append)
        result = verify_authority(ledger.path, reader)
        assert result["ok"] and result["authority_seq"] == result["local_seq"] == 2


def test_live_verifier_fails_closed_when_head_keeps_changing(provisioned, tmp_path, monkeypatch):
    from exo.ledger_authority.verify import verify_authority
    journal, *_ = provisioned
    reader = JournalClient(journal, "memory")
    original = reader.head
    count = 0
    def advancing_head(seq=None):
        nonlocal count
        if seq is None:
            count += 1
            journal.append("memory", f"busy{count}", head(journal), intent(str(count)))
        return original(seq)
    monkeypatch.setattr(reader, "head", advancing_head)
    with Ledger(tmp_path / "copy.sqlite") as ledger:
        with pytest.raises(AuthorityUnavailable, match="advanced during verification"):
            verify_authority(ledger.path, reader)
        assert len(ledger) == 0
    assert count == 6


def test_local_factory_tolerates_a_concurrent_first_keygen(tmp_path, monkeypatch):
    """Two processes creating the same seat key at once: the loser uses the winner's key (as ensure_key did)."""
    monkeypatch.delenv("EXO_LEDGER_AUTHORITY", raising=False)
    monkeypatch.delenv("EXO_LEDGER_AUTHORITY_REQUIRED", raising=False)
    key_path = tmp_path / "keys" / "seat.key"
    real_save = crypto.save_key

    def lose_race(key, path):
        real_save(crypto.keygen(), path)  # the other process wins between exists() and O_EXCL
        raise FileExistsError(path)

    monkeypatch.setattr(crypto, "save_key", lose_race)
    with open_writable_ledger(tmp_path / "ledger.sqlite", "seat", key_path, auto_keygen=True) as led:
        assert led.append("note", {"text": "first"})["author"] == "seat"


def test_local_factory_missing_key_without_keygen_names_the_fix(tmp_path, monkeypatch):
    monkeypatch.delenv("EXO_LEDGER_AUTHORITY", raising=False)
    monkeypatch.delenv("EXO_LEDGER_AUTHORITY_REQUIRED", raising=False)
    with pytest.raises(RuntimeError, match="exo keygen seat"):
        open_writable_ledger(tmp_path / "ledger.sqlite", "seat", tmp_path / "missing.key")


def test_base64_alias_of_checkpoint_key_is_rejected():
    """Different Base64 text can decode to the same key when unused padding bits differ."""
    from exo.ledger_authority import validate_identity
    key = crypto.keygen()
    canonical_pub = crypto.pubkey_b64(key)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    last = canonical_pub[-2]
    alt = next(c for c in alphabet
               if c != last and (alphabet.index(c) >> 2) == (alphabet.index(last) >> 2))
    aliased_pub = canonical_pub[:-2] + alt + "="
    assert aliased_pub != canonical_pub
    assert crypto.unb64(aliased_pub) == crypto.unb64(canonical_pub)
    identity = {"version": 1, "stream_id": "TEST-stream", "checkpoint_pub": canonical_pub,
                "writers": {"memory": {"pub": aliased_pub, "author": "memory", "authors": ["memory"]}}}
    with pytest.raises(AuthorityError, match="must be separate"):
        validate_identity(identity)


def test_distinct_keys_still_pass_separation():
    from exo.ledger_authority import validate_identity
    checkpoint = crypto.keygen()
    writer = crypto.keygen()
    identity = {"version": 1, "stream_id": "TEST-stream", "checkpoint_pub": crypto.pubkey_b64(checkpoint),
                "writers": {"memory": {"pub": crypto.pubkey_b64(writer), "author": "memory",
                                       "authors": ["memory"]}}}
    assert validate_identity(identity)["checkpoint_pub"] == crypto.pubkey_b64(checkpoint)
