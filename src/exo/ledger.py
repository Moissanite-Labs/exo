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

Head anchor (D-040). Triggers stop a careless delete, not a deliberate one: a writer can drop a trigger, delete the
tail and put the trigger back, and the shorter chain still links. So every append also writes the new head (sequence
number, row count and chain hash) to two files outside the database: `<ledger>.head` beside it and
`$EXO_HOME/keys/heads/<hash of the ledger path>.json` (the keys directory, which the gate treats as credentials).
The hash is of the path with its folder resolved but not the file, so a ledger file swapped for a symlink keeps its
anchor. Both are 0600, read only as regular files and written through a fresh temporary file. The anchors are
written after COMMIT, from the committed head, so a writer killed in between leaves them one event behind (caught up
by the next append), never ahead of the ledger. The copy under keys/ is written even when the one beside the ledger
fails. Before it writes, an append catches the anchors up when the one beside the ledger trails or none is at the
head. It refuses when the one beside the ledger still trails, or when none is at the head and none can be written:
appends never go on more than one event past every anchor. `verify` checks every anchor it finds: the ledger must
hold at least that many events and the chain hash at that position must match. A ledger file that is gone while an
anchor remains fails too (`missing_ledger_problem`). A ledger file that is a symlink fails verify, and append and
anchor refuse it. Without an authority such a file is opened read-only, and only when it is a regular file, so
opening it writes nothing through the link. The exceptions are SQLite's own `-shm` file beside a target that
already has a `-wal` file, and its `-wal` and `-shm` files beside a target read live because no copy of it could be
taken at open (`_snapshot`). `verify` warns when the anchors are more than one event apart. `append` also refuses to
write past an anchor that does not match, so a truncated ledger cannot be papered over by the next event, and
`anchor()` will not overwrite one unless forced. A trigger found missing when the ledger is opened is recreated, and
`verify` reports it.

What anchoring does not protect against: a process running as the same user that rewrites the database and both
anchor files together, or deletes both anchors (verify then reports the ledger as unanchored, not as broken).

Trust (D-040). `verify(trusted=...)` takes either a map of author to allowed public keys or a key registry
(exo.keyring.KeyRegistry). With the registry, an event signed by a key nobody pinned fails.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import stat
import threading
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from nacl.signing import SigningKey

from . import crypto
from .core.private import atomic_write
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
TRIGGERS = ("events_no_update", "events_no_delete")

RECOVER = ("Check with: exo memory verify. Only if no events were removed (an append killed by an older Exo can "
           "leave its anchor ahead), run: exo log anchor --force")

logger = logging.getLogger(__name__)


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


def _ledger_key(path: str | os.PathLike) -> Path:
    """The ledger's path with its folder resolved but not the file. A ledger file swapped for a symlink keeps the
    name of its anchor under keys/heads/, and for a regular file this is what `path.resolve()` gave."""
    path = Path(path)
    return path.parent.resolve() / path.name


def anchor_paths_for(path: str | os.PathLike) -> list[Path]:
    """`<ledger>.head` beside the database, and one file per ledger under `$EXO_HOME/keys/heads/`."""
    from .store import exo_home
    path = Path(path)
    name = hashlib.sha256(str(_ledger_key(path)).encode("utf-8")).hexdigest()[:24] + ".json"
    return [path.with_name(path.name + ".head"), exo_home() / "keys" / "heads" / name]


