"""One agent, one log (D-028, docs/MEMORY-MODEL.md): the single-writer lock, the read view the memory provider
gets, and the ``memory_zoom`` tool.

The log itself lives in sessions.sqlite (``SessionStore.log``); the tree is built by ``AgentContext``.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Any

from exo.core.context import render_message
from exo.core.private import private_file
from exo.core.types import ToolSpec

PAGE = 30_000  # characters of one entry per zoom page


class WriterBusy(RuntimeError):
    """Another process already writes this agent's log and tree."""


def lock_path(home: str | os.PathLike, agent: str) -> Path:
    return Path(home) / "run" / f"agent-{agent}.lock"


def socket_path(home: str | os.PathLike, agent: str) -> Path:
    """Where an in-process core that holds the lock serves, so a second window joins it."""
    return Path(home) / "run" / f"agent-{agent}.sock"


def claim_writer(home: str | os.PathLike, agent: str) -> Any:
    """Take the agent's writer lock (an OS file lock, gone when the process dies). Returns the open file; keep it
    open while writing and pass it to ``release_writer``. Raises WriterBusy when another process holds it."""
    path = lock_path(home, agent)
    private_file(path)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        holder = fh.read().strip()
        fh.close()
        raise WriterBusy(f"agent {agent} is already running in another process"
                         + (f" (pid {holder})" if holder else "") + "; connect to it instead") from None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def release_writer(fh: Any) -> None:
    if fh is not None and not fh.closed:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


class AgentLogView:
    """Read side of an agent's log for the memory provider: ``len``, ``search``, ``zoom``, ``date``. The same
    shapes as exo-memory's episodic layer, so its recall reads the one log instead of keeping a second one."""

    def __init__(self, store: Any, agent: str):
        self.store = store
        self.agent = agent

    def __len__(self) -> int:
        return self.store.last_pos(self.agent)

    def search(self, query: str, n: int = 5, skip_recent: int = 0) -> list[dict[str, Any]]:
        last = len(self)
        out = []
        for hit in self.store.search(query, limit=n * 3, agent=self.agent):
            if skip_recent and hit["pos"] > last - skip_recent:
                continue
            entry = self.store.log(self.agent, hit["pos"] - 1, hit["pos"])
            role = entry[0].message.role if entry else "user"
            out.append({"id": hit["pos"], "kind": {"assistant": "talk"}.get(role, role), "date": hit["created"],
                        "text": hit["snippet"], "session": hit["session"]})
            if len(out) >= n:
                break
        return out

    def zoom(self, start: int, n: int = 1, page: int = 1) -> str:
        """Entries ``start`` .. ``start + n - 1`` in full (one long entry is paged)."""
        n = max(1, min(int(n), 64))
        entries = [e for e in self.store.log(self.agent, int(start) - 1, int(start) - 1 + n)
                   if not e.hidden and e.kind != "private"]
        if not entries:
            return f"no log entries {start}-{int(start) + n - 1}"
        if n == 1:
            text = render_message(entries[0].message, limit=10**9)
            pages = max(1, -(-len(text) // PAGE))
            p = max(1, min(int(page), pages))
            body = text[(p - 1) * PAGE:p * PAGE]
            return f"#{start} {entries[0].created} thread {entries[0].session}" + \
                (f" (page {p} of {pages})" if pages > 1 else "") + "\n" + body
        return "\n\n".join(f"#{e.pos} {e.created} thread {e.session}\n{render_message(e.message, 4000)}"
                           for e in entries)

    def date(self, pos: int) -> str:
        e = self.store.log(self.agent, int(pos) - 1, int(pos))
        return e[0].created if e else f"no log entry {pos}"

    def subagent(self, name: str, start: int = 1, n: int = 40, caller: str | None = None) -> str:
        """A subagent's own log, by role, title or session id (the newest match, the later row on a tied timestamp).
        One under a private thread, at any depth, opens only from that thread or a fork of it: the hidden set starts
        at each private thread that is not the caller or one of its parents (D-028)."""
        rows = self.store._q(
            "WITH RECURSIVE up(id) AS (SELECT id FROM sessions WHERE id=? UNION SELECT s.parent FROM sessions s JOIN up"
            " ON s.id=up.id WHERE s.parent IS NOT NULL), hid(id) AS (SELECT id FROM sessions WHERE kind='private' AND"
            " id NOT IN up UNION SELECT c.id FROM sessions c JOIN hid ON c.parent=hid.id) SELECT id, title FROM"
            " sessions WHERE agent=? AND kind='subagent' AND (id=? OR title LIKE ?) AND id NOT IN hid"
            " ORDER BY created DESC, rowid DESC LIMIT 1", (caller, self.agent, name, f"{name}%"))
        if not rows:
            return f"no subagent named {name!r}"
        sid = rows[0]["id"]
        msgs = self.store.messages(sid, since=max(0, int(start) - 1), upto=int(start) - 1 + max(1, min(int(n), 200)))
        head = f"subagent {rows[0]['title']} ({sid}), messages {start}-{int(start) + len(msgs) - 1}"
        return head + "\n\n" + "\n\n".join(render_message(m, 4000) for m in msgs)


ZOOM = ToolSpec(
    name="memory_zoom", toolset="memory", risk="low",
    description=("Open your own memory log in full. The memory view in your instructions labels each part "
                 "'Log a-b'; memory_zoom(start=a, n=b-a+1) shows those entries word for word, n=1 one entry "
                 "(page for a long one). subagent=<role or name> opens that subagent's own log instead. Zoom "
                 "before acting on a summary. Read only."),
    parameters={"type": "object", "required": ["start"], "properties": {
        "start": {"type": "integer", "description": "First log entry."},
        "n": {"type": "integer", "description": "How many entries (default 1, at most 64)."},
        "page": {"type": "integer", "description": "Page of a long entry when n is 1."},
        "subagent": {"type": "string", "description": "Open this subagent's log (role, title or session id)."}}})


def register_zoom(registry: Any, view: AgentLogView) -> None:
    def zoom(args: dict[str, Any], ctx: Any) -> str:
        start = int(args.get("start") or 1)
        if args.get("subagent"):
            return view.subagent(str(args["subagent"]), start, int(args.get("n") or 40), getattr(ctx, "session", None))
        return view.zoom(start, int(args.get("n") or 1), int(args.get("page") or 1))

    registry.register(ZOOM, zoom, replace=True)


__all__ = ["AgentLogView", "WriterBusy", "claim_writer", "lock_path", "register_zoom", "release_writer",
           "socket_path"]
