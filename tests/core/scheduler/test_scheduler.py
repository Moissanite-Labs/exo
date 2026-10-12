"""Scheduler: cron parsing, due times with a fake clock, runs with a fake model."""

from __future__ import annotations

import asyncio
import functools
from datetime import datetime

import pytest

from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider
from exo.core.scheduler import Scheduler, handle_schedule_edit, next_run, parse_cron
from exo.protocol import schema as s


def aio(fn):
    @functools.wraps(fn)
    def run(*a, **k):
        return asyncio.run(fn(*a, **k))
    return run


def ts(*a):
    return datetime(*a).timestamp()


def mk(tmp_path, script=None):
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={})
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
    r = await handle_schedule_edit(eng, {"action": "add", "when": "every 1h", "prompt": "make the report", "title": "Report"})
    jid = r["job"]["id"]
    r = await handle_schedule_edit(eng, {"action": "run_now", "job_id": jid})
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

    eng._channels["chat9"] = Chan()
    sch = eng.scheduler = Scheduler(eng)
    sch.launch(sch.add("every 1h", "x", title="T", deliver="chat9"))
    await sch.wait()
    assert got == ["T: the report"] and not bodies(eng, s.Notice)


@aio
async def test_pause_resume(tmp_path):
    eng, clk = mk(tmp_path), Clock(ts(2026, 10, 11, 8, 0))
    sch = eng.scheduler = Scheduler(eng, clk)
    jid = sch.add("every 10m", "x")["id"]
    await handle_schedule_edit(eng, {"action": "pause", "job_id": jid})
    clk.t += 3600
    assert sch.due() == [] and await sch.tick() == []
    r = await handle_schedule_edit(eng, {"action": "resume", "job_id": jid})
    assert not r["job"]["paused"] and r["job"]["next_run"] == clk.t + 600  # no catch-up for the paused time
    assert sch.due() == []
    assert (await handle_schedule_edit(eng, {"action": "remove", "job_id": "nope"}))["error"]


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
    assert (await handle_schedule_edit(eng, {"action": "run_now", "job_id": job["id"]}))["error"]
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
    assert "error" in await handle_schedule_edit(eng, {"action": "dance"})
    assert (await handle_schedule_edit(eng, s.ScheduleEdit(action="list")))["jobs"] == []


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
