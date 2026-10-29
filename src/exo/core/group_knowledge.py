"""Explicit shared knowledge for one saved Swarm group (M02-M04).

The human owner publishes text with source references on purpose; nothing here
reads sessions, memories or messages, so no private content is ingested or
published automatically. Publishing uses compare-and-swap on a monotonically
increasing revision, and every revision is preserved. Group members may read
their own group's knowledge; everyone else fails closed.

Caller identity must be resolved by a trusted runtime before using this
adapter. Owner access here is metadata authority, not permission to
impersonate a member.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Collection

from exo.core.groups import GroupStore
from exo.protocol.schema import now_ts

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
MAX_SOURCES = 50
MAX_SOURCE_LEN = 500
MAX_TEXT_LEN = 100_000


@dataclass(frozen=True)
class KnowledgeItem:
    key: str
    revision: int
    text: str
    sources: tuple[str, ...]
    publisher: str
    updated: str


@dataclass(frozen=True)
class KnowledgeRevision:
    key: str
    revision: int
    text: str
    sources: tuple[str, ...]
    publisher: str
    created: str


class RevisionConflict(ValueError):
    """The expected revision did not match the stored one; nothing was written."""


class GroupKnowledge:
    def __init__(self, groups: GroupStore):
        self.groups = groups
        self.store = groups.sessions
        self.store._q("""CREATE TABLE IF NOT EXISTS swarm_knowledge (
            group_id TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL,
            text TEXT NOT NULL, sources TEXT NOT NULL, publisher TEXT NOT NULL,
            updated TEXT NOT NULL, PRIMARY KEY (group_id, key))""")
        self.store._q("""CREATE TABLE IF NOT EXISTS swarm_knowledge_revisions (
            group_id TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL,
            text TEXT NOT NULL, sources TEXT NOT NULL, publisher TEXT NOT NULL,
            created TEXT NOT NULL, PRIMARY KEY (group_id, key, revision))""")

    # -- validation ------------------------------------------------------

    @staticmethod
    def _validate_key(key) -> str:
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise ValueError("invalid knowledge key")
        return key

    @staticmethod
    def _validate_text(text) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("knowledge text is required")
        if len(text) > MAX_TEXT_LEN:
            raise ValueError("knowledge text is too long")
        return text

    @staticmethod
    def _validate_sources(sources) -> tuple[str, ...]:
        if isinstance(sources, (str, bytes)) or not isinstance(sources, Collection):
            raise ValueError("sources must be a collection of references")
        refs = tuple(sources)
        if not refs or len(refs) > MAX_SOURCES:
            raise ValueError("knowledge needs between 1 and 50 source references")
        if any(
            not isinstance(s, str) or not s.strip() or len(s) > MAX_SOURCE_LEN
            for s in refs
        ):
            raise ValueError("invalid source reference")
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate source reference")
        return refs

    # -- authorization ---------------------------------------------------

    def _group(self, group_id: str, owner: str):
        group = self.groups.get(group_id, owner=owner)
        if group is None:
            raise ValueError("no group")
        return group

    def _reader(self, group_id: str, *, owner: str, member: str):
        """Fail closed unless owner matches the group and member belongs to it."""
        group = self._group(group_id, owner)
        if member not in {m.profile for m in group.members}:
            raise ValueError("unknown member")
        return group

    # -- owner publishing ------------------------------------------------

    def publish(
        self,
        group_id: str,
        *,
        owner: str,
        key: str,
        text: str,
        sources: Collection[str],
        expected_revision: int = 0,
    ) -> KnowledgeItem:
        """Owner-only explicit publish with revision compare-and-swap.

        ``expected_revision`` must equal the current revision (0 for a new
        key). A mismatch raises RevisionConflict and stores nothing.
        """
        self._group(group_id, owner)
        key = self._validate_key(key)
        text = self._validate_text(text)
        refs = self._validate_sources(sources)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        created = now_ts()

        def save(db):
            row = db.execute(
                "SELECT revision FROM swarm_knowledge WHERE group_id=? AND key=?",
                (group_id, key),
            ).fetchone()
            current = row["revision"] if row else 0
            if current != expected_revision:
                raise RevisionConflict(
                    f"knowledge {key!r} is at revision {current}, not {expected_revision}"
                )
            revision = current + 1
            encoded = json.dumps(list(refs))
            db.execute(
                "INSERT INTO swarm_knowledge VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (group_id, key) DO UPDATE SET revision=excluded.revision, "
                "text=excluded.text, sources=excluded.sources, "
                "publisher=excluded.publisher, updated=excluded.updated",
                (group_id, key, revision, text, encoded, owner, created),
            )
            db.execute(
                "INSERT INTO swarm_knowledge_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (group_id, key, revision, text, encoded, owner, created),
            )
            return KnowledgeItem(key, revision, text, refs, owner, created)

        return self.store._tx(save)

    # -- member reading --------------------------------------------------

    @staticmethod
    def _item(row) -> KnowledgeItem:
        return KnowledgeItem(
            row["key"],
            row["revision"],
            row["text"],
            tuple(json.loads(row["sources"])),
            row["publisher"],
            row["updated"],
        )

    @staticmethod
    def _rev(row) -> KnowledgeRevision:
        return KnowledgeRevision(
            row["key"],
            row["revision"],
            row["text"],
            tuple(json.loads(row["sources"])),
            row["publisher"],
            row["created"],
        )

    def read(
        self, group_id: str, *, owner: str, member: str, key: str
    ) -> KnowledgeItem | None:
        """A member reads one current item of its own group's knowledge."""
        self._reader(group_id, owner=owner, member=member)
        key = self._validate_key(key)
        rows = self.store._q(
            "SELECT * FROM swarm_knowledge WHERE group_id=? AND key=?", (group_id, key)
        )
        return self._item(rows[0]) if rows else None

    def keys(self, group_id: str, *, owner: str, member: str) -> list[str]:
        """Keys of the group's current knowledge, in key order."""
        self._reader(group_id, owner=owner, member=member)
        return [
            row["key"]
            for row in self.store._q(
                "SELECT key FROM swarm_knowledge WHERE group_id=? ORDER BY key",
                (group_id,),
            )
        ]

    def history(
        self, group_id: str, *, owner: str, member: str, key: str
    ) -> list[KnowledgeRevision]:
        """Every preserved revision of one key, oldest first."""
        self._reader(group_id, owner=owner, member=member)
        key = self._validate_key(key)
        return [
            self._rev(row)
            for row in self.store._q(
                "SELECT * FROM swarm_knowledge_revisions WHERE group_id=? AND key=? ORDER BY revision",
                (group_id, key),
            )
        ]
