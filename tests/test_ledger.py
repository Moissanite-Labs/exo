import hashlib
import json
import os
import shutil
import sqlite3
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
