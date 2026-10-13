"""Single-stream authority journal. Signatures leave this object only after COMMIT.

Provisioning is explicit. Normal construction never creates a database or changes keys.
The owner must protect this directory and keep it outside system rollback snapshots.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Callable

from exo import crypto
from exo.ledger import chain_hash, event_id

from . import (MAX_BATCH, MAX_BATCH_BYTES, MAX_RECORD, MAX_SEQ, NAME, AuthorityError, HeadConflict, canonical, digest,
              validate_expected_head, validate_identity, validate_intent, verify_checkpoint, verify_record)

SCHEMA = """
CREATE TABLE identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), document TEXT NOT NULL);
CREATE TABLE records (seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE, record TEXT NOT NULL);
CREATE TABLE operations (operation_id TEXT PRIMARY KEY, writer TEXT NOT NULL, intent_hash TEXT NOT NULL,
                         seq INTEGER NOT NULL UNIQUE REFERENCES records(seq));
CREATE TABLE checkpoints (seq INTEGER PRIMARY KEY, document TEXT NOT NULL);
"""


class AuthorityJournal:
    def __init__(self, path: Path, identity: dict, keys: dict, checkpoint_key,
                 *, fault: Callable[[str], None] | None = None):
        self.path = Path(path)
        self.identity = validate_identity(identity)
        self.keys, self.checkpoint_key = dict(keys), checkpoint_key
        self.lock = threading.RLock()
        self.fault = fault or (lambda point: None)  # explicit test injection, never a wire option
        if (set(self.keys) != set(self.identity["writers"]) or
                crypto.pubkey_b64(checkpoint_key) != self.identity["checkpoint_pub"] or
                any(crypto.pubkey_b64(key) != self.identity["writers"][writer]["pub"]
                    for writer, key in self.keys.items())):
            raise AuthorityError("provisioned signing keys do not match pinned identity")
        with self._db() as db:
            db.execute("BEGIN")  # one coherent snapshot across concurrent journal instances
            try:
                row = db.execute("SELECT document FROM identity WHERE singleton=1").fetchone()
                if row is None or row[0] != canonical(self.identity).decode():
                    raise AuthorityError("authority identity changed; explicit migration required")
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise AuthorityError("authority journal integrity check failed")
                self._verify(db)
                db.execute("COMMIT")
            except AuthorityError:
                raise
            except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise AuthorityError("established authority state is incomplete") from exc

    @classmethod
    def provision(cls, path: Path, identity: dict, keys: dict, checkpoint_key) -> "AuthorityJournal":
        """Create a new empty stream only. Caller must be an explicit administrator provisioning path."""
        path = Path(path)
        identity = validate_identity(identity)
        if (set(keys) != set(identity["writers"]) or crypto.pubkey_b64(checkpoint_key) != identity["checkpoint_pub"] or
                any(crypto.pubkey_b64(k) != identity["writers"][w]["pub"] for w, k in keys.items())):
            raise AuthorityError("keys do not match new identity")
        # Exclusive creation prevents replacing an established stream; parent creation is the operator's job.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO identity VALUES (1,?)", (canonical(identity).decode(),))
            genesis = cls._checkpoint(identity, checkpoint_key, 0, None, crypto.GENESIS,
                                      crypto.GENESIS, crypto.GENESIS, None)
            db.execute("INSERT INTO checkpoints VALUES (0,?)", (canonical(genesis).decode(),))
            db.execute("COMMIT")
        finally:
            db.close()
        # Persist the directory entry too. Failed/incomplete provisioning remains refused, never auto-replaced.
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return cls(path, identity, keys, checkpoint_key)

    @contextlib.contextmanager
    def _db(self):
        # mode=rw refuses missing established state instead of silently minting a new history.
        try:
            db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10,
                                 isolation_level=None)
        except sqlite3.Error as exc:
            raise AuthorityError("established authority journal is unavailable") from exc
        try:
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                raise AuthorityError("authority journal requires WAL durability")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    @staticmethod
    def _checkpoint(identity, key, seq, eid, chain, prev_chain, previous, writer):
        return crypto.sign_doc({"version": 1, "type": "exo-ledger-checkpoint", "stream_id": identity["stream_id"],
                                "identity_hash": digest(identity), "seq": seq, "event_id": eid, "chain": chain,
                                "prev_chain": prev_chain, "prev_checkpoint": previous, "writer": writer,
                                "pub": identity["checkpoint_pub"]}, key)

    def _verify(self, db):
        row = db.execute("SELECT document FROM checkpoints WHERE seq=0").fetchone()
        if row is None:
            raise AuthorityError("authority genesis is missing")
        previous = json.loads(row[0])
        verify_checkpoint(self.identity, previous)
        seen = set()
        for seq, eid, raw in db.execute("SELECT seq,event_id,record FROM records ORDER BY seq"):
            record = json.loads(raw)
            verify_record(self.identity, record, previous)
            if seq != record["event"]["seq"] or eid != record["event"]["id"]:
                raise AuthorityError("journal index does not match signed history")
            if any(ref not in seen for ref in record["event"]["refs"]):
                raise AuthorityError("journal reference is not an earlier event")
            cp = db.execute("SELECT document FROM checkpoints WHERE seq=?", (seq,)).fetchone()
            if cp is None or cp[0] != canonical(record["checkpoint"]).decode():
                raise AuthorityError("journal checkpoint is missing or inconsistent")
            op = db.execute("SELECT writer,intent_hash FROM operations WHERE seq=?", (seq,)).fetchone()
            intent = {k: record["event"][k] for k in ("author", "kind", "body", "session", "refs")}
            if op is None or op != (record["checkpoint"]["writer"], digest(intent)):
                raise AuthorityError("journal operation mapping is missing or inconsistent")
            seen.add(eid)
            previous = record["checkpoint"]
        if (db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] != previous["seq"] + 1 or
                db.execute("SELECT count(*) FROM operations").fetchone()[0] != previous["seq"]):
            raise AuthorityError("authority journal contains inconsistent tails")

    @staticmethod
    def _head(db, seq=None):
        row = (db.execute("SELECT document FROM checkpoints ORDER BY seq DESC LIMIT 1").fetchone() if seq is None
               else db.execute("SELECT document FROM checkpoints WHERE seq=?", (seq,)).fetchone())
        if row is None:
            raise AuthorityError("checkpoint is unavailable")
        return json.loads(row[0])

    def head(self, seq=None):
        if seq is not None and (type(seq) is not int or not 0 <= seq <= MAX_SEQ):
            raise AuthorityError("invalid checkpoint sequence")
        with self.lock, self._db() as db:
            return self._head(db, seq)

    def lookup(self, operation_id, *, writer=None):
        if not isinstance(operation_id, str) or not NAME.fullmatch(operation_id):
            raise AuthorityError("invalid operation id")
        with self.lock, self._db() as db:
            row = db.execute("SELECT r.record,o.writer FROM operations o JOIN records r ON r.seq=o.seq "
                             "WHERE o.operation_id=?", (operation_id,)).fetchone()
            if row is not None and writer is not None and row[1] != writer:
                raise AuthorityError("operation belongs to another writer")
            return json.loads(row[0]) if row else None

    def read_since(self, seq, limit=MAX_BATCH):
        if type(seq) is not int or not 0 <= seq <= MAX_SEQ or type(limit) is not int or not 1 <= limit <= MAX_BATCH:
            raise AuthorityError("invalid replay range")
        with self.lock, self._db() as db:
            records, size = [], 2
            for row in db.execute("SELECT record FROM records WHERE seq>? ORDER BY seq LIMIT ?", (seq, limit)):
                length = len(row[0].encode()) + 1
                if size + length > MAX_BATCH_BYTES:
                    if not records:
                        raise AuthorityError("stored event exceeds replay limit")
                    break
                records.append(json.loads(row[0]))
                size += length
            return records

    def append(self, writer, operation_id, expected_head, intent):
        if (not isinstance(writer, str) or writer not in self.identity["writers"] or
                not isinstance(operation_id, str) or not NAME.fullmatch(operation_id)):
            raise AuthorityError("writer or operation is not provisioned")
        expected_head = validate_expected_head(expected_head)
        intent = validate_intent(intent)
        if intent["author"] not in self.identity["writers"][writer]["authors"]:
            raise AuthorityError("writer may not use this author attribution")
        fingerprint = digest(intent)
        with self.lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = db.execute("SELECT writer,intent_hash,seq FROM operations WHERE operation_id=?",
                                 (operation_id,)).fetchone()
                if old:
                    if old[:2] != (writer, fingerprint):
                        raise AuthorityError("operation id belongs to different intent or writer")
                    record = json.loads(db.execute("SELECT record FROM records WHERE seq=?", (old[2],)).fetchone()[0])
                    db.execute("ROLLBACK")
                    return record
                previous = self._head(db)
                if expected_head != {"seq": previous["seq"], "chain": previous["chain"]}:
                    raise HeadConflict(previous)
                for ref in intent["refs"]:
                    if not db.execute("SELECT 1 FROM records WHERE event_id=?", (ref,)).fetchone():
                        raise AuthorityError("reference is not in the authoritative stream")
                event = {**intent, "ts": crypto.now_ts(), "prev": previous["chain"],
                         "pub": self.identity["writers"][writer]["pub"]}
                event["id"] = event_id(event)
                event = crypto.sign_doc(event, self.keys[writer])
                seq = previous["seq"] + 1
                chain = chain_hash(previous["chain"], event["id"])
                checkpoint = self._checkpoint(self.identity, self.checkpoint_key, seq, event["id"], chain,
                                              previous["chain"], digest(previous), writer)
                record = {"event": {**event, "seq": seq, "chain": chain}, "checkpoint": checkpoint}
                encoded_record = canonical(record)
                if len(encoded_record) > MAX_RECORD:
                    raise AuthorityError("signed record exceeds replay limit")
                # All committed records form the durable anchor outbox; no separate enqueue can get lost.
                db.execute("INSERT INTO records VALUES (?,?,?)", (seq, event["id"], encoded_record.decode()))
                db.execute("INSERT INTO checkpoints VALUES (?,?)", (seq, canonical(checkpoint).decode()))
                db.execute("INSERT INTO operations VALUES (?,?,?,?)", (operation_id, writer, fingerprint, seq))
                self.fault("before_commit")
                db.execute("COMMIT")
                self.fault("after_commit")
                return record
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise


class JournalClient:
    """In-process contract adapter for portable tests; production uses an authenticated OS transport."""
    def __init__(self, journal: AuthorityJournal, writer: str):
        if not isinstance(writer, str) or writer not in journal.identity["writers"]:
            raise AuthorityError("unknown writer")
        self.journal, self.writer, self.identity = journal, writer, journal.identity

    def append(self, operation_id, expected_head, intent):
        return self.journal.append(self.writer, operation_id, expected_head, intent)

    def lookup(self, operation_id):
        return self.journal.lookup(operation_id, writer=self.writer)

    def head(self, seq=None):
        return self.journal.head(seq)

    def read_since(self, seq, limit=MAX_BATCH):
        return self.journal.read_since(seq, limit)
