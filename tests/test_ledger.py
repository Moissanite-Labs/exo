import sqlite3
import threading

import pytest

from exo import crypto
from exo.crypto import GENESIS
from exo.ledger import Ledger, LedgerError


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
    assert led.verify() == {"ok": True, "checked": 3, "head": c["chain"], "anchored": True}


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
