"""``/group plan_list``: read-only, bounded, owner+group scoped listing of durable plans.

Offline: a temporary EXO_HOME and a temporary SQLite file, no network, no keychain. Plan rows are seeded
straight into the real tables (created by ``PlanExecutions._ensure_schema``) with a known schema.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from exo import principal
from exo.core import group_commands
from exo.core.group_plan_execution import PlanExecutions
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore
from exo.server.engine import CommandFailed

REFUSAL = "not_found: no such plan"
SECRET = "SECRET-RESULT-TEXT"
ERR = "SECRET-ERROR-TEXT"


@pytest.fixture(autouse=True)
def _offline_home(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")


class Room:
    def __init__(self, tmp_path):
        self.store = SessionStore(tmp_path / "sessions.sqlite")
        self.groups = GroupStore(self.store)
        self.gid = self.make_group("host")
        self.eng = SimpleNamespace(agent="host", store=self.store)

    def make_group(self, owner):
        return self.groups.create(owner=owner, mode="socratic", members=["one", "two"],
                                  known_profiles=["one", "two"]).id

    def schema(self):
        PlanExecutions(self.groups)._ensure_schema()

    def plan(self, n, *, gid=None, owner="host", created=None, state="settled", tasks=()):
        pid = f"{n:064x}"
        gid = gid or self.gid
        self.store._q("INSERT INTO swarm_plans(plan_id,group_id,owner,execution_id,state,created,finished)"
                      " VALUES (?,?,?,'execution',?,?,?)",
                      (pid, gid, owner, state, created or f"2026-01-01T00:00:{n:02d}.000Z",
                       None if state == "running" else "2026-02-01T00:00:00.000Z"))
        for i, tstate in enumerate(tasks):
            self.store._q("INSERT INTO swarm_plan_tasks VALUES (?,?,1,?,'one',?,NULL,NULL,?,?)",
                          (pid, f"t{i}", gid, tstate, json.dumps(SECRET), ERR))
        return pid

    def dump(self):
        names = [r[0] for r in self.store._q("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {"names": names,
                **{n: [tuple(r) for r in self.store._q(f"SELECT * FROM {n} ORDER BY 1,2")] for n in names}}

    def tables(self):
        return {r[0] for r in self.store._q("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def room(tmp_path):
    return Room(tmp_path)


def run(eng, raw, who="principal"):
    async def go():
        token = principal.CALLER.set(principal.Caller(who))
        try:
            return await group_commands.command(eng, SimpleNamespace(args=f"plan_list {raw}"))
        finally:
            principal.CALLER.reset(token)
    return asyncio.run(go())


def ask(room, agent=None, who="principal", **over):
    body = {"id": room.gid, **over}
    eng = room.eng if agent is None else SimpleNamespace(agent=agent, store=room.store)
    return run(eng, json.dumps({k: v for k, v in body.items() if v is not ...}), who)


def test_no_plan_tables_gives_empty_list_and_creates_none(room):
    assert "swarm_plans" not in room.tables()
    before = room.dump()
    out = ask(room)
    assert out == {"id": room.gid, "plans": [], "plans_truncated": False}
    assert room.dump() == before
    assert not {"swarm_plans", "swarm_plan_tasks"} & room.tables()


def test_only_plans_table_present_still_empty_counts_and_no_schema_added(room):
    room.store._q(PlanExecutions._SCHEMA[0])
    room.store._q("INSERT INTO swarm_plans(plan_id,group_id,owner,execution_id,state,created)"
                  " VALUES (?,?,'host','e','running','2026-01-01')", ("a" * 64, room.gid))
    out = ask(room)
    assert out["plans"][0]["task_count"] == 0 and "swarm_plan_tasks" not in room.tables()


def test_empty_tables_give_empty_list(room):
    room.schema()
    assert ask(room) == {"id": room.gid, "plans": [], "plans_truncated": False}


def test_two_plans_newest_first_and_ids_roundtrip_plan_status(room):
    room.schema()
    old = room.plan(1, tasks=("completed", "failed", "stopped"))
    new = room.plan(2, state="running", tasks=("running", "pending", "pending"))
    out = ask(room)
    assert [p["plan_id"] for p in out["plans"]] == [new, old] and out["plans_truncated"] is False
    first, second = out["plans"]
    assert set(first) == {"plan_id", "state", "created", "finished", "task_count", "tasks_by_state"}
    assert len(first["plan_id"]) == 64
    assert first["state"] == "running" and first["finished"] is None and first["task_count"] == 3
    assert first["tasks_by_state"] == {"completed": 0, "failed": 0, "pending": 2, "running": 1, "stopped": 0}
    assert second["state"] == "settled" and second["created"] == "2026-01-01T00:00:01.000Z"
    assert second["finished"] == "2026-02-01T00:00:00.000Z"
    assert second["tasks_by_state"] == {"completed": 1, "failed": 1, "pending": 0, "running": 0, "stopped": 1}
    for p in out["plans"]:
        status = asyncio.run(_status(room, p["plan_id"]))
        assert status["plan_id"] == p["plan_id"] and status["task_count"] == p["task_count"]


async def _status(room, plan_id):
    token = principal.CALLER.set(principal.Caller("principal"))
    try:
        return await group_commands.command(
            room.eng, SimpleNamespace(args="plan_status " + json.dumps({"id": room.gid, "plan_id": plan_id})))
    finally:
        principal.CALLER.reset(token)


def test_created_ties_are_ordered_deterministically_by_plan_id_descending(room):
    room.schema()
    ids = [room.plan(n, created="2026-03-03T00:00:00.000Z") for n in (5, 9, 1, 7)]
    out = ask(room)
    assert [p["plan_id"] for p in out["plans"]] == sorted(ids, reverse=True)
    assert out == ask(room)


def test_limit_truncation_and_default(room):
    room.schema()
    ids = [room.plan(n) for n in range(1, 6)]
    newest = ids[::-1]
    out = ask(room, limit=2)
    assert [p["plan_id"] for p in out["plans"]] == newest[:2] and out["plans_truncated"] is True
    out = ask(room, limit=5)
    assert [p["plan_id"] for p in out["plans"]] == newest and out["plans_truncated"] is False
    out = ask(room, limit=4)
    assert len(out["plans"]) == 4 and out["plans_truncated"] is True
    assert len(ask(room)["plans"]) == 5  # default bound is above this fixture


def test_default_and_maximum_limits_bound_the_result(room):
    room.schema()
    for n in range(1, 261):
        room.store._q("INSERT INTO swarm_plans(plan_id,group_id,owner,execution_id,state,created)"
                      " VALUES (?,?,'host','e','settled',?)", (f"{n:064x}", room.gid, f"2026-01-01T{n // 60:02d}:{n % 60:02d}"))
    default = ask(room)
    assert len(default["plans"]) == group_commands._PLANS_LIMIT and default["plans_truncated"] is True
    top = ask(room, limit=group_commands._PLANS_MAX)
    assert len(top["plans"]) == group_commands._PLANS_MAX and top["plans_truncated"] is True
    with pytest.raises(CommandFailed) as err:
        ask(room, limit=group_commands._PLANS_MAX + 1)
    assert err.value.code == "bad_request"


def test_sql_is_bounded_not_fetch_all(room, monkeypatch):
    room.schema()
    for n in range(1, 31):
        room.plan(n, tasks=("completed",))
    seen = []
    real = room.store._q
    monkeypatch.setattr(room.store, "_q", lambda sql, params=(): (seen.append((sql, params)), real(sql, params))[1])
    out = ask(room, limit=3)
    assert len(out["plans"]) == 3
    plan_select = [s for s, _ in seen if "FROM swarm_plans" in s]
    assert plan_select and all("LIMIT ?" in s for s in plan_select)
    assert all("result" not in s and "error" not in s for s, _ in seen if "swarm_plan_tasks" in s)
    counts = [(s, p) for s, p in seen if "FROM swarm_plan_tasks" in s]
    assert len(counts) == 1 and len(counts[0][1]) == 1 + 3  # group + exactly the returned plans


def test_group_and_owner_isolation(room):
    room.schema()
    mine = room.plan(1, tasks=("completed",))
    other_group = room.make_group("host")
    room.plan(2, gid=other_group)
    room.plan(3, owner="mallory")  # a row stamped with another owner in my group is never listed
    foreign = room.make_group("mallory")
    room.plan(4, gid=foreign, owner="mallory")
    assert [p["plan_id"] for p in ask(room)["plans"]] == [mine]
    assert len(ask(room, id=other_group)["plans"]) == 1
    # Caller "mallory" owns the foreign group and sees only that group's plan.
    seen = ask(room, agent="mallory", id=foreign)["plans"]
    assert [p["plan_id"] for p in seen] == [f"{4:064x}"]
    for gid, agent in ((foreign, None), (room.gid, "mallory"), ("g-nope", None)):
        with pytest.raises(CommandFailed) as err:
            ask(room, agent=agent, id=gid)
        assert str(err.value) == REFUSAL and err.value.code == "not_found"


def test_unknown_and_foreign_are_indistinguishable_and_leave_no_schema(room):
    foreign = room.make_group("mallory")
    refusals = set()
    for gid in ("g-nope", foreign):
        with pytest.raises(CommandFailed) as err:
            ask(room, id=gid)
        refusals.add((err.value.code, err.value.message, str(err.value), err.value.retryable))
    assert len(refusals) == 1 and "swarm_plans" not in room.tables()


def test_output_never_carries_result_or_error_text(room):
    room.schema()
    room.plan(1, tasks=("completed", "failed"))
    text = json.dumps(ask(room))
    assert SECRET not in text and ERR not in text
    for key in ("result", "error", "tasks\"", "profile", "task_id"):
        assert key not in text


def test_read_only_rows_unchanged(room):
    room.schema()
    room.plan(1, tasks=("completed", "failed"))
    room.plan(2, state="running", tasks=("pending",))
    before = room.dump()
    ask(room)
    ask(room, limit=1)
    assert room.dump() == before


@pytest.mark.parametrize("raw", [
    "", "[]", "not-json", "{}", '{"plan_id": "%s"}' % ("a" * 64), '{"id": 5}', '{"id": " "}',
    '{"id": "%s"}' % ("g" * 129), '{"id": "g", "extra": 1}', '{"id": "g", "plan_id": "%s"}' % ("a" * 64),
    '{"id": "g", "limit": 0}', '{"id": "g", "limit": -1}', '{"id": "g", "limit": 201}',
    '{"id": "g", "limit": true}', '{"id": "g", "limit": "5"}', '{"id": "g", "limit": 1.5}',
    '{"id": "g", "limit": null}',
])
def test_bad_arguments_are_bad_request_before_any_store_access(raw):
    class Boom:
        def __getattr__(self, name):
            raise AssertionError(f"store touched: {name}")
    with pytest.raises(CommandFailed) as err:
        run(SimpleNamespace(agent="host", store=Boom()), raw)
    assert err.value.code == "bad_request"


def test_non_principal_is_forbidden_before_any_access():
    with pytest.raises(CommandFailed) as err:
        run(SimpleNamespace(agent="host"), "{}", who="local")
    assert err.value.code == "forbidden"


def test_usage_text_lists_plan_list():
    with pytest.raises(CommandFailed) as err:
        asyncio.run(_unknown())
    assert "plan_list" in err.value.message


async def _unknown():
    token = principal.CALLER.set(principal.Caller("principal"))
    try:
        return await group_commands.command(SimpleNamespace(agent="host"), SimpleNamespace(args="nope {}"))
    finally:
        principal.CALLER.reset(token)
