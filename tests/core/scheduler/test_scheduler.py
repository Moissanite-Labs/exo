"""Scheduler: cron parsing, due times with a fake clock, runs with a fake model."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import sqlite3
import time
from datetime import datetime

import pytest

from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider
from exo.core.models.fake import tool_turn
from exo.scheduler import PROMPT, Scheduler, next_run, parse_cron
from exo.core.seats import Seat
from exo.core.types import ProviderEvent, ToolSpec
from exo.core.agents.delegate import BLOCKED_TOOLS
from exo.protocol import schema as s
from exo import principal as pr
from exo.server.app import Dispatcher
from exo.server.engine import CommandFailed


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


async def edit(scheduler, body):
    caller = pr.Caller("principal:test")
    token = pr.CALLER.set(caller)
    try:
        return await scheduler.edit(body)
    finally:
        caller.live = False
        pr.CALLER.reset(token)


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


# ---------------------------------------------------------------- daylight saving: cron fields are local time

# zone -> (the day clocks go back, the day they go forward, the hour that day skips); 01:00-01:59 repeats in both
DST = {"America/New_York": ((2026, 11, 1), (2026, 3, 8), 2), "Europe/London": ((2026, 10, 25), (2026, 3, 29), 1)}


@pytest.fixture(params=sorted(DST))
def dst(request):
    """Run in a zone with daylight saving, then put the old zone back (only TZ: the autouse env stays)."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is POSIX only")
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("TZ", request.param)
            time.tzset()
            fall = DST[request.param][0]
            if ts(*fall, 1, 30) == datetime(*fall, 1, 30, fold=1).timestamp():
                pytest.skip(f"no time zone data for {request.param}")  # not a silent pass
            yield DST[request.param]
    finally:
        time.tzset()


def test_next_run_is_strictly_after_across_dst(dst):
    """In the repeated hour's second pass next_run gave the first pass's time, up to an hour in the past."""
    wrong = []
    for day in dst[:2]:
        after, end = ts(*day, 0, 0), ts(*day, 4, 0)
        while after < end:
            for line in ("*/15 * * * *", "30 1 * * *", "50 * * * *", "0 2 * * *", "30 2 * * *", "* * * * *"):
                if not after < next_run(line, after) <= after + 2 * 86400:
                    wrong.append((line, datetime.fromtimestamp(after)))
            after += 37
    assert not wrong, f"{len(wrong)} wrong, first {wrong[:3]}"


def test_next_run_exact_times_across_dst(dst):
    (y, mo, d), spring, gap = dst
    second = datetime(y, mo, d, 1, 20, fold=1).timestamp()  # 01:20 in the repeated hour's second pass
    assert next_run("*/15 * * * *", second) == datetime(y, mo, d, 1, 30, fold=1).timestamp() == second + 600
    assert next_run("30 1 * * *", second) == second + 600
    assert next_run("50 * * * *", second) == second + 1800
    assert next_run("* * * * *", second) == second + 60
    assert next_run("*/15 * * * *", ts(y, mo, d, 1, 20)) == ts(y, mo, d, 1, 30)  # the first pass as before
    # a fixed-time job that ran in the first pass does not run again in the second
    assert next_run("30 1 * * *", ts(y, mo, d, 1, 31)) == ts(y, mo, d + 1, 1, 30)
    # a */n job that ran late in the first pass skips the second (docs/SCHEDULE.md)
    assert next_run("*/15 * * * *", ts(y, mo, d, 1, 50)) == ts(y, mo, d, 2, 0) == ts(y, mo, d, 1, 50) + 70 * 60
    # clocks going forward: a slot in the skipped hour runs an hour later
    before = ts(*spring, gap - 1, 50)
    assert next_run(f"30 {gap} * * *", before) == ts(*spring, gap + 1, 30) == before + 40 * 60
    assert next_run("*/15 * * * *", before) == ts(*spring, gap + 1, 0) == before + 600


