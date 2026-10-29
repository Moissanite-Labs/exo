import hashlib
import json
import os
import shutil
import sqlite3
import stat
import threading

import pytest

from exo import crypto
from exo.crypto import GENESIS
from exo.ledger import Ledger, LedgerError, anchor_paths_for


def test_append_iter_head(tmp_path, signer):
    led = Ledger(tmp_path / "l.sqlite", signer)
    assert led.head().seq == 0 and led.head().chain == GENESIS
    a = led.append("utterance", {"text": "hello"}, "principal", session="s1")
    b = led.append("ruling", {"text": "always ask before posting"}, "principal", refs=[a["id"]])
    c = led.append("note", {"text": "n"}, session="s2")  # author defaults to the signer
    assert a["prev"] == GENESIS
    assert b["prev"] == a["chain"]
    assert c["author"] == "tester"
    assert led.head() == (3, c["id"], c["chain"])
    assert [e["kind"] for e in led.iter()] == ["utterance", "ruling", "note"]
    assert [e["id"] for e in led.iter(kind="ruling")] == [b["id"]]
    assert [e["id"] for e in led.iter(since=1)] == [b["id"], c["id"]]
    assert [e["id"] for e in led.iter(session="s1")] == [a["id"]]
    assert [e["id"] for e in led.iter(since=b["ts"])][0] == b["id"]
    assert led.get(b["id"])["refs"] == [a["id"]]
    assert led.get(a["id"])["body"] == {"text": "hello"}
    assert led.verify() == {"ok": True, "checked": 3, "head": c["chain"], "anchored": True, "anchored_seq": 3}


def test_verdict_requires_target(tmp_path, signer):
    led = Ledger(tmp_path / "l.sqlite", signer)
    out = led.append("artifact", {"text": "draft"}, "agent")
    with pytest.raises(LedgerError):
        led.append("verdict", {"verdict": "maybe", "target": out["id"]}, "principal")
    with pytest.raises(LedgerError):
        led.append("verdict", {"verdict": "reject"}, "principal")
    with pytest.raises(LedgerError):
        led.append("verdict", {"verdict": "reject", "target": "f" * 64}, "principal")
    v = led.append("verdict", {"verdict": "should_have_been", "target": out["id"], "text": "Y not X"}, "principal")
    assert v["refs"] == [out["id"]]


def test_rejects_bad_input(tmp_path, signer):
    led = Ledger(tmp_path / "l.sqlite", signer)
    with pytest.raises(LedgerError):
        led.append("gossip", {}, "a")
    with pytest.raises(LedgerError):
        led.append("note", ["not", "a", "dict"], "a")
    with pytest.raises(LedgerError):
        led.append("note", {}, "a", refs=["missing"])
    with pytest.raises(LedgerError):
        Ledger(tmp_path / "l.sqlite").append("note", {}, "a")
    assert len(led) == 0


def test_no_updates_or_deletes(tmp_path, signer):
    led = Ledger(tmp_path / "l.sqlite", signer)
    led.append("note", {"text": "x"}, "a")
    with pytest.raises(sqlite3.DatabaseError):
        led.db.execute("UPDATE events SET body = '{}'")
    with pytest.raises(sqlite3.DatabaseError):
        led.db.execute("DELETE FROM events")


def _tamper(path, sql):
    db = sqlite3.connect(path)
    db.execute("DROP TRIGGER events_no_update")
    db.execute("DROP TRIGGER events_no_delete")
    db.execute(sql)
    db.commit()
    db.close()


@pytest.mark.parametrize("sql,reason", [
    ("UPDATE events SET body = '{\"text\":\"forged\"}' WHERE seq = 2", "id does not match"),
    ("DELETE FROM events WHERE seq = 2", "prev does not link"),
    ("UPDATE events SET author = 'mallory' WHERE seq = 1", "id does not match"),
])
def test_tamper_detected(tmp_path, signer, sql, reason):
    path = tmp_path / "l.sqlite"
    led = Ledger(path, signer)
    for i in range(3):
        led.append("note", {"text": str(i)}, "a")
    led.close()
    _tamper(path, sql)
    res = Ledger(path).verify()
    assert not res["ok"] and reason in res["error"]


