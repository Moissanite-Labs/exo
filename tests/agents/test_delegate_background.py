"""Opt-in background delegation on the standalone core: an immediate task id, a tracked run, a notice on completion.

Fake models only: no network, no model calls, no config, auth or security change. Each test drives the real engine,
the real ``delegate`` tool, the real ingress path and the real ``tasks`` table; only the provider is a fake.
"""

from __future__ import annotations

import asyncio
import time

from exo import provenance, subagents
from exo.core.agents.delegate import SPEC
from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider, tool_turn
from exo.protocol import schema as s


class Keyed:
    """Fake provider keyed by the latest user message: the first rule whose key it contains gives the steps, and
    the number of assistant replies since that message picks the step. An ``asyncio.Event`` step holds the call
    until it is set, so a test can keep a child open while the parent's turn ends."""

    name = "fake"

    def __init__(self, rules: list) -> None:
        self.rules = rules
        self.calls: list[dict] = []

    async def stream(self, messages, tools, **opts):
        users = [i for i, m in enumerate(messages) if m.role == "user"]
        text = messages[users[-1]].text() if users else ""
        step = sum(1 for m in messages[users[-1] + 1:] if m.role == "assistant") if users else 0
        self.calls.append({"user": text, "tools": sorted(t.name for t in tools)})
        spec = "fallback"
        for key, steps in self.rules:
            if key in text:
                spec = steps[min(step, len(steps) - 1)]
                break
        if isinstance(spec, asyncio.Event):
            await spec.wait()  # a barrier step: the child holds here, then says what follows it
            spec = steps[min(step + 1, len(steps) - 1)]
        async for ev in FakeProvider([spec]).stream(messages, tools):
            yield ev


def engine(tmp_path, rules: list, **cfg) -> CoreEngine:
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={},
                     **cfg)
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    eng.loop.provider = Keyed(rules)
    return eng


