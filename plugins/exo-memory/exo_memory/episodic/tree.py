# Vendored from aska-digital/optchat-memory v1.0.0 (commit 8682354), the episodic layer of exo-memory.
# MIT License. Copyright (c) 2026 aska-digital. Full text: LICENSE-optchat in this directory.
# Changes for Exo are marked "exo:".
"""Binary summary tree (OptChat spec section 3).

``node(l, i)`` covers messages ``[i*2**l, (i+1)*2**l)``. Level 0 summarizes
one message; level ``l > 0`` merges its two children ``(l-1, 2i)`` and
``(l-1, 2i+1)``. The tree is purely binary: every parent comes from exactly
its two children.

Addressing (spec section 3): a node is named ``id+n`` — ``id`` is its first
message, ``n = 2**l`` is how many messages it covers. The agent reads
``2184+8`` in the view and calls ``zoom(2184, 8)`` directly.

Free nodes: when the source already fits in ``NODE`` bytes it IS the node,
with no model call — level 0 keeps ``kind + ": " + text`` verbatim, so
short messages stay word-for-word up the tree until merged.

Layout: ``<root>/tree/YYYY-MM-DD.jsonl`` — one JSON object per line::

    {"l": 0, "i": 42, "text": "...", "size": 123, "date": "2026-10-04T..."}

The tree is a cache in principle (rebuildable from the log) but costs
model calls to rebuild, so it is stored and never recomputed.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Optional

logger = logging.getLogger(__name__)

# Target size of one summary line, in UTF-8 bytes (spec constant NODE).
NODE_BYTES = 512


class SummaryTree:
    """In-memory node table over a JSONL node store. Serialized by the provider."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.tree_dir = self.root / "tree"
        self.tree_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        # (l, i) -> summary text
        self._nodes: dict[tuple[int, int], str] = {}
        self._scan()

    # -- persistence -----------------------------------------------------

    def _day_files(self) -> list[Path]:
        return sorted(self.tree_dir.glob("*.jsonl"))

    def _scan(self) -> None:
        self._nodes = {}
        for path in self._day_files():
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    if not raw.strip():
                        continue
                    try:
                        obj = json.loads(raw)
                        self._nodes[(int(obj["l"]), int(obj["i"]))] = obj["text"]
                    except (ValueError, KeyError, TypeError) as exc:
                        logger.warning("optchat: torn tree line skipped in %s: %s", path, exc)

    def _today_path(self) -> Path:
        return self.tree_dir / (datetime.now().astimezone().strftime("%Y-%m-%d") + ".jsonl")

    def set(self, l: int, i: int, text: str) -> None:
        """Store a built node (append + fsync; never recomputed once stored)."""
        with self._lock:
            obj = {
                "l": l,
                "i": i,
                "text": text,
                "size": len(text.encode("utf-8")),
                "date": datetime.now().astimezone().isoformat(),
            }
            path = self._today_path()
            # Create owner-only (0600) so a shared home never exposes the tree.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.chmod(path, 0o600)  # force even when the file pre-existed looser
            except OSError:
                pass
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.seek(0, os.SEEK_END)  # O_APPEND offset isn't reflected in Python's tell
                fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._nodes[(l, i)] = text

    def get(self, l: int, i: int) -> Optional[str]:
        with self._lock:
            return self._nodes.get((l, i))

    def built(self, l: int, i: int) -> bool:
        with self._lock:
            return (l, i) in self._nodes

    # -- structure --------------------------------------------------------

    @staticmethod
    def span(l: int) -> int:
        return 1 << l

    @staticmethod
    def address(l: int, i: int) -> tuple[int, int]:
        """``(l, i)`` -> ``(id, n)`` with ``id`` = first message, ``n`` = span."""
        n = 1 << l
        return (i * n, n)

    @staticmethod
    def children(l: int, i: int) -> tuple[tuple[int, int], tuple[int, int]]:
        return ((l - 1, 2 * i), (l - 1, 2 * i + 1))

    @staticmethod
    def is_free_node(source_text: str, node_bytes: int = NODE_BYTES) -> bool:
        """True when the source fits in the node budget — no model call needed."""
        return len(source_text.encode("utf-8")) <= node_bytes

    # -- zoom (spec section 6, tools) ------------------------------------------

    # A long message comes in pages (spec section 6): zoom(id, 1) returns one
    # page at a time so a single huge message can't flood the context.
    ZOOM_PAGE_CHARS = 4000

    def zoom(self, log, msg_id: int, n: int, total: int, page: int = 1) -> str:
        """Open line ``id+n`` into its two children; ``n == 1`` gives the message whole, in pages."""
        if n < 1 or (n & (n - 1)) != 0:
            return "No line %d+%d." % (msg_id, n)
        if msg_id % n != 0 or msg_id + n > total:
            return "No line %d+%d." % (msg_id, n)
        if n == 1:
            try:
                m = log.read(msg_id)
            except IndexError:
                return "No line %d+%d." % (msg_id, n)
            text = f"{m['kind']}: {m['text']}"
            pages = max(1, -(-len(text) // self.ZOOM_PAGE_CHARS))  # ceil
            if page < 1 or page > pages:
                return "No line %d+%d." % (msg_id, n)
            start = (page - 1) * self.ZOOM_PAGE_CHARS
            chunk = text[start : start + self.ZOOM_PAGE_CHARS]
            # Newlines kept: only the whole-message case keeps them, per the spec.
            out = f"{msg_id}+0|{chunk}"
            if pages > 1:
                out += f"\n[page {page} of {pages} — zoom({msg_id}, 1, page={page + 1}) for the next]" if page < pages else f"\n[page {page} of {pages} — last page]"
            return out
        l = n.bit_length() - 1
        i = msg_id // n
        (al, ai), (bl, bi) = self.children(l, i)
        a = self.get(al, ai)
        b = self.get(bl, bi)
        if a is None or b is None:
            return "No line %d+%d." % (msg_id, n)
        (aid, an) = self.address(al, ai)
        (bid, bn) = self.address(bl, bi)
        # Child lines render like view lines (newlines flattened); only the
        # whole-message case (n == 1) keeps newlines, per the spec.
        return f"{aid}+{an}|{a.replace(chr(10), ' ')}\n{bid}+{bn}|{b.replace(chr(10), ' ')}"

    def message_date(self, log, msg_id: int) -> str:
        try:
            return log.read(msg_id).get("date", "")
        except IndexError:
            return ""