def test_forged_signature_detected(tmp_path, signer):
    """A forger who recomputes id and chain still cannot produce a valid signature."""
    path = tmp_path / "l.sqlite"
    led = Ledger(path, signer)
    ev = led.append("note", {"text": "x"}, "a")
    led.close()
    other = crypto.keygen()
    _tamper(path, f"UPDATE events SET sig = '{crypto.sign(b'junk', other)}' WHERE id = '{ev['id']}'")
    res = Ledger(path).verify()
    assert not res["ok"] and "signature" in res["error"]


def test_trusted_keys(tmp_path, signer):
    led = Ledger(tmp_path / "l.sqlite", signer)
    led.append("note", {"text": "x"}, "tester")
    assert led.verify(trusted={"tester": [signer.pub]})["ok"]
    res = led.verify(trusted={"tester": [crypto.pubkey_b64(crypto.keygen())]})
    assert not res["ok"] and "not trusted" in res["error"]


def test_concurrent_appends_keep_chain(tmp_path, signer):
    path = tmp_path / "l.sqlite"
    Ledger(path).close()
    errors = []

    def worker(n):
        try:
            led = Ledger(path, signer)  # one connection per writer, like separate processes
            for i in range(20):
                led.append("action", {"worker": n, "i": i}, f"w{n}")
            led.close()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    led = Ledger(path)
    assert len(led) == 80
    assert led.verify()["ok"]
    assert [a["seq"] for _, a in led.anchors()] == [80, 80]  # anchors only move forward, even when writers race


@pytest.mark.parametrize("point,next_seq", [("commit", 2), ("anchor", 3)])
def test_a_writer_killed_mid_append_never_leaves_the_anchor_ahead(tmp_path, point, next_seq):
    """SIGKILL, OOM or power loss: the head anchor may lag the ledger by the newest event, never lead it. An
    anchor ahead reads as "events were removed" and refuses every later append."""
    import subprocess
    import sys
    path, key = tmp_path / "l.sqlite", tmp_path / "w.key"
    crypto.save_key(crypto.keygen(), key)
    with Ledger(path, crypto.Signer.from_file("w", key)) as led:
        led.append("note", {"i": 1})
    script = """
import os, sys
from exo import crypto
from exo.ledger import Ledger
led = Ledger(sys.argv[1], crypto.Signer.from_file("w", sys.argv[2]))
if sys.argv[3] == "anchor":  # dies after COMMIT, before the anchors are written
    led._write_anchors = lambda *a: os._exit(79)
else:  # dies at COMMIT, so SQLite rolls the row back
    real = led.db
    class Db:
        def __getattr__(self, name):
            return getattr(real, name)
        def execute(self, sql, *a):
            if sql == "COMMIT":
                os._exit(79)
            return real.execute(sql, *a)
    led.db = Db()
led.append("note", {"i": 2})
"""
    res = subprocess.run([sys.executable, "-c", script, str(path), str(key), point], capture_output=True, text=True)
    assert res.returncode == 79, res.stderr
    with Ledger(path, crypto.Signer.from_file("w", key)) as led:
        assert led.verify()["ok"]
        assert led.append("note", {"i": 3})["seq"] == next_seq
        assert led.verify()["anchored"] and [a["seq"] for _, a in led.anchors()] == [next_seq, next_seq]


def test_an_anchor_that_cannot_be_written_stops_appends_one_event_later(tmp_path, signer, monkeypatch):
    """A store-only writer must not freeze both anchors and then truncate past them (#221 review)."""
    from exo import ledger as ledger_mod
    led = Ledger(tmp_path / "l.sqlite", signer)
    for i in range(3):
        led.append("note", {"i": i}, "agent")
    beside, home = led.anchor_paths()
    real = ledger_mod._atomic_write

    def broken(path, data):
        if path == beside:
            raise PermissionError(13, "read-only", str(path))
        return real(path, data)
    monkeypatch.setattr(ledger_mod, "_atomic_write", broken)
    assert led.append("note", {"i": 3}, "agent")["seq"] == 4      # committed; the anchor is one event behind
    assert [a["seq"] for _, a in led.anchors()] == [3, 4]          # the copy under keys/ still moved
    with pytest.raises(LedgerError, match="cannot be written") as refused:
        led.append("note", {"i": 4}, "agent")
    assert "Restore write access" in str(refused.value) and "--force" not in str(refused.value)
    assert led.head().seq == 4
    assert led.verify()["anchored_seq"] == 3
    for force in (False, True):  # one line for the CLI, not a PermissionError traceback (#221 delta review)
        with pytest.raises(LedgerError, match="cannot be written"):
            led.anchor(force=force)
    monkeypatch.setattr(ledger_mod, "_atomic_write", real)
    assert led.append("note", {"i": 4}, "agent")["seq"] == 5      # caught up, then appended
    assert [a["seq"] for _, a in led.anchors()] == [5, 5] and led.verify()["ok"]


