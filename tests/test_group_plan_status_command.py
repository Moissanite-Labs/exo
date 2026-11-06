"""``/group plan_status``: read-only, owner-scoped view of durable plan task state.

Offline only: real CoreEngine members, the fake provider and a real PlanExecutions store (the rig of
``test_group_plan_real_engine_e2e``). No host is injected on the command engine, no network, no live provider.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_group_plan_real_engine_e2e import BOUND, MODELS, PROFILES, run_scenario

from exo import principal
from exo.core import group_commands
from exo.server.engine import CommandFailed


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    for name in (*PROFILES, "owner"):
        (home / "seats" / f"{name}.toml").write_text(
            f'name = "{name}"\n', encoding="utf-8"
        )
    (home / "config.json").write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    monkeypatch.setenv("EXO_HOME", str(home))
    return home


REFUSAL = "not_found: no such plan"


def engine(rig, agent=None):
    """The command's engine: seat name and sessions DB only. No ``_group_host`` is set."""
    return SimpleNamespace(agent=agent or rig.owner, store=rig.launch.store)


async def status(rig, args, who="principal", agent=None):
    token = principal.CALLER.set(principal.Caller(who))
    try:
        return await group_commands.command(
            engine(rig, agent), SimpleNamespace(args=f"plan_status {args}")
        )
    finally:
        principal.CALLER.reset(token)


def arg(rig, **over):
    body = {"id": rig.gid, "plan_id": rig.plan.plan_id, **over}
    return json.dumps({k: v for k, v in body.items() if v is not ...})


def by_task(out):
    return {t["task_id"]: t for t in out["tasks"]}


