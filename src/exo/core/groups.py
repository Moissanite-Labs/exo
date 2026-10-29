"""Saved Swarm definitions, not an executor or a message-sharing authority.

Membership and mode are immutable. Each member gets a fresh private session so
saving a group never publishes an existing conversation or imports agent history.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Collection

from exo.core.seats import ID_RE
from exo.core.sessions import SessionStore
from exo.protocol.schema import new_id, now_ts


@dataclass(frozen=True)
class GroupMember:
    profile: str
    session: str


@dataclass(frozen=True)
class SavedGroup:
    id: str
    owner: str
    mode: str
    conductor: str | None
    members: tuple[GroupMember, ...]
    created: str


class GroupStore:
    """Internal storage adapter. Callers supply trusted owner and profile catalog.

    No protocol command or agent tool exposes this adapter. A future runtime must
    authorize profile execution and message audiences separately from metadata.
    """

    def __init__(self, sessions: SessionStore):
        self.sessions = sessions
        sessions._q("""CREATE TABLE IF NOT EXISTS swarm_groups (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, mode TEXT NOT NULL,
            conductor TEXT, members TEXT NOT NULL, created TEXT NOT NULL
        )""")

    @staticmethod
    def _decode(row) -> SavedGroup:
        return SavedGroup(row["id"], row["owner"], row["mode"], row["conductor"],
                          tuple(GroupMember(**m) for m in json.loads(row["members"])), row["created"])

    def create(self, *, owner: str, mode: str, members: Collection[str],
               known_profiles: Collection[str], conductor: str | None = None) -> SavedGroup:
        if not isinstance(owner, str) or not ID_RE.fullmatch(owner):
            raise ValueError("invalid group owner")
        if mode not in ("socratic", "conductor"):
            raise ValueError("unknown group mode")
        if isinstance(members, (str, bytes)):
            raise ValueError("members must be a collection of profile IDs")
        profiles = tuple(members)
        if len(profiles) < 2 or any(not isinstance(p, str) or not ID_RE.fullmatch(p) for p in profiles):
            raise ValueError("a group needs at least two valid profile IDs")
        if len(set(profiles)) != len(profiles):
            raise ValueError("duplicate group member")
        if any(p not in known_profiles for p in profiles):
            raise ValueError("unknown group profile")
        if mode == "conductor" and conductor not in profiles:
            raise ValueError("conductor must be a group member")
        if mode == "socratic" and conductor is not None:
            raise ValueError("socratic groups have no conductor")
        group = SavedGroup(new_id("g"), owner, mode, conductor,
                           tuple(GroupMember(p, new_id("s")) for p in profiles), now_ts())

        def save(db):
            for member in group.members:
                db.execute("INSERT INTO sessions (id, title, created, updated, agent, seat, kind)"
                           " VALUES (?, ?, ?, ?, ?, ?, 'private')",
                           (member.session, "Group " + group.id, group.created, group.created,
                            member.profile, member.profile))
            db.execute("INSERT INTO swarm_groups VALUES (?, ?, ?, ?, ?, ?)",
                       (group.id, owner, mode, conductor,
                        json.dumps([{"profile": m.profile, "session": m.session} for m in group.members]),
                        group.created))
        self.sessions._tx(save)
        return group

    def get(self, group_id: str, *, owner: str) -> SavedGroup | None:
        rows = self.sessions._q("SELECT * FROM swarm_groups WHERE id=? AND owner=?", (group_id, owner))
        return self._decode(rows[0]) if rows else None

    def listing(self, *, owner: str) -> list[SavedGroup]:
        return [self._decode(row) for row in self.sessions._q(
            "SELECT * FROM swarm_groups WHERE owner=? ORDER BY created, id", (owner,))]