async def until(pred, timeout: float = 10.0):
    """Poll until ``pred`` is truthy and return its value; the failure names the condition that never held."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if value := pred():
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


def tool_text(eng: CoreEngine, sid: str) -> str:
    msgs = [m for m in eng.store.messages(sid) if m.role == "tool" and m.tool_results]
    return msgs[-1].tool_results[0].text() if msgs else ""


def row(eng: CoreEngine, task_id: str) -> dict:
    return next((r for r in eng.agents.list() if r["task_id"] == task_id), {})


def notices(eng: CoreEngine, sid: str) -> list:
    return [m for m in eng.store.messages(sid) if "[delegate notice" in m.text()]


BG = {"goal": "CHILD-B measure the config loader", "role": "builder", "background": True}


def test_background_returns_at_once_and_its_notice_lands_in_the_parents_session(tmp_path):
    child_up = asyncio.Event()
    rules = [("PARENT", [tool_turn("delegate", BG), "parent finished while the child ran"]),
             ("SECOND", ["read the notice"]),
             ("[delegate notice", ["acknowledged the notice"]),
             ("CHILD-B", [child_up, "the loader is in config.py"])]
    eng = engine(tmp_path, rules)

    async def main():
        sid = (await eng.loop.create_session(title="parent")).id
        res = await eng.loop.send(sid, "PARENT start")

        # 1. the tool returned while its child was still gated: the parent's turn ended, the task did not
        assert res.stop_reason == "end" and res.message.text() == "parent finished while the child ran"
        task_id = await until(lambda: next(iter(eng.agents.bg), None))
        assert task_id.startswith("k_") and task_id in tool_text(eng, sid)  # the call returned its task id
        assert tool_text(eng, sid).startswith("Started task ") and "keep working" in tool_text(eng, sid)
        assert row(eng, task_id)["status"] in ("queued", "running")
        assert notices(eng, sid) == []  # nothing is claimed before the child has finished
        assert row(eng, task_id)["parent"] == sid

        # 2. the child finishes; its receipt reaches the parent through the harness's own notice path
        child_up.set()
        got = await until(lambda: notices(eng, sid))
        assert len(got) == 1 and task_id in got[0].text() and "the loader is in config.py" in got[0].text()
        assert "builder" in got[0].text() and "this is its result, not the user" in got[0].text()
        assert provenance.author_of(got[0]) == provenance.DELEGATE  # never the principal's voice
        assert "|" not in got[0].text().split("\n", 1)[1].split("\n", 1)[1]  # English, never the packet

        # 3. the run was tracked the whole way, on the same rows and events the synchronous path uses
        assert await until(lambda: row(eng, task_id)["status"] == "done")
        assert row(eng, task_id)["receipt"] and row(eng, task_id)["child_session"]
        assert await until(lambda: not eng.agents.bg) and not eng.agents.loops
        evs = [e for e in eng.hub.since(0) if isinstance(e.body, s.TaskUpdated) and e.body.task_id == task_id]
        assert [e.body.status for e in evs] == ["queued", "running", "done"]
        assert all(e.session == sid for e in evs)  # board events stay on the parent's session

        # 4. the parent model reads it: woken on its own (idle, no client watching) or with the next turn
        await eng.loop.send(sid, "SECOND go")
        assert any("[delegate notice" in c["user"] for c in eng.loop.provider.calls)

    asyncio.run(main())


def test_a_background_child_stays_stoppable_by_its_parent(tmp_path):
    child_up = asyncio.Event()
    rules = [("PARENT", [tool_turn("delegate", {"goal": "CHILD-C slow work", "role": "builder",
                                               "background": True}), "parent finished"]),
             ("[delegate notice", ["acknowledged the notice"]),
             ("CHILD-C", [child_up, tool_turn("glob", {"pattern": "*.nothing"}), "child finished anyway"])]
    eng = engine(tmp_path, rules)

    async def main():
        sid = (await eng.loop.create_session(title="parent")).id
        await eng.loop.send(sid, "PARENT start")
        task_id = await until(lambda: next(iter(eng.agents.bg), None))
        live = await until(lambda: r if (r := row(eng, task_id))["status"] == "running" else None)
        assert live["child_session"] in eng.agents.loops  # the same registry the synchronous path stops

        stopped = await subagents.stop(eng, task_id, by=sid)  # the existing control plane, unchanged
        assert stopped["stopped"] is True and stopped["child_session"] == live["child_session"]
        child_up.set()  # the child reaches its next round, where the stop takes effect
        assert await until(lambda: row(eng, task_id)["status"] != "running")
        assert row(eng, task_id)["status"] == "cancelled"  # a no-op stop cannot pass as an ordinary finish
        assert "stopped by parent" in row(eng, task_id)["detail"]  # and the receipt says who stopped it
        assert await until(lambda: not eng.agents.bg) and not eng.agents.loops  # nothing outlives its task
        assert await until(lambda: notices(eng, sid))  # a stopped task still reports, once
        assert len(notices(eng, sid)) == 1

    asyncio.run(main())


def test_a_queued_background_task_is_stoppable_before_it_starts(tmp_path):
    """Blocker 3: a task waiting for a concurrency slot has no loop, so ``subagent stop`` cancels its run."""
    hold, child_up = asyncio.Event(), asyncio.Event()
    rules = [("PARENT", [tool_turn("delegate", {"goal": "CHILD-Q first", "role": "builder", "background": True}),
                         tool_turn("delegate", {"goal": "CHILD-R second", "role": "builder", "background": True}),
                         "parent done"]),
             ("[delegate notice", ["acknowledged the notice"]),
             ("CHILD-Q", [hold, "first result"]),
             ("CHILD-R", [child_up, "second result"])]
    eng = engine(tmp_path, rules, delegation={"max_concurrent": 1})  # one slot: the second task waits

    async def main():
        sid = (await eng.loop.create_session(title="parent")).id
        await eng.loop.send(sid, "PARENT start")
        first, second = list(eng.agents.bg)
        assert row(eng, first)["status"] == "running" and row(eng, second)["status"] == "queued"
        assert row(eng, second)["child_session"] is None and not eng.agents.loops or set(eng.agents.loops) == {
            row(eng, first)["child_session"]}  # nothing will ever interrupt the queued one: no child of its own yet
        stopped = await subagents.stop(eng, second, by=sid)  # the old path answered "nothing to stop" here
        assert stopped["stopped"] is True and stopped["child_session"] is None
        assert row(eng, second)["status"] == "cancelled" and "stopped by parent" in row(eng, second)["detail"]
        assert second not in eng.agents.bg  # its run is gone, not merely hidden
        await until(lambda: any(second in m.text() for m in notices(eng, sid)))  # and it reported once
        hold.set()  # the running task is untouched by that stop
        assert await until(lambda: row(eng, first)["status"] == "done")
        child_up.set()
        assert row(eng, first)["detail"] and "stopped by" not in row(eng, first)["detail"]
        assert [n.text().count("[delegate notice") for n in notices(eng, sid)] == [1, 1]
        assert await until(lambda: not eng.agents.bg) and not eng.agents.loops

    asyncio.run(main())


def test_a_background_run_that_cannot_start_reports_a_failure_instead_of_vanishing(tmp_path):
    """Blocker 2: routing (or setup) raising yields a failed row, a ``task.updated`` and one notice, not a stuck row."""
    eng = engine(tmp_path, [("[delegate notice", ["acknowledged the notice"])])

    def broken(tier: str):
        raise RuntimeError("no route for that tier")

    async def main():
        sid = (await eng.loop.create_session(title="parent")).id
        eng.agents.route = broken  # what an unusable router would do, after the call already returned an id
        b = eng.agents._brief(sid, 0, {"goal": "CHILD-X unreachable", "role": "builder"}, str(tmp_path))
        eng.agents._background(sid, [b])
        assert b.id in eng.agents.bg  # the tool answered at once, as usual
        got = await until(lambda: notices(eng, sid))
        assert len(got) == 1 and b.id in got[0].text() and "no route for that tier" in got[0].text()
        assert row(eng, b.id)["status"] == "failed"  # never left queued for fail_stale to find on restart
        assert "RuntimeError" in row(eng, b.id)["detail"]
        evs = [e.body.status for e in eng.hub.since(0)
               if isinstance(e.body, s.TaskUpdated) and e.body.task_id == b.id]
        assert evs == ["queued", "failed"]  # the row it was queued on is closed, never left to look started
        assert "running" not in evs
        assert await until(lambda: not eng.agents.bg)

    asyncio.run(main())


def test_a_notice_wakes_no_turn_when_the_operator_turned_job_wakes_off(tmp_path):
    """Blocker 1: the wake rule is the operator's ``jobs.wake`` (plus open approvals), as for a job notice."""
    child_up = asyncio.Event()
    rules = [("PARENT", [tool_turn("delegate", BG), "parent finished while the child ran"]),
             ("[delegate notice", ["acknowledged the notice"]),
             ("CHILD-B", [child_up, "the loader is in config.py"])]
    eng = engine(tmp_path, rules, jobs={"wake": False})

    async def main():
        sid = (await eng.loop.create_session(title="parent")).id
        await eng.loop.send(sid, "PARENT start")
        task_id = await until(lambda: next(iter(eng.agents.bg), None))
        calls = len(eng.loop.provider.calls)
        child_up.set()
        got = await until(lambda: notices(eng, sid))  # kept for the next turn instead of starting one
        assert len(got) == 1 and task_id in got[0].text() and "the loader is in config.py" in got[0].text()
        assert eng.jobs.cfg.wake is False  # the operator's setting is what decided it
        assert not eng.loop.state(sid).running and len(eng.loop.provider.calls) == calls  # no turn was started
        assert eng.agents.notices.path.held == {}  # and nothing is waiting on an approval
        res = await eng.loop.send(sid, "SECOND go")  # the notice waited for the next turn, where it is read
        assert res.stop_reason == "end" and len(notices(eng, sid)) == 1

    asyncio.run(main())


