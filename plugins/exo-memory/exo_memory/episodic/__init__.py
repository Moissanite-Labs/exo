"""Episodic layer of exo-memory: verbatim daily log, binary summary tree, view, zoom and date.

Vendored from aska-digital/optchat-memory v1.0.0 (MIT, Copyright (c) 2026 aska-digital; see
LICENSE-optchat). store.py, tree.py, view.py and compact.py are the optchat modules with small
changes marked "exo:". This file holds what was the optchat provider glue, reshaped as a
plain class so exo-memory can own the one memory-provider slot (D-007).

The log lives under the principal store (``<store>/episodic/``), never under the Exo home, so
memory follows the principal across seats and engines.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .compact import Compactor, compact_prompt
from .store import ChatLog, LogLocked
from .tree import NODE_BYTES, SummaryTree
from .view import UNBUILT_PLACEHOLDER, View

logger = logging.getLogger(__name__)

__all__ = ["EpisodicLayer", "LogLocked", "entries_from_messages", "ZOOM_SCHEMA", "DATE_SCHEMA"]

CAP_CHARS = 30_000          # max chars of one tool result kept in the log (optchat CAP)
SNIPPET_CHARS = 240
INDEX_CHARS = 4000          # chars of each message that feed the search index

ZOOM_SCHEMA = {
    "name": "exo_zoom",
    "description": ("Open the episodic memory line id+n into the two lines of n/2 messages it was made from; "
                    "n = 1 gives message id in full. Zoom before acting on a summary."),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "First message id of the line."},
            "n": {"type": "integer", "description": "How many messages the line covers (power of 2)."},
            "page": {"type": "integer", "description": "Page of a long message (n=1 only); 1-based."},
        },
        "required": ["id", "n"],
    },
}

DATE_SCHEMA = {
    "name": "exo_date",
    "description": "The date and time of episodic memory message id.",
    "parameters": {"type": "object", "properties": {"id": {"type": "integer", "description": "Message id."}},
                   "required": ["id"]},
}


def _content_text(content: Any) -> str:
    from exo.capture import content_text
    return content_text(content)


def cap_echo(text: str, cap: int = CAP_CHARS) -> str:
    if len(text) <= cap:
        return text
    head = cap * 3 // 4
    return f"{text[:head]}\n...[exo-memory: cut {len(text) - cap} chars]...\n{text[-(cap - head):]}"


def entries_from_messages(messages: Iterable[dict]) -> Iterator[tuple[str, str]]:
    """OpenAI-style messages -> (log kind, text). System rows are the harness's and never logged."""
    for m in messages:
        role = m.get("role")
        text = _content_text(m.get("content"))
        if role == "user":
            if text.strip():
                yield ("user", text)
        elif role == "assistant":
            if text.strip():
                yield ("talk", text)
            for tc in m.get("tool_calls") or []:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                yield ("tool", f"{fn.get('name', '?')}({fn.get('arguments', '')})")
        elif role == "tool":
            if text.strip():
                yield ("echo", cap_echo(text))
        elif role == "work":
            if text.strip():
                yield ("work", text)


