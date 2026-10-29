"""sessions.sqlite: sessions, messages, events, summaries and FTS5 search.

One file per Exo home. A session is a thread of an agent (D-028, docs/MEMORY-MODEL.md): a message in an
agent thread also gets ``pos``, its place in the agent's one append-only log, and a hash that chains it to
the entry before, so the log can be verified. Undo hides log entries instead of deleting them. Messages and events are stored as JSON made with the
core codec (``to_dict`` / ``from_dict``). Events get their ``seq`` from the
database, so an event is durable before anyone sees it (``EventBus.emit``
writes, then publishes). Forks copy the parent's messages up to the fork point
and record ``parent`` and ``fork_seq``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from exo.core import trace
from exo.core.hooks import maybe_await
from exo.core.private import private_file
from exo.core.types import Message, from_dict, to_dict
from exo.protocol.schema import EVENTS, Event, SessionInfo, new_id, now_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', seat TEXT, model TEXT, cwd TEXT,
    parent TEXT, fork_seq INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, session TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,
    turn_id TEXT, created TEXT NOT NULL, data TEXT NOT NULL, UNIQUE (session, seq)
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(text, msg_id UNINDEXED, session UNINDEXED);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, session TEXT, type TEXT NOT NULL,
    ts TEXT NOT NULL, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_session ON events (session, seq);
CREATE INDEX IF NOT EXISTS events_type ON events (type, seq);
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY, signature TEXT NOT NULL UNIQUE, source TEXT NOT NULL, severity TEXT NOT NULL,
    title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', session TEXT, turn TEXT, model TEXT, version TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open', fixed_in TEXT, fixed_at TEXT, fixed_turns INTEGER,
    sessions TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS finding_views (
    viewer TEXT PRIMARY KEY, snapshot TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS summaries (
    id TEXT PRIMARY KEY, session TEXT NOT NULL, level INTEGER NOT NULL, idx INTEGER NOT NULL,
    start_seq INTEGER NOT NULL, end_seq INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL, lane TEXT NOT NULL DEFAULT 'live', UNIQUE (session, level, idx)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, agent TEXT NOT NULL, session TEXT, command TEXT NOT NULL, cwd TEXT, state TEXT NOT NULL,
    exit_code INTEGER, pid INTEGER, started REAL NOT NULL, ended REAL, log TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS captures (
    agent TEXT NOT NULL, pos INTEGER NOT NULL, ts TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY (agent, pos)
);
"""

# Columns added by D-028; older files get them on open.
UPGRADES = (("sessions", "agent", "TEXT"), ("sessions", "kind", "TEXT NOT NULL DEFAULT ''"),
            ("messages", "agent", "TEXT"), ("messages", "pos", "INTEGER"), ("messages", "hash", "TEXT"),
            ("messages", "hidden", "INTEGER NOT NULL DEFAULT 0"),
            ("messages", "copy_pos", "INTEGER"),  # derived original position of a fork copy (#450)
            ("summaries", "principal", "INTEGER NOT NULL DEFAULT 0"),  # D-032: principal messages in a leaf
            ("summaries", "captured", "INTEGER NOT NULL DEFAULT 0"),   # ... and how many the capture hook took
            ("summaries", "attempts", "INTEGER NOT NULL DEFAULT 0"),   # failed summary tries so far
            ("findings", "quiet", "TEXT"))  # D-025: {"turns", "days"} quiet since the fix (fixed_turns is unused)
LOGGED_KINDS = ("thread", "scheduled", "private")  # thread kinds whose messages enter the agent log
SCHEMA_VERSION = 2


def canonical_cwd(path: str | None) -> str | None:
    """Real path with each component spelled as it is on disk (fixes ``~/work`` vs ``~/Work``)."""
    if not path:
        return path
    real = os.path.realpath(path)
    try:
        parts = Path(real).parts
        cur = parts[0]
        for part in parts[1:]:
            names = os.listdir(cur)
            match = part if part in names else next((n for n in names if n.lower() == part.lower()), part)
            cur = os.path.join(cur, match)
        return cur
    except OSError:
        return real