@aio
async def test_job_in_the_repeated_hour_runs_once(tmp_path, dst):
    """Added, resumed or overdue in the second pass, a job got a past next_run and ran back to back for an hour."""
    eng, clk = mk(tmp_path), Clock(datetime(*dst[0], 1, 20, fold=1).timestamp())
    sch = Scheduler(eng, clk)
    job = sch.add("*/15 * * * *", "x")
    assert job["next_run"] == clk.t + 600
    sch.pause(job["id"])
    assert sch.pause(job["id"], False)["next_run"] == clk.t + 600
    sch.db.execute("UPDATE jobs SET next_run=? WHERE id=?", (clk.t - 5, job["id"]))  # overdue, as after a wake
    sch.db.commit()
    assert await sch.tick() == [job["id"]]
    await sch.wait()
    assert sch.get(job["id"])["next_run"] == clk.t + 600
    assert await sch.tick() == []  # not relaunched as soon as the run ended


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
    r = await edit(eng.scheduler, {"action": "add", "when": "every 1h", "prompt": "make the report", "title": "Report"})
    jid = r["job"]["id"]
    r = await edit(eng.scheduler, {"action": "run_now", "job_id": jid})
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


@pytest.mark.parametrize("caller", ["local", "unknown", "seat:helper", "channel", "stdio"])
@pytest.mark.parametrize("action", ["add", "pause", "resume", "run_now", "remove"])
@aio
async def test_schedule_mutations_need_principal_and_leave_jobs_untouched(tmp_path, caller, action):
    eng = mk(tmp_path)
    sch = eng.scheduler
    job = sch.add("every 1h", "original job")
    before = sch.list()
    try:
        result = await Dispatcher(eng, caller=caller).run(s.Command.of(s.ScheduleEdit(
            action=action, job_id=job["id"], when="every 1m", prompt="replacement")))
        assert not result.ok and result.error.code == "forbidden"
        assert sch.list() == before and not sch.running
        assert not bodies(eng, s.TaskUpdated)
        listing = await Dispatcher(eng, caller=caller).run(s.Command.of(s.ScheduleEdit(action="list")))
        assert listing.ok and listing.result["jobs"] == before
    finally:
        await eng.close()


@pytest.mark.parametrize("caller", ["principal:tui", "principal:web"])
@aio
async def test_principal_can_manage_every_schedule_action(tmp_path, caller):
    eng = mk(tmp_path)
    d = Dispatcher(eng, caller=caller)

    async def edit(action, **kw):
        result = await d.run(s.Command.of(s.ScheduleEdit(action=action, **kw)))
        assert result.ok, result.error
        return result.result

    try:
        job = (await edit("add", when="every 1h", prompt="the report"))["job"]
        assert (await edit("list"))["jobs"] == [job]
        assert (await edit("pause", job_id=job["id"]))["job"]["paused"]
        assert not (await edit("resume", job_id=job["id"]))["job"]["paused"]
        assert (await edit("run_now", job_id=job["id"]))["task_id"]
        await eng.scheduler.wait()
        assert eng.scheduler.get(job["id"])["last_status"] == "done"
        assert (await edit("remove", job_id=job["id"]))["removed"] == job["id"]
        assert (await edit("list"))["jobs"] == []
    finally:
        await eng.close()


@pytest.mark.parametrize("stamped", [False, True])
@pytest.mark.parametrize("direct", [False, True])
@aio
async def test_schedule_mutation_refuses_missing_or_expired_principal(tmp_path, stamped, direct):
    eng = mk(tmp_path)
    token = pr.CALLER.set(pr.Caller("principal:tui", live=False) if stamped else None)
    try:
        method = eng.scheduler.edit if direct else eng._schedule_edit
        with pytest.raises(CommandFailed, match="forbidden"):
            await method(s.ScheduleEdit(action="add", when="every 1h", prompt="report"))
        assert eng.scheduler.list() == []
        assert await eng._schedule_edit(s.ScheduleEdit(action="list")) == {"jobs": []}
    finally:
        pr.CALLER.reset(token)
        await eng.close()


@pytest.mark.parametrize("name", sorted(BLOCKED_TOOLS))
@aio
async def test_scheduled_model_cannot_call_a_blocked_tool_even_when_gate_allows(tmp_path, name):
    eng = mk(tmp_path, [tool_turn(name, call_id="blocked"), "report"])
    ran, gate = [], []
    eng.registry.register(ToolSpec(name=name, description="blocked tool", risk="low", toolset="test"),
                          lambda args, ctx: ran.append(name) or "should not run", replace=True)
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: gate.append(kw["tool_name"]) or {"action": "allow"})
    try:
        sch = eng.scheduler
        assert await sch.run(sch.add("every 1h", "report"), "test") == "done"
        sid = next(t.child_session for t in bodies(eng, s.TaskUpdated) if t.status == "running")
        [result] = [r for m in eng.store.messages(sid) for r in m.tool_results]
        assert result.call_id == "blocked" and result.is_error
        assert "not available to this scheduled run" in result.text()
        assert name not in {t.name for t in eng.loop.provider.calls[0]["tools"]}
        assert not ran and not gate
    finally:
        await eng.close()