def test_an_append_counts_the_ledger_only_in_the_anchor_check(tmp_path, signer):
    """The post-COMMIT anchor write takes indexed lookups only: no second pass over every event (#221 review)."""
    led = Ledger(tmp_path / "l.sqlite", signer)
    for i in range(5):
        led.append("note", {"i": i}, "agent")
    seen: list[str] = []
    led.db.set_trace_callback(seen.append)
    led.append("note", {"i": 5}, "agent")
    led.db.set_trace_callback(None)
    counts = [q for q in seen if "COUNT(*)" in q]
    assert len(counts) == len(led.anchor_paths())                  # one per anchor, as before the fix
    assert all("seq <=" in q for q in counts)
    assert [(a["seq"], a["count"]) for _, a in led.anchors()] == [(6, 6), (6, 6)]


@pytest.mark.parametrize("gone,stops_at", [("beside", 4), ("both", 3)])
def test_with_no_anchor_writable_appends_stop_one_event_past_the_last_anchor(tmp_path, signer, monkeypatch, gone,
                                                                             stops_at):
    """`<ledger>.head` deleted and no anchor writable: the copy under keys/ must not freeze while appends go on, or
    a truncation back to it verifies clean (#221 delta review). With both anchors gone the head itself has no
    anchor, so the first append is refused."""
    from exo import ledger as ledger_mod
    led = Ledger(tmp_path / "l.sqlite", signer)
    for i in range(3):
        led.append("note", {"i": i}, "agent")
    beside, home = led.anchor_paths()
    beside.unlink()
    if gone == "both":
        home.unlink()
    real = ledger_mod._atomic_write

    def broken(path, data):
        raise PermissionError(13, "read-only", str(path))
    monkeypatch.setattr(ledger_mod, "_atomic_write", broken)
    for seq in range(4, stops_at + 1):
        assert led.append("note", {"i": seq}, "agent")["seq"] == seq   # the accepted lag of one event
    with pytest.raises(LedgerError, match="cannot be written"):
        led.append("note", {"i": 9}, "agent")
    assert led.head().seq == stops_at
    assert [(p, a["seq"]) for p, a in led.anchors()] == ([(home, 3)] if gone == "beside" else [])
    monkeypatch.setattr(ledger_mod, "_atomic_write", real)
    assert led.append("note", {"i": 9}, "agent")["seq"] == stops_at + 1  # caught up first, then appended
    assert [a["seq"] for _, a in led.anchors()] == [stops_at + 1] * 2 and led.verify()["ok"]


def _truncate(path, keep):
    """Drop the delete trigger, delete past seq `keep`, put the trigger back: the shorter chain still links."""
    db = sqlite3.connect(path)
    db.execute("DROP TRIGGER events_no_delete")
    db.execute("DELETE FROM events WHERE seq > ?", (keep,))
    db.execute("CREATE TRIGGER events_no_delete BEFORE DELETE ON events "
               "BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END")
    db.commit()
    db.close()


def test_a_ledger_swapped_for_a_symlink_to_a_truncated_copy_fails(tmp_path, signer):
    """A store-only writer copies the ledger, truncates the copy, puts a symlink to it in the ledger's place and
    rewrites `<ledger>.head`. The anchor under keys/ is still named after the ledger's own path, and a symlinked
    ledger fails verify and refuses appends (#221 delta review)."""
    path = tmp_path / "ledger.sqlite"
    home = anchor_paths_for(path)[1]
    assert home.name == hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24] + ".json"  # no migration
    with Ledger(path, signer) as led:
        for i in range(6):
            led.append("note", {"i": i}, "agent")
    alt = tmp_path / "ledger.alt"
    shutil.copy(path, alt)
    _truncate(alt, 3)
    with Ledger(alt) as copy:
        head = copy.head()
    path.unlink()
    path.symlink_to(alt)
    beside = path.with_name(path.name + ".head")
    beside.write_text(json.dumps({"v": 1, "ledger": str(path), "seq": 3, "count": 3, "chain": head.chain,
                                  "id": head.id}))
    assert anchor_paths_for(path) == [beside, home]  # the swap does not move the keys/ anchor
    led = Ledger(path, signer)
    res = led.verify()
    assert not res["ok"] and "symbolic link" in res["error"]
    with pytest.raises(LedgerError, match="symbolic link"):
        led.append("note", {"i": 6}, "agent")
    for force in (False, True):  # re-anchoring would bless the copy
        with pytest.raises(LedgerError, match="symbolic link"):
            led.anchor(force=force)
    assert "events were removed" in led.check_anchors()  # the keys/ anchor alone catches the truncation
    assert led.head().seq == 3


