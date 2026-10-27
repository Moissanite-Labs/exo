"""Optional local skill ranking. Selection is a per-turn value, never shared session state.

Only the runtime's enabled index is eligible. Metadata is reread, within per-file and
per-turn bounds, so edits do not need cache invalidation. New skills still require a restart.
"""
from __future__ import annotations

import math
import os
import re
import stat
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from exo.config import ConfigError
from exo.provenance import defuse_pins
from exo.core.skills import MAX_DESC, Skill, SkillIndex, parse_frontmatter

HEADER = ("# Relevant skills\nLoad full instructions with the `skill` tool before using a skill. "
          "You can also load an enabled skill by name even when it is not listed here.")
MAX_METADATA_BYTES = 16_384
MAX_METADATA_FILES = 256  # reads per turn; a larger catalog truncates, it does not read more
MAX_QUERY_CHARS = 8_192
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    max_chars: int = 2_048
    top_k: int = 6
    max_files: int = MAX_METADATA_FILES

    @classmethod
    def of(cls, value: Any) -> Settings:
        if not isinstance(value, dict) or set(value) - {"enabled", "max_chars", "top_k", "max_files"}:
            raise ConfigError("skill_discovery must be an object with enabled, max_chars, top_k and max_files only")
        enabled, limit, count = value.get("enabled", False), value.get("max_chars", 2_048), value.get("top_k", 6)
        files = value.get("max_files", MAX_METADATA_FILES)
        if type(enabled) is not bool:
            raise ConfigError("skill_discovery.enabled must be a boolean")
        if type(limit) is not int or not 0 <= limit <= 32_768:
            raise ConfigError("skill_discovery.max_chars must be an integer from 0 to 32768")
        if type(count) is not int or not 0 <= count <= 50:
            raise ConfigError("skill_discovery.top_k must be an integer from 0 to 50")
        if type(files) is not int or not 0 <= files <= 4_096:
            raise ConfigError("skill_discovery.max_files must be an integer from 0 to 4096")
        return cls(enabled, limit, count, files)


@dataclass(frozen=True)
class Selection:
    text: str
    names: tuple[str, ...]
    considered: int
    unavailable: tuple[str, ...]


def _description(skill: Skill) -> str | None:
    """Read a bounded regular-file header without following a final file symlink."""
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(skill.path, flags)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            raw = stream.read(MAX_METADATA_BYTES)
        text = raw.decode("utf-8", errors="replace")
        metadata = parse_frontmatter(text)
        # A changed header name does not expand the runtime's enabled names.
        return " ".join(str(metadata.get("description") or "").split())[:MAX_DESC]
    except (OSError, ValueError):
        return None


def _words(text: str) -> list[str]:
    return _WORDS.findall(text.casefold())


class SkillDiscovery:
    def __init__(self, index: SkillIndex, settings: Settings,
                 overrides: Mapping[str, str] | None = None) -> None:
        self.index, self.settings = index, settings
        self.overrides = dict(overrides or {})

    def select(self, query: str) -> Selection:
        """Rank by exact name, then BM25 over name and description; ties use name.

        The complete returned block, including heading and separators, fits the
        character ceiling. Too-small budgets return no block, never a cut name.
        At most ``max_files`` indexed files are read, in sorted name order; the
        rest are unavailable for the turn, like unreadable files.
        """
        cfg = self.settings
        limit = cfg.max_chars - 2  # the loop inserts one blank line before the block
        if not cfg.enabled or limit < len(HEADER):
            return Selection("", (), 0, ())
        query = query[:MAX_QUERY_CHARS]
        terms = sorted(set(_words(query)))
        rows, unavailable = [], []
        indexed = sorted(tuple(self.index.skills.items()))
        reads = 0
        for name, skill in indexed:
            if reads >= cfg.max_files:  # past the cap the name is only marked, never read
                unavailable.append(name)
                continue
            reads += 1
            description = _description(skill)
            if description is None:
                unavailable.append(name)
                continue
            description = " ".join(self.overrides.get(name, description).split())[:MAX_DESC]
            words = _words(name) + _words(description)
            rows.append((name, description, Counter(words), len(words)))
        if not rows:
            return Selection("", (), 0, tuple(unavailable))
        frequency = Counter(word for _, _, counts, _ in rows for word in counts)
        average = sum(size for _, _, _, size in rows) / len(rows) or 1.0
        aliases = Counter(name.rsplit(":", 1)[-1].casefold() for name, *_ in rows)
        ranked = []
        for name, description, counts, size in rows:
            names = [name]
            tail = name.rsplit(":", 1)[-1]
            if aliases[tail.casefold()] == 1:
                names.append(tail)
            explicit = any(re.search(r"(?<![\w:-])" + re.escape(n) + r"(?![\w:-])", query, re.I) for n in names)
            score = sum(math.log(1 + (len(rows) - frequency[t] + 0.5) / (frequency[t] + 0.5))
                        * (counts[t] * 2.2) / (counts[t] + 1.2 * (0.25 + 0.75 * size / average))
                        for t in terms if counts[t])
            if explicit or score:
                ranked.append((not explicit, -score, name, description))
        selected: list[str] = []
        text = HEADER
        for _, _, name, description in sorted(ranked):
            if len(selected) >= cfg.top_k:
                break
            # Flatten metadata and escape Exo authority markers before budget accounting.
            label = defuse_pins(" ".join(name.split()))
            row = "\n- " + label
            if len(text) + len(row) > limit:
                continue
            detail = defuse_pins(description)
            room = limit - len(text) - len(row) - 2
            if detail and room > 0:
                row += ": " + (detail if len(detail) <= room else detail[:max(0, room - 1)] + "…")
            text += row
            selected.append(name)
        if len(indexed) > cfg.max_files:  # name the cap in the block when the budget has room left
            note = f"[read cap: {reads} of {len(indexed)} indexed files this turn]"
            if len(text) + 1 + len(note) <= limit:
                text += "\n" + note
        return Selection(text, tuple(selected), len(rows), tuple(unavailable))

    def render(self, session: str, query: str) -> str:
        """Loop callback: session identity does not enter scoring or shared state."""
        return self.select(query).text