def test_settled_two_task_plan_in_topological_order(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return await status(rig, arg(rig))

    rig, _, out = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    assert (
        out["state"] == "settled"
        and out["id"] == rig.gid
        and out["plan_id"] == rig.plan.plan_id
    )
    assert [t["task_id"] for t in out["tasks"]] == ["build", "check"]
    assert [t["state"] for t in out["tasks"]] == ["completed", "completed"]
    assert by_task(out)["build"]["result"] == "BUILD-REPLY-7"
    assert by_task(out)["check"]["result"] == "CHECK-REPLY-9"
    assert (
        by_task(out)["build"]["profile"] == "builder"
        and by_task(out)["build"]["attempt"] == 1
    )
    assert not any(t["result_truncated"] or t["error_truncated"] for t in out["tasks"])
    assert (
        out["task_count"] == 2
        and out["tasks_returned"] == 2
        and out["tasks_truncated"] is False
    )
    assert "untrusted" in out["untrusted"]
    for forbidden in ("goal", "prompt", "grants", "write_scopes", "permits"):
        assert forbidden not in json.dumps(out)


def test_running_plan_shows_pending_and_running_not_complete(
    home, tmp_path, monkeypatch
):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(provider.reached.wait(), BOUND)
        out = await status(rig, arg(rig))
        rig.host.stop(rig.gid)
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return out

    _, _, out = run_scenario(home, tmp_path, monkeypatch, "block", body)
    assert out["state"] == "running"
    tasks = by_task(out)
    assert tasks["build"]["state"] == "running" and tasks["build"]["result"] is None
    assert tasks["check"]["state"] == "pending"
    assert all(t["state"] != "completed" for t in out["tasks"])


def test_provider_failure_reports_failed_and_dependency_failed(
    home, tmp_path, monkeypatch
):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return await status(rig, arg(rig))

    _, _, out = run_scenario(home, tmp_path, monkeypatch, "fail", body)
    tasks = by_task(out)
    assert out["state"] == "settled"
    assert (
        tasks["build"]["state"] == "failed"
        and tasks["build"]["error"] == "engine turn did not complete"
    )
    assert (
        tasks["check"]["state"] == "failed"
        and tasks["check"]["error"] == "dependency build failed"
    )
    assert "PROVIDER-SECRET-BOOM" not in json.dumps(out)


def test_stopped_group_reports_stopped_with_error_text(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(provider.reached.wait(), BOUND)
        rig.host.stop(rig.gid, profile="builder")
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        return await status(rig, arg(rig))

    _, _, out = run_scenario(home, tmp_path, monkeypatch, "block", body)
    tasks = by_task(out)
    assert tasks["build"]["state"] == "stopped"
    assert (
        tasks["check"]["state"] == "failed"
        and tasks["check"]["error"] == "dependency build stopped"
    )


def test_unknown_foreign_owner_and_foreign_group_are_indistinguishable(
    home, tmp_path, monkeypatch
):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        other = rig.host.create(mode="socratic", members=PROFILES)
        refusals = {}
        cases = {
            "unknown_plan": (arg(rig, plan_id="0" * 64), None),
            "unknown_group": (arg(rig, id="g-nope"), None),
            "other_group_real_plan": (arg(rig, id=other.id), None),
            "foreign_owner": (arg(rig), "mallory"),
        }
        for name, (args, agent) in cases.items():
            with pytest.raises(CommandFailed) as err:
                await status(rig, args, agent=agent)
            refusals[name] = (
                err.value.code,
                err.value.message,
                str(err.value),
                err.value.retryable,
            )
        return refusals

    _, _, refusals = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    assert len(set(refusals.values())) == 1
    assert str(CommandFailed(*next(iter(refusals.values()))[:2])) == REFUSAL


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        "not-json",
        "{}",
        '{"id": "g"}',
        '{"plan_id": "%s"}' % ("a" * 64),
        '{"id": "g", "plan_id": "%s", "extra": 1}' % ("a" * 64),
        '{"id": 5, "plan_id": "%s"}' % ("a" * 64),
        '{"id": " ", "plan_id": "%s"}' % ("a" * 64),
        '{"id": "%s", "plan_id": "%s"}' % ("g" * 129, "a" * 64),
        '{"id": "g", "plan_id": 7}',
        '{"id": "g", "plan_id": null}',
        '{"id": "g", "plan_id": "%s"}' % ("a" * 63),
        '{"id": "g", "plan_id": "%s"}' % ("a" * 65),
        '{"id": "g", "plan_id": "%s"}' % ("A" * 64),
        '{"id": "g", "plan_id": "%s"}' % ("g" * 64),
        '{"id": "g", "plan_id": "%s"}' % ("a" * 10000),
        '{"id": "g", "plan_id": "%s", "limit": 0}' % ("a" * 64),
        '{"id": "g", "plan_id": "%s", "limit": 201}' % ("a" * 64),
        '{"id": "g", "plan_id": "%s", "limit": true}' % ("a" * 64),
        '{"id": "g", "plan_id": "%s", "limit": "5"}' % ("a" * 64),
    ],
)
def test_bad_arguments_are_bad_request_before_any_store_access(raw):
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("store touched before validation")

    eng = SimpleNamespace(agent="one", store=Boom())
    token = principal.CALLER.set(principal.Caller("principal"))
    try:
        with pytest.raises(CommandFailed) as err:
            asyncio.run(
                group_commands.command(eng, SimpleNamespace(args=f"plan_status {raw}"))
            )
    finally:
        principal.CALLER.reset(token)
    assert err.value.code == "bad_request"


def test_non_principal_refused_before_any_access():
    eng = SimpleNamespace(
        agent="one"
    )  # no store at all: any access would raise AttributeError
    token = principal.CALLER.set(principal.Caller("local"))
    try:
        with pytest.raises(CommandFailed) as err:
            asyncio.run(
                group_commands.command(eng, SimpleNamespace(args="plan_status {}"))
            )
    finally:
        principal.CALLER.reset(token)
    assert err.value.code == "forbidden"


def test_output_is_bounded_and_truncation_is_flagged(home, tmp_path, monkeypatch):
    big = "x" * 20_000

    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        rig.launch.store._q(
            "UPDATE swarm_plan_tasks SET result=?, error=?",
            (json.dumps(big), "e" * 1000),
        )
        full = await status(rig, arg(rig))
        limited = await status(rig, arg(rig, limit=1))
        return full, limited

    _, _, (full, limited) = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    for t in full["tasks"]:
        assert len(t["result"]) == 4000 and t["result_truncated"] is True
        assert len(t["error"]) == 256 and t["error_truncated"] is True
    assert len(json.dumps(full)) < 40_000
    assert limited["tasks_returned"] == 1 and limited["task_count"] == 2
    assert limited["tasks_truncated"] is True and [
        t["task_id"] for t in limited["tasks"]
    ] == ["build"]