def test_background_is_opt_in_complete_and_never_half_set(tmp_path):
    assert "background" in SPEC.parameters["properties"] and "`background`" in SPEC.description
    mixed = {"tasks": [{"goal": "CHILD-D one", "background": True}, {"goal": "CHILD-E two"}]}
    rules = [("PARENT", [tool_turn("delegate", mixed), "after"]),
             ("THIRD", [tool_turn("delegate", {"goal": "CHILD-F wait", "background": False}), "after"]),
             ("CHILD-D", ["d"]), ("CHILD-E", ["e"]), ("CHILD-F", ["the child result"])]

    async def run(name: str, give: str):
        eng = engine(tmp_path / name, rules)
        sid = (await eng.loop.create_session(title="parent")).id
        res = await eng.loop.send(sid, give)
        return eng, sid, tool_text(eng, sid), res

    async def main():
        return (await run("mixed", "PARENT start"), await run("sync", "THIRD start"))

    (eng1, sid1, mixed_out, _), (eng2, _, sync_out, sync_res) = asyncio.run(main())

    # a call runs all of its tasks in one mode, or it starts none
    assert "background is set on some tasks and not others" in mixed_out
    assert eng1.agents.bg == {} and not [e for e in eng1.hub.since(0) if isinstance(e.body, s.TaskUpdated)]

    # background: false is the default: the call waits and returns the receipt in the result, unchanged
    assert sync_res.stop_reason == "end" and sync_out.startswith("Done: task k_")
    assert "Result: the child result" in sync_out and eng2.agents.bg == {}


