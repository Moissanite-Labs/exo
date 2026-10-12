"""sessions.sqlite: sessions, messages, events, summaries and FTS5 search.

One file per Exo home. Messages and events are stored as JSON made with the
core codec (``to_dict`` / ``from_dict``). Events get their ``seq`` from the
database, so an event is durable before anyone sees it (``EventBus.emit``
writes, then publishes). Forks copy the parent's messages up to the fork point
and record ``parent`` and ``fork_seq`` for lineage.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

from exo.core.types import Message, from_dict, to_dict
from exo.protocol.schema import EVENTS, Event, SessionInfo, now_ts

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
CREATE TABLE IF NOT EXISTS summaries (
    id TEXT PRIMARY KEY, session TEXT NOT NULL, level INTEGER NOT NULL, idx INTEGER NOT NULL,
    start_seq INTEGER NOT NULL, end_seq INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL, lane TEXT NOT NULL DEFAULT 'live', UNIQUE (session, level, idx)
);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


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


def _fts_text(msg: Message) -> str:
    parts = [msg.text()]
    parts += [f"{c.name} {json.dumps(c.arguments, ensure_ascii=False)}" for c in msg.tool_calls]
    parts += [r.text() for r in msg.tool_results]
    return "\n".join(p for p in parts if p)


class SessionLocked(ValueError):
    """A turn was sent to a finished (locked) session."""


class SessionStore:
    """Thread-safe wrapper over one sessions.sqlite file."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

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

    # ---------------------------------------------------------------- sessions

    def create_session(self, title: str = "", seat: str | None = None, model: str | None = None,
                       cwd: str | None = None, parent: str | None = None, fork_seq: int | None = None,
                       id: str | None = None) -> SessionInfo:
        sid, ts = id or new_id("s"), now_ts()
        cwd = canonical_cwd(cwd)
        self._q("INSERT INTO sessions (id, title, seat, model, cwd, parent, fork_seq, created, updated)"
                " VALUES (?,?,?,?,?,?,?,?,?)", (sid, title, seat, model, cwd, parent, fork_seq, ts, ts))
        return self.get_session(sid)  # type: ignore[return-value]

    def _info(self, row: sqlite3.Row) -> SessionInfo:
        (count,) = self._q("SELECT COUNT(*) FROM messages WHERE session=?", (row["id"],))[0]
        return SessionInfo(id=row["id"], title=row["title"], seat=row["seat"], model=row["model"],
                           cwd=row["cwd"], parent=row["parent"], created=row["created"],
                           updated=row["updated"], message_count=count)

    def get_session(self, sid: str) -> SessionInfo | None:
        rows = self._q("SELECT * FROM sessions WHERE id=?", (sid,))
        return self._info(rows[0]) if rows else None

    def list_sessions(self, limit: int = 50, seat: str | None = None) -> list[SessionInfo]:
        if seat is None:
            rows = self._q("SELECT * FROM sessions ORDER BY updated DESC, id LIMIT ?", (limit,))
        else:
            rows = self._q("SELECT * FROM sessions WHERE seat=? ORDER BY updated DESC, id LIMIT ?",
                           (seat, limit))
        return [self._info(r) for r in rows]

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

    def fork_point(self, sid: str) -> tuple[str | None, int | None]:
        rows = self._q("SELECT parent, fork_seq FROM sessions WHERE id=?", (sid,))
        return (rows[0]["parent"], rows[0]["fork_seq"]) if rows else (None, None)

    def lineage(self, sid: str) -> list[str]:
        """This session and its ancestors, nearest first."""
        out, cur = [], sid
        while cur and cur not in out:
            out.append(cur)
            cur = self.fork_point(cur)[0]
        return out

    # ---------------------------------------------------------------- messages

    def last_seq(self, sid: str) -> int:
        (seq,) = self._q("SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session=?", (sid,))[0]
        return seq

    def append_message(self, sid: str, msg: Message, turn_id: str | None = None) -> Message:
        """Store ``msg`` (assigning ``id`` and ``meta['seq']``) and return it."""
        msg.id = msg.id or new_id("m")
        if turn_id:
            msg.meta.setdefault("turn_id", turn_id)

        def write(db: sqlite3.Connection) -> None:
            (seq,) = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session=?",
                                (sid,)).fetchone()
            msg.meta["seq"] = seq
            db.execute("INSERT INTO messages (id, session, seq, role, turn_id, created, data)"
                       " VALUES (?,?,?,?,?,?,?)", (msg.id, sid, seq, msg.role, turn_id, now_ts(),
                                                   json.dumps(to_dict(msg), ensure_ascii=False)))
            text = _fts_text(msg)
            if text:
                db.execute("INSERT INTO messages_fts (text, msg_id, session) VALUES (?,?,?)",
                           (text, msg.id, sid))
            db.execute("UPDATE sessions SET updated=? WHERE id=?", (now_ts(), sid))

        self._tx(write)
        return msg

    def messages(self, sid: str, since: int = 0, upto: int | None = None) -> list[Message]:
        """Messages with ``since < seq <= upto``, in order."""
        rows = self._q("SELECT data FROM messages WHERE session=? AND seq>? AND seq<=? ORDER BY seq",
                       (sid, since, upto if upto is not None else 2**62))
        return [from_dict(Message, json.loads(r["data"])) for r in rows]

    def message_seq(self, sid: str, message_id: str) -> int | None:
        rows = self._q("SELECT seq FROM messages WHERE session=? AND id=?", (sid, message_id))
        return rows[0]["seq"] if rows else None

    def truncate(self, sid: str, after_seq: int) -> int:
        """Delete messages with seq > after_seq and summaries touching them."""

        def drop(db: sqlite3.Connection) -> int:
            ids = [r[0] for r in db.execute("SELECT id FROM messages WHERE session=? AND seq>?",
                                             (sid, after_seq))]
            db.executemany("DELETE FROM messages_fts WHERE msg_id=?", [(i,) for i in ids])
            db.execute("DELETE FROM messages WHERE session=? AND seq>?", (sid, after_seq))
            db.execute("DELETE FROM summaries WHERE session=? AND end_seq>?", (sid, after_seq))
            return len(ids)

        return self._tx(drop)

    def undo(self, sid: str, turns: int = 1) -> int:
        """Remove the last ``turns`` user turns (from each user message onward)."""
        rows = self._q("SELECT seq FROM messages WHERE session=? AND role='user' ORDER BY seq DESC"
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
                                    cwd=parent.cwd, parent=sid, fork_seq=upto)
        for m in self.messages(sid, upto=upto):
            m.meta["forked_from"] = m.id
            m.id = None
            self.append_message(child.id, m)
        for row in self._q("SELECT * FROM summaries WHERE session=? AND end_seq<=? AND status='done'",
                           (sid, upto)):
            self._q("INSERT INTO summaries (id, session, level, idx, start_seq, end_seq, text, status,"
                    " lane) VALUES (?,?,?,?,?,?,?,?,?)",
                    (new_id("sum"), child.id, row["level"], row["idx"], row["start_seq"],
                     row["end_seq"], row["text"], row["status"], row["lane"]))
        return self.get_session(child.id)  # type: ignore[return-value]

    def search(self, query: str, limit: int = 20, session: str | None = None) -> list[dict[str, Any]]:
        """FTS5 search over message text. Plain words are quoted, so any input is safe."""
        terms = " ".join('"' + w.replace('"', '""') + '"' for w in query.split())
        if not terms:
            return []
        sql = ("SELECT msg_id, session, snippet(messages_fts, 0, '[', ']', '...', 12) AS snip"
               " FROM messages_fts WHERE messages_fts MATCH ?")
        args: tuple = (terms,)
        if session:
            sql, args = sql + " AND session=?", (terms, session)
        rows = self._q(sql + " ORDER BY rank LIMIT ?", (*args, limit))
        return [{"message_id": r["msg_id"], "session": r["session"], "snippet": r["snip"]} for r in rows]

    # ---------------------------------------------------------------- summaries (tree nodes)

    def save_summary(self, node: Any) -> None:
        self._q("INSERT INTO summaries (id, session, level, idx, start_seq, end_seq, text, status, lane)"
                " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text,"
                " status=excluded.status",
                (node.id, node.session, node.level, node.index, node.start, node.end, node.text,
                 node.status, node.lane))

    def summaries(self, sid: str) -> list[Any]:
        from exo.core.context import SummaryNode

        rows = self._q("SELECT * FROM summaries WHERE session=? ORDER BY level, idx", (sid,))
        return [SummaryNode(id=r["id"], session=r["session"], level=r["level"], index=r["idx"],
                            start=r["start_seq"], end=r["end_seq"], text=r["text"], status=r["status"],
                            lane=r["lane"]) for r in rows]

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
        """Store an event that already has its ``seq`` (the server Hub numbers events)."""
        self._tx(lambda db: db.execute("INSERT INTO events (seq, id, session, type, ts, body) VALUES"
                                       " (?,?,?,?,?,?)", (ev.seq, ev.id, ev.session, ev.type, ev.ts,
                                                          json.dumps(to_dict(ev.body), ensure_ascii=False))))

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
        (seq,) = self._q("SELECT COALESCE(MAX(seq), 0) FROM events")[0]
        return seq


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
                out = fn(ev)
                if asyncio.iscoroutine(out):
                    await out
            except Exception as exc:  # a broken client must not break the turn, but it is recorded
                from exo.core import trace

                trace.report(f"event subscriber on {ev.type}", exc, session)
        return ev