@pytest.mark.parametrize(
    ("total", "error", "result_cap", "error_cap"),
    [
        (
            40,
            "e" * 1000,
            600,
            200,
        ),  # long errors: error cap is a quarter of the 800-character share
        (
            40,
            "dependency build failed",
            777,
            23,
        ),  # short errors leave the rest of the share to the result
        (200, "e" * 1000, 120, 40),  # the row limit: 160-character share per task
    ],
)
def test_aggregate_budget_is_shared_by_result_and_error(
    home, tmp_path, monkeypatch, total, error, result_cap, error_cap
):
    seeded = 2  # the two real plan rows, build and check

    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        for i in range(total - seeded):
            rig.launch.store._q(
                "INSERT INTO swarm_plan_tasks VALUES (?, ?, 1, ?, ?, 'completed', ?, ?, NULL, NULL)",
                (
                    rig.plan.plan_id,
                    f"t{i:03d}" + "k" * 59,
                    rig.gid,
                    "p" * 64,
                    "2026-01-01T00:00:00.000Z",
                    "2026-01-01T00:00:01.000Z",
                ),
            )
        rig.launch.store._q(
            "UPDATE swarm_plan_tasks SET result=?, error=?",
            (json.dumps("r" * 5000), error),
        )
        return await status(rig, arg(rig, limit=200))

    _, _, out = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    tasks = out["tasks"]
    assert (
        out["task_count"] == out["tasks_returned"] == total
        and out["tasks_truncated"] is False
    )
    assert {(len(t["result"]), len(t["error"])) for t in tasks} == {
        (result_cap, min(len(error), error_cap))
    }
    assert all(
        t["result_truncated"] and t["error_truncated"] == (len(error) > error_cap)
        for t in tasks
    )
    assert sum(len(t["result"]) + len(t["error"]) for t in tasks) <= 32_000
    # Identifiers are finite by contract and are never clipped. JSON overhead is on top of the text budget.
    assert all(len(t["task_id"]) <= 64 and len(t["profile"]) <= 64 for t in tasks)
    text = sum(len(t["result"]) + len(t["error"]) for t in tasks)
    assert len(json.dumps(out)) - text <= 450 * total + 600


def test_no_host_needed_and_start_stays_forbidden(home, tmp_path, monkeypatch):
    async def body(rig, provider):
        rig.start()
        await asyncio.wait_for(rig.host.wait(rig.gid), BOUND)
        eng = engine(rig)
        assert getattr(eng, "_group_host", None) is None
        token = principal.CALLER.set(principal.Caller("principal"))
        try:
            out = await group_commands.command(
                eng, SimpleNamespace(args=f"plan_status {arg(rig)}")
            )
            with pytest.raises(CommandFailed) as err:
                await group_commands.command(
                    eng,
                    SimpleNamespace(
                        args=f'start {{"id": "{rig.gid}", "audiences": {{"builder": ["checker"]}}}}'
                    ),
                )
        finally:
            principal.CALLER.reset(token)
        return out, err.value.code

    _, _, (out, code) = run_scenario(home, tmp_path, monkeypatch, "ok", body)
    assert out["state"] == "settled" and code == "forbidden"


def test_never_planned_database_refuses_and_creates_no_plan_tables(
    home, tmp_path, monkeypatch
):
    async def body(rig, provider):
        assert rig.plan_tables() == set()
        with pytest.raises(CommandFailed) as err:
            await status(rig, arg(rig))
        with pytest.raises(CommandFailed) as err2:
            await status(rig, arg(rig, id="g-nope"))
        return rig.plan_tables(), str(err.value), str(err2.value)

    _, _, (tables, first, second) = run_scenario(
        home, tmp_path, monkeypatch, "ok", body
    )
    assert tables == set()
    assert first == second == REFUSAL


