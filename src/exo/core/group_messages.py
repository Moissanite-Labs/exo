"""Internal durable group delivery. No agent execution or external action retry.

Caller identity must be resolved by a trusted runtime before using this adapter.
Owner access here is metadata authority, not permission to impersonate a member.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from exo.core.groups import GroupStore
from exo.protocol.schema import new_id, now_ts


@dataclass(frozen=True)
class GroupMessage:
    seq: int
    id: str
    sender: str
    audience: tuple[str, ...]
    text: str
    created: str


class GroupMessages:
    def __init__(self, groups: GroupStore):
        self.groups = groups
        self.store = groups.sessions
        self.store._q("""CREATE TABLE IF NOT EXISTS swarm_messages (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            group_id TEXT NOT NULL, sender TEXT NOT NULL, audience TEXT NOT NULL,
            text TEXT NOT NULL, created TEXT NOT NULL)""")
        self.store._q("""CREATE TABLE IF NOT EXISTS swarm_delivery (
            group_id TEXT NOT NULL, member TEXT NOT NULL, seq INTEGER NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, member, seq))""")

    def _group(self, group_id, owner):
        group = self.groups.get(group_id, owner=owner)
        if group is None:
            raise ValueError("no group")
        return group

    @staticmethod
    def _decode(row):
        return GroupMessage(row["seq"], row["id"], row["sender"],
                            tuple(json.loads(row["audience"])), row["text"], row["created"])

    def send(self, group_id: str, *, owner: str, sender: str,
             audience: list[str], text: str) -> GroupMessage:
        group = self._group(group_id, owner)
        members = {m.profile for m in group.members}
        if sender not in members:
            raise ValueError("unknown sender")
        if (not isinstance(audience, list) or not audience
                or any(not isinstance(p, str) or p not in members for p in audience)
                or len(set(audience)) != len(audience) or sender in audience):
            raise ValueError("invalid audience")
        if group.mode == "conductor":
            if sender != group.conductor and audience != [group.conductor]:
                raise ValueError("workers may only send to the conductor")
            if sender == group.conductor and len(audience) != 1:
                raise ValueError("conductor messages target one worker")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("message text is required")
        mid, created = new_id("gm"), now_ts()

        def save(db):
            seq = db.execute("INSERT INTO swarm_messages "
                             "(id, group_id, sender, audience, text, created) VALUES (?, ?, ?, ?, ?, ?)",
                             (mid, group_id, sender, json.dumps(audience), text, created)).lastrowid
            db.executemany("INSERT INTO swarm_delivery (group_id, member, seq) VALUES (?, ?, ?)",
                           [(group_id, member, seq) for member in audience])
            return GroupMessage(seq, mid, sender, tuple(audience), text, created)
        return self.store._tx(save)

    def pending(self, group_id: str, *, owner: str, member: str, limit: int = 100) -> list[GroupMessage]:
        group = self._group(group_id, owner)
        if member not in {m.profile for m in group.members}:
            raise ValueError("unknown member")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")

        def read(db):
            rows = db.execute("SELECT m.* FROM swarm_messages m JOIN swarm_delivery d ON m.seq=d.seq "
                              "WHERE d.group_id=? AND d.member=? AND d.consumed=0 "
                              "ORDER BY m.seq LIMIT ?", (group_id, member, limit)).fetchall()
            db.executemany("UPDATE swarm_delivery SET delivered=1 WHERE group_id=? AND member=? AND seq=?",
                           [(group_id, member, row["seq"]) for row in rows])
            return [self._decode(row) for row in rows]
        return self.store._tx(read)

    def consume(self, group_id: str, *, owner: str, member: str, message_id: str) -> None:
        self._group(group_id, owner)

        def ack(db):
            row = db.execute("SELECT d.seq, d.delivered FROM swarm_delivery d JOIN swarm_messages m "
                             "ON m.seq=d.seq WHERE d.group_id=? AND d.member=? AND m.id=?",
                             (group_id, member, message_id)).fetchone()
            if row is None or not row["delivered"]:
                raise ValueError("message has not been delivered to this member")
            db.execute("UPDATE swarm_delivery SET consumed=1 WHERE group_id=? AND member=? AND seq=?",
                       (group_id, member, row["seq"]))
        self.store._tx(ack)