def test_a_steer_queued_while_a_background_child_runs_is_read_in_the_same_turn(tmp_path):
    child_up, parent_gate = asyncio.Event(), asyncio.Event()
    rules = [("STEER-X", ["saw the steer"]),
             ("PARENT", [tool_turn("delegate", {"goal": "CHILD-S long work", "role": "builder", "background": True}),
                         parent_gate, "parent done"]),
             ("[delegate notice", ["acknowledged the notice"]),
             ("CHILD-S", [child_up, "child result"])]
    eng = engine(tmp_path, rules)

    async def main():
        sid = (await eng.loop.create_session(title="parent")).id
        turn = asyncio.ensure_future(eng.loop.send(sid, "PARENT start"))
        task_id = await until(lambda: next(iter(eng.agents.bg), None))
        await until(lambda: tool_text(eng, sid).startswith("Started task "))  # the tool already returned
        assert await eng.loop.send(sid, "STEER-X change of plan", mode="steer") is None  # taken as a steer
        parent_gate.set()
        res = await asyncio.wait_for(turn, 10)
        # the steer was read by the parent's own turn while the child was still held at its gate
        assert res.message.text() == "saw the steer"
        assert any("STEER-X" in c["user"] for c in eng.loop.provider.calls)
        assert row(eng, task_id)["status"] in ("queued", "running") and task_id in eng.agents.bg
        assert notices(eng, sid) == []
        child_up.set()  # the worker carried on and still reports
        assert await until(lambda: notices(eng, sid))
        assert await until(lambda: row(eng, task_id)["status"] == "done")

    asyncio.run(main())


def test_engine_close_drains_background_children_before_store_and_router(tmp_path):
    eng = engine(tmp_path, [("CHILD", [asyncio.Event(), "done"])], jobs={"wake": False})

    async def main():
        sid = (await eng.loop.create_session(title="parent")).id
        b = eng.agents._brief(sid, 0, {"goal": "CHILD", "role": "builder"}, str(tmp_path))
        eng.agents._background(sid, [b])
        await until(lambda: eng.agents.loops)
        tasks = list(eng.agents.bg.values())
        original = eng.router.aclose

        async def close_router():
            assert not eng.agents.bg and not eng.agents.loops
            assert row(eng, b.id)["status"] == "cancelled"
            await original()

        eng.router.aclose = close_router
        await eng.close()
        assert all(t.done() for t in tasks)
        assert not eng.agents.bg and not eng.agents.loops

    asyncio.run(main())


def test_background_retry_can_be_stopped_by_advertised_or_retry_id(tmp_path):
    from exo.core.types import ProviderEvent

    class Fail:
        async def stream(self, *args, **kwargs):
            yield ProviderEvent(kind="error", error="retry me", retryable=False)

    async def main(use_retry):
        eng = engine(tmp_path / str(use_retry), [("CHILD", [asyncio.Event(), "done"])], jobs={"wake": False})
        eng.agents.route = lambda tier: (Fail(), "fake:fast") if tier == "fast" else (eng.loop.provider, "fake:std")
        sid = (await eng.loop.create_session(title="parent")).id
        b = eng.agents._brief(sid, 0, {"goal": "CHILD", "tier": "fast"}, str(tmp_path))
        # Hold the retry before it creates a child: there is no live loop to interrupt.
        run = eng.agents.run

        async def attempt(parent, brief, escalated=False):
            if escalated:
                eng.agents.sems[brief.depth] = asyncio.Semaphore(0)
            return await run(parent, brief, escalated)

        eng.agents.run = attempt
        eng.agents._background(sid, [b])
        retry = await until(lambda: next((r for r in eng.agents.list() if r["task_id"] != b.id), None))
        assert retry["status"] == "queued" and row(eng, b.id)["status"] == "failed"
        result = await subagents.stop(eng, retry["task_id"] if use_retry else b.id, by=sid)
        assert result["stopped"]
        assert row(eng, retry["task_id"])["status"] == "cancelled"
        assert "stopped by parent" in row(eng, retry["task_id"])["detail"]
        assert not eng.agents.bg
        got = notices(eng, sid)
        assert len(got) == 1 and "cancelled" in got[0].text().lower()
        await eng.close()

    asyncio.run(main(False))
    asyncio.run(main(True))
