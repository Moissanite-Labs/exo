"""Append-only, signed, hash-chained event log in SQLite.

Each event is a canonical JSON document:

    id       sha256 of the canonical event without `id` and `sig`
    ts       UTC timestamp, ISO 8601 with microseconds
    author   who wrote the event (a person or an agent seat)
    kind     one of KINDS
    body     JSON object
    session  optional session id
    refs     list of earlier event ids this event points at
    prev     chain hash of the previous event (GENESIS for the first)
    pub      base64 Ed25519 public key of the signer
    sig      Ed25519 signature over sha256(canonical event without `sig`)

The chain hash of an event is sha256(prev + id). It is stored per row and becomes the
next event's `prev`. Rows cannot be updated or deleted: triggers abort any attempt.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

from nacl.signing import SigningKey

from . import crypto
from .crypto import GENESIS, Signer

KINDS = frozenset({
    "utterance", "ruling", "verdict", "action", "artifact", "ingest", "question", "answer",
    "belief_proposed", "belief_accepted", "note",
})
VERDICTS = frozenset({"accept", "edit", "reject", "should_have_been"})
SIGNED_KEYS = ("id", "ts", "author", "kind", "body", "session", "refs", "prev", "pub", "sig")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, ts TEXT NOT NULL, author TEXT NOT NULL,
  kind TEXT NOT NULL, body TEXT NOT NULL, session TEXT, refs TEXT NOT NULL, prev TEXT NOT NULL,
  pub TEXT NOT NULL, sig TEXT NOT NULL, chain TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS events_session ON events(session);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
"""
COLS = "seq,id,ts,author,kind,body,session,refs,prev,pub,sig,chain"


class LedgerError(ValueError):
    pass


class Head(NamedTuple):
    seq: int
    id: str | None
    chain: str


def event_id(event: Mapping) -> str:
    return crypto.sha256_hex(crypto.canonical({k: v for k, v in event.items() if k not in ("id", "sig")}))


def chain_hash(prev: str, eid: str) -> str:
    return crypto.sha256_hex((prev + eid).encode("ascii"))


def _row(row) -> dict:
    r = dict(zip(COLS.split(","), row))
    r["body"] = json.loads(r["body"])
    r["refs"] = json.loads(r["refs"])
    return r


def signed_part(event: Mapping) -> dict:
    return {k: event[k] for k in SIGNED_KEYS}


class Ledger:
    """`Ledger(path, signer)`. `signer` may be a `crypto.Signer`, a bare `SigningKey`, or None
    for read-only use."""

    def __init__(self, path: str | os.PathLike, signer: Signer | SigningKey | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(signer, SigningKey):
            signer = Signer("", signer)
        self.signer = signer
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- read ----------------------------------------------------------------

    def head(self) -> Head:
        row = self.db.execute("SELECT seq, id, chain FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return Head(*row) if row else Head(0, None, GENESIS)

    def get(self, eid: str) -> dict | None:
        row = self.db.execute(f"SELECT {COLS} FROM events WHERE id = ?", (eid,)).fetchone()
        return _row(row) if row else None

    def iter(self, kind: str | Iterable[str] | None = None, since: int | str | None = None,
             session: str | None = None) -> Iterator[dict]:
        """Events in append order. `since` is a sequence number (exclusive) or an ISO
        timestamp (inclusive)."""
        where, params = ["1=1"], []
        if kind is not None:
            kinds = [kind] if isinstance(kind, str) else list(kind)
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            params += kinds
        if isinstance(since, int):
            where.append("seq > ?")
            params.append(since)
        elif isinstance(since, str):
            where.append("ts >= ?")
            params.append(since)
        if session is not None:
            where.append("session = ?")
            params.append(session)
        cur = self.db.execute(f"SELECT {COLS} FROM events WHERE {' AND '.join(where)} ORDER BY seq", params)
        for row in cur:
            yield _row(row)

    def __len__(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # --- write ---------------------------------------------------------------

    def append(self, kind: str, body: Mapping, author: str | None = None, session: str | None = None,
               refs: Iterable[str] = ()) -> dict:
        if self.signer is None:
            raise LedgerError("ledger opened without a signer; cannot append")
        author = author or self.signer.author
        if not author:
            raise LedgerError("author is required")
        if kind not in KINDS:
            raise LedgerError(f"unknown kind {kind!r}; expected one of {sorted(KINDS)}")
        if not isinstance(body, Mapping):
            raise LedgerError("body must be a JSON object")
        body = json.loads(crypto.canonical(dict(body)))  # normalise and reject non-JSON values early
        refs = list(dict.fromkeys(refs))
        if kind == "verdict":
            if body.get("verdict") not in VERDICTS:
                raise LedgerError(f"verdict must be one of {sorted(VERDICTS)}")
            target = body.get("target")
            if not isinstance(target, str):
                raise LedgerError("verdict needs body.target, the id of the judged event")
            if target not in refs:
                refs.insert(0, target)

        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                for r in refs:
                    if not self.db.execute("SELECT 1 FROM events WHERE id = ?", (r,)).fetchone():
                        raise LedgerError(f"ref {r} is not an event in this ledger")
                head = self.head()
                event = {"ts": crypto.now_ts(), "author": author, "kind": kind, "body": body,
                         "session": session, "refs": refs, "prev": head.chain, "pub": self.signer.pub}
                event["id"] = event_id(event)
                event = crypto.sign_doc(event, self.signer.key)
                chain = chain_hash(head.chain, event["id"])
                self.db.execute(
                    f"INSERT INTO events ({COLS.split(',', 1)[1]}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (event["id"], event["ts"], author, kind, crypto.canonical(body).decode("utf-8"), session,
                     json.dumps(refs), event["prev"], event["pub"], event["sig"], chain))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return {**event, "seq": head.seq + 1, "chain": chain}

    # --- verify --------------------------------------------------------------

    def verify(self, trusted: Mapping[str, Iterable[str]] | None = None) -> dict:
        """Recompute every id, chain link and signature. If `trusted` maps author to allowed
        public keys, also require each event's key to be one of them."""
        prev, checked = GENESIS, 0
        for ev in self.iter():
            doc = signed_part(ev)
            if ev["prev"] != prev:
                return _bad(ev, "prev does not link to the previous chain hash", checked)
            if event_id(doc) != ev["id"]:
                return _bad(ev, "id does not match event content", checked)
            if chain_hash(prev, ev["id"]) != ev["chain"]:
                return _bad(ev, "stored chain hash is wrong", checked)
            if not crypto.verify_doc(doc, ev["pub"]):
                return _bad(ev, "signature does not verify", checked)
            if trusted is not None and ev["pub"] not in set(trusted.get(ev["author"], ())):
                return _bad(ev, f"key is not trusted for author {ev['author']!r}", checked)
            prev, checked = ev["chain"], checked + 1
        return {"ok": True, "checked": checked, "head": prev}


def _bad(ev: dict, reason: str, checked: int) -> dict:
    return {"ok": False, "seq": ev["seq"], "id": ev["id"], "error": reason, "checked": checked}