@pytest.mark.parametrize("already_registered", [False, True])
@aio
async def test_scheduled_tool_allowlist_is_fixed_before_the_run(tmp_path, already_registered):
    eng = mk(tmp_path, [tool_turn("enable_tool", call_id="enable"),
                        tool_turn("later_tool", call_id="late"), "report"])
    ran, gate = [], []
    enabled = False

    def install():
        eng.registry.register(ToolSpec(name="later_tool", description="late tool", risk="low", toolset="test"),
                              lambda args, ctx: ran.append("later_tool") or "late", check_fn=lambda: enabled)

    if already_registered:
        install()

    def enable(args, ctx):
        nonlocal enabled
        ran.append("enable_tool")
        enabled = True
        if not already_registered:
            install()
        return "enabled"

    eng.registry.register(ToolSpec(name="enable_tool", description="allowed tool", risk="low", toolset="test"), enable)
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: gate.append(kw["tool_name"]) or {"action": "allow"})
    try:
        sch = eng.scheduler
        assert await sch.run(sch.add("every 1h", "report"), "test") == "done"
        sid = next(t.child_session for t in bodies(eng, s.TaskUpdated) if t.status == "running")
        results = [r for m in eng.store.messages(sid) for r in m.tool_results]
        assert [(r.call_id, r.is_error) for r in results] == [("enable", False), ("late", True)]
        assert ran == gate == ["enable_tool"]
        assert all("later_tool" not in {t.name for t in call["tools"]} for call in eng.loop.provider.calls)
    finally:
        await eng.close()


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
async def test_capped_run_reports_its_wrap_up(tmp_path, monkeypatch):
    import exo.scheduler

    monkeypatch.setattr(exo.scheduler, "Loop", functools.partial(exo.scheduler.Loop, max_iterations=2))
    eng = mk(tmp_path, [tool_turn("no_such_tool", call_id="c1"), tool_turn("no_such_tool", call_id="c2"),
                        "did half the report; next: the totals"])
    sch = eng.scheduler = Scheduler(eng)
    sch.launch(sch.add("every 1h", "x", title="R"))
    await sch.wait()
    assert bodies(eng, s.TaskUpdated)[-1].status == "failed"
    note = bodies(eng, s.Notice)[-1]
    assert note.level == "warn" and "stopped early (max_iterations)" in note.text
    assert note.text.endswith("did half the report; next: the totals")  # the job's status carries the report


