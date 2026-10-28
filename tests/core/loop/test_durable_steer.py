"""Durable steering guarantees transcript insertion once, never exactly-once tool effects."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from exo.core.engine import CoreEngine
from exo.core.loop import Loop
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.sessions import SessionStore
from exo.core.types import Message, ToolResult
from exo.protocol import schema as s


@pytest.fixture
def rig(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    loop = Loop(store, FakeProvider(["unused"]))
    sid = store.create_session(agent="default").id
    store.append_message(sid, Message.user("original task"))
    loop.state(sid).running = loop.state(sid).accepting_steers = True
    yield store, loop, sid
    store.close()


def reopen(store):
    path = store.path
    store.close()
    other = SessionStore(path)
    return other, Loop(other, FakeProvider([]))


def engine_for(store, loop):
    engine = CoreEngine.__new__(CoreEngine)
    engine.store, engine.loop, engine.cwd = store, loop, "/"
    engine.agents = SimpleNamespace(loops={})
    return engine


@pytest.mark.parametrize("path", ["send", "internal"])
def test_acceptance_is_durable_and_resume_does_not_start_turn(rig, path):
    store, loop, sid = rig
    if path == "send":
        asyncio.run(loop.send(sid, "correction", mode="steer", meta={"author": "local"}))
        queued = loop.state(sid).steer[0]
    else:
        queued = Message.user("correction")
        queued.meta = {"author": "peer:tester", "peer": {"from": "tester"}}
        assert loop.steer(sid, queued)
    other, restored = reopen(store)
    try:
        engine = engine_for(other, restored)
        async def inspect():
            await restored.resume(sid)
            return await engine._session_get(s.SessionGet(session=sid))
        snapshot = asyncio.run(inspect())
        assert snapshot["steers"] == [queued.id]
        assert snapshot["queued_steers"][0]["id"] == queued.id
        assert snapshot["queued_steers"][0]["content"][0]["text"] == "correction"
        assert snapshot["queued_steers"][0]["meta"]["author"] == queued.meta["author"]
        assert restored.state(sid).steer == [queued]
        assert [m.text() for m in other.messages(sid)] == ["original task"]
        assert not restored.state(sid).running and restored.provider.calls == []
    finally:
        other.close()


@pytest.mark.parametrize("crash_at", ["ack", "delivery_before_event"])
def test_real_ack_survives_process_exit_without_shutdown_hook(tmp_path, crash_at):
    script = '''
import asyncio, json, os, sys
from types import SimpleNamespace
from exo.core.engine import CoreEngine
from exo.core.loop import Loop
from exo.core.models.fake import FakeProvider
from exo.core.sessions import SessionStore
from exo.core.types import Message
from exo.principal import CALLER, Caller
from exo.protocol import schema as s
store = SessionStore(sys.argv[1]); loop = Loop(store, FakeProvider([]))
sid = store.create_session(agent="default").id
store.append_message(sid, Message.user("initial"))
loop.state(sid).running = loop.state(sid).accepting_steers = True
engine = CoreEngine.__new__(CoreEngine)
engine.store, engine.loop, engine.cwd, engine.agents = store, loop, "/", SimpleNamespace(loops={})
CALLER.set(Caller("principal"))
ack = asyncio.run(engine._turn_send(s.TurnSend(session=sid, text="preserve correction", mode="steer")))
with open(sys.argv[2], "w") as f: json.dump({"session": sid, "ack": ack}, f)
if sys.argv[3] == "delivery_before_event":
    async def crash(*args): os._exit(0)
    loop.bus.emit = crash
    asyncio.run(loop._drain_steer(sid, "t-delivery", loop.state(sid)))
os._exit(0)
'''
    path, receipt = tmp_path / "sessions.sqlite", tmp_path / "receipt.json"
    subprocess.run([sys.executable, "-c", script, str(path), str(receipt), crash_at], check=True, timeout=10)
    ack = json.loads(receipt.read_text())
    store = SessionStore(path)
    try:
        loop = Loop(store, FakeProvider([]))
        asyncio.run(loop.resume(ack["session"]))
        pending = loop.state(ack["session"]).steer
        if crash_at == "ack":
            assert ack["ack"] == {"steered": True, "message_id": pending[0].id}
            assert pending[0].text() == "preserve correction" and pending[0].meta["author"] == "principal"
        else:
            assert pending == []
            delivered = [m for m in store.messages(ack["session"]) if m.id == ack["ack"]["message_id"]]
            assert len(delivered) == 1 and delivered[0].text() == "preserve correction"
            assert store.deliver_steers(ack["session"], "t-retry") == []
            assert len([m for m in store.messages(ack["session"]) if m.id == delivered[0].id]) == 1
            assert store.verify_log("default")["ok"]
        assert loop.provider.calls == []
    finally:
        store.close()


@pytest.mark.parametrize("principal", [True, False])
def test_cancellation_is_durable_and_respects_author(rig, principal):
    store, loop, sid = rig
    local, human = Message.user("local"), Message.user("human")
    local.meta["author"], human.meta["author"] = "local", "principal"
    loop.steer(sid, local)
    loop.steer(sid, human)
    gone = loop.unsteer(sid, principal=principal)
    assert gone == [human if principal else local]
    other, restored = reopen(store)
    try:
        assert [m.id for m in restored.state(sid).steer] == [local.id if principal else human.id]
        assert restored.unsteer(sid, gone[0].id, principal=True) == []
    finally:
        other.close()


def test_atomic_delivery_rollback_then_retry_and_idempotency(rig):
    store, loop, sid = rig
    msg = Message.user("deliver me once")
    msg.id = "m_stable"
    assert loop.steer(sid, msg)
    assert loop.steer(sid, msg) and len(loop.state(sid).steer) == 1
    store._q("CREATE TRIGGER reject_queue_delete BEFORE DELETE ON queued_steers BEGIN SELECT RAISE(ABORT, 'write failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="write failed"):
        asyncio.run(loop._drain_steer(sid, "t1", loop.state(sid)))
    assert [m.text() for m in store.messages(sid)] == ["original task"]
    assert len(loop.state(sid).steer) == 1
    assert not store._q("SELECT * FROM messages_fts WHERE msg_id=?", (msg.id,))
    store._q("DROP TRIGGER reject_queue_delete")
    asyncio.run(loop._drain_steer(sid, "t1", loop.state(sid)))
    first = store._q("SELECT seq, pos, hash, data FROM messages WHERE id=?", (msg.id,))[0]
    assert loop.steer(sid, msg)  # same ID/payload already delivered: accepted idempotently, never requeued
    asyncio.run(loop._drain_steer(sid, "t2", loop.state(sid)))
    second = store._q("SELECT seq, pos, hash, data FROM messages WHERE id=?", (msg.id,))[0]
    assert tuple(first) == tuple(second)
    assert len(store._q("SELECT * FROM messages_fts WHERE msg_id=?", (msg.id,))) == 1
    assert store.verify_log("default")["ok"]


def test_crash_after_commit_before_event_does_not_replay(rig):
    store, loop, sid = rig
    msg = Message.user("committed before event")
    loop.steer(sid, msg)
    async def crash(*args):
        raise RuntimeError("event transport stopped")
    loop.bus.emit = crash
    with pytest.raises(RuntimeError, match="transport stopped"):
        asyncio.run(loop._drain_steer(sid, "t1", loop.state(sid)))
    other, restored = reopen(store)
    try:
        assert restored.state(sid).steer == []
        assert [m.id for m in other.messages(sid)].count(msg.id) == 1
        assert asyncio.run(restored._drain_steer(sid, "t2", restored.state(sid))) == ""
        assert [m.id for m in other.messages(sid)].count(msg.id) == 1
    finally:
        other.close()


@pytest.mark.parametrize("operation", ["send", "internal", "cancel"])
def test_failed_queue_write_or_cancellation_never_acknowledges_success(rig, operation):
    store, loop, sid = rig
    msg = Message.user("correction")
    if operation == "cancel":
        loop.steer(sid, msg)
        kind = "DELETE"
    else:
        kind = "INSERT"
    store._q(f"CREATE TRIGGER fail_queue BEFORE {kind} ON queued_steers BEGIN SELECT RAISE(ABORT, 'save failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="save failed"):
        if operation == "send":
            asyncio.run(engine_for(store, loop)._turn_send(s.TurnSend(session=sid, text="correction", mode="steer")))
        elif operation == "internal":
            loop.steer(sid, msg)
        else:
            loop.unsteer(sid, msg.id)
    assert len(loop.state(sid).steer) == (1 if operation == "cancel" else 0)


@pytest.mark.parametrize("old_author,new_author", [("principal", "local"), ("peer:tester", "principal")])
def test_recovered_corrections_precede_new_request_without_taking_its_author(rig, old_author, new_author):
    store, loop, sid = rig
    msg = Message.user("old correction")
    msg.meta["author"] = old_author
    loop.steer(sid, msg)
    other, restored = reopen(store)
    authors = []
    async def tool(call, ctx):
        authors.append(ctx["author"])
        return ToolResult.of_text(call.id, "ok")
    restored.run_tool = tool
    restored.provider = FakeProvider([tool_turn("check"), "done"])
    try:
        asyncio.run(restored.send(sid, "newer request", meta={"author": new_author}))
        users = [m for m in other.messages(sid) if m.role == "user"]
        assert [m.text() for m in users] == ["original task", "old correction", "newer request"]
        assert users[-2].meta["author"] == old_author
        assert authors == [new_author]
        assert restored.state(sid).steer == []
    finally:
        other.close()


def test_queue_is_session_scoped_fork_does_not_inherit_and_undo_retires(rig):
    store, loop, sid = rig
    msg = Message.user("pending correction")
    loop.steer(sid, msg)
    fork = store.fork(sid)
    assert loop.state(fork.id).steer == []
    assert len(loop.state(sid).steer) == 1
    store.undo(sid)
    assert loop.state(sid).steer == []
    assert store.deliver_steers(sid, "later") == []
    assert all(m.id != msg.id for m in store.messages(sid))


def test_delivery_rechecks_anchor_visibility(rig):
    store, loop, sid = rig
    loop.steer(sid, Message.user("no resurrection"))
    # Emulate a concurrent visibility change; the delivery transaction must validate its own source anchor.
    store._q("UPDATE messages SET hidden=1 WHERE session=?", (sid,))
    assert store.deliver_steers(sid, "later") == []
    assert loop.state(sid).steer == []


def test_private_queue_stays_in_private_session(rig):
    store, loop, sid = rig
    private = store.create_session(agent="default", kind="private")
    loop.state(private.id).accepting_steers = True
    secret = Message.user("PRIVATE-CORRECTION")
    loop.steer(private.id, secret)
    assert loop.state(sid).steer == []
    assert store.search("PRIVATE-CORRECTION", agent="default") == []
    asyncio.run(loop._drain_steer(private.id, "t-private", loop.state(private.id)))
    assert store.messages(private.id)[0].text() == "PRIVATE-CORRECTION"
    assert store.search("PRIVATE-CORRECTION", agent="default") == []


def test_reused_identity_cannot_change_payload_or_session(rig):
    store, loop, sid = rig
    msg = Message.user("one")
    msg.id = "m_shared"
    loop.steer(sid, msg)
    changed = Message.user("different")
    changed.id = msg.id
    with pytest.raises(ValueError, match="different content"):
        loop.steer(sid, changed)
    other = store.create_session(agent="default").id
    loop.state(other).accepting_steers = True
    with pytest.raises(ValueError, match="different content or session"):
        loop.steer(other, msg)
    assert loop.state(sid).steer == [msg] and loop.state(other).steer == []


def test_undo_of_delivered_message_never_requeues_same_identity(rig):
    store, loop, sid = rig
    msg = Message.user("already delivered")
    loop.steer(sid, msg)
    store.deliver_steers(sid, "t1")
    store.undo(sid)
    assert all(m.id != msg.id for m in store.messages(sid))
    assert loop.steer(sid, msg)
    assert loop.state(sid).steer == []
    assert store.deliver_steers(sid, "t2") == []
    assert all(m.id != msg.id for m in store.messages(sid))


@pytest.mark.parametrize("operation", ["persist", "cancel", "deliver"])
def test_commit_refusal_rolls_back_queue_transition_and_preserves_retry(rig, operation):
    store, loop, sid = rig
    message = Message.user("commit must precede acceptance")
    if operation != "persist":
        loop.steer(sid, message)
    refused = []

    def authorizer(action, arg1, arg2, database, source):
        if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT" and not refused:
            refused.append(True)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def transition():
        if operation == "persist":
            return loop.steer(sid, message)
        if operation == "cancel":
            return loop.unsteer(sid, message.id)
        return store.deliver_steers(sid, "t-delivery")

    store._db.set_authorizer(authorizer)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            transition()
    finally:
        store._db.set_authorizer(None)
    assert refused == [True]
    assert not store._db.in_transaction
    assert [m.text() for m in store.messages(sid)] == ["original task"]
    assert loop.state(sid).steer == ([] if operation == "persist" else [message])
    assert transition()  # a failed commit left no hidden active transaction or half-applied transition
    if operation == "persist":
        assert loop.state(sid).steer == [message]
    else:
        assert loop.state(sid).steer == []
    assert [m.text() for m in store.messages(sid)] == (
        ["original task", message.text()] if operation == "deliver" else ["original task"])
    assert store.verify_log("default")["ok"]


def test_transaction_preserves_original_error_when_rollback_also_refused(rig):
    store, _, _ = rig
    original = RuntimeError("operation rejected")

    def authorizer(action, arg1, arg2, database, source):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and arg1 == "ROLLBACK" else sqlite3.SQLITE_OK

    def fail(db):
        raise original

    store._db.set_authorizer(authorizer)
    try:
        with pytest.raises(RuntimeError, match="operation rejected") as failure:
            store._tx(fail)
        assert failure.value is original
    finally:
        store._db.set_authorizer(None)
        if store._db.in_transaction:
            store._db.execute("ROLLBACK")


@pytest.mark.parametrize("file_backed", [True, False])
def test_commit_and_rollback_refusal_discards_unaccepted_queue_connection(tmp_path, file_backed):
    store = SessionStore(tmp_path / "poisoned.sqlite" if file_backed else ":memory:")
    loop = Loop(store, FakeProvider([]))
    sid = store.create_session(agent="default").id
    loop.state(sid).accepting_steers = True
    poisoned = store._db
    refused = []

    def authorizer(action, arg1, arg2, database, source):
        if action == sqlite3.SQLITE_TRANSACTION and arg1 in ("COMMIT", "ROLLBACK"):
            refused.append(arg1)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    poisoned.set_authorizer(authorizer)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            loop.steer(sid, Message.user("must never be consumed without acceptance"))
        assert refused == ["COMMIT", "ROLLBACK"]
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            poisoned.execute("SELECT 1")
        if file_backed:
            assert loop.state(sid).steer == []  # reopen committed file state; rejected input cannot appear accepted
            assert loop.steer(sid, Message.user("accepted after recovery"))
            assert [m.text() for m in loop.state(sid).steer] == ["accepted after recovery"]
        else:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                _ = loop.state(sid).steer  # an in-memory database cannot be silently recreated empty
    finally:
        store.close()
