"""Independent checkpoint witness; stores commitments, never event bodies.

Deploy the witness under a separate administrator and protect its database and
key independently of the append authority. This module supplies the durable
protocol engine, not a deployed network service. A saved receipt proves that
a prefix was witnessed; learning the current witnessed head requires a fresh,
authenticated query to that independent service.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
from pathlib import Path

from exo import crypto

from . import (
    HASH, AuthorityError, canonical, digest, validate_identity,
    verify_checkpoint, verify_checkpoint_successor,
)


class WitnessConflict(AuthorityError):
    """A checkpoint conflicts with the witnessed prefix, or skips part of it."""


def _check_receipt(identity: dict, receipt: dict, witness_pub: str) -> None:
    fields = {"version", "type", "stream_id", "identity_hash", "seq", "event_id",
              "chain", "checkpoint_hash", "prev_receipt", "pub", "sig"}
    if (not isinstance(receipt, dict) or set(receipt) != fields or
            type(receipt["version"]) is not int or receipt["version"] != 1 or
            receipt["type"] != "exo-ledger-witness-receipt" or
            receipt["stream_id"] != identity["stream_id"] or
            receipt["identity_hash"] != digest(identity) or receipt["pub"] != witness_pub or
            type(receipt["seq"]) is not int or receipt["seq"] < 0 or
            any(not isinstance(receipt[k], str) or not HASH.fullmatch(receipt[k])
                for k in ("chain", "checkpoint_hash", "prev_receipt"))):
        raise AuthorityError("invalid witness receipt")
    canonical(receipt)
    if not crypto.verify_doc(receipt, witness_pub):
        raise AuthorityError("witness receipt signature is not trusted")
    if receipt["seq"] == 0:
        if (receipt["event_id"] is not None or receipt["chain"] != crypto.GENESIS or
                receipt["prev_receipt"] != crypto.GENESIS):
            raise AuthorityError("invalid witness genesis receipt")
    elif not isinstance(receipt["event_id"], str) or not HASH.fullmatch(receipt["event_id"]):
        raise AuthorityError("invalid witnessed event identity")


def verify_receipt(identity: dict, checkpoint: dict, receipt: dict, witness_pub: str,
                   previous: dict | None = None) -> None:
    """Verify against independently pinned authority and witness identities.

    With ``previous``, also prove that this receipt extends that witnessed prefix.
    Without it, a positive-sequence receipt proves only the signed commitment.
    Neither case claims the supplied receipt is the witness's latest head.
    """
    canonical(checkpoint)
    verify_checkpoint(identity, checkpoint)
    _check_receipt(identity, receipt, witness_pub)
    if (receipt["checkpoint_hash"] != digest(checkpoint) or
            any(receipt[k] != checkpoint[k] for k in ("seq", "event_id", "chain"))):
        raise AuthorityError("witness receipt does not bind this checkpoint")
    if previous is not None:
        _check_receipt(identity, previous, witness_pub)
        if (receipt["seq"] != previous["seq"] + 1 or receipt["prev_receipt"] != digest(previous) or
                checkpoint["prev_chain"] != previous["chain"] or
                checkpoint["prev_checkpoint"] != previous["checkpoint_hash"]):
            raise AuthorityError("witness receipt does not extend the previous receipt")


class WitnessJournal:
    """One explicitly provisioned stream. Normal startup never creates state.

    Caller supplies trusted identity/configuration and protects the directory.
    Every accepted checkpoint and its receipt commit atomically with FULL SQLite
    synchronization before the receipt leaves this object. Identical retries
    return the original receipt; conflicting or skipped checkpoints are refused.
    """

    def __init__(self, path: Path, identity: dict, key, *, fault=None):
        self.path, self.identity = Path(path), validate_identity(identity)
        self.key, self.pub = key, crypto.pubkey_b64(key)
        self.lock = threading.RLock()
        self.fault = fault or (lambda point: None)
        if self.pub == self.identity["checkpoint_pub"] or any(
                self.pub == w["pub"] for w in self.identity["writers"].values()):
            raise AuthorityError("the witness must have an independent key")
        with self._db() as db:
            db.execute("BEGIN")  # coherent verification even while another instance appends
            try:
                metadata = db.execute("SELECT identity,witness_pub FROM metadata WHERE singleton=1").fetchone()
                if metadata != (canonical(self.identity).decode(), self.pub):
                    raise AuthorityError("witness identity changed; explicit migration required")
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise AuthorityError("witness journal integrity check failed")
                self._verify(db)
            except AuthorityError:
                raise
            except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise AuthorityError("established witness state is incomplete") from exc
            finally:
                db.execute("ROLLBACK")

    @classmethod
    def provision(cls, path: Path, identity: dict, key, genesis: dict):
        """Explicit new-stream creation. Incomplete provisioning is never overwritten."""
        path, identity = Path(path), validate_identity(identity)
        canonical(genesis)
        verify_checkpoint(identity, genesis)
        pub = crypto.pubkey_b64(key)
        if genesis["seq"] != 0 or pub == identity["checkpoint_pub"] or any(
                pub == w["pub"] for w in identity["writers"].values()):
            raise AuthorityError("provisioning requires genesis and an independent witness key")
        receipt = cls._receipt(identity, key, genesis, None)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                       "identity TEXT NOT NULL, witness_pub TEXT NOT NULL)")
            db.execute("CREATE TABLE witnessed (seq INTEGER PRIMARY KEY, checkpoint TEXT NOT NULL, "
                       "receipt TEXT NOT NULL)")
            db.execute("INSERT INTO metadata VALUES (1,?,?)", (canonical(identity).decode(), pub))
            db.execute("INSERT INTO witnessed VALUES (0,?,?)", (canonical(genesis).decode(), canonical(receipt).decode()))
            db.execute("COMMIT")
        finally:
            db.close()
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return cls(path, identity, key)

    @contextlib.contextmanager
    def _db(self):
        try:
            db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True,
                                 isolation_level=None, timeout=10)
        except sqlite3.Error as exc:
            raise AuthorityError("established witness journal is unavailable") from exc
        try:
            if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                raise AuthorityError("established witness journal must use WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    @staticmethod
    def _receipt(identity, key, checkpoint, previous):
        return crypto.sign_doc({
            "version": 1, "type": "exo-ledger-witness-receipt", "stream_id": identity["stream_id"],
            "identity_hash": digest(identity), "seq": checkpoint["seq"], "event_id": checkpoint["event_id"],
            "chain": checkpoint["chain"], "checkpoint_hash": digest(checkpoint),
            "prev_receipt": digest(previous) if previous is not None else crypto.GENESIS,
            "pub": crypto.pubkey_b64(key),
        }, key)

    def _verify(self, db):
        previous_checkpoint = previous_receipt = None
        count = 0
        for seq, raw_checkpoint, raw_receipt in db.execute("SELECT seq,checkpoint,receipt FROM witnessed ORDER BY seq"):
            checkpoint, receipt = json.loads(raw_checkpoint), json.loads(raw_receipt)
            if type(seq) is not int or seq != count or checkpoint.get("seq") != seq:
                raise AuthorityError("witness journal has a missing or inconsistent prefix")
            verify_receipt(self.identity, checkpoint, receipt, self.pub, previous_receipt)
            if previous_checkpoint is not None:
                verify_checkpoint_successor(self.identity, checkpoint, previous_checkpoint)
            previous_checkpoint, previous_receipt = checkpoint, receipt
            count += 1
        if not count:
            raise AuthorityError("witness genesis is missing")

    def head(self):
        """Current local witnessed head. Transport authentication/freshness is the deployer's job."""
        with self.lock, self._db() as db:
            row = db.execute("SELECT receipt FROM witnessed ORDER BY seq DESC LIMIT 1").fetchone()
            if row is None:
                raise AuthorityError("witness genesis is missing")
            return json.loads(row[0])

    def lookup(self, seq):
        if type(seq) is not int or seq < 0:
            raise AuthorityError("invalid witness sequence")
        with self.lock, self._db() as db:
            row = db.execute("SELECT receipt FROM witnessed WHERE seq=?", (seq,)).fetchone()
            return json.loads(row[0]) if row else None

    def observe(self, checkpoint):
        checkpoint = json.loads(canonical(checkpoint))
        verify_checkpoint(self.identity, checkpoint)
        with self.lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = db.execute("SELECT checkpoint,receipt FROM witnessed WHERE seq=?", (checkpoint["seq"],)).fetchone()
                if old is not None:
                    if old[0] != canonical(checkpoint).decode():
                        raise WitnessConflict("another checkpoint is already witnessed at this sequence")
                    db.execute("ROLLBACK")
                    return json.loads(old[1])
                latest = db.execute("SELECT checkpoint,receipt FROM witnessed ORDER BY seq DESC LIMIT 1").fetchone()
                if latest is None:
                    raise AuthorityError("witness genesis is missing")
                previous, previous_receipt = map(json.loads, latest)
                try:
                    verify_checkpoint_successor(self.identity, checkpoint, previous)
                except AuthorityError as exc:
                    raise WitnessConflict("checkpoint does not extend the witnessed prefix") from exc
                receipt = self._receipt(self.identity, self.key, checkpoint, previous_receipt)
                db.execute("INSERT INTO witnessed VALUES (?,?,?)",
                           (checkpoint["seq"], canonical(checkpoint).decode(), canonical(receipt).decode()))
                self.fault("before_commit")
                db.execute("COMMIT")
                self.fault("after_commit")
                return receipt
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