def _refuses(led):
    res = led.verify()
    assert not res["ok"] and "symbolic link" in res["error"]
    with pytest.raises(LedgerError, match="symbolic link"):
        led.append("note", {"i": 0}, "agent")
    with pytest.raises(LedgerError, match="symbolic link"):
        led.anchor()


def test_opening_a_dangling_symlinked_ledger_creates_nothing(tmp_path, signer):
    """Opening set WAL mode and ran the schema before any symlink check, so a dangling link made the database it
    points at (#277). It is opened read-only now, and an empty ledger in memory stands in for a missing target."""
    planted = tmp_path / "elsewhere" / "planted.db"
    planted.parent.mkdir()
    path = tmp_path / "ledger.sqlite"
    path.symlink_to(planted)
    with Ledger(path, signer) as led:
        _refuses(led)
        assert led.head().seq == 0 and list(led.iter()) == []
    assert list(planted.parent.iterdir()) == [] and not path.with_name("ledger.sqlite.head").exists()


def test_opening_a_ledger_symlinked_to_another_database_leaves_it_as_it_was(tmp_path, signer):
    """A link to some other SQLite database of the user's left it in WAL mode with the ledger's tables and triggers
    added (#277). Now it stays byte for byte as it was."""
    other = tmp_path / "notes.db"
    db = sqlite3.connect(other)
    db.execute("CREATE TABLE notes (t TEXT)")
    db.execute("INSERT INTO notes VALUES ('pat')")
    db.commit()
    db.close()
    before = other.read_bytes()
    path = tmp_path / "ledger.sqlite"
    path.symlink_to(other)
    with Ledger(path, signer) as led:
        _refuses(led)
        assert led.head().seq == 0
    assert other.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "exo-home") == ["ledger.sqlite", "notes.db"]


@pytest.mark.parametrize("kind", ["fifo", "folder", "loop"])
def test_opening_a_ledger_symlinked_to_a_fifo_or_folder_does_not_hang(tmp_path, signer, kind):
    """A read-only open of a FIFO blocks until a writer comes, so `exo log verify` and `exo log tail` would hang
    (#277). Only a regular file is opened. Anything else reads as an empty ledger, and verify, append and anchor
    refuse."""
    target = tmp_path / "target"
    if kind == "fifo":
        os.mkfifo(target)
    elif kind == "folder":
        target.mkdir()
    path = tmp_path / "ledger.sqlite"
    path.symlink_to(path if kind == "loop" else target)
    opened: list = []
    t = threading.Thread(target=lambda: opened.append(Ledger(path, signer)), daemon=True)
    t.start()
    t.join(10)
    if t.is_alive():  # let the blocked open go before failing
        os.close(os.open(target, os.O_WRONLY | os.O_NONBLOCK))
        t.join(10)
        pytest.fail("opening a ledger symlinked to a FIFO blocked")
    with opened[0] as led:
        _refuses(led)
        assert led.head().seq == 0


