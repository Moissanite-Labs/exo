"""Durable, fail-closed execution receipts. No stale-claim takeover or retry.

An active receipt means execution was claimed, not that its process is alive.
After a crash it remains active and blocks another start pending reconciliation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from exo.core.groups import GroupStore
from exo.protocol.schema import new_id, now_ts


@dataclass(frozen=True)
class ExecutionReceipt:
    id: str
    group_id: str
    state: str
    created: str
    finished: str | None
    members: dict[str, str]


class GroupExecutions:
    def __init__(self, groups: GroupStore):
        self.groups = groups
        self.store = groups.sessions
        self.store._q("""CREATE TABLE IF NOT EXISTS swarm_executions (
            group_id TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL, created TEXT NOT NULL, finished TEXT,
            members TEXT NOT NULL)""")

    def _group(self, group_id, owner):
        group = self.groups.get(group_id, owner=owner)
        if group is None:
            raise ValueError("no group")
        return group

    def claim(self, group_id: str, *, owner: str) -> ExecutionReceipt:
        group = self._group(group_id, owner)
        receipt = ExecutionReceipt(new_id("gx"), group_id, "active", now_ts(), None,
                                   {m.profile: "queued" for m in group.members})

        def claim(db):
            if db.execute("SELECT 1 FROM swarm_executions WHERE group_id=?", (group_id,)).fetchone():
                raise ValueError("group execution already claimed; inspect its receipt")
            db.execute("INSERT INTO swarm_executions VALUES (?, ?, ?, ?, ?, ?)",
                       (group_id, receipt.id, receipt.state, receipt.created, None, json.dumps(receipt.members)))
        self.store._tx(claim)
        return receipt

    def get(self, group_id: str, *, owner: str) -> ExecutionReceipt | None:
        self._group(group_id, owner)
        rows = self.store._q("SELECT * FROM swarm_executions WHERE group_id=?", (group_id,))
        if not rows:
            return None
        row = rows[0]
        return ExecutionReceipt(row["id"], row["group_id"], row["state"], row["created"],
                                row["finished"], json.loads(row["members"]))

    def finish(self, group_id: str, *, owner: str, execution_id: str,
               members: dict[str, str]) -> None:
        group = self._group(group_id, owner)
        if (set(members) != {m.profile for m in group.members}
                or any(s not in ("completed", "stopped", "failed") for s in members.values())):
            raise ValueError("terminal status required for every member")
        encoded = json.dumps(dict(members))

        def finish(db):
            row = db.execute("SELECT id, state, members FROM swarm_executions WHERE group_id=?",
                             (group_id,)).fetchone()
            if row is None or row["id"] != execution_id:
                raise ValueError("no execution")
            if row["state"] == "settled":
                if json.loads(row["members"]) != members:
                    raise ValueError("execution already settled with different results")
                return
            db.execute("UPDATE swarm_executions SET state='settled', finished=?, members=? WHERE group_id=?",
                       (now_ts(), encoded, group_id))
        self.store._tx(finish)