@pytest.mark.parametrize("last, wrap_up", [
    (ProviderEvent(kind="error", error="HTTP 400: bad request", retryable=False), True),  # failed, no text
    (tool_turn("no_such_tool", call_id="c3"), True),  # only a stray tool call
    ("unused", False),  # wrap-up off
])
@aio
async def test_capped_run_without_a_wrap_up_reports_no_tool_round_text(tmp_path, monkeypatch, last, wrap_up):
    import exo.scheduler

    monkeypatch.setattr(exo.scheduler, "Loop",
                        functools.partial(exo.scheduler.Loop, max_iterations=2, wrap_up=wrap_up))
    eng = mk(tmp_path, [tool_turn("no_such_tool", call_id="c1", text="Opening the ledger."),
                        tool_turn("no_such_tool", call_id="c2", text="Now the totals sheet."), last])
    sch = eng.scheduler = Scheduler(eng)
    sch.launch(sch.add("every 1h", "x", title="R"))
    await sch.wait()
    note = bodies(eng, s.Notice)[-1]
    assert "stopped early (max_iterations)" in note.text
    assert "totals sheet" not in note.text and "ledger" not in note.text  # a mid-task line is not the report


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
    await edit(eng.scheduler, {"action": "pause", "job_id": jid})
    clk.t += 3600
    assert sch.due() == [] and await sch.tick() == []
    r = await edit(eng.scheduler, {"action": "resume", "job_id": jid})
    assert not r["job"]["paused"] and r["job"]["next_run"] == clk.t + 600  # no catch-up for the paused time
    assert sch.due() == []
    assert (await edit(eng.scheduler, {"action": "remove", "job_id": "nope"}))["error"]


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
    assert (await edit(eng.scheduler, {"action": "run_now", "job_id": job["id"]}))["error"]
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
    assert "error" in await edit(eng.scheduler, {"action": "dance"})
    assert (await edit(eng.scheduler, s.ScheduleEdit(action="list")))["jobs"] == []


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
async def test_stop_ends_the_loop_when_a_wake_comes_in_the_same_pass(tmp_path):
    """On Python 3.11, wait_for drops a cancel when the event it waits on is set in the same pass. The loop checks
    for the cancel itself, so stop() still ends it, and a slot that came due is not claimed."""
    eng = mk(tmp_path)
    sch, clock = eng.scheduler, Clock(ts(2026, 10, 11, 8, 0))
    sch.clock = clock
    job = sch.add("every 1h", "x")
    sch.start()
    await asyncio.sleep(0)  # the loop now waits on its wake event
    main = sch.tasks()[0]
    clock.t = job["next_run"] + 1

    async def stop_in_the_waking_pass():
        sch._wake.set()
        await sch.stop()  # its cancel goes out in the pass that set the wake

    stopping = asyncio.ensure_future(stop_in_the_waking_pass())
    done, _ = await asyncio.wait({stopping}, timeout=10)
    if not done:  # leave nothing running for asyncio.run to wait on
        main.cancel()
        stopping.cancel()
    assert done, "stop() did not end the scheduler's loop"
    assert main.cancelled() and sch.running == {}
    assert sch.get(job["id"])["next_run"] == job["next_run"]  # left for the next start


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


# ---------------------------------------------------------------- the run's session state is the engine's

async def live_run(tmp_path, **kw):
    """A scheduled run held inside its first tool call until ``gate`` is set: (engine, run session, gate)."""
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={},
                     attach_scheduler=Scheduler, **kw)
    eng.loop.provider = FakeProvider([tool_turn("scheduled_hold"), "scheduled done", "next turn"], repeat=True)
    entered, gate = asyncio.Event(), asyncio.Event()

    async def hold(args, ctx):
        entered.set()
        await gate.wait()
        return "held result"

    eng.registry.register(ToolSpec(name="scheduled_hold", description="held fake tool", risk="low", toolset="test"), hold)
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: {"action": "allow"})
    eng.scheduler.launch(eng.scheduler.add("every 1h", "report"))
    await asyncio.wait_for(entered.wait(), 2)
    return eng, next(t.child_session for t in bodies(eng, s.TaskUpdated) if t.status == "running"), gate


async def settle(eng):
    await asyncio.wait_for(eng.scheduler.wait(), 2)
    while eng._tasks:
        await asyncio.wait_for(asyncio.gather(*list(eng._tasks)), 2)


def turns(eng, sid):
    return [type(e.body).__name__ for e in eng.hub.since(0, sid) if isinstance(e.body, (s.TurnStarted, s.TurnCompleted))]


def one_result_per_call(eng, sid):
    """Each tool call has exactly one result, in the message right after it, and none is a made-up 'not run'."""
    msgs = eng.store.messages(sid)
    for i, m in enumerate(msgs):
        if m.tool_calls:
            assert [r.call_id for r in msgs[i + 1].tool_results] == [c.id for c in m.tool_calls]
    results = [r for m in msgs for r in m.tool_results]
    assert len(results) == sum(len(m.tool_calls) for m in msgs)
    assert not any("not run" in r.text() for r in results)
    return msgs