def test_opening_a_ledger_symlinked_to_another_exo_ledger_makes_no_wal_or_shm_files(tmp_path, signer):
    """A read-only open of a WAL database still made its `-wal` and `-shm` files. Another Exo ledger is always in WAL
    mode and is the likeliest target. `immutable=1` makes none when the target has no `-wal` file (#277). With a live
    writer, the open reads through the writer's own files and adds none."""
    other = tmp_path / "other" / "ledger.sqlite"
    with Ledger(other, signer) as led:
        for i in range(3):
            led.append("note", {"i": i}, "agent")
    before, beside = other.read_bytes(), sorted(p.name for p in other.parent.iterdir())
    path = tmp_path / "ledger.sqlite"
    path.symlink_to(other)
    with Ledger(path, signer) as led:
        _refuses(led)
        assert [e["body"]["i"] for e in led.iter()] == [0, 1, 2]
        assert sorted(p.name for p in other.parent.iterdir()) == beside
    assert other.read_bytes() == before and sorted(p.name for p in other.parent.iterdir()) == beside
    with Ledger(other, signer) as live:
        live.append("note", {"i": 3}, "agent")
        beside = sorted(p.name for p in other.parent.iterdir())
        assert "ledger.sqlite-wal" in beside
        with Ledger(path, signer) as led:
            assert len(led) == 4
        assert sorted(p.name for p in other.parent.iterdir()) == beside


def test_reads_through_a_symlink_are_a_snapshot_of_the_target_at_open(tmp_path, signer):
    """A read-only open of a closed target (no `-wal` beside it) held an `immutable=1` connection to the target
    itself: a writer that appended and checkpointed replaced the file under it, so the reader stayed stale and
    reading raised `database disk image is malformed` (#317). The target is copied at open now: reads
    are the target as it stood at open, and a fresh open sees it as it stands then."""
    target = tmp_path / "target.sqlite"
    with Ledger(target, signer) as led:
        for i in range(3):
            led.append("note", {"i": i}, "agent")
    path = tmp_path / "ledger.sqlite"
    path.symlink_to(target)
    reader = Ledger(path, signer)
    assert len(reader) == 3
    with Ledger(target, signer) as writer:
        for i in range(3, 200):
            writer.append("note", {"i": i}, "agent")
        assert len(reader) == 3  # appends alone do not reach the snapshot either
        writer.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert len(reader) == 3
    assert [e["body"]["i"] for e in reader.iter()] == [0, 1, 2]  # raised DatabaseError before the copy
    reader.close()
    with Ledger(path, signer) as again:
        assert len(again) == 200


PAD = "x" * 6000  # two pages an event, so a short ledger spans several 64-page copy steps


def _padded(target, signer, start, stop, pad=PAD):
    with Ledger(target, signer) as led:
        for i in range(start, stop):
            led.append("note", {"i": i, "pad": pad}, "agent")
        led.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _write_during_copies(monkeypatch, write, copies):
    """Copy a symlinked ledger's target in 64-page steps and run `write` after the first step of each of the first
    `copies` copies: a writer that lands while the copy is half done, as in abu-aska's review of #416. Returns a
    list that gets one entry per copy."""
    made = []

    class Stepped(sqlite3.Connection):
        def backup(self, target, **kwargs):
            made.append(len(made) < copies)

            def step(status, remaining, total):
                if made[-1]:
                    made[-1] = False
                    write()
            return super().backup(target, pages=64, progress=step)

    connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda db, *a, **k: connect(
        db, *a, **({**k, "factory": Stepped} if isinstance(db, str) and "immutable=1" in db else k)))
    return made


def test_a_writer_during_the_copy_does_not_leave_a_torn_snapshot(tmp_path, signer, monkeypatch):
    """The copy reads the target with `immutable=1`, which takes no lock. A writer that appended and checkpointed
    while it was half done left it torn: `integrity_check` failed, and lookups and reads went wrong for the reader's
    life (#317 review). The target is checked before and after, and a copy taken while it changed is taken again."""
    target, path = tmp_path / "target.sqlite", tmp_path / "ledger.sqlite"
    _padded(target, signer, 0, 100)
    path.symlink_to(target)
    beside = sorted(p.name for p in tmp_path.iterdir())
    made = _write_during_copies(monkeypatch, lambda: _padded(target, signer, 100, 150), copies=1)
    with Ledger(path, signer) as reader:
        assert reader.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        events = list(reader.iter())
        assert [e["body"]["i"] for e in events] == list(range(150))
        assert all(reader.get(e["id"]) == e for e in events)
        assert len(made) == 2  # the first copy was thrown away
        _refuses(reader)
    assert sorted(p.name for p in tmp_path.iterdir()) == beside


