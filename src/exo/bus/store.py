"""Append-only SQLite log with a hash chain, plus checkpoints. Schema matches Divan Bus v0.1."""
from __future__ import annotations

import json
import sqlite3
import threading

from . import wire

SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
  seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, prev TEXT NOT NULL, ts TEXT NOT NULL,
  from_seat TEXT NOT NULL, to_json TEXT NOT NULL, thread TEXT, kind TEXT NOT NULL, body TEXT NOT NULL,
  enc_json TEXT, attach_json TEXT NOT NULL, sig TEXT NOT NULL, chain TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS log_thread ON log(thread);
CREATE VIEW IF NOT EXISTS head AS SELECT seq, chain AS head FROM log ORDER BY seq DESC LIMIT 1;
CREATE TABLE IF NOT EXISTS checkpoints (
  seq INTEGER NOT NULL, head TEXT NOT NULL, ts TEXT NOT NULL, doc_json TEXT NOT NULL, created_at TEXT NOT NULL);
"""

COLS = "seq,id,prev,ts,from_seat,to_json,thread,kind,body,enc_json,attach_json,sig,chain,received_at"


class PrevMismatch(Exception):
    def __init__(self, seq: int, head: str):
        super().__init__(f"prev mismatch; head is {head}")
        self.seq, self.head = seq, head


def row_to_envelope(row) -> dict:
    """The exact envelope as accepted, plus seq and chain (not part of the signed document)."""
    r = dict(zip(COLS.split(","), row))
    return {
        "v": 1, "id": r["id"], "ts": r["ts"], "from": r["from_seat"], "to": json.loads(r["to_json"]),
        "thread": r["thread"], "kind": r["kind"], "body": r["body"],
        "attach": json.loads(r["attach_json"]), "enc": json.loads(r["enc_json"]) if r["enc_json"] else None,
        "prev": r["prev"], "sig": r["sig"], "seq": r["seq"], "chain": r["chain"],
    }


def strip(env: dict) -> dict:
    return {k: v for k, v in env.items() if k in wire.ENVELOPE_KEYS}


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def head(self) -> tuple[int, str]:
        row = self.db.execute("SELECT seq, head FROM head").fetchone()
        return (row[0], row[1]) if row else (0, wire.GENESIS)

    def append(self, env: dict) -> tuple[int, str]:
        """Append a validated envelope. Raises PrevMismatch if env['prev'] is not the current head."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                seq, head = self.head()
                if env["prev"] != head:
                    raise PrevMismatch(seq, head)
                chain = wire.chain_hash(head, env["id"])
                self.db.execute(
                    f"INSERT INTO log ({COLS.split(',', 1)[1]}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (env["id"], env["prev"], env["ts"], env["from"], json.dumps(env["to"]), env["thread"],
                     env["kind"], env["body"], json.dumps(env["enc"]) if env["enc"] else None,
                     json.dumps(env["attach"]), env["sig"], chain, wire.now_ts()),
                )
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            return seq + 1, chain

    def _select(self, where: str, params: tuple, limit: int, order: str = "ASC") -> list[dict]:
        rows = self.db.execute(f"SELECT {COLS} FROM log WHERE {where} ORDER BY seq {order} LIMIT ?",
                               (*params, limit)).fetchall()
        return [row_to_envelope(r) for r in rows]

    def messages(self, since: int = 0, to: str | None = None, thread: str | None = None,
                 limit: int = 200) -> list[dict]:
        where, params = "seq > ?", [since]
        if to:
            where += " AND EXISTS (SELECT 1 FROM json_each(log.to_json) WHERE value = ?)"
            params.append(to)
        if thread:
            where += " AND thread = ?"
            params.append(thread)
        return self._select(where, tuple(params), limit)

    def inbox(self, audiences: list[str], since: int = 0, limit: int = 200) -> list[dict]:
        marks = ",".join("?" * len(audiences))
        return self._select(f"seq > ? AND EXISTS (SELECT 1 FROM json_each(log.to_json) WHERE value IN ({marks}))",
                            (since, *audiences), limit)

    def thread(self, thread_id: str, limit: int = 500) -> list[dict]:
        return self._select("thread = ? OR id = ?", (thread_id, thread_id), limit)

    def recent(self, limit: int = 200) -> list[dict]:
        return self._select("1=1", (), limit, order="DESC")

    def search(self, query: str, limit: int = 50) -> list[dict]:
        like = f"%{query}%"
        return self._select("enc_json IS NULL AND (body LIKE ? OR thread LIKE ? OR from_seat LIKE ?)",
                            (like, like, like), limit, order="DESC")

    def claims(self, open_only: bool = True, limit: int = 200) -> list[dict]:
        """A claim is open until its author posts a report in the same thread."""
        where = "kind = 'claim'"
        if open_only:
            where += (" AND NOT EXISTS (SELECT 1 FROM log r WHERE r.kind = 'report'"
                      " AND r.thread = log.thread AND r.from_seat = log.from_seat AND r.seq > log.seq)")
        return self._select(where, (), limit)

    def verify(self, from_seq: int = 1, to_seq: int | None = None, keys_for=None) -> dict:
        """Recompute ids, chain links and (if keys_for(seat, envelope) -> [pubkeys] is given) signatures.

        An inverted or empty range verifies nothing, so it reports ok False
        instead of ok True with checked 0: a caller must not read an empty
        check as a clean log. The one exception is the default call on an
        empty log: verify() with no to_seq and from_seq at 1 asks whether
        the whole log is clean, and an empty log is clean, so it reports ok
        True with an empty marker. An explicit range on an empty log still reports
        ok False. to_seq is clamped to the head so the returned
        range always matches what was checked.
        """
        from_seq = max(1, from_seq)
        head_seq, head_hash = self.head()
        if to_seq is None:
            if head_seq == 0 and from_seq == 1:
                return {"ok": True, "empty": True, "from": 1, "to": 0,
                        "checked": 0, "head": head_hash}
            to_seq = head_seq
        to_seq = max(0, min(to_seq, head_seq))
        if to_seq < from_seq:
            return {"ok": False, "error": "range is empty: nothing to verify",
                    "from": from_seq, "to": to_seq, "checked": 0, "head": head_hash}
        prev_row = self.db.execute("SELECT chain FROM log WHERE seq = ?", (from_seq - 1,)).fetchone()
        prev = prev_row[0] if prev_row else wire.GENESIS
        checked = 0
        for env in self._select("seq >= ? AND seq <= ?", (from_seq, to_seq), to_seq - from_seq + 1):
            e = strip(env)
            if env["prev"] != prev:
                return self._bad(env, "prev does not link to the previous chain hash", checked)
            if wire.envelope_id(e) != env["id"]:
                return self._bad(env, "id does not match envelope content", checked)
            if wire.chain_hash(prev, env["id"]) != env["chain"]:
                return self._bad(env, "stored chain hash is wrong", checked)
            if keys_for and not any(wire.verify_doc(e, k) for k in keys_for(env["from"], e)):
                return self._bad(env, "signature does not verify", checked)
            prev, checked = env["chain"], checked + 1
        return {"ok": True, "from": from_seq, "to": to_seq, "checked": checked, "head": prev}

    @staticmethod
    def _bad(env: dict, reason: str, checked: int) -> dict:
        return {"ok": False, "seq": env["seq"], "id": env["id"], "error": reason, "checked": checked}

    def checkpoints(self) -> list[dict]:
        rows = self.db.execute("SELECT doc_json FROM checkpoints ORDER BY seq").fetchall()
        return [json.loads(r[0]) for r in rows]

    def add_checkpoint(self, doc: dict) -> None:
        self.db.execute("INSERT INTO checkpoints (seq, head, ts, doc_json, created_at) VALUES (?,?,?,?,?)",
                        (doc["seq"], doc["head"], doc["ts"], json.dumps(doc), wire.now_ts()))
