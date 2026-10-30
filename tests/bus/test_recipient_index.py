"""Recipient lookup is indexed, transactionally maintained and equivalent after migration."""
import json

import pytest
from nacl.signing import SigningKey

from exo.bus import wire
from exo.bus.store import Store


def add(store, key, recipients, body):
    env = wire.finish_envelope(wire.make_envelope("alpha", recipients, body, store.head()[1]), key)
    store.append(env)
    return env


def test_index_matches_old_predicate_and_preserves_hash_chain(tmp_path):
    store = Store(tmp_path / "bus.db")
    key = SigningKey.generate()
    for i in range(100):
        add(store, key, ["beta", "group"] if i % 7 == 0 else ["other"], f"m{i}")
    for since in (0, 9, 90, 100):
        for limit in (0, 1, 20):
            old = store._select("seq > ? AND EXISTS (SELECT 1 FROM json_each(log.to_json)"
                                " WHERE value IN (?,?))", (since, "beta", "group"), limit)
            assert store.inbox(["beta", "group"], since, limit) == old
            assert store.messages(since=since, to="beta", limit=limit) == old
    assert store.inbox([]) == []
    assert store.verify()["ok"]
    plan = store.db.execute("EXPLAIN QUERY PLAN SELECT seq FROM log_recipients"
                            " WHERE recipient=? AND seq>?", ("beta", 50)).fetchall()
    assert any("SEARCH log_recipients USING PRIMARY KEY" in row[-1] for row in plan)
    assert not any("SCAN" in row[-1] for row in plan)
    store.db.close()


def test_backfill_and_old_writer_insert_are_atomic(tmp_path):
    path = tmp_path / "bus.db"
    store = Store(path)
    key = SigningKey.generate()
    add(store, key, ["beta"], "old")
    old = store.recent()
    for name in ("log_recipient_insert", "log_recipient_update", "log_recipient_delete"):
        store.db.execute(f"DROP TRIGGER {name}")
    store.db.execute("DROP TABLE log_recipients")
    store.db.execute("DROP TABLE store_migrations")
    store.db.close()
    store = Store(path)
    assert store.inbox(["beta"]) == old and store.verify()["ok"]
    add(store, key, ["beta"], "new")
    assert len(store.inbox(["beta"])) == 2
    store.db.execute("BEGIN IMMEDIATE")
    store.db.execute("UPDATE log SET to_json=? WHERE seq=1", (json.dumps(["other"]),))
    store.db.execute("ROLLBACK")
    assert len(store.inbox(["beta"])) == 2
    # A rolled-back append must leave neither an envelope nor a derived recipient.
    store.db.execute("CREATE TRIGGER reject_test BEFORE INSERT ON log"
                     " BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(Exception, match="test failure"):
        add(store, key, ["unique"], "refused")
    assert store.inbox(["unique"]) == [] and store.head()[0] == 2
    store.db.close()
    reopened = Store(path)
    assert len(reopened.inbox(["beta"])) == 2 and reopened.verify()["ok"]
    reopened.db.close()