def test_a_target_changed_during_every_copy_without_wal_is_refused(tmp_path, signer, monkeypatch):
    """Repeated changing copies without a remaining writer WAL report unavailable; direct target data survives."""
    from exo.ledger import SNAPSHOT_TRIES

    target, path = tmp_path / "target.sqlite", tmp_path / "ledger.sqlite"
    _padded(target, signer, 0, 100)
    path.symlink_to(target)
    added = []

    def write():
        _padded(target, signer, 100 + 10 * len(added), 110 + 10 * len(added))
        added.append(1)
    made = _write_during_copies(monkeypatch, write, copies=SNAPSHOT_TRIES)
    with pytest.raises(LedgerError, match="private snapshot unavailable"):
        Ledger(path, signer)
    assert len(made) == SNAPSHOT_TRIES
    with Ledger(target, signer) as direct:
        assert len(direct) == 100 + 10 * SNAPSHOT_TRIES


def test_a_copy_that_fails_on_an_unchanged_target_is_refused(tmp_path, signer, monkeypatch):
    """A changed failed copy retries; an unchanged failed copy refuses without making target sidecars."""
    target, path = tmp_path / "target.sqlite", tmp_path / "ledger.sqlite"
    _padded(target, signer, 0, 3)
    path.symlink_to(target)
    tried = []

    class Full(sqlite3.Connection):
        def backup(self, target_db, **kwargs):
            tried.append(1)
            if len(tried) == 1:
                _padded(target, signer, 3, 4)  # the first copy fails under a writer, the second on its own
            raise sqlite3.OperationalError("database or disk is full")

    connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda db, *a, **k: connect(
        db, *a, **({**k, "factory": Full} if isinstance(db, str) and "immutable=1" in db else k)))
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(LedgerError, match="private snapshot unavailable"):
        Ledger(path, signer)
    assert tried == [1, 1]
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_failed_copy_on_read_only_media_reports_unavailable(tmp_path, signer, monkeypatch):
    """Read-only media gets the same explicit unavailable result, without a stale immutable fallback."""
    target, path = tmp_path / "target.sqlite", tmp_path / "ledger.sqlite"
    _padded(target, signer, 0, 3)
    path.symlink_to(target)
    before = sorted(p.name for p in tmp_path.iterdir())

    class Full(sqlite3.Connection):
        def backup(self, target_db, **kwargs):
            raise sqlite3.OperationalError("database or disk is full")

    class ReadOnlyMedia(sqlite3.Connection):  # what SQLite raises when it cannot make the `-shm` file
        def execute(self, *args):
            err = sqlite3.OperationalError("unable to open database file")
            err.sqlite_errorcode, err.sqlite_errorname = sqlite3.SQLITE_CANTOPEN, "SQLITE_CANTOPEN"
            raise err

    connect = sqlite3.connect

    def opened(db, *a, **k):
        if isinstance(db, str) and "mode=ro" in db:
            k = {**k, "factory": Full if "immutable=1" in db else ReadOnlyMedia}
        return connect(db, *a, **k)

    monkeypatch.setattr(sqlite3, "connect", opened)
    with pytest.raises(LedgerError, match="private snapshot unavailable"):
        Ledger(path, signer)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_the_snapshot_is_read_only(tmp_path, signer):
    """The copy of a symlinked ledger took writes the read-only target refused (#317 review). `query_only` refuses
    them again. `check_anchors` cannot lock it and checks it as it stands, which cannot change."""
    target, path = tmp_path / "target.sqlite", tmp_path / "ledger.sqlite"
    _padded(target, signer, 0, 3)
    path.symlink_to(target)
    with Ledger(path, signer) as reader:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.db.execute("CREATE TABLE scratch(x)")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.db.execute("INSERT INTO events (id) VALUES ('x')")
        assert reader.check_anchors() == "" and len(reader) == 3