# -- liveness diagnosis: read-only, on-disk sqlite, no engine -----------------------------------------
PLAN = "ab" * 32
LIVE_TABLES = ("swarm_plans", "swarm_plan_tasks", "swarm_supervision_plans", "swarm_supervision_tasks",
               "swarm_supervision_attempts", "swarm_supervision_requests")


class Room:
    def __init__(self, tmp_path, *, tasks=(), durable=False, plan_state="running"):
        from exo.core.group_attempt_ledger import AttemptLedger, Scope, TaskKey
        from exo.core.group_plan_execution import PlanExecutions
        from exo.core.group_supervision import TaskIdentity
        from exo.core.groups import GroupStore
        from exo.core.sessions import SessionStore
        self.store = SessionStore(tmp_path / "sessions.sqlite")
        groups = GroupStore(self.store)
        self.gid = groups.create(owner="host", mode="socratic", members=["one", "two"],
                                 known_profiles=["one", "two"]).id
        self.keys = {}
        if tasks:
            PlanExecutions(groups)._ensure_schema()
            self.store._q("INSERT INTO swarm_plans(plan_id,group_id,owner,execution_id,state,created)"
                          " VALUES (?,?,'host','execution',?,'fixture')", (PLAN, self.gid, plan_state))
            for tid in tasks:
                self.store._q("INSERT INTO swarm_plan_tasks(plan_id,task_id,attempt,group_id,profile,state)"
                              " VALUES (?,?,1,?,'one','pending')", (PLAN, tid, self.gid))
            scope = Scope(self.gid, "host", PLAN, "execution")
            self.keys = {t: TaskKey(scope, TaskIdentity(PLAN, t, "one")) for t in tasks}
            self.ledger = AttemptLedger(groups)
            if durable:
                self.ledger.initialize(scope, plan_digest="d", request_id="init",
                                       tasks=tuple(k.identity for k in self.keys.values()))

    def legacy(self, tid, state):
        self.store._q("UPDATE swarm_plan_tasks SET state=? WHERE task_id=?", (state, tid))

    def settle(self):
        self.store._q("UPDATE swarm_plans SET state='settled'")

    def advance(self, tid, *, dispatch=False, uncertain=False):
        res = self.ledger.reserve_initial(self.keys[tid], holder_id="h", request_id=f"r-{tid}")
        if dispatch or uncertain:
            self.ledger.mark_dispatch(res.fence, request_id=f"d-{tid}")
        if uncertain:
            self.ledger.mark_uncertain(res.fence, reason="owner_lost", request_id=f"u-{tid}")

    def snapshot(self):
        names = [r[0] for r in self.store._q("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out = {"master": [tuple(r) for r in self.store._q("SELECT type,name,sql FROM sqlite_master ORDER BY name")]}
        for n in names:
            if n in LIVE_TABLES:
                out[n] = [tuple(r) for r in self.store._q(f"SELECT * FROM {n} ORDER BY 1,2")]
        return out

    def ask(self, **over):
        eng = SimpleNamespace(agent="host", store=self.store)
        body = json.dumps({"id": self.gid, "plan_id": PLAN, **over})

        async def go():
            token = principal.CALLER.set(principal.Caller("principal"))
            try:
                return await group_commands.command(eng, SimpleNamespace(args=f"plan_status {body}"))
            finally:
                principal.CALLER.reset(token)
        return asyncio.run(go())


@pytest.fixture
def room_factory(tmp_path):
    made = []

    def make(**kw):
        made.append(Room(tmp_path, **kw))
        return made[-1]
    yield make
    for r in made:
        r.store.close()


def test_running_plan_without_ledger_is_unknown_and_unlabelled(room_factory):
    room = room_factory(tasks=("a", "b"))
    room.legacy("a", "running")
    before = room.snapshot()
    out = room.ask()
    assert room.snapshot() == before
    assert out["state"] == "running" and out["liveness"]["status"] == "unknown"
    assert out["durable"] == {"status": "none"}
    assert out["reconcile_counts"] == {"in_flight_or_lost": 0, "uncertain": 0, "never_started": 0,
                                       "terminal": 0, "unlabelled": 2}
    assert all("reconcile" not in t for t in out["tasks"])  # legacy per-task shape is unchanged
    hint = out["liveness"]["reconcile_hint"]
    assert "another process" in hint and "Do not restart" in hint and "terminal" in hint
    assert "No tool reconciles" in hint


def test_durable_counts_cover_every_label_once(room_factory):
    room = room_factory(tasks=("t1", "t2", "t3", "t4", "t5", "t6", "t7"), durable=True)
    room.advance("t2")                                  # reserved, pending -> never_started
    room.advance("t3", dispatch=True)                   # running + intent -> in_flight_or_lost
    room.legacy("t3", "running")
    room.advance("t4", dispatch=True)                   # pending + intent -> uncertain (inspect)
    room.advance("t5", uncertain=True)                  # completed + uncertain -> uncertain
    room.legacy("t5", "completed")
    room.advance("t6", dispatch=True)                   # completed + intent -> terminal
    room.legacy("t6", "completed")
    room.advance("t7", uncertain=True)                  # running + uncertain -> uncertain
    room.legacy("t7", "running")
    before = room.snapshot()
    out = room.ask()
    assert room.snapshot() == before
    counts = out["reconcile_counts"]
    assert counts == {"in_flight_or_lost": 1, "uncertain": 3, "never_started": 2, "terminal": 1,
                      "unlabelled": 0}
    assert sum(counts.values()) == out["task_count"] == 7
    labels = {t["task_id"]: t["reconcile"] for t in out["tasks"]}
    assert labels["t1"] == "not_admitted" and labels["t2"] == "reserved"
    assert labels["t3"] == "in_flight_or_lost" and labels["t6"] == "settled"
    assert out["state"] == "running" and out["liveness"]["status"] == "unknown"


def test_settled_plan_reports_settled(room_factory):
    room = room_factory(tasks=("a",), plan_state="settled")
    room.legacy("a", "completed")
    before = room.snapshot()
    out = room.ask()
    assert room.snapshot() == before
    assert out["liveness"]["status"] == "settled" and out["state"] == "settled"
    assert out["reconcile_counts"]["unlabelled"] == 1


def test_counts_cover_all_tasks_when_limit_truncates(room_factory):
    names = tuple(f"t{i}" for i in range(6))
    room = room_factory(tasks=names, durable=True)
    for n in names[3:]:
        room.advance(n, dispatch=True)
        room.legacy(n, "running")
    out = room.ask(limit=1)
    assert out["tasks_returned"] == 1 and out["tasks_truncated"] is True and out["task_count"] == 6
    assert out["reconcile_counts"] == {"in_flight_or_lost": 3, "uncertain": 0, "never_started": 3,
                                       "terminal": 0, "unlabelled": 0}


def test_unknown_state_fails_closed_and_wording_is_neutral(room_factory):
    room = room_factory(tasks=("a",), plan_state="mystery")
    out = room.ask()
    assert out["liveness"]["status"] == "unknown"
    text = json.dumps(out["liveness"]).lower()
    for word in ("orphan", "dead", "safe to", "inactive", "takeover"):
        assert word not in text


def test_refusal_on_database_without_plan_tables_writes_nothing(room_factory):
    room = room_factory()
    before = room.snapshot()
    with pytest.raises(CommandFailed) as err:
        room.ask()
    assert str(err.value) == REFUSAL
    assert room.snapshot() == before
    assert not {"swarm_plans", "swarm_supervision_plans"} & set(before)


def test_missing_ledger_row_outside_page_nulls_counts_not_zeroes(room_factory):
    room = room_factory(tasks=("a", "b", "c"), durable=True)
    room.advance("a", uncertain=True)
    room.legacy("a", "completed")
    room.settle()
    room.store._q("DELETE FROM swarm_supervision_tasks WHERE task_id='c'")  # fixture damage, not via the command
    before = room.snapshot()
    out = room.ask(limit=1)
    assert room.snapshot() == before
    assert out["tasks_returned"] == 1 and out["task_count"] == 3 and out["tasks_truncated"] is True
    assert out["durable"] == {"status": "unavailable"}
    assert out["reconcile_counts"] is None  # not {"unlabelled": 3} and not a hidden uncertain=0
    assert out["liveness"]["status"] == "settled"
    assert out["tasks"][0]["reconcile"] == "terminal_outcome_uncertain"  # readable task keeps its label
    # the damaged task is on the page when it is asked for: still no counts, no label for it
    full = room.ask()
    assert full["reconcile_counts"] is None
    assert "reconcile" not in {t["task_id"]: t for t in full["tasks"]}["c"]


def test_unexpected_ledger_exception_nulls_counts_and_stays_read_only(room_factory, monkeypatch):
    from exo.core.group_attempt_ledger import AttemptLedger

    def boom(self, key):
        raise RuntimeError("disk gone")
    room = room_factory(tasks=("a", "b"), durable=True)
    before = room.snapshot()
    monkeypatch.setattr(AttemptLedger, "read", boom)
    out = room.ask()
    assert room.snapshot() == before
    assert out["durable"] == {"status": "unavailable"}
    assert out["reconcile_counts"] is None
    assert [t["task_id"] for t in out["tasks"]] == ["a", "b"]
    assert all("reconcile" not in t for t in out["tasks"])


def test_absent_ledger_is_legacy_not_unreadable(room_factory):
    room = room_factory(tasks=("a", "b"))
    out = room.ask(limit=1)
    assert out["durable"] == {"status": "none"}
    assert out["reconcile_counts"] == {"in_flight_or_lost": 0, "uncertain": 0, "never_started": 0,
                                       "terminal": 0, "unlabelled": 2}


def test_settled_plan_with_uncertain_task_is_not_reported_clean(room_factory):
    room = room_factory(tasks=("a", "b"), durable=True)
    room.advance("a", uncertain=True)
    room.legacy("a", "completed")
    room.advance("b", dispatch=True)
    room.legacy("b", "completed")
    room.settle()
    before = room.snapshot()
    out = room.ask()
    assert room.snapshot() == before
    assert out["liveness"]["status"] == "settled"
    assert out["reconcile_counts"]["uncertain"] == 1 and out["reconcile_counts"]["terminal"] == 1
    assert "not that every task outcome is known" in out["liveness"]["reconcile_hint"]


_MISMATCH_CASES = {
    "M1_execution_id": ("UPDATE swarm_supervision_plans SET execution_id='other-exec'", False, False),
    "M2_owner": ("UPDATE swarm_supervision_plans SET owner='someone-else'", False, False),
    "M3_group_id": ("UPDATE swarm_supervision_plans SET group_id='other-grp'", False, False),
    "M4_missing_plan_row": ("DELETE FROM swarm_supervision_plans", True, False),
    "M5_settled_execution_id": ("UPDATE swarm_supervision_plans SET execution_id='x'", False, True),
}


@pytest.mark.parametrize("sql,fk_off,settled", list(_MISMATCH_CASES.values()), ids=list(_MISMATCH_CASES))
def test_ledger_mismatch_is_unavailable_never_legacy_none(room_factory, sql, fk_off, settled):
    room = room_factory(tasks=("a", "b", "c"), durable=True)
    room.advance("a", uncertain=True)
    if fk_off:
        room.store._q("PRAGMA foreign_keys=OFF")
    room.store._q(sql)  # deliberate fixture corruption, not via the command
    if settled:
        room.settle()
    before = room.snapshot()
    assert before["swarm_supervision_tasks"]  # the uncertain task's ledger rows still exist
    out = room.ask()
    assert room.snapshot() == before  # read-only after corruption
    assert out["durable"] == {"status": "unavailable"}
    assert out["reconcile_counts"] is None
    assert all("reconcile" not in t for t in out["tasks"])
    assert out["liveness"]["status"] == ("settled" if settled else "unknown")