class EpisodicLayer:
    """Log + tree + view + optional background compactor for one principal store."""

    def __init__(self, root: str | os.PathLike, *, view_chars: int = 6000, node_bytes: int = NODE_BYTES,
                 compact: bool = True, summarizer=None, agent_name: str = "the agent") -> None:
        from exo.store import tokens
        self._tokens = tokens
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        ignore = self.root / ".gitignore"
        if not ignore.exists():  # the raw record never enters the store's git history
            ignore.write_text("# exo-memory episodic log: private raw record\n*\n", encoding="utf-8")
        self.lock = threading.RLock()
        self.log = ChatLog(self.root)
        self.tree = SummaryTree(self.root)
        self.view_path = self.root / "view.json"
        self.view = View.load(self.tree, view_chars, self.view_path, len(self.log)) or View(self.tree, view_chars)
        with self.lock:
            self.view.fold_to(len(self.log))
        self.view.save(self.view_path)
        self.node_bytes = node_bytes
        self._docs: list[tuple[int, str, str, Counter, str]] = []
        self._df: Counter = Counter()
        self._index_existing()
        self.compactor: Optional[Compactor] = None
        if compact:
            self.compactor = Compactor(self.tree, self.log, self.view, summarize=summarizer,
                                       system=compact_prompt(agent_name), node_bytes=node_bytes,
                                       view_path=self.view_path)
            self.compactor.start()

    # -- search index ------------------------------------------------------------

    def _index_one(self, i: int, kind: str, text: str, date: str) -> None:
        toks = Counter(self._tokens(text[:INDEX_CHARS]))
        self._docs.append((i, kind, date, toks, " ".join(text.split())[:SNIPPET_CHARS]))
        self._df.update(toks.keys())

    def _index_existing(self) -> None:
        for path in sorted(self.log.main_dir.glob("*.jsonl")):
            with open(path, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    try:
                        m = json.loads(raw)
                        self._index_one(int(m["i"]), m.get("kind", ""), m.get("text", ""), m.get("date", ""))
                    except (ValueError, KeyError, TypeError):
                        continue
        self._docs.sort(key=lambda d: d[0])

    # -- writing -----------------------------------------------------------------

    def append(self, kind: str, text: str, session: str = "") -> int:
        with self.lock:
            i = self.log.append(kind, text, session=session)
            self._index_one(i, kind, text, self.log.read(i).get("date", ""))
        return i

    def append_many(self, entries: Iterable[tuple[str, str]], session: str = "") -> int:
        n = 0
        for kind, text in entries:
            self.append(kind, text, session)
            n += 1
        if n and self.compactor:
            self.compactor.notify()
        return n

    def __len__(self) -> int:
        return len(self.log)

    # -- reading -----------------------------------------------------------------

    def search(self, query: str, n: int = 5, skip_recent: int = 0) -> list[dict]:
        """Messages that share words with `query`, best first. The newest `skip_recent`
        messages are left out (they are still in the live context)."""
        q = set(self._tokens(query))
        if not q:
            return []
        with self.lock:
            docs = self._docs[: max(0, len(self._docs) - skip_recent)]
            N = max(len(self._docs), 1)
            scored = []
            for i, kind, date, toks, snip in docs:
                s = sum(math.log(1 + N / (1 + self._df[w])) * (1 + math.log(toks[w])) for w in q if w in toks)
                if s:
                    scored.append((s, i, kind, date, snip))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [{"id": i, "kind": k, "date": d, "text": t, "score": round(s, 3)} for s, i, k, d, t in scored[:n]]

    def view_lines(self) -> list[tuple[int, int, Optional[str]]]:
        with self.lock:
            self.view.fold_to(len(self.log))
            out = []
            for part in self.view.parts:
                msg_id, n = part.address()
                out.append((msg_id, n, self.tree.get(part.l, part.i)))
            self.view.save(self.view_path)
            return out

    def render(self, budget_chars: int) -> str:
        """The view as `id+n|text` lines under `budget_chars`. Runs of unsummarized messages
        collapse to one line. When the view is still too long, the newest lines go first:
        recent turns are in the live context anyway."""
        if budget_chars <= 0 or len(self.log) == 0:
            return ""
        lines: list[str] = []
        run: list[int] = []

        def flush() -> None:
            if run:
                a, b = run[0], run[-1]
                lines.append(f"{a}..{b}|({b - a + 1} messages not summarized yet: exo_zoom(id, 1))"
                             if b > a else f"{a}+1|{UNBUILT_PLACEHOLDER}")
                run.clear()

        for msg_id, n, text in self.view_lines():
            if text is None:
                run.extend(range(msg_id, msg_id + n))
                continue
            flush()
            lines.append(f"{msg_id}+{n}|{' '.join(text.split())}")
        flush()
        tail = "(newer lines omitted; recent turns are in context)"
        while lines and sum(len(x) + 1 for x in lines) > budget_chars:
            lines.pop()
            if sum(len(x) + 1 for x in lines) + len(tail) + 1 <= budget_chars:
                lines.append(tail)
                break
        return "\n".join(lines)

    def zoom(self, msg_id: int, n: int, page: int = 1) -> str:
        with self.lock:
            return self.tree.zoom(self.log, msg_id, n, len(self.log), page=page)

    def date(self, msg_id: int) -> str:
        with self.lock:
            return self.tree.message_date(self.log, msg_id)

    def notify(self) -> None:
        if self.compactor:
            self.compactor.notify()

    def close(self) -> None:
        if self.compactor:
            self.compactor.stop()
            self.compactor = None
        with self.lock:
            self.view.save(self.view_path)
            self.log.close()