class Ledger:
    """`Ledger(path, signer)`. `signer` may be a `crypto.Signer`, a bare `SigningKey`, or None
    for read-only use."""

    _captured_anchors: tuple[tuple[Path, bytes], ...]

    def __init__(self, path: str | os.PathLike, signer: Signer | SigningKey | None = None,
                 *, authority=None, authority_author: str | None = None):
        from .ledger_authority import AuthorityError, validate_identity
        from .ledger_authority.factory import required
        if signer is not None and (authority is not None or required()):
            raise AuthorityError("direct-key ledger writes are disabled; use the installed authority factory")
        self.authority = authority
        self.authority_author = authority_author
        if authority is not None:
            validate_identity(authority.identity)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(signer, SigningKey):
            signer = Signer("", signer)
        self.signer = signer
        self._lock = threading.Lock()
        self.missing_triggers: list[str] = []
        if authority is None and self.path.is_symlink():  # opened without writing through the link (#277)
            self.db = _open_symlinked(self.path)
            return
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.missing_triggers = self._missing_triggers()
        if self.missing_triggers:
            logger.warning("ledger %s: append-only trigger(s) %s were missing; recreated", self.path,
                           ", ".join(self.missing_triggers))
        self.db.executescript(SCHEMA)

    @classmethod
    def read_only(cls, path: str | os.PathLike) -> "Ledger":
        """Open a stable read transaction without creating a ledger, repairing triggers or changing journal mode."""
        path = _ledger_key(path)  # bind relative folders without resolving away a file symlink
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise LedgerError("read-only ledger must be a regular, non-symlink file")
        reader = cls.__new__(cls)
        reader.path, reader.signer = path, None
        reader.authority, reader.authority_author = None, None
        reader._lock = threading.Lock()
        reader.missing_triggers = []
        # Anchors follow COMMIT. Capture them before BEGIN establishes the read snapshot:
        # an intact concurrent append can then only extend, never overtake, this evidence.
        reader._captured_anchors = tuple((p, crypto.canonical(a)) for p in anchor_paths_for(path)
                                         if (a := cls._read_anchor(p)) is not None)
        reader.db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False,
                                    isolation_level=None, timeout=5)
        try:
            reader.db.execute("PRAGMA query_only=ON")
            reader.db.execute("BEGIN")
            reader.head()  # establish the snapshot before verification and scoring share it
            after = path.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or not stat.S_ISREG(after.st_mode):
                raise LedgerError("ledger path changed during read-only open")
        except BaseException:
            reader.close()
            raise
        return reader

    def _missing_triggers(self) -> list[str]:
        """Triggers absent from an existing events table (a fresh database has neither table nor triggers)."""
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return []
        have = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        return [t for t in TRIGGERS if t not in have]

    # --- head anchors (D-040) --------------------------------------------------

    def anchor_paths(self) -> list[Path]:
        return anchor_paths_for(self.path)

    @staticmethod
    def _read_anchor(path: Path) -> dict | None:
        """The anchor at `path`, None when there is none. Only a regular file counts: a symlink or anything else
        there is unreadable, since this code only ever writes a regular file."""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError:  # ELOOP: a symlink, even a dangling one
            return {"unreadable": True}
        try:
            with os.fdopen(fd, "rb") as fh:
                if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                    return {"unreadable": True}
                a = json.loads(fh.read().decode("utf-8"))
        except (OSError, ValueError):
            return {"unreadable": True}
        return a if isinstance(a, dict) else {"unreadable": True}

    def _anchor_problem(self, a: Mapping, head: "Head") -> str:
        return self._anchor_check(a, head)[0]

    def _anchor_check(self, a: Mapping, head: "Head") -> tuple[str, int]:
        """Why anchor `a` disagrees with the ledger ('' when it agrees), and the events up to its seq."""
        if a.get("unreadable") or not isinstance(a.get("seq"), int) or not isinstance(a.get("chain"), str):
            return "the anchor file is unreadable", 0
        if a["seq"] > head.seq:
            return f"the ledger ends at seq {head.seq} but its anchor says seq {a['seq']}: events were removed", 0
        if a["seq"] == 0:
            return ("" if a["chain"] == GENESIS else "the anchor's empty-ledger hash is wrong"), 0
        if not self._anchor_links(a):
            return f"the chain hash at seq {a['seq']} does not match its anchor: the ledger was rewritten", 0
        count = self.db.execute("SELECT COUNT(*) FROM events WHERE seq <= ?", (a["seq"],)).fetchone()[0]
        if isinstance(a.get("count"), int) and count != a["count"]:
            return f"{a['count']} events were anchored up to seq {a['seq']}, {count} remain", count
        return "", count

    def _anchor_links(self, a: Mapping) -> bool:
        """The ledger holds anchor `a`'s chain hash at its seq: one indexed lookup."""
        if a.get("seq") == 0:
            return a.get("chain") == GENESIS
        row = self.db.execute("SELECT chain FROM events WHERE seq = ?", (a.get("seq"),)).fetchone()
        return row is not None and row[0] == a.get("chain")

    def anchors(self) -> list[tuple[Path, dict]]:
        if hasattr(self, "_captured_anchors"):
            return [(p, json.loads(raw)) for p, raw in self._captured_anchors]
        out = []
        for p in self.anchor_paths():
            a = self._read_anchor(p)
            if a is not None:
                out.append((p, a))
        return out

    def check_anchors(self) -> str:
        """'' when every anchor found matches the ledger, else why not. A writer updates the anchors after its
        COMMIT, in a second transaction under the write lock, so the check takes that lock too: it never reads the
        head before a commit and the anchors after it. A database it cannot lock (read-only) is checked as it
        stands."""
        with self._lock:
            locked = True
            try:
                self.db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                locked = False
            try:
                head = self.head()
                for p, a in self.anchors():
                    why = self._anchor_problem(a, head)
                    if why:
                        return f"head anchor {p}: {why}"
                return ""
            finally:
                if locked:
                    self.db.execute("ROLLBACK")

    def _try_anchors(self, head: "Head", count: int) -> dict[Path, OSError]:
        """Write the head to every anchor path. Returns the paths that could not be written, with why."""
        doc = json.dumps({"v": 1, "ledger": str(_ledger_key(self.path)), "seq": head.seq, "count": count,
                          "chain": head.chain, "id": head.id}, sort_keys=True).encode("utf-8")
        failed: dict[Path, OSError] = {}
        for p in self.anchor_paths():
            try:
                _atomic_write(p, doc)
            except OSError as exc:
                failed[p] = exc
        return failed

    def _write_anchors(self, head: "Head", count: int) -> None:
        """Write the head to every anchor path. The anchor beside the ledger is required: a failure there is raised,
        but only after the copy under keys/ is written. That copy is best effort."""
        beside = self.anchor_paths()[0]
        failed = self._try_anchors(head, count)
        for p, exc in failed.items():
            if p != beside:
                logger.warning("ledger %s: cannot write head anchor %s: %s", self.path, p, exc)
        if beside in failed:
            raise failed[beside]

    def _catch_up(self, head: "Head", count: int, found: list[tuple[Path, int]]) -> None:
        """Before an insert, under the write lock: bring the anchors up to the head when the anchor beside the
        ledger trails it, or when no anchor holds it (the last write failed or was cut short). So an append never
        goes on more than one event past every anchor. It refuses when the anchor beside the ledger trails and
        cannot be written, or when no anchor holds the head and none can be written."""
        beside = self.anchor_paths()[0]
        seqs = dict(found)
        trails = beside in seqs and seqs[beside] < head.seq
        if not trails and (head.seq == 0 or max(seqs.values(), default=-1) >= head.seq):
            return
        failed = self._try_anchors(head, count)
        if trails and beside in failed:
            raise LedgerError(f"refusing to append: head anchor {beside} is at seq {seqs[beside]}, the ledger at "
                              f"seq {head.seq}, and it cannot be written ({failed[beside]}). Restore write access "
                              "to it, and the next append catches it up")
        if len(failed) == len(self.anchor_paths()):
            paths = " and ".join(str(p) for p in failed)
            raise LedgerError(f"refusing to append: no head anchor is at the ledger's seq {head.seq}, and {paths} "
                              f"cannot be written ({failed[beside]}). Restore write access to one of them, and the "
                              "next append catches the anchors up")

    def _count(self, checked: list[tuple[int, int]], head: "Head") -> int:
        """Events in the ledger, from the newest (seq, count) an anchor check already took: only the tail after it
        is counted again. With no anchor, every event is counted."""
        if not checked:
            return len(self)
        seq, count = max(checked)
        if seq < head.seq:
            count += self.db.execute("SELECT COUNT(*) FROM events WHERE seq > ?", (seq,)).fetchone()[0]
        return count

    def _anchor_committed(self, mine: "Head", count: int) -> None:
        """After an append's COMMIT: anchor the committed head under the write lock, so anchors only move forward.
        The anchors were checked in full before the insert, so this takes indexed lookups only. Events another
        process appended since are counted from the tail."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head = self.head()
            for p, a in self.anchors():
                if not isinstance(a.get("seq"), int) or a["seq"] > head.seq or not self._anchor_links(a):
                    raise LedgerError(f"head anchor {p} does not match the ledger")
            if head.seq > mine.seq:
                count += self.db.execute("SELECT COUNT(*) FROM events WHERE seq > ?", (mine.seq,)).fetchone()[0]
            self._write_anchors(head, count)
        finally:
            self.db.execute("ROLLBACK")

    def _anchor_head(self, force: bool = False) -> tuple["Head", list[tuple[Path, dict]]]:
        """Write the committed head to the anchors under the write lock, so they only move forward. An anchor
        that disagrees with the ledger is evidence and stays unless `force`. Returns the head and what it replaced."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head = self.head()
            bad = [(p, a, why) for p, a in self.anchors() if (why := self._anchor_problem(a, head))]
            if bad and not force:
                raise LedgerError(f"head anchor {bad[0][0]}: {bad[0][2]}")
            self._write_anchors(head, len(self))
            return head, [(p, a) for p, a, _ in bad]
        finally:
            self.db.execute("ROLLBACK")

    def anchor(self, force: bool = False) -> dict:
        """Anchor the current head (a store that predates D-040). It refuses to overwrite an anchor that disagrees
        with the ledger, since events may have been removed. `force` does, for a store left with an anchor ahead
        by an append killed before its COMMIT (older Exo wrote the anchor first), and lists what it replaced."""
        if why := self._symlink_problem():  # anchoring would bless whatever database the link points at
            raise LedgerError(f"refusing to anchor: {why}")
        with self._lock:
            try:
                head, replaced = self._anchor_head(force)
            except LedgerError as exc:
                raise LedgerError(f"{exc}. {RECOVER}") from None
            except OSError as exc:  # only the anchor beside the ledger raises
                raise LedgerError(f"head anchor {self.anchor_paths()[0]} cannot be written ({exc}). Restore "
                                  "write access to it") from None
        out: dict = {"seq": head.seq, "chain": head.chain}
        if replaced:
            out["replaced"] = [{"path": str(p), "seq": a.get("seq"), "chain": a.get("chain")} for p, a in replaced]
        return out

    def clear_anchors(self, beside: bool = True) -> None:
        """Remove this path's anchors: only for a new ledger made where an old one was deleted. `beside=False`
        keeps `<ledger>.head`, for a ledger copied in together with its anchor (`exo import-store`)."""
        for p in self.anchor_paths()[0 if beside else 1:]:
            p.unlink(missing_ok=True)

    @property
    def writable(self) -> bool:
        return self.authority is not None or self.signer is not None

    @property
    def author(self) -> str | None:
        if self.authority is not None:
            return self.authority_author or self.authority.identity["writers"][self.authority.writer]["author"]
        return self.signer.author if self.signer else None

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

    def _symlink_problem(self) -> str:
        """A ledger file that is a symlink may have been swapped for another database: verify fails, and append and
        anchor refuse."""
        if self.path.is_symlink():
            return f"the ledger {self.path} is a symbolic link, not the database itself: it may have been swapped"
        return ""

    # --- write ---------------------------------------------------------------

    def append(self, kind: str, body: Mapping, author: str | None = None, session: str | None = None,
               refs: Iterable[str] = (), *, operation_id: str | None = None) -> dict:
        if self.authority is not None:
            return self._append_authority(kind, body, author, session, refs, operation_id)
        from .ledger_authority.factory import required
        if required():
            raise LedgerError("direct-key ledger writes are disabled; use the installed authority factory")
        if self.signer is None:
            raise LedgerError("ledger opened without a signer; cannot append")
        if why := self._symlink_problem():
            raise LedgerError(f"refusing to append: {why}")
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
                checked: list[tuple[int, int]] = []
                found: list[tuple[Path, int]] = []
                for p, a in self.anchors():
                    why, upto = self._anchor_check(a, head)
                    if why:
                        raise LedgerError(f"refusing to append: head anchor {p}: {why}. {RECOVER}")
                    checked.append((a["seq"], upto))
                    found.append((p, a["seq"]))
                count = self._count(checked, head)
                self._catch_up(head, count, found)
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
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
            try:  # after COMMIT: an anchor is never ahead of the ledger, even if this process dies here
                self._anchor_committed(Head(head.seq + 1, str(event["id"]), chain), count + 1)
            except (LedgerError, OSError, sqlite3.Error) as exc:  # the event stands; the next append catches up or refuses
                logger.warning("ledger %s: seq %d committed, head anchor not updated: %s", self.path, head.seq + 1, exc)
        return {**event, "seq": head.seq + 1, "chain": chain}

    def sync_authority(self) -> dict:
        from .ledger_authority import AuthorityError
        from .ledger_authority.projection import synchronize
        if self.authority is None:
            raise AuthorityError("ledger has no pinned authority")
        return synchronize(self, self.authority)

    def _append_authority(self, kind, body, author, session, refs, operation_id):
        import secrets
        from .ledger_authority import (AppendOutcomeUnknown, AuthorityUnavailable, HeadConflict,
                                       ProjectionPending, AuthorityError, canonical, validate_intent, verify_checkpoint, verify_record)
        authority = self.authority
        author = author or self.authority_author or authority.identity["writers"][authority.writer]["author"]
        refs = list(dict.fromkeys(refs))
        if kind == "verdict" and isinstance(body, Mapping) and body.get("target") not in refs:
            refs.insert(0, body.get("target"))
        intent = validate_intent({"author": author, "kind": kind, "body": dict(body), "session": session, "refs": refs})
        operation_id = operation_id or secrets.token_hex(16)
        # Recovery does not repeat an external action. It copies only already-committed ledger records.
        for attempt in range(3):
            checkpoint = self.sync_authority()
            try:
                record = authority.append(operation_id, {"seq": checkpoint["seq"], "chain": checkpoint["chain"]}, intent)
                break
            except HeadConflict:
                if attempt == 2:
                    raise
            except (AuthorityUnavailable, OSError) as exc:
                raise AppendOutcomeUnknown(operation_id) from exc
        # The service may already have committed even if its reply cannot be verified
        # or the predecessor lookup fails. Preserve the operation ID for reconciliation.
        try:
            canonical(record)
            cp = record["checkpoint"]
            verify_checkpoint(authority.identity, cp)
            predecessor = checkpoint if cp["seq"] == checkpoint["seq"] + 1 else authority.head(cp["seq"] - 1)
            verify_record(authority.identity, record, predecessor)
            returned_intent = {k: record["event"][k] for k in intent}
            if record["checkpoint"]["writer"] != authority.writer or canonical(returned_intent) != canonical(intent):
                raise AuthorityError("authority returned a different append intent")
        except Exception as exc:
            raise AppendOutcomeUnknown(operation_id) from exc
        try:
            self.sync_authority()
        except Exception as exc:
            raise ProjectionPending(operation_id, record) from exc
        return record["event"]

    # --- verify --------------------------------------------------------------

    def verify(self, trusted: Mapping[str, Iterable[str]] | Any | None = None) -> dict:
        """Recompute every id, chain link and signature, then check the head anchors and the append-only
        triggers. `trusted` is a map of author to allowed public keys, or a key registry with
        `reason(author, pub)` (exo.keyring): an event whose key it does not trust fails."""
        if why := self._symlink_problem():
            return {"ok": False, "error": why, "checked": 0}
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
            if trusted is not None and hasattr(trusted, "reason"):
                why = trusted.reason(ev["author"], ev["pub"])
                if why:
                    return _bad(ev, f"untrusted key for author {ev['author']!r}: {why}", checked)
            elif trusted is not None and ev["pub"] not in set(trusted.get(ev["author"], ())):
                return _bad(ev, f"key is not trusted for author {ev['author']!r}", checked)
            prev, checked = ev["chain"], checked + 1
        missing = sorted(set(self.missing_triggers) | set(self._missing_triggers()))
        if missing:
            return {"ok": False, "error": f"append-only trigger {', '.join(missing)} was missing (recreated on "
                    "open); the ledger may have been edited", "checked": checked, "head": prev}
        why = self.check_anchors()
        if why:
            return {"ok": False, "error": why, "checked": checked, "head": prev}
        found = sorted((a["seq"], str(p)) for p, a in self.anchors() if isinstance(a.get("seq"), int))
        res = {"ok": True, "checked": checked, "head": prev, "anchored": bool(found),
               "anchored_seq": found[0][0] if found else None}
        if found and found[-1][0] - found[0][0] > 1:  # appends go on while one anchor cannot be written (#277)
            res["warning"] = (f"head anchor {found[0][1]} is at seq {found[0][0]}, {found[-1][0] - found[0][0]} "
                              "events behind the other: it is not being written")
        return res