def _chain(prev: str, agent: str, pos: int, session: str, data: str) -> str:
    return hashlib.sha256(f"{prev}\n{agent}\n{pos}\n{session}\n{data}".encode()).hexdigest()


class LogEntry:
    """One entry of an agent log: position, thread, thread kind, time, message, hidden (undone)."""

    __slots__ = ("pos", "session", "kind", "created", "message", "hidden")

    def __init__(self, pos: int, session: str, kind: str, created: str, message: Message, hidden: bool):
        self.pos, self.session, self.kind, self.created = pos, session, kind, created
        self.message, self.hidden = message, hidden


def _fts_text(msg: Message) -> str:
    parts = [msg.text()]
    parts += [f"{c.name} {json.dumps(c.arguments, ensure_ascii=False)}" for c in msg.tool_calls]
    parts += [r.text() for r in msg.tool_results]
    return "\n".join(p for p in parts if p)


class SessionLocked(ValueError):
    """A turn was sent to a finished (locked) session."""


class SessionStore:
    """Thread-safe wrapper over one sessions.sqlite file."""

    def __init__(self, path: str | Path = ":memory:", migrate: bool = True):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = self._open()
        with self._lock:
            deadline = time.monotonic() + 2  # a new file's switch to WAL can be busy without waiting (#246)
            while True:
                try:
                    self._db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            # One write transaction: two processes opening the file at once cannot both add a column (#246).
            # BEGIN goes inside the script because executescript commits a pending transaction first.
            try:
                self._db.executescript("BEGIN IMMEDIATE;" + SCHEMA)
                for table, col, decl in UPGRADES:
                    have = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
                    if col not in have:
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                        if table == "messages" and col == "copy_pos":
                            self._db.execute("UPDATE messages SET copy_pos=json_extract(data, '$.meta.pos')"
                                             " WHERE pos IS NULL AND CASE WHEN json_valid(data) THEN"
                                             " json_extract(data, '$.meta.local')='fork' ELSE 0 END")
                self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS messages_log ON messages (agent, pos)"
                                 " WHERE pos IS NOT NULL")
                self._db.execute("CREATE INDEX IF NOT EXISTS messages_fork_copy ON messages(session,seq,copy_pos)"
                                 " WHERE pos IS NULL AND copy_pos IS NOT NULL")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")
        if migrate:
            self.migrate_agents()

    def _open(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            private_file(self.path)  # owner-only, also when a late reader reopens a file that was removed
        db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")  # per connection; WAL mode stays set in the file itself
        return db

    @property
    def _db(self) -> sqlite3.Connection:
        """The connection, reopened if it is used after close() (a late reader at shutdown, a test that inspects)."""
        db = self._conn
        if db is None:
            with self._lock:
                if self._conn is None:
                    if self.path == ":memory:":  # a new connection would be an empty database
                        raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
                    self._conn = self._open()
                db = self._conn
        return db

    def close(self) -> None:
        """Release the connection and its file handles.

        Not final: any later use opens the file again, so a reader or writer that runs after
        ``CoreRuntime.close()`` (a turn finishing, a test that inspects) keeps working, and holds a
        handle until the next close. Only a ``:memory:`` store stays closed.
        """
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _tx(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                out = fn(self._db)
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")
            return out

    def _read(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """One snapshot for several SELECTs. A deferred BEGIN takes no write lock, so under WAL it neither waits for
        nor blocks a writer on another connection to the file (another process, or another store), as ``_tx`` does.
        Calls on this store still take turns under its one lock and connection, and it cannot nest in a ``_tx``."""
        with self._lock:
            self._db.execute("BEGIN")
            try:
                return fn(self._db)
            finally:
                self._db.execute("ROLLBACK")

    # ---------------------------------------------------------------- sessions

    def create_session(self, title: str = "", seat: str | None = None, model: str | None = None,
                       cwd: str | None = None, parent: str | None = None, fork_seq: int | None = None,
                       id: str | None = None, agent: str | None = None, kind: str = "") -> SessionInfo:
        """``agent`` makes this a thread of that agent's one memory; ``kind`` is thread (default for an agent),
        private, scheduled or subagent."""
        sid, ts = id or new_id("s"), now_ts()
        cwd = canonical_cwd(cwd)
        kind = (kind or "thread") if agent else ""
        if kind and kind not in (*LOGGED_KINDS, "subagent"):
            raise ValueError(f"unknown thread kind {kind!r}")
        self._q("INSERT INTO sessions (id, title, seat, model, cwd, parent, fork_seq, created, updated, agent, kind)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (sid, title, seat, model, cwd, parent, fork_seq, ts, ts, agent, kind))
        return self.get_session(sid)  # type: ignore[return-value]

    def _info(self, row: sqlite3.Row, count: int | None = None) -> SessionInfo:
        if count is None:
            (count,) = self._q("SELECT COUNT(*) FROM messages WHERE session=? AND hidden=0", (row["id"],))[0]
        return SessionInfo(id=row["id"], title=row["title"], seat=row["seat"], model=row["model"],
                           cwd=row["cwd"], parent=row["parent"], created=row["created"],
                           updated=row["updated"], message_count=count, agent=row["agent"], kind=row["kind"] or "")

    def get_session(self, sid: str) -> SessionInfo | None:
        rows = self._q("SELECT * FROM sessions WHERE id=?", (sid,))
        return self._info(rows[0]) if rows else None

    def list_sessions(self, limit: int = 50, seat: str | None = None, agent: str | None = None) -> list[SessionInfo]:
        # One query: the limited selection is a subquery, so each correlated count runs only for a
        # row the limit keeps. A count in the outer select runs before the sort, for every
        # provisional candidate, however small the limit (#365).
        cols = {k: v for k, v in (("seat", seat), ("agent", agent)) if v is not None}
        page = ("SELECT * FROM sessions" + (" WHERE " + " AND ".join(f"{k}=?" for k in cols) if cols else "")
                + " ORDER BY updated DESC, id LIMIT ?")
        rows = self._q("SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session=s.id AND m.hidden=0)"
                       f" AS message_count FROM ({page}) s ORDER BY s.updated DESC, s.id",
                       (*cols.values(), limit))
        return [self._info(r, r["message_count"]) for r in rows]

    def thread(self, sid: str) -> tuple[str | None, str]:
        """(agent, kind) of a session; (None, "") outside any agent."""
        rows = self._q("SELECT agent, kind FROM sessions WHERE id=?", (sid,))
        return (rows[0]["agent"], rows[0]["kind"] or "") if rows else (None, "")

    def private(self, sid: str | None) -> bool:
        """A private thread, or a session under one at any depth (its delegate child): not remembered (D-028)."""
        return bool(sid) and bool(self._q(
            "WITH RECURSIVE up(parent, kind) AS (SELECT parent, kind FROM sessions WHERE id=? UNION SELECT s.parent,"
            " s.kind FROM sessions s JOIN up ON s.id=up.parent) SELECT 1 FROM up WHERE kind='private'", (sid,)))

    def update_session(self, sid: str, **fields: Any) -> SessionInfo:
        allowed = {"title", "seat", "model", "cwd"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update {sorted(bad)}")
        sets = ", ".join(f"{k}=?" for k in fields) + (", " if fields else "") + "updated=?"
        self._q(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), now_ts(), sid))
        info = self.get_session(sid)
        if info is None:
            raise KeyError(sid)
        return info

    def get_meta(self, sid: str) -> dict[str, Any]:
        rows = self._q("SELECT meta FROM sessions WHERE id=?", (sid,))
        return json.loads(rows[0]["meta"]) if rows else {}

    def set_meta(self, sid: str, meta: dict[str, Any]) -> None:
        self._q("UPDATE sessions SET meta=? WHERE id=?", (json.dumps(meta), sid))

    def lock(self, sid: str, reason: str = "done") -> None:
        """Close a session to new turns (a finished child). Reading and forking stay allowed."""
        self.set_meta(sid, {**self.get_meta(sid), "locked": reason or "done"})

    def status(self, sid: str) -> str:
        """``locked:<reason>`` for a finished child session, else ``open``."""
        reason = self.get_meta(sid).get("locked")
        return f"locked:{reason}" if reason else "open"

    def assert_open(self, sid: str) -> None:
        reason = self.get_meta(sid).get("locked")
        if reason:
            raise SessionLocked(f"session {sid} is finished ({reason}) and takes no new turns; fork it to continue")

    # ---------------------------------------------------------------- messages

    def last_seq(self, sid: str) -> int:  # counts hidden rows too, so a seq is never reused
        return self._q("SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session=?", (sid,))[0][0]

    def append_message(self, sid: str, msg: Message, turn_id: str | None = None) -> Message:
        """Store ``msg`` (assigning ``id`` and ``meta['seq']``) and return it."""
        msg.id = msg.id or new_id("m")
        if turn_id:
            msg.meta.setdefault("turn_id", turn_id)

        def write(db: sqlite3.Connection) -> None:
            (seq,) = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session=?",
                                (sid,)).fetchone()
            msg.meta["seq"] = seq
            row = db.execute("SELECT agent, kind FROM sessions WHERE id=?", (sid,)).fetchone()
            agent = row["agent"] if row and row["kind"] in LOGGED_KINDS and not msg.meta.get("local") else None
            pos = digest = None
            if agent:  # the next place in the agent's one log, given inside this write transaction
                last = db.execute("SELECT pos, hash FROM messages WHERE agent=? AND pos IS NOT NULL"
                                  " ORDER BY pos DESC LIMIT 1", (agent,)).fetchone()
                pos = (last["pos"] if last else 0) + 1
                msg.meta["pos"] = pos
            data = json.dumps(to_dict(msg), ensure_ascii=False)
            if agent:
                digest = _chain(last["hash"] if last else "", agent, pos, sid, data)
            db.execute("INSERT INTO messages (id, session, seq, role, turn_id, created, data, agent, pos, hash, copy_pos)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,json_extract(?, '$.meta.pos'))",
                       (msg.id, sid, seq, msg.role, turn_id, now_ts(), data, agent, pos, digest,
                        data if msg.meta.get("local") == "fork" else "null"))
            text = _fts_text(msg)
            if text:
                db.execute("INSERT INTO messages_fts (text, msg_id, session) VALUES (?,?,?)",
                           (text, msg.id, sid))
            db.execute("UPDATE sessions SET updated=? WHERE id=?", (now_ts(), sid))

        self._tx(write)
        return msg

    def messages(self, sid: str, since: int = 0, upto: int | None = None) -> list[Message]:
        """Messages with ``since < seq <= upto``, in order."""
        rows = self._q("SELECT data FROM messages WHERE session=? AND seq>? AND seq<=? AND hidden=0 ORDER BY seq",
                       (sid, since, upto if upto is not None else 2**62))
        return [from_dict(Message, json.loads(r["data"]), strict=False) for r in rows]

    def message_seq(self, sid: str, message_id: str) -> int | None:
        rows = self._q("SELECT seq FROM messages WHERE session=? AND id=?", (sid, message_id))
        return rows[0]["seq"] if rows else None

    def truncate(self, sid: str, after_seq: int) -> int:
        """Remove messages with seq > after_seq and summaries holding them. Agent log entries are hidden,
        not deleted, so the log stays append-only. Context notes stay: they hold other threads' news."""

        def drop(db: sqlite3.Connection) -> int:
            keep = " AND json_extract(data, '$.meta.note') IS NULL"
            ids = [r[0] for r in db.execute("SELECT id FROM messages WHERE session=? AND seq>? AND hidden=0" + keep,
                                             (sid, after_seq))]
            logged = db.execute("SELECT agent FROM messages WHERE session=? AND seq>? AND hidden=0 AND pos IS NOT NULL",
                                (sid, after_seq)).fetchone()
            if logged:  # agent tree nodes (ranges are log positions) holding an undone entry, and the merges above them
                db.execute("DELETE FROM summaries WHERE session=? AND EXISTS (SELECT 1 FROM messages m WHERE m.session=?"
                           " AND m.seq>? AND m.hidden=0 AND m.pos BETWEEN summaries.start_seq AND summaries.end_seq)",
                           (f"agent:{logged['agent']}", sid, after_seq))
            db.executemany("DELETE FROM messages_fts WHERE msg_id=?", [(i,) for i in ids])
            db.execute("UPDATE messages SET hidden=1 WHERE session=? AND seq>? AND pos IS NOT NULL", (sid, after_seq))
            db.execute("DELETE FROM messages WHERE session=? AND seq>? AND pos IS NULL" + keep, (sid, after_seq))
            db.execute("DELETE FROM summaries WHERE session=? AND end_seq>?", (sid, after_seq))
            return len(ids)

        return self._tx(drop)

    def undo(self, sid: str, turns: int = 1) -> int:
        """Remove the last ``turns`` user turns (from each user message onward)."""
        rows = self._q("SELECT seq FROM messages WHERE session=? AND role='user' AND hidden=0"
                       " AND json_extract(data, '$.meta.note') IS NULL ORDER BY seq DESC"
                       " LIMIT ?", (sid, turns))
        return self.truncate(sid, rows[-1]["seq"] - 1) if rows else 0

    def fork(self, sid: str, at_message: str | None = None, title: str = "") -> SessionInfo:
        """New session holding the parent's messages up to and including ``at_message``."""
        parent = self.get_session(sid)
        if parent is None:
            raise KeyError(sid)
        upto = self.last_seq(sid) if at_message is None else self.message_seq(sid, at_message)
        if upto is None:
            raise KeyError(at_message)
        child = self.create_session(title=title or parent.title, seat=parent.seat, model=parent.model,
                                    cwd=parent.cwd, parent=sid, fork_seq=upto, agent=parent.agent,
                                    kind=parent.kind if parent.kind != "scheduled" else "thread")
        for m in self.messages(sid, upto=upto):
            m.meta["forked_from"] = m.id
            m.meta["local"] = "fork"  # the original is already in the agent log
            m.id = None
            self.append_message(child.id, m)
        # the copies are renumbered 1..n: a range with an undone (hidden or deleted) row up to its end would shift
        for row in self._q("SELECT * FROM summaries WHERE session=? AND end_seq<=? AND status='done' AND end_seq=("
                           "SELECT COUNT(*) FROM messages m WHERE m.session=? AND hidden=0 AND seq<=summaries.end_seq)",
                           (sid, upto, sid)):
            self._q("INSERT INTO summaries (id, session, level, idx, start_seq, end_seq, text, status,"
                    " lane) VALUES (?,?,?,?,?,?,?,?,?)",
                    (new_id("sum"), child.id, row["level"], row["idx"], row["start_seq"],
                     row["end_seq"], row["text"], row["status"], row["lane"]))
        return self.get_session(child.id)  # type: ignore[return-value]

    def search(self, query: str, limit: int = 20, session: str | None = None,
               agent: str | None = None, *, owner: str | None = None) -> list[dict[str, Any]]:
        """FTS5 search over message text. Plain words are quoted, so any input is safe. ``agent`` searches that
        agent's log (a private thread and everything under it left out, D-028 #632) and adds each hit's ``pos``."""
        terms = " ".join('"' + w.replace('"', '""') + '"' for w in query.split())
        if not terms:
            return []
        if agent:
            # The private boundary is hereditary: hid holds a private thread and its descendants at any depth, so
            # the exclusion runs inside the one query (a private hit cannot eat the limit, #632). UNION, not
            # UNION ALL, ends a parent loop.
            rows = self._q("WITH RECURSIVE hid(id) AS (SELECT id FROM sessions WHERE kind='private' UNION SELECT"
                           " c.id FROM sessions c JOIN hid ON c.parent=hid.id) SELECT f.msg_id, f.session,"
                           " snippet(messages_fts, 0, '[', ']', '...', 12) AS snip, m.pos, m.created FROM"
                           " messages_fts f JOIN messages m ON m.id=f.msg_id JOIN sessions s ON s.id=m.session"
                           " WHERE messages_fts MATCH ? AND m.agent=? AND m.pos IS NOT NULL AND m.hidden=0 AND"
                           " s.id NOT IN hid AND (? IS NULL OR s.agent=?) ORDER BY rank LIMIT ?",
                           (terms, agent, owner, owner, limit))
            return [{"message_id": r["msg_id"], "session": r["session"], "snippet": r["snip"], "pos": r["pos"],
                     "created": r["created"]} for r in rows]
        sql = ("SELECT msg_id, session, snippet(messages_fts, 0, '[', ']', '...', 12) AS snip"
               " FROM messages_fts WHERE messages_fts MATCH ?")
        args: tuple = (terms,)
        if session:
            sql, args = sql + " AND session=?", (terms, session)
        if owner is not None:
            sql += " AND session IN (SELECT id FROM sessions WHERE agent=?)"
            args = (*args, owner)
        rows = self._q(sql + " ORDER BY rank LIMIT ?", (*args, limit))
        return [{"message_id": r["msg_id"], "session": r["session"], "snippet": r["snip"]} for r in rows]

    # ---------------------------------------------------------------- agent log (D-028)

    def last_pos(self, agent: str) -> int:
        return self._q("SELECT COALESCE(MAX(pos), 0) FROM messages WHERE agent=?", (agent,))[0][0]

    def log(self, agent: str, since: int = 0, upto: int | None = None) -> list[LogEntry]:
        """Agent log entries with ``since < pos <= upto``, in order, hidden ones included (flagged)."""
        rows = self._q("SELECT m.pos, m.session, m.created, m.data, m.hidden, s.kind FROM messages m LEFT JOIN"
                       " sessions s ON s.id=m.session WHERE m.agent=? AND m.pos>? AND m.pos<=? ORDER BY m.pos",
                       (agent, since, upto if upto is not None else 2**62))
        return [LogEntry(r["pos"], r["session"], r["kind"] or "", r["created"],
                         from_dict(Message, json.loads(r["data"]), strict=False), bool(r["hidden"])) for r in rows]

    def last_logged_seq(self, sid: str, upto_pos: int) -> int:
        """The seq of this thread's last log entry with pos <= upto_pos (0 if none). A fork copy counts at its
        original's pos (kept in its meta) while the tree can hold that original (visible, not private)."""
        (seq,) = self._q("SELECT COALESCE(MAX(c.seq), 0) FROM messages c WHERE c.session=? AND COALESCE(c.pos, (SELECT"
                         " o.pos FROM messages o JOIN sessions s ON s.id=o.session WHERE c.copy_pos IS NOT NULL"
                         " AND o.agent=(SELECT agent FROM sessions WHERE id=c.session) AND o.pos=c.copy_pos"
                         " AND o.hidden=0 AND s.kind!='private'))<=?", (sid, upto_pos))[0]
        return seq

    def orphaned_copies(self, sid: str, upto_seq: int) -> list[Message]:
        return [from_dict(Message, json.loads(r["data"]), strict=False) for r in self._q(
            "SELECT c.data FROM sessions f JOIN messages c ON c.session=f.id WHERE f.id=? AND c.seq<=MIN(?, COALESCE(f.fork_seq, 0))"
            " AND c.pos IS NULL AND c.copy_pos IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM messages o JOIN sessions s ON s.id=o.session WHERE o.agent=f.agent AND o.hidden=0"
            " AND o.pos=c.copy_pos AND s.kind!='private') ORDER BY c.seq", (sid, upto_seq))]

    def agents(self) -> list[str]:
        return [r[0] for r in self._q("SELECT DISTINCT agent FROM messages WHERE pos IS NOT NULL ORDER BY agent")]

    def verify_log(self, agent: str) -> dict[str, Any]:
        """Check an agent log end to end: positions 1..n with no gap, and every hash chains to the one before."""
        prev, n = "", 0
        for r in self._q("SELECT pos, session, data, hash FROM messages WHERE agent=? AND pos IS NOT NULL"
                         " ORDER BY pos", (agent,)):
            n += 1
            if r["pos"] != n:
                return {"ok": False, "entries": n - 1, "error": f"gap: expected pos {n}, found {r['pos']}"}
            if _chain(prev, agent, n, r["session"], r["data"]) != r["hash"]:
                return {"ok": False, "entries": n - 1, "error": f"hash mismatch at pos {n}"}
            prev = r["hash"]
        return {"ok": True, "entries": n, "error": ""}

    def migrate_agents(self) -> int:
        """Once per file (PRAGMA user_version < 2): every session becomes a thread of its seat's agent
        (``default`` without a seat) and its messages enter that agent's log in time order with
        ``origin=import``. Delegate children become ``subagent`` (not logged), ``scheduled: ...`` runs become
        ``scheduled``, fork copies stay out of the log. Nothing is deleted. Returns entries imported."""

        def run(db: sqlite3.Connection) -> int:
            (version,) = db.execute("PRAGMA user_version").fetchone()
            if version >= SCHEMA_VERSION:
                return 0
            has_tasks = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()
            children = {r[0] for r in db.execute("SELECT child FROM tasks WHERE child IS NOT NULL")} if has_tasks else set()
            for r in db.execute("SELECT id, seat, title FROM sessions WHERE agent IS NULL").fetchall():
                kind = ("subagent" if r["id"] in children else
                        "scheduled" if (r["title"] or "").startswith("scheduled: ") else "thread")
                db.execute("UPDATE sessions SET agent=?, kind=? WHERE id=?", (r["seat"] or "default", kind, r["id"]))
            rows = db.execute("SELECT m.id, m.session, m.data, s.agent FROM messages m JOIN sessions s ON"
                              " s.id=m.session WHERE m.pos IS NULL AND s.kind IN ('thread','scheduled','private')"
                              " ORDER BY m.created, s.created, m.seq").fetchall()
            heads: dict[str, tuple[int, str]] = {}
            n = 0
            for r in rows:
                d = json.loads(r["data"])
                meta = d.setdefault("meta", {})
                if meta.get("forked_from") or meta.get("local"):
                    continue
                agent = r["agent"]
                if agent not in heads:
                    last = db.execute("SELECT pos, hash FROM messages WHERE agent=? AND pos IS NOT NULL"
                                      " ORDER BY pos DESC LIMIT 1", (agent,)).fetchone()
                    heads[agent] = (last["pos"], last["hash"]) if last else (0, "")
                pos, prev = heads[agent][0] + 1, heads[agent][1]
                meta.setdefault("origin", "import")
                meta["pos"] = pos
                data = json.dumps(d, ensure_ascii=False)
                digest = _chain(prev, agent, pos, r["session"], data)
                db.execute("UPDATE messages SET data=?, agent=?, pos=?, hash=? WHERE id=?",
                           (data, agent, pos, digest, r["id"]))
                heads[agent] = (pos, digest)
                n += 1
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return n

        return self._tx(run)

    # ---------------------------------------------------------------- summaries (tree nodes)

    def save_summary(self, node: Any) -> None:
        self._q("INSERT INTO summaries (id, session, level, idx, start_seq, end_seq, text, status, lane,"
                " principal, captured, attempts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
                " text=excluded.text, status=excluded.status, principal=excluded.principal,"
                " captured=excluded.captured, attempts=excluded.attempts",
                (node.id, node.session, node.level, node.index, node.start, node.end, node.text,
                 node.status, node.lane, node.principal, node.captured, node.attempts))

    def mark_captured(self, agent: str, positions: list[int], source: str) -> None:
        """Record that the principal store took these log positions (D-032). The log itself is never touched."""
        ts = now_ts()
        for pos in positions:
            self._q("INSERT OR IGNORE INTO captures (agent, pos, ts, source) VALUES (?,?,?,?)", (agent, pos, ts, source))

    def captured_positions(self, agent: str) -> set[int]:
        return {r[0] for r in self._q("SELECT pos FROM captures WHERE agent=?", (agent,))}

    def has_summary(self, node_id: str) -> bool:
        return bool(self._q("SELECT 1 FROM summaries WHERE id=?", (node_id,)))

    def summaries(self, sid: str) -> list[Any]:
        from exo.core.context import SummaryNode

        rows = self._q("SELECT * FROM summaries WHERE session=? ORDER BY level, idx", (sid,))
        return [SummaryNode(id=r["id"], session=r["session"], level=r["level"], index=r["idx"],
                            start=r["start_seq"], end=r["end_seq"], text=r["text"], status=r["status"],
                            lane=r["lane"], principal=r["principal"], captured=r["captured"],
                            attempts=r["attempts"]) for r in rows]

    # ---------------------------------------------------------------- events

    def append_event(self, body: Any, session: str | None = None) -> Event:
        """Write an event and return it with its database-assigned ``seq``."""
        ev = Event.of(body, seq=0, session=session)

        def write(db: sqlite3.Connection) -> int:
            cur = db.execute("INSERT INTO events (id, session, type, ts, body) VALUES (?,?,?,?,?)",
                             (ev.id, session, ev.type, ev.ts, json.dumps(to_dict(body), ensure_ascii=False)))
            return cur.lastrowid

        ev.seq = self._tx(write)
        return ev

    def write_event(self, ev: Event) -> None:
        """Store an event the server Hub numbered. Seats of one home each run a Hub on this file, and ``append_event``
        lets SQLite pick the seq (#237), so the seq is raised past the table's highest inside the write transaction;
        ``Hub.publish`` then takes ``ev.seq``."""
        def write(db: sqlite3.Connection) -> None:
            ev.seq = max(ev.seq, db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0] + 1)
            db.execute("INSERT INTO events (seq, id, session, type, ts, body) VALUES (?,?,?,?,?,?)",
                       (ev.seq, ev.id, ev.session, ev.type, ev.ts, json.dumps(to_dict(ev.body), ensure_ascii=False)))

        self._tx(write)

    def events_since(self, since: int = 0, session: str | None = None, limit: int = 10000,
                     with_global: bool = False, skip: tuple[str, ...] = ()) -> list[Event]:
        """Events with seq > since; ``with_global`` also keeps session-less events; ``skip`` drops event types."""
        drop = f" AND type NOT IN ({','.join('?' * len(skip))})" if skip else ""
        if session is None:
            rows = self._q(f"SELECT * FROM events WHERE seq>?{drop} ORDER BY seq LIMIT ?", (since, *skip, limit))
        else:
            extra = " OR session IS NULL" if with_global else ""
            rows = self._q(f"SELECT * FROM events WHERE seq>? AND (session=?{extra}){drop} ORDER BY seq LIMIT ?",
                           (since, session, *skip, limit))
        out = []
        for r in rows:
            cls = EVENTS.get(r["type"])
            raw = json.loads(r["body"])
            body = from_dict(cls, raw, strict=False) if cls else raw
            out.append(Event(seq=r["seq"], type=r["type"], body=body, session=r["session"],
                             ts=r["ts"], id=r["id"]))
        return out

    def last_event_seq(self) -> int:
        return self._q("SELECT COALESCE(MAX(seq), 0) FROM events")[0][0]


Subscriber = Callable[[Event], Any]


class EventBus:
    """Write-then-publish. Subscribers may be sync or async callables."""

    def __init__(self, store: SessionStore):
        self.store = store
        self._subs: list[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        self._subs.append(fn)
        return lambda: self._subs.remove(fn) if fn in self._subs else None

    async def emit(self, body: Any, session: str | None = None) -> Event:
        ev = self.store.append_event(body, session)  # durable first
        for fn in list(self._subs):
            try:
                await maybe_await(fn(ev))
            except Exception as exc:  # a broken client must not break the turn, but it is recorded
                trace.report(f"event subscriber on {ev.type}", exc, session)
        return ev