def _temp_store_on_disk() -> bool:
    """SQLite's default build (`SQLITE_TEMP_STORE` 0 or 1) keeps a private temporary database in a file."""
    db = sqlite3.connect(":memory:")
    try:
        return bool({"TEMP_STORE=0", "TEMP_STORE=1"} & {r[0] for r in db.execute("PRAGMA compile_options")})
    finally:
        db.close()


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="lists open files through /proc")
@pytest.mark.skipif(not _temp_store_on_disk(), reason="this SQLite keeps temporary databases in memory")
def test_the_snapshot_is_kept_in_a_private_temporary_file(tmp_path, signer):
    """The whole target was copied into memory: opening a 99 MB ledger added 111 MB to the reader's resident memory
    (#317). It goes into SQLite's private temporary database now. Past SQLite's page cache the copy is an
    owner-only temporary file, unlinked at once and gone at close. None is made beside the target."""
    target, path = tmp_path / "target.sqlite", tmp_path / "ledger.sqlite"
    _padded(target, signer, 0, 80, pad="x" * 100_000)  # 8 MB, past SQLite's 2 MB page cache
    path.symlink_to(target)
    beside = sorted(p.name for p in tmp_path.iterdir())

    def unlinked():
        out = set()
        for fd in os.listdir("/proc/self/fd"):
            try:
                st = os.stat(f"/proc/self/fd/{fd}")
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_nlink == 0:
                out.add((fd, stat.S_IMODE(st.st_mode), st.st_size))
        return out
    before = unlinked()
    with Ledger(path, signer) as reader:
        assert len(reader) == 80
        temp = unlinked() - before
        assert [(mode, size > target.stat().st_size // 2) for _fd, mode, size in temp] == [(0o600, True)]
        assert sorted(p.name for p in tmp_path.iterdir()) == beside
    assert not unlinked() - before


def test_an_append_does_not_fsync_its_anchors(tmp_path, signer, monkeypatch):
    """An append writes two anchors. They go through mkstemp now (#273) but skip the helper's fsync, as before, so
    an append costs what it did."""
    calls: list = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    with Ledger(tmp_path / "l.sqlite", signer) as led:
        led.append("note", {"i": 0}, "agent")
        assert all(a["seq"] == 1 for _, a in led.anchors()) and len(led.anchors()) == 2
    assert calls == []


def test_verify_warns_when_the_anchors_are_more_than_one_event_apart(tmp_path, signer, monkeypatch):
    """A keys/ copy that cannot be written only logs a warning per append, by design, so a frozen copy can fall far
    behind. verify says so, without failing (#277). One event apart is the accepted lag and gets no warning."""
    from exo import ledger as ledger_mod
    led = Ledger(tmp_path / "l.sqlite", signer)
    led.append("note", {"i": 0}, "agent")
    home = led.anchor_paths()[1]
    real = ledger_mod._atomic_write

    def frozen(path, data):
        if path == home:
            raise PermissionError(13, "read-only", str(path))
        return real(path, data)
    monkeypatch.setattr(ledger_mod, "_atomic_write", frozen)
    led.append("note", {"i": 1}, "agent")
    res = led.verify()
    assert res["ok"] and "warning" not in res
    led.append("note", {"i": 2}, "agent")
    res = led.verify()
    assert res["ok"] and res["anchored_seq"] == 1
    assert res["warning"] == f"head anchor {home} is at seq 1, 2 events behind the other: it is not being written"


def test_an_anchor_write_does_not_follow_a_symlink_planted_at_its_temporary_name(tmp_path, signer):
    """The old temporary name was `.<name>.<pid>.<thread>.tmp`, opened without O_EXCL or O_NOFOLLOW: a symlink there
    turned the next anchor write into a write to any file the user can write (#221 delta review)."""
    victim = tmp_path / "config.json"
    victim.write_text("{}")
    led = Ledger(tmp_path / "store" / "l.sqlite", signer)
    beside = led.anchor_paths()[0]
    planted = beside.with_name(f".{beside.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    planted.symlink_to(victim)
    led.append("note", {"i": 0}, "agent")
    assert victim.read_text() == "{}"
    assert not beside.is_symlink() and json.loads(beside.read_text())["seq"] == 1
    assert [p for p in beside.parent.iterdir() if p.name.endswith(".tmp")] == [planted]  # nothing else left behind
    assert led.verify()["ok"]


def test_an_anchor_that_is_a_symlink_is_unreadable(tmp_path, signer):
    """An anchor is only ever a regular file Exo wrote. A symlink in its place fails verify and stops appends."""
    led = Ledger(tmp_path / "l.sqlite", signer)
    for i in range(2):
        led.append("note", {"i": i}, "agent")
    beside = led.anchor_paths()[0]
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(beside.read_bytes())
    beside.unlink()
    beside.symlink_to(elsewhere)
    res = led.verify()
    assert not res["ok"] and "unreadable" in res["error"]
    with pytest.raises(LedgerError, match="unreadable"):
        led.append("note", {"i": 2}, "agent")
