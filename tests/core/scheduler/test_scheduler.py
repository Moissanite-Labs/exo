"""Scheduler: cron parsing, due times with a fake clock, runs with a fake model."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import sqlite3
from datetime import datetime

import pytest

from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider
from exo.core.models.fake import tool_turn
from exo.scheduler import PROMPT, Scheduler, next_run, parse_cron
from exo.core.seats import Seat
from exo.core.types import ToolSpec
from exo.core.agents.delegate import BLOCKED_TOOLS
from exo.protocol import schema as s


def aio(fn):
    @functools.wraps(fn)
    def run(*a, **k):
        return asyncio.run(fn(*a, **k))
    return run


def ts(*a):
    return datetime(*a).timestamp()


def mk(tmp_path, script=None):
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={},
                     attach_scheduler=Scheduler)
    eng.loop.provider = FakeProvider(script or ["the report"], repeat=True)
    return eng


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def bodies(eng, cls):
    return [e.body for e in eng.hub.since(0) if isinstance(e.body, cls)]


def test_cron_parse_fields():
    mi, hr, dom, mon, dow, dset, wset = parse_cron("*/15 9-17/4 1,15 * 1-5")
    assert mi == {0, 15, 30, 45} and hr == {9, 13, 17} and dom == {1, 15}
    assert mon == set(range(1, 13)) and dow == {1, 2, 3, 4, 5} and dset and wset
    assert parse_cron("0 0 * * 7")[4] == {0}
    assert parse_cron("5/20 * * * *")[0] == {5, 25, 45}


@pytest.mark.parametrize("line", ["* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "*/0 * * * *",
                                  "a * * * *", "5-1 * * * *", "* * 31 2 *"])
def test_cron_rejects(line):
    with pytest.raises(ValueError):
        next_run(line, 0)


def test_next_run_cron():
    base = ts(2026, 10, 11, 8, 30)
    assert next_run("0 9 * * *", base) == ts(2026, 10, 11, 9, 0)
    assert next_run("0 9 * * *", ts(2026, 10, 11, 9, 0)) == ts(2026, 10, 12, 9, 0)  # strictly after
    assert next_run("*/10 * * * *", ts(2026, 10, 11, 8, 55)) == ts(2026, 10, 11, 9, 0)
    assert next_run("0 0 1 1 *", base) == ts(2027, 1, 1)
    assert next_run("0 0 29 2 *", base) == ts(2028, 2, 29)
    # 2026-10-11 is a Sunday; weekday 1 is Monday
    assert next_run("0 7 * * 1", base) == ts(2026, 10, 12, 7, 0)
    # day and weekday both set: either one matches
    assert next_run("0 7 15 * 1", base) == ts(2026, 10, 12, 7, 0)


def test_next_run_every():
    assert next_run("every 5m", 1000) == 1300
    assert next_run("every 2h", 0) == 7200
    assert next_run("Every 1d", 0) == 86400
    with pytest.raises(ValueError):
        next_run("every 0m", 0)
    with pytest.raises(ValueError):
        next_run("every m", 0)


def test_add_list_remove_and_due(tmp_path):
    eng, clk = mk(tmp_path), Clock(ts(2026, 10, 11, 8, 0))
    sch = Scheduler(eng, clk)
    job = sch.add("every 10m", "say hi", title="hi")
    assert job["next_run"] == clk.t + 600 and job["tier"] == "fast" and job["deliver"] == "notice"
    assert sch.due() == []
    clk.t += 600
    assert [j["id"] for j in sch.due()] == [job["id"]]
    assert (tmp_path / "home" / "schedule.sqlite").exists()
    sch.remove(job["id"])
    assert sch.list() == []
    with pytest.raises(ValueError):
        sch.add("bad", "x")
    with pytest.raises(ValueError):
        sch.add("every 1m", "x", tier="huge")


@aio
async def test_run_now_emits_task_and_notice(tmp_path):
    eng = mk(tmp_path)
    r = await eng.scheduler.edit({"action": "add", "when": "every 1h", "prompt": "make the report", "title": "Report"})
    jid = r["job"]["id"]
    r = await eng.scheduler.edit({"action": "run_now", "job_id": jid})
    assert r["task_id"]
    await eng.scheduler.wait()
    ev = [t for t in bodies(eng, s.TaskUpdated) if t.task_id == r["task_id"]]
    assert [t.status for t in ev] == ["queued", "running", "done"]
    assert ev[1].child_session and all(t.detail for t in ev)
    run = eng.store.get_session(ev[1].child_session)
    assert (run.agent, run.kind) == (eng.agent, "scheduled")  # a cron run writes into the agent's one log (D-028)
    assert eng.store.last_pos(eng.agent) >= 2
    notes = bodies(eng, s.Notice)
    assert notes and notes[-1].text == "Report: the report" and notes[-1].level == "info"
    job = eng.scheduler.get(jid)
    assert job["last_status"] == "done" and job["last_run"]


@aio
async def test_failed_run_warns(tmp_path):
    eng = mk(tmp_path)
    eng.loop.provider = FakeProvider([])  # script exhausted: the model errors
    eng.loop.max_retries = 0
    sch = eng.scheduler = Scheduler(eng)
    job = sch.add("every 1h", "x", title="Bad")
    sch.launch(job)
    await sch.wait()
    assert bodies(eng, s.TaskUpdated)[-1].status == "failed"
    assert bodies(eng, s.Notice)[-1].level == "warn"
    assert sch.get(job["id"])["last_status"] == "failed"
    assert not sch.loops


@aio
async def test_unknown_channel_falls_back_to_notice_with_warning(tmp_path):
    eng = mk(tmp_path)
    sch = eng.scheduler = Scheduler(eng)
    job = sch.add("every 1h", "x", title="T", deliver="chat9")
    sch.launch(job)
    await sch.wait()
    notes = bodies(eng, s.Notice)
    assert [n.level for n in notes] == ["warn", "info"]


@aio
async def test_channel_delivery(tmp_path):
    eng = mk(tmp_path)
    got = []

    class Chan:
        async def deliver(self, text):
            got.append(text)

    eng.channels["chat9"] = Chan()
    sch = eng.scheduler = Scheduler(eng)
    sch.launch(sch.add("every 1h", "x", title="T", deliver="chat9"))
    await sch.wait()
    assert got == ["T: the report"] and not bodies(eng, s.Notice)


@aio
async def test_pause_resume(tmp_path):
    eng, clk = mk(tmp_path), Clock(ts(2026, 10, 11, 8, 0))
    sch = eng.scheduler = Scheduler(eng, clk)
    jid = sch.add("every 10m", "x")["id"]
    await eng.scheduler.edit({"action": "pause", "job_id": jid})
    clk.t += 3600
    assert sch.due() == [] and await sch.tick() == []
    r = await eng.scheduler.edit({"action": "resume", "job_id": jid})
    assert not r["job"]["paused"] and r["job"]["next_run"] == clk.t + 600  # no catch-up for the paused time
    assert sch.due() == []
    assert (await eng.scheduler.edit({"action": "remove", "job_id": "nope"}))["error"]


@aio
async def test_missed_runs_catch_up_once(tmp_path):
    eng, clk = mk(tmp_path), Clock(ts(2026, 10, 11, 8, 0))
    sch = eng.scheduler = Scheduler(eng, clk)
    jid = sch.add("every 10m", "x")["id"]
    clk.t += 10 * 600  # down for ten slots
    assert await sch.tick() == [jid]
    await sch.wait()
    assert len([t for t in bodies(eng, s.TaskUpdated) if t.status == "queued"]) == 1
    assert await sch.tick() == []
    assert sch.get(jid)["next_run"] == clk.t + 600


@aio
async def test_one_run_at_a_time(tmp_path):
    eng = mk(tmp_path)
    sch = eng.scheduler = Scheduler(eng)
    job = sch.add("every 1h", "x")
    assert sch.launch(job)
    assert sch.launch(job) is None
    assert (await eng.scheduler.edit({"action": "run_now", "job_id": job["id"]}))["error"]
    await sch.wait()
    assert sch.launch(job)
    await sch.wait()


@aio
async def test_start_stop_runs_due_job(tmp_path):
    eng = mk(tmp_path)
    sch = eng.scheduler = Scheduler(eng)
    jid = sch.add("every 1h", "x")["id"]
    sch.db.execute("UPDATE jobs SET next_run=? WHERE id=?", (sch.clock() - 5, jid))
    sch.db.commit()
    sch.start()
    for _ in range(100):
        await asyncio.sleep(0.02)
        if sch.get(jid)["last_status"]:
            break
    await sch.stop()
    assert sch.get(jid)["last_status"] == "done"


@aio
async def test_bad_action(tmp_path):
    eng = mk(tmp_path)
    assert "error" in await eng.scheduler.edit({"action": "dance"})
    assert (await eng.scheduler.edit(s.ScheduleEdit(action="list")))["jobs"] == []


@aio
async def test_loop_survives_a_failing_tick_and_reports_it(tmp_path):
    eng = mk(tmp_path)
    sch = eng.scheduler = Scheduler(eng)
    sch.error_pause = 0.01
    real, calls = sch.tick, []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("bad row")
        return await real()

    sch.tick = flaky
    jid = sch.add("every 1h", "x")["id"]
    sch.db.execute("UPDATE jobs SET next_run=? WHERE id=?", (sch.clock() - 5, jid))
    sch.db.commit()
    sch.start()
    for _ in range(100):
        await asyncio.sleep(0.02)
        if sch.get(jid)["last_status"]:
            break
    await sch.stop()
    assert sch.get(jid)["last_status"] == "done"  # the loop kept going after the first tick raised
    errs = bodies(eng, s.HarnessError)
    assert any(b.where == "scheduler tick" and "bad row" in b.message for b in errs)


@aio
async def test_unexpected_loop_exit_is_reported(tmp_path):
    eng = mk(tmp_path)
    sch = eng.scheduler = Scheduler(eng)

    async def boom():
        raise ValueError("loop bug")

    sch._run_loop = boom
    sch.start()
    await asyncio.sleep(0.05)
    assert any(b.where == "scheduler loop" and "loop bug" in b.message for b in bodies(eng, s.HarnessError))
    assert any("scheduler stopped" in n.text for n in bodies(eng, s.Notice))


@aio
async def test_scheduled_turn_uses_the_normal_turn_path(tmp_path):
    eng = mk(tmp_path)
    seen = []

    class Memory:
        def system_prompt_block(self):
            return "MEMBLOCK"

        def sync_turn(self, user, assistant, session_id=None):
            seen.append(("sync", session_id))

    eng.loop.memory = Memory()
    eng.plugins.hooks.register("on_turn_start", lambda session_id, message: seen.append(("start", session_id)))
    eng.plugins.hooks.register("on_turn_end", lambda session_id: seen.append(("end", session_id)))
    sch = eng.scheduler = Scheduler(eng)
    job = sch.add("every 1h", "make the report", title="R")
    sch.launch(job)
    await sch.wait()
    child = next(t.child_session for t in bodies(eng, s.TaskUpdated) if t.child_session)
    assert [k for k, sid in seen if sid == child] == ["start", "sync", "end"]  # hooks and memory, like any turn


@aio
async def test_scheduled_prompt_refresh_matches_soul_trace_without_changing_main_prompt(tmp_path):
    soul = tmp_path / "SOUL.md"
    soul.write_text("Soul one")
    seat = Seat(name="test-seat", soul="Soul one", soul_path=str(soul))
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                     seat=seat, cwd=str(tmp_path), env={}, attach_scheduler=Scheduler)
    provider = eng.loop.provider = FakeProvider(["report"], repeat=True)
    sch = eng.scheduler
    job = sch.add("every 1h", "report")
    for text in ["Soul one", "Soul two changed", "Soul two changed"]:
        soul.write_text(text)
        sch.launch(job)
        await sch.wait()
        prompt = provider.calls[-1]["messages"][0].text()
        started = bodies(eng, s.TurnStarted)[-1]
        assert text in prompt and prompt.count(PROMPT) == 1
        assert started.soul == hashlib.sha1(text.encode()).hexdigest()[:8]
        assert eng.loop.system_prompt == eng._compose_prompt() and PROMPT not in eng.loop.system_prompt
        assert not {t.name for t in provider.calls[-1]["tools"]} & BLOCKED_TOOLS
        assert not sch.loops
    assert "Soul one" not in provider.calls[-1]["messages"][0].text()
    children = [t.child_session for t in bodies(eng, s.TaskUpdated) if t.status == "running"]
    assert all(eng.store.messages(sid)[0].meta["author"] == "schedule" for sid in children)
    interactive = await eng.loop.create_session(cwd=str(tmp_path))
    await eng.run_turn(s.TurnSend(session=interactive.id, text="hello"))
    assert "Soul two changed" in provider.calls[-1]["messages"][0].text()
    assert PROMPT not in provider.calls[-1]["messages"][0].text()


async def blocked_run(tmp_path, stage):
    """One temporary scheduled turn blocked on fake model, async tool or real core approval future."""
    eng = mk(tmp_path)
    entered, closed = asyncio.Event(), asyncio.Event()
    tool_ran = []

    class BlockedProvider(FakeProvider):
        async def stream(self, messages, tools, **opts):
            entered.set()
            try:
                await asyncio.Event().wait()
                async for ev in super().stream(messages, tools, **opts):
                    yield ev
            finally:
                closed.set()

    async def tool(args, ctx):
        tool_ran.append(1)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    if stage == "model":
        eng.loop.provider = BlockedProvider(["never finished"])
    else:
        eng.registry.register(ToolSpec(name="scheduled_block", description="blocking fake tool", risk="low", toolset="test"), tool)
        eng.loop.provider = FakeProvider([tool_turn("scheduled_block"), "never finished"])
        if stage == "approval":
            def ask(tool_name="", **kw):
                if tool_name == "scheduled_block":
                    entered.set()
                    return {"action": "approve", "message": "may I run?", "rule_key": "scheduled-test"}
            eng.plugins.hooks.register("pre_tool_call", ask)
        else:
            # This fake tool must be explicitly permitted under the fail-closed execution gate.
            eng.plugins.hooks.register("pre_tool_call", lambda **kw: {"action": "allow"})
    sch = eng.scheduler
    job = sch.add("every 1h", "report")
    sch.launch(job)
    await asyncio.wait_for(entered.wait(), 1)
    if stage == "approval":
        async def pending():
            while not eng.approvals.open:
                await asyncio.sleep(0)
        await asyncio.wait_for(pending(), 1)
    sid = next(t.child_session for t in bodies(eng, s.TaskUpdated) if t.status == "running")
    return eng, sch, job, sid, closed, tool_ran


@pytest.mark.parametrize("stage", ["model", "tool", "approval"])
@pytest.mark.parametrize("cancel", [False, True])
@aio
async def test_scheduled_interrupt_and_task_cancellation_reap_all_work(tmp_path, stage, cancel):
    eng, sch, job, sid, closed, tool_ran = await blocked_run(tmp_path, stage)
    assert sid in sch.loops
    if cancel:
        sch.running[job["id"]].cancel()
    else:
        assert await eng._turn_interrupt(s.TurnInterrupt(session=sid)) == {"interrupted": 1}
    await asyncio.wait_for(sch.wait(), 1)
    assert not sch.loops and not sch.running
    assert sch.get(job["id"])["last_status"] == "failed"
    if stage == "approval":
        assert not eng.approvals.open and not tool_ran
        assert bodies(eng, s.ApprovalResolved)[-1].decision == "cancelled"
    else:
        assert closed.is_set()  # deleting the registry entry is insufficient if a model/tool worker survives
    if not cancel:
        assert bodies(eng, s.TurnCompleted)[-1].stop_reason == "interrupted"


@aio
async def test_interrupting_scheduled_child_does_not_interrupt_another_interactive_turn(tmp_path, monkeypatch):
    eng = mk(tmp_path)
    scheduled_entered, interactive_entered = asyncio.Event(), asyncio.Event()

    class Blocked(FakeProvider):
        def __init__(self, entered):
            super().__init__(["never finished"])
            self.entered = entered
        async def stream(self, messages, tools, **opts):
            self.entered.set()
            await asyncio.Event().wait()
            async for ev in super().stream(messages, tools, **opts):
                yield ev

    eng.loop.provider = Blocked(interactive_entered)
    monkeypatch.setattr(eng.agents, "route", lambda tier: (Blocked(scheduled_entered), "fake:scheduled"))
    session = await eng.loop.create_session(cwd=str(tmp_path))
    interactive = asyncio.create_task(eng.run_turn(s.TurnSend(session=session.id, text="hello")))
    await asyncio.wait_for(interactive_entered.wait(), 1)
    sch = eng.scheduler
    sch.launch(sch.add("every 1h", "report"))
    await asyncio.wait_for(scheduled_entered.wait(), 1)
    child = next(t.child_session for t in bodies(eng, s.TaskUpdated) if t.status == "running")
    assert await eng._turn_interrupt(s.TurnInterrupt(session=child)) == {"interrupted": 1}
    await asyncio.wait_for(sch.wait(), 1)
    assert not interactive.done() and eng.loop.state(session.id).running
    assert await eng._turn_interrupt(s.TurnInterrupt(session=session.id)) == {"interrupted": 1}
    assert (await asyncio.wait_for(interactive, 1)).stop_reason == "interrupted"


@aio
async def test_next_run_listing_error_is_reported_and_scheduler_survives(tmp_path, monkeypatch):
    eng = mk(tmp_path)
    sch = eng.scheduler
    sch.error_pause = .01
    calls, real = [], sch.list
    def flaky_list():
        calls.append(1)
        if len(calls) == 2:  # after tick: this used to be outside the recovery try
            raise sqlite3.OperationalError("temporary listing failure")
        return real()
    monkeypatch.setattr(sch, "list", flaky_list)
    job = sch.add("every 1h", "report")
    sch.db.execute("UPDATE jobs SET next_run=? WHERE id=?", (sch.clock() - 1, job["id"]))
    sch.db.commit()
    sch.start()
    try:
        async def recovered():
            while len(calls) < 4 or sch.get(job["id"])["last_status"] != "done":
                await asyncio.sleep(.01)
        await asyncio.wait_for(recovered(), 1)
        assert not sch._main.done()
        assert any(b.where == "scheduler tick" and "temporary listing failure" in b.message
                   for b in bodies(eng, s.HarnessError))
        assert not any(b.where == "scheduler loop" for b in bodies(eng, s.HarnessError))
    finally:
        await sch.stop()


# Two seats in one EXO_HOME share schedule.sqlite: each slot runs once, a seat-bound job only in its seat.

def seat_mk(tmp_path, name, clk):
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={},
                     seat=Seat(name=name), attach_scheduler=lambda e: Scheduler(e, clk))
    eng.loop.provider = FakeProvider([f"report from {name}"], repeat=True)
    return eng


def queued_runs(tmp_path):
    from exo.core.sessions import SessionStore
    evs = SessionStore(tmp_path / "home" / "sessions.sqlite").events_since(0)
    return [e.body for e in evs if isinstance(e.body, s.TaskUpdated) and e.body.status == "queued"]


@aio
async def test_two_timers_on_one_home_claim_a_slot_once(tmp_path, monkeypatch):
    clk = Clock(ts(2026, 10, 11, 8, 0))
    scout, forge = seat_mk(tmp_path, "scout", clk), seat_mk(tmp_path, "forge", clk)
    jid = scout.scheduler.add("every 1h", "make the report")["id"]  # no seat: any one seat runs it
    clk.t += 3600
    due_a, due_b = scout.scheduler.due(), forge.scheduler.due()
    assert [j["id"] for j in due_a] == [jid] == [j["id"] for j in due_b]  # both timers woke for the same slot
    monkeypatch.setattr(scout.scheduler, "due", lambda: due_a)
    monkeypatch.setattr(forge.scheduler, "due", lambda: due_b)
    assert await scout.scheduler.tick() + await forge.scheduler.tick() == [jid]
    await scout.scheduler.wait()
    await forge.scheduler.wait()
    assert len(queued_runs(tmp_path)) == 1
    assert scout.scheduler.get(jid)["next_run"] == clk.t + 3600


@aio
async def test_seat_bound_job_runs_only_in_its_seat(tmp_path):
    clk = Clock(ts(2026, 10, 11, 8, 0))
    scout, forge = seat_mk(tmp_path, "scout", clk), seat_mk(tmp_path, "forge", clk)
    (tmp_path / "home" / "seats").mkdir(exist_ok=True)
    (tmp_path / "home" / "seats" / "scout.toml").write_text('[seat]\nname = "scout"\n')
    jid = forge.scheduler.add("every 1h", "make the report", seat="scout")["id"]  # added from forge, for scout
    clk.t += 3600
    assert forge.scheduler.due() == [] and await forge.scheduler.tick() == []
    r = await forge.scheduler.edit({"action": "run_now", "job_id": jid})
    assert "scout" in r["error"] and "task_id" not in r
    assert await scout.scheduler.tick() == [jid]
    await scout.scheduler.wait()
    assert len(queued_runs(tmp_path)) == 1
    child = [e.body for e in scout.hub.since(0) if isinstance(e.body, s.TaskUpdated) and e.body.child_session]
    assert scout.store.get_session(child[0].child_session).agent == "scout"


@aio
async def test_other_seats_due_job_does_not_spin_the_timer(tmp_path):
    clk = Clock(ts(2026, 10, 11, 8, 0))
    scout, forge = seat_mk(tmp_path, "scout", clk), seat_mk(tmp_path, "forge", clk)
    scout.scheduler.add("every 1h", "make the report", seat="scout")
    clk.t += 3600  # scout's core is down: its job stays past due
    sch, calls = forge.scheduler, []
    real = sch.tick

    async def counted():
        calls.append(1)
        return await real()

    sch.tick = counted
    sch.start()
    try:
        await asyncio.sleep(0.3)
    finally:
        await sch.stop()
    assert 1 <= len(calls) <= 2  # it sleeps; it does not wake every 50 ms for a job it may not run
    assert queued_runs(tmp_path) == []


@aio
async def test_a_job_another_seats_core_adds_starts_without_a_wake(tmp_path):
    clk = Clock(ts(2026, 10, 11, 8, 0))
    scout, forge = seat_mk(tmp_path, "scout", clk), seat_mk(tmp_path, "forge", clk)
    (tmp_path / "home" / "seats").mkdir(exist_ok=True)
    (tmp_path / "home" / "seats" / "scout.toml").write_text('[seat]\nname = "scout"\n')
    sch = scout.scheduler
    sch.poll = 0.05  # 60 s in a real core; the other process's add cannot set this one's wake event
    sch.start()
    try:
        await asyncio.sleep(0.1)  # scout's timer is asleep with no jobs
        jid = forge.scheduler.add("every 1h", "make the report", seat="scout")["id"]
        forge.scheduler.db.execute("UPDATE jobs SET next_run=? WHERE id=?", (clk.t - 1, jid))
        forge.scheduler.db.commit()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if sch.get(jid)["last_status"]:
                break
    finally:
        await sch.stop()
    assert sch.get(jid)["last_status"] == "done"


@aio
async def test_job_paused_between_due_and_claim_does_not_launch(tmp_path, monkeypatch):
    eng, clk = mk(tmp_path), Clock(ts(2026, 10, 11, 8, 0))
    sch = eng.scheduler = Scheduler(eng, clk)
    jid = sch.add("every 1h", "x")["id"]
    clk.t += 3600
    due = sch.due()
    sch.pause(jid)
    monkeypatch.setattr(sch, "due", lambda: due)
    assert await sch.tick() == []
    assert sch.running == {} and queued_runs(tmp_path) == []


@aio
async def test_add_rejects_a_seat_no_core_could_run(tmp_path):
    clk = Clock(ts(2026, 10, 11, 8, 0))
    scout = seat_mk(tmp_path, "scout", clk)
    with pytest.raises(ValueError, match="nosuch"):
        scout.scheduler.add("every 1h", "x", seat="nosuch")
    r = await scout.scheduler.edit({"action": "add", "when": "every 1h", "prompt": "x", "seat": "nosuch"})
    assert "nosuch" in r["error"] and scout.scheduler.list() == []
    assert scout.scheduler.add("every 1h", "x", seat="scout")["seat"] == "scout"  # its own seat
    (tmp_path / "home" / "seats").mkdir(exist_ok=True)
    (tmp_path / "home" / "seats" / "forge.toml").write_text('[seat]\nname = "forge"\n')
    assert scout.scheduler.add("every 1h", "x", seat="forge")["seat"] == "forge"  # a seat file names it