def missing_ledger_problem(path: str | os.PathLike) -> str:
    """For a ledger file that is gone: '' when no anchor says it held events, else why that fails verify. It opens
    no database, so the check does not put an empty one in place of the evidence."""
    for p in anchor_paths_for(path):
        a = Ledger._read_anchor(p)
        if a is None:
            continue
        if a.get("unreadable") or not isinstance(a.get("seq"), int):
            return f"ledger {path} is missing and its head anchor {p} is unreadable"
        if a["seq"] > 0:
            return f"ledger {path} is missing but its head anchor {p} says seq {a['seq']}: the ledger was deleted"
    return ""


def _open_symlinked(path: Path) -> sqlite3.Connection:
    """A ledger file that is a symlink, opened read-only: no WAL switch, no schema, no file made at a dangling target.
    Only a regular file is opened: opening a FIFO would block. A target with a `-wal` file beside it has a writer: it
    is read live (SQLite's `mode=ro`) through the writer's own files, and stays current. A target without one holds
    all its data in its main file, and `_snapshot` copies it at open (#317). Reads are then a snapshot of the target
    as it stood at open: a fresh open sees later appends. The rare target a writer changes during every copy is read
    live too, and so is one whose copy fails (a full temporary folder). SQLite may then leave its `-wal` and `-shm`
    files beside it. A target that is gone, not a regular file, not a database or without an events table reads as an
    empty ledger kept in memory. Reads see the target's events, verify reports the link, and append and anchor
    refuse."""
    target, db = Path(os.path.realpath(path)), None
    try:
        if target.is_file():
            db = _snapshot(target)
            if db is None:
                db = _open_live(target)
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
                return db
    except sqlite3.DatabaseError:
        pass
    if db is not None:
        db.close()
    db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    db.executescript(SCHEMA)
    return db


