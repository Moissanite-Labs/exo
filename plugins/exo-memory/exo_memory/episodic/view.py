# Vendored from aska-digital/optchat-memory v1.0.0 (commit 8682354), the episodic layer of exo-memory.
# MIT License. Copyright (c) 2026 aska-digital. Full text: LICENSE-optchat in this directory.
# Changes for Exo are marked "exo:".
"""The view (OptChat spec section 3).

The view is a list of tree nodes ("parts") that tiles the whole chat
``[0, T)``, oldest first. It is what the agent's recall sees.

Rendering: one line per part, ``id+n|text`` (newlines shown as spaces),
inside ``<chat>`` tags. No dates on lines (the agent calls ``date(id)``
when it needs one). **The view never holds a whole message** — only
summaries; a part whose node isn't built yet renders as
``id+1|(not summarized yet: zoom it)``.

How it changes (spec section 3.2 — the easiest part to get wrong):
on a new message, append ``part(0, i)`` and ``fit()``; on a node built,
``fit()``. ``fit()`` repeatedly merges the *most due* adjacent pair —
oldest relative to its size, ``due = (T - last) / 2**l`` where ``last``
is the pair's LAST message — among pairs whose parent is already built.
Measuring from the pair's first message is wrong: near ties it merges
old pairs and rewrites old lines the push order keeps (spec section
3.2, "Mistakes to avoid" #1). Never split: once merged, a part stays
merged; the view only appends at the end and coarsens. A line at level
``l`` changes about once every ``2**l`` messages, so consecutive views
share most of their bytes (this is what made the design cheap; here it
keeps prefetch output stable and small).

Deliberately NOT done: showing recent messages whole (one big tool
result would permanently erase old detail), or cut text for
unsummarized messages (the agent would act on half a message).

Persistence (spec section 3.2): the view is saved to ``view.json`` as
``[l, i]`` pairs and loaded at start — never rebuilt from the log, the
rebuilt view differs from the live one. ``view.json`` is a cache: any
corruption falls back to a clean refold.

Threading: the background compactor mutates the view while turns read
it, so every method serializes on an internal lock. Lock order is
view -> tree/log; never take another lock while holding this one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Optional

from exo.core.private import atomic_write  # exo: #273

from .tree import SummaryTree

logger = logging.getLogger(__name__)

UNBUILT_PLACEHOLDER = "(not summarized yet: zoom it)"


@dataclass
class Part:
    l: int  # level
    i: int  # index within the level

    def address(self) -> tuple[int, int]:
        return SummaryTree.address(self.l, self.i)


class View:
    """Incrementally maintained tiling. ``fold_to`` / ``refit`` keep it live;
    the provider serializes turn-level access and the compactor shares it
    through the internal lock."""

    def __init__(self, tree: SummaryTree, budget_chars: int) -> None:
        self.tree = tree
        self.budget = budget_chars
        self.parts: list[Part] = []
        self.folded = 0  # messages folded so far
        self._lock = RLock()
        self._render_cache: Optional[str] = None

    # -- incremental maintenance ----------------------------------------

    def _part_size(self, part: Part) -> int:
        text = self.tree.get(part.l, part.i)
        if text is None:
            text = UNBUILT_PLACEHOLDER
        return len(text.encode("utf-8"))

    def _size(self) -> int:
        return sum(self._part_size(p) for p in self.parts)

    def fold_to(self, total: int) -> None:
        """Append ``part(0, i)`` for every new message, then fit."""
        with self._lock:
            if total < self.folded:
                # Log truncation should not happen (append-only); refold cleanly.
                self.parts = []
                self.folded = 0
            while self.folded < total:
                self.parts.append(Part(0, self.folded))
                self.folded += 1
            self.refit()

    def refit(self) -> None:
        """Merge the most due pair until under budget. Parents enter only once built."""
        with self._lock:
            self._render_cache = None
            total = self.folded
            size = self._size()
            while size > self.budget:
                best = -1
                best_due = -1.0
                for idx in range(len(self.parts) - 1):
                    a, b = self.parts[idx], self.parts[idx + 1]
                    if a.l != b.l or b.i != a.i + 1 or a.i % 2 != 0:
                        continue
                    if not self.tree.built(a.l + 1, a.i // 2):
                        continue
                    # Spec 3.2: age measured from the pair's LAST message,
                    # in units of its own line size.
                    last = (a.i + 2) * (1 << a.l) - 1
                    due = (total - last) / float(1 << a.l)
                    if due > best_due:
                        best_due = due
                        best = idx
                if best < 0:
                    break  # wait until a parent is built
                a = self.parts[best]
                self.parts[best : best + 2] = [Part(a.l + 1, a.i // 2)]
                size = self._size()

    # -- compactor support (spec section 4.1, rule 3) ---------------------

    def first_unbuilt_message(self) -> int:
        """First message whose view line is not a built summary (== total when all built)."""
        with self._lock:
            for part in self.parts:
                if not self.tree.built(part.l, part.i):
                    return part.i * (1 << part.l)
            return self.folded

    def context_lines(self, upto_msg: int) -> list[str]:
        """Bare texts of built parts ending at or before ``upto_msg``.

        Stops at the first unbuilt part (spec section 4): a compaction
        never sees a placeholder or half a message.
        """
        with self._lock:
            lines = []
            for part in self.parts:
                msg_id, n = part.address()
                if msg_id + n > upto_msg:
                    break
                text = self.tree.get(part.l, part.i)
                if text is None:
                    break
                lines.append(text.replace("\n", " "))
            return lines

    # -- persistence (spec section 3.2) -----------------------------------

    def save(self, path: Path) -> None:
        """Write ``[l, i]`` pairs. Best-effort cache: never raises, owner-only."""
        with self._lock:
            data = {
                "folded": self.folded,
                "budget": self.budget,
                "parts": [[p.l, p.i] for p in self.parts],
            }
        try:
            # exo: replace through mkstemp, so a symlink at the path is not followed (#273). A cache: no fsync.
            atomic_write(path, json.dumps(data), sync=False)
        except OSError as exc:
            logger.warning("optchat: view save failed for %s: %s", path, exc)

    @classmethod
    def load(cls, tree: SummaryTree, budget_chars: int, path: Path, total: int) -> Optional["View"]:
        """Load a saved view. Returns None when missing, corrupt, or invalid —
        the caller refolds from the log in that case."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            folded = int(data["folded"])
            raw_parts = data["parts"]
        except FileNotFoundError:
            return None  # first run: refold quietly
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("optchat: view load failed for %s (%s); refolding", path, exc)
            return None
        if not isinstance(raw_parts, list) or folded < 0 or folded > total:
            logger.warning("optchat: view file %s failed validation; refolding", path)
            return None
        parts: list[Part] = []
        start = 0
        try:
            for item in raw_parts:
                l, i = int(item[0]), int(item[1])
                if l < 0 or i < 0 or l > 60:
                    raise ValueError("bad part")
                if i * (1 << l) != start:
                    raise ValueError("parts do not tile [0, folded)")
                parts.append(Part(l, i))
                start += 1 << l
            if start != folded:
                raise ValueError("parts do not tile [0, folded)")
        except (ValueError, TypeError, IndexError) as exc:
            logger.warning("optchat: view file %s failed validation (%s); refolding", path, exc)
            return None
        view = cls(tree, budget_chars)
        view.parts = parts
        view.folded = folded
        return view

    # -- rendering --------------------------------------------------------

    def render(self) -> str:
        """The ``<chat>`` block the agent sees. Cached until the next refit."""
        with self._lock:
            if self._render_cache is not None:
                return self._render_cache
            lines = ["<chat>"]
            for part in self.parts:
                msg_id, n = part.address()
                text = self.tree.get(part.l, part.i)
                if text is None:
                    text = UNBUILT_PLACEHOLDER
                flat = text.replace("\n", " ")
                lines.append(f"{msg_id}+{n}|{flat}")
            lines.append("</chat>")
            self._render_cache = "\n".join(lines)
            return self._render_cache
