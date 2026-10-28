"""Read-only session-tree drill-down prototype (#676); not registered as a runtime tool.

Construct only with a caller session established by the host, never model-supplied scope.
Agent-wide trees use log positions and deliberately are not supported by this seq-based view.
"""
from __future__ import annotations

import hashlib
from typing import Any

PAGE_CHARS = 30_000


def _page(text: str, page: int) -> dict[str, Any]:
    pages = max(1, (len(text) + PAGE_CHARS - 1) // PAGE_CHARS)
    if type(page) is not int or not 1 <= page <= pages:
        raise ValueError("page outside source")
    return {"text": text[(page - 1) * PAGE_CHARS:page * PAGE_CHARS], "page": page,
            "pages": pages, "characters": len(text),
            "sha256": hashlib.sha256(text.encode()).hexdigest()}


class SessionSummaryView:
    """A host-bound ordinary session only. No cross-session lookup, writes or inference."""

    def __init__(self, store: Any, caller_session: str, agent: str):
        self.store, self.session, self.agent = store, caller_session, agent
        self._scope()

    def _scope(self) -> None:
        if (not self.agent or self.store.thread(self.session) != (self.agent, "thread")
                or self.store.private(self.session)):
            raise ValueError("ordinary owned session required")

    def _node(self, node_id: str) -> Any:
        self._scope()  # re-check after a session is reparented or its kind changes
        rows = self.store._q("SELECT * FROM summaries WHERE id=? AND session=?", (node_id, self.session))
        if not rows:
            raise ValueError("node unavailable")
        row = rows[0]
        if (row["level"] < 0 or row["idx"] < 0 or row["start_seq"] < 1
                or row["end_seq"] < row["start_seq"]):
            raise ValueError("invalid node span")
        return row

    @staticmethod
    def _ref(row: Any) -> dict[str, Any]:
        return {"id": row["id"], "session": row["session"], "level": row["level"],
                "index": row["idx"], "start": row["start_seq"], "end": row["end_seq"],
                "status": row["status"], "lane": row["lane"]}

    def describe(self, node_id: str, page: int = 1) -> dict[str, Any]:
        node = self._node(node_id)
        children = []
        if node["level"]:
            rows = self.store._q("SELECT * FROM summaries WHERE session=? AND level=? AND idx IN (?,?)"
                                 " AND lane=? ORDER BY idx", (self.session, node["level"] - 1,
                                 node["idx"] * 2, node["idx"] * 2 + 1, node["lane"]))
            # Never assert ancestry from index alone if the stored spans disagree.
            if (len(rows) != 2 or rows[0]["start_seq"] != node["start_seq"]
                    or rows[1]["end_seq"] != node["end_seq"]
                    or rows[0]["end_seq"] + 1 != rows[1]["start_seq"]):
                raise ValueError("incomplete or inconsistent children")
            children = [self._ref(self._node(r["id"])) for r in rows]
        return {"node": self._ref(node), "summary": _page(node["text"], page),
                "children": children, "source_unit": "session_seq"}

    def sources(self, node_id: str, after: int = 0) -> dict[str, Any]:
        """At most 16 visible original-message references; cursor is a session seq."""
        node = self._node(node_id)
        if type(after) is not int or after < 0:
            raise ValueError("invalid cursor")
        rows = self.store._q("SELECT id, seq FROM messages WHERE session=? AND seq>=? AND seq<=?"
                             " AND hidden=0 ORDER BY seq LIMIT 17",
                             (self.session, max(node["start_seq"], after + 1), node["end_seq"]))
        return {"messages": [{"id": r["id"], "seq": r["seq"], "session": self.session} for r in rows[:16]],
                "next_after": rows[15]["seq"] if len(rows) > 16 else None}

    def source(self, node_id: str, seq: int, page: int = 1) -> dict[str, Any]:
        """Exact stored JSON, paged without silently dropping tool calls or metadata.

        The digest identifies this payload; it is not a verified ledger signature.
        """
        node = self._node(node_id)
        if type(seq) is not int or not node["start_seq"] <= seq <= node["end_seq"]:
            raise ValueError("source outside node")
        rows = self.store._q("SELECT id, data FROM messages WHERE session=? AND seq=? AND hidden=0",
                             (self.session, seq))
        if not rows:
            raise ValueError("source unavailable")
        return {"session": self.session, "seq": seq, "message_id": rows[0]["id"],
                "format": "stored_message_json", "payload": _page(rows[0]["data"], page)}