def _open_live(target: Path) -> sqlite3.Connection:
    """The target read live (`mode=ro`), through a writer's `-wal` file when there is one. SQLite opens the `-wal` and
    `-shm` files at the first read, and makes them beside a WAL target that has none. Where it cannot (read-only
    media, or a folder the reader cannot write), that read fails with `SQLITE_CANTOPEN`, and the ledger would read as
    empty. A target with no `-wal` file holds all its data in its main file, so it is then read with `immutable=1`, as
    before #317."""
    db = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True, check_same_thread=False, isolation_level=None,
                         timeout=30)
    try:
        db.execute("SELECT 1 FROM sqlite_master").fetchone()
        return db
    except sqlite3.OperationalError as e:
        db.close()
        if (e.sqlite_errorcode & 0xFF) != sqlite3.SQLITE_CANTOPEN or Path(f"{target}-wal").exists():
            raise
    return sqlite3.connect(f"{target.as_uri()}?mode=ro&immutable=1", uri=True, check_same_thread=False,
                           isolation_level=None, timeout=30)


SNAPSHOT_TRIES = 3  # copies of a symlinked ledger a writer may spoil before it is read live instead (#317)


def _snapshot(target: Path) -> sqlite3.Connection | None:
    """A read-only copy of a symlinked ledger's target, or None to read it live: a `-wal` file is beside it, a writer
    changed it during each of `SNAPSHOT_TRIES` copies, or a copy failed while it stayed the same (a full temporary
    folder: a second copy would fill it again). A target that is not a ledger raises DatabaseError.

    The copy reads the target with `immutable=1`, which makes no `-wal` or `-shm` file beside it but takes no lock. A
    writer that appends and checkpoints during the copy leaves it torn: it fails `integrity_check`, lookups miss
    events, and reads can raise `database disk image is malformed` for the reader's life (#317). So the target's
    inode, size and mtime, and the absence of a `-wal` file, are checked before and after. A copy taken while any of
    them changed is thrown away. Every write moves the mtime, but many appends keep the size. So the check misses a
    write that keeps the size and lands in the same tick of the file system's clock as the write before it.

    The copy goes into SQLite's private temporary database, not into memory. About 2 MB stays in SQLite's page cache
    and the rest goes to a temporary file in SQLite's temporary folder: the first folder it can write of
    `SQLITE_TMPDIR`, `TMPDIR`, `/var/tmp`, `/usr/tmp`, `/tmp` and the current folder. SQLite makes it owner-only,
    unlinks it at once and drops it at close. So an open reader holds disk space the size of the ledger, not memory.
    (A SQLite built with `SQLITE_TEMP_STORE` 2 or 3 keeps this database in memory. The default build does not.)
    `query_only` keeps the copy read-only, as the target was."""
    for _ in range(SNAPSHOT_TRIES):
        before = _target_state(target)
        if before is None:
            return None
        src = sqlite3.connect(f"{target.as_uri()}?mode=ro&immutable=1", uri=True, check_same_thread=False,
                              isolation_level=None, timeout=30)
        try:
            if not src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
                raise sqlite3.DatabaseError(f"{target} has no events table")
            db = sqlite3.connect("", check_same_thread=False, isolation_level=None)
            try:
                src.backup(db)
            except sqlite3.DatabaseError:  # a target cut short under the copy: try again
                db.close()
                if _target_state(target) == before:  # the copy itself failed (a full temporary folder): read live
                    return None
                continue
        finally:
            src.close()
        if _target_state(target) == before:
            db.execute("PRAGMA query_only=ON")
            return db
        db.close()
    return None


def _target_state(target: Path) -> tuple[int, int, int] | None:
    """The target's inode, size and mtime. None while a `-wal` file is beside it (a writer has it open) or when it is
    gone: it is then read live, and a target that is gone reads as an empty ledger."""
    try:
        st = target.stat()
    except OSError:
        return None
    return None if Path(f"{target}-wal").exists() else (st.st_ino, st.st_size, st.st_mtime_ns)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write an anchor (0600) through `atomic_write`: a symlink planted in the folder cannot redirect it. No fsync,
    as before #273, so an append, which writes two anchors, costs what it did."""
    if not path.parent.exists():  # keys/heads under $EXO_HOME: private like the rest of keys/
        path.parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
    atomic_write(path, data, sync=False)


def _bad(ev: dict, reason: str, checked: int) -> dict:
    return {"ok": False, "seq": ev["seq"], "id": ev["id"], "error": reason, "checked": checked}
