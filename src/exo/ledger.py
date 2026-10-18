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

Head anchor (D-040). Triggers stop a careless delete, not a deliberate one: a writer can drop a trigger,
delete the tail and put the trigger back, and the shorter chain still links. So every append also writes the
new head (sequence number, row count and chain hash) to two files outside the database: `<ledger>.head` beside
it and `$EXO_HOME/keys/heads/<hash of the ledger path>.json` (the keys directory, which the gate treats as
credentials). Both are 0600. The anchors are written after COMMIT, from the committed head, so a writer killed
in between leaves them one event behind (caught up by the next append), never ahead of the ledger. When the
anchor beside the ledger cannot be written, the copy under keys/ still is, and the next append tries again before
it writes and refuses if it still cannot: the anchor never trails by more than one event while appends go on. `verify`
checks every anchor it finds: the ledger must hold at least that many events and the chain hash at that position
must match. A ledger file that is gone while an anchor remains fails too (`missing_ledger_problem`). `append`
refuses to write past an anchor that does not match, so a truncated ledger cannot be papered over by the next
event, and `anchor()` will not overwrite one unless forced. A trigger found missing when the ledger is opened is
recreated, and `verify` reports it.

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
import threading
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

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


def anchor_paths_for(path: str | os.PathLike) -> list[Path]:
    """`<ledger>.head` beside the database, and one file per ledger under `$EXO_HOME/keys/heads/`."""
    from .store import exo_home
    path = Path(path)
    name = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:24] + ".json"
    return [path.with_name(path.name + ".head"), exo_home() / "keys" / "heads" / name]


class Ledger:
    """`Ledger(path, signer)`. `signer` may be a `crypto.Signer`, a bare `SigningKey`, or None
    for read-only use."""

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
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.missing_triggers = self._missing_triggers()
        if self.missing_triggers:
            logger.warning("ledger %s: append-only trigger(s) %s were missing; recreated", self.path,
                           ", ".join(self.missing_triggers))
        self.db.executescript(SCHEMA)
        self._lock = threading.Lock()

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
        try:
            a = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
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

    def _write_anchors(self, head: "Head", count: int) -> None:
        """Write the head to every anchor path. The anchor beside the ledger is required: a failure there is raised,
        but only after the copy under keys/ is written. That copy is best effort."""
        doc = json.dumps({"v": 1, "ledger": str(self.path.resolve()), "seq": head.seq, "count": count,
                          "chain": head.chain, "id": head.id}, sort_keys=True).encode("utf-8")
        failed: OSError | None = None
        for i, p in enumerate(self.anchor_paths()):
            try:
                _atomic_write(p, doc)
            except OSError as exc:
                if i == 0:
                    failed = exc
                else:
                    logger.warning("ledger %s: cannot write head anchor %s: %s", self.path, p, exc)
        if failed is not None:
            raise failed

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
        with self._lock:
            try:
                head, replaced = self._anchor_head(force)
            except LedgerError as exc:
                raise LedgerError(f"{exc}. {RECOVER}") from None
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
                for p, a in self.anchors():
                    why, upto = self._anchor_check(a, head)
                    if why:
                        raise LedgerError(f"refusing to append: head anchor {p}: {why}. {RECOVER}")
                    checked.append((a["seq"], upto))
                count = self._count(checked, head)
                beside = self.anchor_paths()[0]
                if (a0 := self._read_anchor(beside)) is not None and a0["seq"] < head.seq:
                    # the last anchor write failed or was cut short: catch up first, so the anchor trails by at most
                    # the one event a crash after COMMIT can leave, and never freezes while appends go on
                    try:
                        self._write_anchors(head, count)
                    except OSError as exc:
                        raise LedgerError(f"refusing to append: head anchor {beside} is at seq {a0['seq']}, the "
                                          f"ledger at seq {head.seq}, and it cannot be written ({exc}). {RECOVER}") from None
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
        seqs = [a["seq"] for _, a in self.anchors() if isinstance(a.get("seq"), int)]
        return {"ok": True, "checked": checked, "head": prev, "anchored": bool(seqs),
                "anchored_seq": min(seqs) if seqs else None}


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


def _atomic_write(path: Path, data: bytes) -> None:
    if not path.parent.exists():  # keys/heads under $EXO_HOME: private like the rest of keys/
        path.parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _bad(ev: dict, reason: str, checked: int) -> dict:
    return {"ok": False, "seq": ev["seq"], "id": ev["id"], "error": reason, "checked": checked}