@aio
async def test_running_scheduled_session_is_live_and_a_steer_lands_in_its_turn(tmp_path):
    """The run's own Loop shares the engine's session state: session.list shows it live, the peer and job targets
    see it running, a turn.send steer lands in the run's turn, and session.resume leaves its open call alone."""
    eng, sid, gate = await live_run(tmp_path)
    listed = (await eng._session_list(s.SessionList()))["sessions"]
    assert [r["status"] for r in listed if r["id"] == sid] == ["live"]
    assert eng._peer_target.state(sid) == eng.jobs.target.state(sid) == "running"
    got = await eng._session_resume(s.SessionResume(session=sid))  # a second window opens the run's thread
    assert not [m for m in got["messages"] if m["role"] == "tool"]
    out = await eng._turn_send(s.TurnSend(session=sid, text="also check the logs", mode="steer"))
    assert out["steered"] and out["message_id"]
    gate.set()
    await settle(eng)
    assert turns(eng, sid) == ["TurnStarted", "TurnCompleted"]  # one turn: the steer did not start another
    msgs = one_result_per_call(eng, sid)
    steer = next(m for m in msgs if m.text() == "also check the logs")
    assert msgs[msgs.index(steer) - 1].role == "tool" and steer.meta["turn_id"] == msgs[0].meta["turn_id"]


@aio
async def test_turn_send_to_a_running_scheduled_session_waits_for_the_run(tmp_path):
    """A plain send queues behind the run on the one per-session lock: never a second turn at the same time."""
    eng, sid, gate = await live_run(tmp_path)
    assert await eng._turn_send(s.TurnSend(session=sid, text="and then this")) == {"accepted": True}
    await asyncio.sleep(0.05)
    assert turns(eng, sid) == ["TurnStarted"]
    gate.set()
    await settle(eng)
    assert turns(eng, sid) == ["TurnStarted", "TurnCompleted"] * 2
    msgs = one_result_per_call(eng, sid)
    assert [m.text() for m in msgs if m.role == "assistant" and m.text()] == ["scheduled done", "next turn"]


@pytest.mark.parametrize("wake", [True, False])
@aio
async def test_job_notice_for_a_running_scheduled_run_is_steered_in(tmp_path, wake):
    """A background job the run started ends mid-run: its notice is steered in after the tool result. It is not
    stored between a call and its result (wake off), and it does not start a second turn (wake on)."""
    eng, sid, gate = await live_run(tmp_path, jobs={"wake": wake})
    t = time.time()
    receipt = await eng.jobs.path.submit(eng.jobs._notice("j_test", "done", 0, "echo hi", t, t, sid))
    assert receipt.mode == "steered"
    gate.set()
    await settle(eng)
    assert turns(eng, sid) == ["TurnStarted", "TurnCompleted"]
    msgs = one_result_per_call(eng, sid)
    notice = next(i for i, m in enumerate(msgs) if m.meta.get("job"))
    assert msgs[notice - 1].role == "tool"


@pytest.mark.parametrize("user_running", [True, False])
@aio
async def test_an_unaddressed_peer_message_never_picks_a_running_run(tmp_path, user_running):
    """A run reads as running, yet a peer message with no session goes to the user's thread, idle or running: the
    run would answer with the job's prompt and narrowed tools and send the reply to the job's deliver target."""
    from exo.core.ingress import Ingress

    eng, sid, gate = await live_run(tmp_path)
    target, ing = eng._peer_target, Ingress("peer", "x", "trusted", "hi")
    assert target.state(sid) == "running"
    assert target.pick_session(ing) is None  # only the run is live: no thread is picked
    user = (await eng.loop.create_session(title="mine")).id
    if user_running:
        eng.loop.state(user).running = True  # stands in for a user turn held in a tool call
    assert sid not in {r["id"] for r in target.live_sessions()}
    assert target.pick_session(ing) == user
    assert target.pick_session(Ingress("peer", "x", "trusted", "hi", session=sid)) == sid  # addressed: still the run
    eng.loop.state(user).running = False
    gate.set()
    await settle(eng)


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
    r = await edit(forge.scheduler, {"action": "run_now", "job_id": jid})
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
    r = await edit(scout.scheduler, {"action": "add", "when": "every 1h", "prompt": "x", "seat": "nosuch"})
    assert "nosuch" in r["error"] and scout.scheduler.list() == []
    assert scout.scheduler.add("every 1h", "x", seat="scout")["seat"] == "scout"  # its own seat
    (tmp_path / "home" / "seats").mkdir(exist_ok=True)
    (tmp_path / "home" / "seats" / "forge.toml").write_text('[seat]\nname = "forge"\n')
    assert scout.scheduler.add("every 1h", "x", seat="forge")["seat"] == "forge"  # a seat file names it
