"""Pet XP is a verified local projection, never a writable score or a new source of authority."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from exo import crypto
from exo.config import Config
from exo.keyring import KeyRegistry
from exo.ledger import Ledger, anchor_paths_for
from exo.pet.scoring import score, xp_for_level
from exo.pet.status import PetStatus, command
from exo.principal import CALLER, Caller
from exo.protocol import schema as s
from exo.server.engine import CommandFailed


class Events:
    def __init__(self):
        self.rows = []

    def add(self, kind, body, *, author="principal", session="s1", at="2026-01-01T00:00:00Z", refs=()):
        row = {"id": f"e{len(self.rows) + 1}", "kind": kind, "body": body, "author": author, "session": session, "ts": at, "refs": list(refs)}
        self.rows.append(row)
        return row["id"]

    def turn(self, text, *, at="2026-01-01T00:00:00Z", author="agent", session="s1", **extra):
        user = self.add("utterance", {"role": "user", "text": text}, at=at, session=session)
        self.add("utterance", {"role": "assistant", "text": "answer", **extra}, author=author, at=at, session=session, refs=[user])


def test_ladder_exact_gates_and_pending_runtime_hatch():
    assert [xp_for_level(x) for x in [1, 6, 16, 30, 45, 60, 72, 84, 92, 99]] == [0, 260, 2343, 8757, 20158, 36245, 52489, 71731, 86224, 100000]
    for invalid in (True, 0, 100, 1.5):
        with pytest.raises(ValueError):
            xp_for_level(invalid)
    events = Events()
    for i in range(30):
        events.turn(str(i))
    result = score(events.rows, ["principal"])
    assert result["personal"] == {"xp": 305, "xp_half_units": 610, "level": 6, "earned_form": "cell"}
    assert result["form"] == "egg" and result["next_form"] == "cell" and result["progress"] == 1


def test_same_text_across_sessions_uses_exact_rolling_24_hours():
    events = Events()
    events.turn("same text", at="2026-01-01T00:00:00Z")
    events.turn("same   text", at="2026-01-01T23:59:59Z", session="s2")
    assert score(events.rows, ["principal"])["personal"]["xp"] == 10
    # Exactly 24 hours after the last repetition, not just a different UTC date.
    events.turn("same text", at="2026-01-02T23:59:59Z", session="s3")
    assert score(events.rows, ["principal"])["personal"]["xp"] == 20


def test_day_curve_uses_half_units_and_resets_at_utc_boundary():
    events = Events()
    for _ in range(100):
        events.add("answer", {"text": "teaching"})  # 600 raw XP
    proposal = events.add("belief_proposed", {"proposal_id": "p1"}, author="agent")
    events.add("belief_accepted", {"proposal_id": "p1"}, refs=[proposal])
    result = score(events.rows, ["principal"])
    assert result["personal"]["xp"] == 607.5 and result["personal"]["xp_half_units"] == 1215
    for _ in range(200):
        events.add("ruling", {"text": "a ruling"})
    assert score(events.rows, ["principal"])["personal"]["xp"] == 1000
    events.add("answer", {"text": "new UTC day"}, at="2026-01-02T01:00:00+01:00")
    assert score(events.rows, ["principal"])["personal"]["xp"] == 1006


@pytest.mark.parametrize("author", ["principal", "tool", "job", "system", "delegate", "schedule", "local", "peer:friend", "bus:seat", "bot:b1"])
def test_non_agent_assistant_claims_earn_nothing(author):
    events = Events(); events.turn("instruction", author=author)
    assert score(events.rows, ["principal"])["personal"]["xp"] == 0


@pytest.mark.parametrize("mark", ["source", "origin", "src", "source_ts", "imported", "copied", "forked_from"])
def test_imported_copied_or_backfilled_utterances_earn_nothing(mark):
    events = Events(); events.turn("instruction", **{mark: "import"})
    assert score(events.rows, ["principal"])["personal"]["xp"] == 0


def test_principal_roles_session_pairing_and_duplicate_replay():
    events = Events()
    user = events.add("utterance", {"role": "user", "text": "instruction"}, author="peer:friend")
    events.add("utterance", {"role": "assistant", "text": "answer"}, author="agent", refs=[user])
    user = events.add("utterance", {"role": "user", "text": "real instruction"})
    events.add("utterance", {"role": "assistant", "text": "wrong session"}, author="agent", session="s2", refs=[user])
    events.add("utterance", {"role": "assistant", "text": "right session"}, author="agent", refs=[user])
    assert score(events.rows, ["principal"])["personal"]["xp"] == 10
    assert score(events.rows + events.rows, ["principal"]) == score(events.rows, ["principal"])
    assert score(tuple(events.rows), ["principal"]) == score(iter(events.rows), ["principal"])


def test_promotion_births_require_evidence_and_never_double_count_or_decay():
    events = Events()
    events.add("note", {"type": "learn_promotion", "proposal_id": "invented"}, author="agent")
    proposed = events.add("belief_proposed", {"proposal_id": "p1"}, author="exo-learn")
    accepted = events.add("belief_accepted", {"proposal_id": "p1"}, author="exo-learn", refs=[proposed])
    events.add("note", {"type": "learn_promotion", "proposal_id": "p1"}, author="exo-learn", refs=[accepted])
    events.add("note", {"type": "learn_revert", "proposal_id": "p1"}, author="exo-learn", refs=[accepted])
    events.add("belief_accepted", {"proposal_id": "p1"}, refs=[proposed])
    events.add("action", {"type": "delegation", "status": "done", "result": "claims success"}, author="agent")
    result = score(events.rows, ["principal"])
    assert result["learned_count"] == 1 and result["personal"]["xp"] == 15


def test_verdict_weights_and_nonprincipal_teaching_are_not_rulings():
    events = Events(); target = events.add("artifact", {})
    for verdict in ("accept", "reject", "edit", "should_have_been"):
        events.add("verdict", {"target": target, "verdict": verdict})
    events.add("ruling", {"text": "agent claim"}, author="agent")
    events.add("answer", {"text": "peer claim"}, author="peer:friend")
    assert score(events.rows, ["principal"])["personal"]["xp"] == 24


def test_one_form_per_day_after_explicit_test_hatch_date():
    events = Events()
    for day in range(15):
        at = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat()
        for i in range(200):
            events.add("ruling", {"text": "learning"}, at=at)
    assert score(events.rows, ["principal"])["personal"]["earned_form"] == "cube"
    assert score(events.rows, ["principal"], hatch_at="2026-02-01T00:00:00Z", as_of="2026-02-01T00:00:00Z")["form"] == "cell"
    assert score(events.rows, ["principal"], hatch_at="2026-02-01T00:00:00Z", as_of="2026-02-02T00:00:00Z")["form"] == "tile"
    assert score(events.rows, ["principal"], hatch_at="2026-02-01T00:00:00Z", as_of="2026-02-03T00:00:00Z")["form"] == "cube"


@pytest.fixture
def store_rig(tmp_path, monkeypatch):
    home, root = tmp_path / "home", tmp_path / "principal"
    home.mkdir(); root.mkdir()
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.delenv("EXO_LEDGER_AUTHORITY", raising=False)
    monkeypatch.delenv("EXO_LEDGER_AUTHORITY_REQUIRED", raising=False)
    Config({"learn": {"store": str(root)}}, home).save()
    signer = crypto.Signer("agent", crypto.keygen())
    registry = KeyRegistry(KeyRegistry.path_for(home), {"keys": {"agent": [signer.pub]}, "principals": []})
    registry.path.parent.mkdir(parents=True)
    registry.path.write_text(json.dumps({"v": 1, "keys": registry.keys, "principals": []}))
    engine = SimpleNamespace(home=home)
    yield engine, root, signer, registry


def test_no_store_missing_and_verified_anchored_empty_are_distinct(store_rig):
    engine, root, signer, registry = store_rig
    service = PetStatus(engine)
    missing = asyncio.run(service.read())
    assert missing["state"] == "unavailable" and missing["personal"]["xp"] is None
    assert not (root / "ledger.sqlite").exists()
    with Ledger(root / "ledger.sqlite", signer) as ledger:
        ledger.anchor()
    empty = asyncio.run(service.read())
    assert empty["state"] == "empty" and empty["personal"]["xp"] == 0 and empty["personal"]["level"] == 1
    assert empty["form"] == "egg" and empty["community"] == {"state": "pending", "hatch_at": None}
    Config({}, engine.home).save()
    assert asyncio.run(service.read())["state"] == "no_store"


def signed_turn(root, signer):
    with Ledger(root / "ledger.sqlite", signer) as ledger:
        user = ledger.append("utterance", {"text": "SECRET CONTENT", "role": "user"}, "principal", session="s1")
        ledger.append("utterance", {"text": "SECRET ANSWER", "role": "assistant"}, "agent", session="s1", refs=[user["id"]])


def test_signed_verified_status_read_only_and_no_prose_export(store_rig):
    engine, root, signer, registry = store_rig
    signed_turn(root, signer)
    paths = [root / "ledger.sqlite", *anchor_paths_for(root / "ledger.sqlite"), registry.path]
    before = {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths}
    result = asyncio.run(PetStatus(engine).read())
    assert result["state"] == "ready" and result["personal"]["xp"] == 10
    assert "SECRET" not in json.dumps(result) and str(root) not in json.dumps(result)
    assert {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths} == before


def test_tamper_truncation_freezes_only_verified_memory_and_cold_start_is_unknown(store_rig):
    engine, root, signer, registry = store_rig
    signed_turn(root, signer)
    service = PetStatus(engine)
    good = asyncio.run(service.read())
    db = sqlite3.connect(root / "ledger.sqlite")
    trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='events_no_delete'").fetchone()[0]
    db.execute("DROP TRIGGER events_no_delete")
    db.execute("DELETE FROM events WHERE seq=2")
    db.execute(trigger); db.commit(); db.close()
    stale = asyncio.run(service.read())
    assert stale["state"] == "unverified" and stale["stale"] and stale["personal"] == good["personal"]
    assert asyncio.run(PetStatus(engine).read())["personal"]["xp"] is None


def test_unpinned_valid_signature_earns_nothing(store_rig):
    engine, root, _, registry = store_rig
    signed_turn(root, crypto.Signer("rogue", crypto.keygen()))
    result = asyncio.run(PetStatus(engine).read())
    assert result["state"] == "unverified" and result["personal"]["xp"] is None


def test_read_only_api_never_repairs_triggers_or_creates_paths(store_rig):
    _, root, signer, _ = store_rig
    signed_turn(root, signer)
    db = sqlite3.connect(root / "ledger.sqlite")
    trigger = db.execute("SELECT name FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone()[0]
    db.execute(f'DROP TRIGGER "{trigger}"'); db.close()
    with Ledger.read_only(root / "ledger.sqlite") as reader:
        assert not reader.verify()["ok"]
    db = sqlite3.connect(root / "ledger.sqlite")
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (trigger,)).fetchone(); db.close()
    with pytest.raises(FileNotFoundError):
        Ledger.read_only(root / "missing" / "ledger.sqlite")
    assert not (root / "missing").exists()


def test_pet_command_is_principal_only_and_validates_arguments_before_read(store_rig):
    engine, root, signer, _ = store_rig
    signed_turn(root, signer)
    async def scenario():
        token = CALLER.set(Caller("local"))
        try:
            with pytest.raises(CommandFailed, match="forbidden"):
                await command(engine, s.CommandRun(name="pet"))
        finally:
            CALLER.reset(token)
        token = CALLER.set(Caller("principal"))
        try:
            with pytest.raises(CommandFailed, match="bad_request"):
                await command(engine, s.CommandRun(name="pet", args="reset"))
            assert (await command(engine, s.CommandRun(name="pet", args="status")))["personal"]["xp"] == 10
        finally:
            CALLER.reset(token)
    asyncio.run(scenario())


@pytest.mark.parametrize('author', ['tool', 'job', 'system', 'delegate', 'schedule', 'peer:friend', 'bus:seat'])
def test_signed_pinned_non_agent_assistant_is_not_a_turn(store_rig, author):
    engine, root, signer, _ = store_rig
    with Ledger(root / 'ledger.sqlite', signer) as ledger:
        user = ledger.append('utterance', {'role': 'user', 'text': 'principal instruction'}, 'principal', session='s1')
        ledger.append('utterance', {'role': 'assistant', 'text': 'not an agent answer'}, author, session='s1', refs=[user['id']])
    result = asyncio.run(PetStatus(engine).read())
    assert result['state'] == 'ready' and result['personal']['xp'] == 0


def test_broken_anchor_freezes_memory_without_repair_or_cache_trust(store_rig):
    engine, root, signer, registry = store_rig
    signed_turn(root, signer)
    service = PetStatus(engine)
    good = asyncio.run(service.read())
    anchor = anchor_paths_for(root / 'ledger.sqlite')[0]
    body = json.loads(anchor.read_text()); body['chain'] = '0' * 64
    anchor.write_text(json.dumps(body))
    corrupted = anchor.read_bytes()
    result = asyncio.run(service.read())
    assert result['state'] == 'unverified' and result['stale'] and result['personal'] == good['personal']
    assert anchor.read_bytes() == corrupted
    assert asyncio.run(PetStatus(engine).read())['personal']['xp'] is None


def test_changing_registry_or_store_cannot_inherit_frozen_growth(store_rig):
    engine, root, signer, registry = store_rig
    signed_turn(root, signer)
    service = PetStatus(engine)
    assert asyncio.run(service.read())['personal']['xp'] == 10
    registry.path.write_text(json.dumps({'v': 1, 'keys': {}, 'principals': []}))
    assert asyncio.run(service.read())['personal']['xp'] is None
    other = root.parent / 'other-principal'
    Config({'learn': {'store': str(other)}}, engine.home).save()
    result = asyncio.run(service.read())
    assert result['state'] == 'unavailable' and result['personal']['xp'] is None and not other.exists()


def test_missing_registry_is_not_created_and_unanchored_is_not_verified(store_rig):
    engine, root, signer, registry = store_rig
    signed_turn(root, signer)
    registry.path.unlink()
    result = asyncio.run(PetStatus(engine).read())
    assert result['state'] == 'unverified' and result['personal']['xp'] is None and not registry.path.exists()
    registry.path.write_text(json.dumps({'v': 1, 'keys': registry.keys, 'principals': []}))
    for anchor in anchor_paths_for(root / 'ledger.sqlite'):
        anchor.unlink()
    result = asyncio.run(PetStatus(engine).read())
    assert result['state'] == 'unverified' and result['verification']['reason'] == 'unanchored'


def test_invalid_signature_and_file_symlink_never_score(store_rig):
    engine, root, signer, _ = store_rig
    signed_turn(root, signer)
    db = sqlite3.connect(root / 'ledger.sqlite')
    trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='events_no_update'").fetchone()[0]
    db.execute('DROP TRIGGER events_no_update')
    db.execute("UPDATE events SET sig='forged' WHERE seq=2")
    db.execute(trigger); db.commit(); db.close()
    assert asyncio.run(PetStatus(engine).read())['personal']['xp'] is None
    moved = root / 'moved.sqlite'; (root / 'ledger.sqlite').rename(moved)
    (root / 'ledger.sqlite').symlink_to(moved)
    assert asyncio.run(PetStatus(engine).read())['state'] == 'unverified'


def authority_fixture(path, name):
    from exo.ledger_authority.journal import AuthorityJournal, JournalClient
    key, checkpoint = crypto.keygen(), crypto.keygen()
    identity = {'version': 1, 'stream_id': name, 'checkpoint_pub': crypto.pubkey_b64(checkpoint),
                'writers': {'memory': {'pub': crypto.pubkey_b64(key), 'author': 'memory', 'authors': ['memory', 'principal']}}}
    journal = AuthorityJournal.provision(path, identity, {'memory': key}, checkpoint)
    return JournalClient(journal, 'memory')


def test_authority_verifies_exact_snapshot_and_cache_binds_its_identity(store_rig, monkeypatch):
    import exo.pet.status as module
    engine, root, _, _ = store_rig
    authority = authority_fixture(root.parent / 'authority.sqlite', 'authority-a')
    with Ledger(root / 'ledger.sqlite', authority=authority, authority_author='memory') as ledger:
        user = ledger.append('utterance', {'text': 'instruction', 'role': 'user'}, 'principal', session='s1')
        ledger.append('utterance', {'text': 'answer', 'role': 'assistant'}, 'memory', session='s1', refs=[user['id']])
    monkeypatch.setenv('EXO_LEDGER_AUTHORITY', 'fixture')
    monkeypatch.setattr(module, 'open_authority', lambda path: authority)
    service = PetStatus(engine)
    result = asyncio.run(service.read())
    assert result['state'] == 'ready' and result['personal']['xp'] == 10
    assert result['verification']['source'] == 'authority' and result['verification']['fresh'] is False
    other = authority_fixture(root.parent / 'other-authority.sqlite', 'authority-b')
    monkeypatch.setattr(module, 'open_authority', lambda path: other)
    result = asyncio.run(service.read())
    assert result['state'] == 'unavailable' and result['verification']['reason'] == 'ledger_busy'
    assert result['personal']['xp'] is None and not result['stale']
    # A configured backend that cannot authenticate its identity never falls back to legacy keys or old XP.
    def unavailable(path):
        raise RuntimeError('a backend failure must not leak this text or a path')
    monkeypatch.setattr(module, 'open_authority', unavailable)
    result = asyncio.run(service.read())
    assert result['state'] == 'unavailable' and result['personal']['xp'] is None
    assert 'backend failure' not in json.dumps(result)


def test_concurrent_calls_share_reader_and_cancellation_does_not_cancel_it(store_rig, monkeypatch):
    engine, root, signer, _ = store_rig
    signed_turn(root, signer)
    service = PetStatus(engine)
    started, release = threading.Event(), threading.Event()
    calls = []
    original = service._read
    def held(context):
        calls.append(context.identity); started.set()
        assert release.wait(5)
        return original(context)
    monkeypatch.setattr(service, '_read', held)
    async def scenario():
        first = asyncio.create_task(service.read())
        assert await asyncio.to_thread(started.wait, 3)
        second = asyncio.create_task(service.read())
        # Ensure second's context work has returned without relying on a wall-clock delay.
        while second._fut_waiter is None or second._fut_waiter is first._fut_waiter:
            await asyncio.sleep(0)
        for _ in range(5):
            await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert service._task is not None and not service._task.cancelled()
        release.set()
        result = await second
        assert result['personal']['xp'] == 10
    asyncio.run(asyncio.wait_for(scenario(), 10))
    assert len(calls) == 1


def test_configuration_change_while_reading_never_returns_old_principal_growth(store_rig, monkeypatch):
    engine, root, signer, _ = store_rig
    signed_turn(root, signer)
    service = PetStatus(engine)
    started, release = threading.Event(), threading.Event()
    original = service._read
    def held(context):
        if context.root == root:
            started.set(); assert release.wait(5)
        return original(context)
    monkeypatch.setattr(service, '_read', held)
    async def scenario():
        first = asyncio.create_task(service.read())
        assert await asyncio.to_thread(started.wait, 3)
        Config({'learn': {'store': str(root.parent / 'new-owner')}}, engine.home).save()
        second = await service.read()
        assert second['personal']['xp'] is None and second['verification']['reason'] == 'configuration_changed'
        release.set()
        result = await first
        assert result['personal']['xp'] is None
    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_legacy_append_after_snapshot_is_not_false_tamper(store_rig, monkeypatch):
    """An independent writer process commits after the reader has pinned its snapshot."""
    import subprocess
    import sys
    engine, root, signer, _ = store_rig
    signed_turn(root, signer)
    key = root / "writer.key"
    crypto.save_key(signer.key, key)
    original = Ledger.read_only
    def interleave(path):
        reader = original(path)
        try:
            subprocess.run([sys.executable, "-c", "from exo.ledger import Ledger; from exo.crypto import Signer; import sys; "
                            "l=Ledger(sys.argv[1],Signer.from_file('agent',sys.argv[2])); "
                            "l.append('answer',{'text':'synthetic'},'principal'); l.close()", str(path), str(key)], check=True)
        except BaseException:
            reader.close(); raise
        return reader
    monkeypatch.setattr(Ledger, "read_only", interleave)
    service = PetStatus(engine)
    result = asyncio.run(service.read())
    assert result["state"] == "ready" and result["personal"]["xp"] == 10
    monkeypatch.setattr(Ledger, "read_only", original)
    assert asyncio.run(service.read())["personal"]["xp"] == 16


def test_legacy_capture_before_snapshot_accepts_newer_intact_rows(store_rig, monkeypatch):
    engine, root, signer, _ = store_rig
    signed_turn(root, signer)
    original = Ledger._read_anchor
    once = []
    def interleave(path):
        anchor = original(path)
        if not once:
            once.append(True)
            with Ledger(root / "ledger.sqlite", signer) as writer:
                writer.append("answer", {"text": "synthetic"}, "principal")
        return anchor
    monkeypatch.setattr(Ledger, "_read_anchor", staticmethod(interleave))
    result = asyncio.run(PetStatus(engine).read())
    assert result["state"] == "ready" and result["personal"]["xp"] == 16


def test_authority_capture_excludes_newer_tail_then_next_read_confirms_it(store_rig, monkeypatch):
    import exo.pet.status as module
    engine, root, _, _ = store_rig
    authority = authority_fixture(root.parent / "authority.sqlite", "authority-a")
    with Ledger(root / "ledger.sqlite", authority=authority, authority_author="memory") as writer:
        writer.append("answer", {"text": "synthetic"}, "principal")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "fixture")
    monkeypatch.setattr(module, "open_authority", lambda path: authority)
    original = module.capture_authority
    once = []
    def interleave(a):
        captured = original(a)
        if not once:
            once.append(True)
            with Ledger(root / "ledger.sqlite", authority=authority, authority_author="memory") as other:
                other.append("answer", {"text": "newer"}, "principal")
        return captured
    monkeypatch.setattr(module, "capture_authority", interleave)
    service = PetStatus(engine)
    first = asyncio.run(service.read())
    assert first["state"] == "ready" and first["personal"]["xp"] == 6
    assert first["verification"]["checked"] == 1 and first["verification"]["fresh"] is False
    second = asyncio.run(service.read())
    assert second["personal"]["xp"] == 12 and second["verification"]["checked"] == 2


def test_authority_append_after_local_snapshot_preserves_verified_prefix(store_rig, monkeypatch):
    import exo.pet.status as module
    engine, root, _, _ = store_rig
    authority = authority_fixture(root.parent / "authority.sqlite", "authority-a")
    with Ledger(root / "ledger.sqlite", authority=authority, authority_author="memory") as writer:
        writer.append("answer", {"text": "synthetic"}, "principal")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "fixture")
    monkeypatch.setattr(module, "open_authority", lambda path: authority)
    original = Ledger.read_only
    def interleave(path):
        reader = original(path)
        try:
            with Ledger(path, authority=authority, authority_author="memory") as other:
                other.append("answer", {"text": "newer"}, "principal")
        except BaseException:
            reader.close(); raise
        return reader
    monkeypatch.setattr(Ledger, "read_only", interleave)
    result = asyncio.run(PetStatus(engine).read())
    assert result["state"] == "ready" and result["personal"]["xp"] == 6


def test_authority_real_missing_tail_still_rejected(store_rig, monkeypatch):
    import exo.pet.status as module
    engine, root, _, _ = store_rig
    authority = authority_fixture(root.parent / "authority.sqlite", "authority-a")
    with Ledger(root / "ledger.sqlite", authority=authority, authority_author="memory") as writer:
        writer.append("answer", {"text": "synthetic"}, "principal")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "fixture")
    monkeypatch.setattr(module, "open_authority", lambda path: authority)
    service = PetStatus(engine)
    assert asyncio.run(service.read())["personal"]["xp"] == 6
    db = sqlite3.connect(root / "ledger.sqlite")
    trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='events_no_delete'").fetchone()[0]
    db.execute("DROP TRIGGER events_no_delete"); db.execute("DELETE FROM events")
    db.execute(trigger); db.commit(); db.close()
    result = asyncio.run(service.read())
    assert result["state"] == "unverified" and result["stale"] and result["personal"]["xp"] == 6
    assert asyncio.run(PetStatus(engine).read())["personal"]["xp"] is None


def test_authority_advances_during_capture_without_false_integrity(store_rig, monkeypatch):
    import exo.pet.status as module
    engine, root, _, _ = store_rig
    authority = authority_fixture(root.parent / "authority.sqlite", "authority-a")
    with Ledger(root / "ledger.sqlite", authority=authority, authority_author="memory") as writer:
        writer.append("answer", {"text": "original"}, "principal")
    original = authority.read_since
    once = []
    def read_and_append(seq, limit):
        records = original(seq, limit)
        if not once:
            once.append(True)
            with Ledger(root / "ledger.sqlite", authority=authority, authority_author="memory") as other:
                other.append("answer", {"text": "newer"}, "principal")
        return records
    monkeypatch.setattr(authority, "read_since", read_and_append)
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "fixture")
    monkeypatch.setattr(module, "open_authority", lambda path: authority)
    result = asyncio.run(PetStatus(engine).read())
    assert result["state"] == "ready" and result["verification"]["checked"] == 1
    assert result["personal"]["xp"] == 6


def test_authority_first_append_after_genesis_capture_is_busy_not_empty_or_tampered(store_rig, monkeypatch):
    import exo.pet.status as module
    engine, root, _, _ = store_rig
    authority = authority_fixture(root.parent / "authority.sqlite", "authority-a")
    with Ledger(root / "ledger.sqlite", authority=authority, authority_author="memory"):
        pass
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "fixture")
    monkeypatch.setattr(module, "open_authority", lambda path: authority)
    original = module.capture_authority
    once = []
    def interleave(a):
        captured = original(a)
        if not once:
            once.append(True)
            with Ledger(root / "ledger.sqlite", authority=authority, authority_author="memory") as other:
                other.append("answer", {"text": "first"}, "principal")
        return captured
    monkeypatch.setattr(module, "capture_authority", interleave)
    service = PetStatus(engine)
    first = asyncio.run(service.read())
    assert first["state"] == "unavailable" and first["verification"]["reason"] == "ledger_busy"
    assert first["personal"]["xp"] is None and not first["stale"]
    second = asyncio.run(service.read())
    assert second["state"] == "ready" and second["personal"]["xp"] == 6
