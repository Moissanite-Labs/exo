"""Unattended ingress turns finish, including a job waking a scheduled run's thread."""

import asyncio
import functools

import pytest

from exo.bus.ingress import BusTarget
from exo.core.engine import CoreEngine
from exo.core.ingress import Ingress, IngressPath
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.types import ToolSpec
from exo.principal import CHANNEL
from exo.protocol import schema as s
from exo.scheduler import Scheduler
from exo.server.app import ChannelLink, Dispatcher


def aio(fn):
    @functools.wraps(fn)
    def run(*args, **kw):
        return asyncio.run(fn(*args, **kw))
    return run


class RepeatingTools(FakeProvider):
    """Fail promptly if the cap disappears, without running a live or unlimited model loop."""

    def __init__(self):
        super().__init__([tool_turn("glob", {"pattern": "*.none"})], repeat=True)

    async def stream(self, messages, tools, **opts):
        if len(self.calls) == 24:
            assert opts.get("tool_choice") == "none", "wake continued past 24 tool rounds"
            self.script = ["checked 24 pages; next: the totals"]
        async for event in super().stream(messages, tools, **opts):
            yield event


def engine(tmp_path):
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                     cwd=str(tmp_path), env={}, peers={"policy": {"local": "auto"}}, attach_scheduler=Scheduler)
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    return eng


def completed(eng, sid):
    return [e.body for e in eng.hub.since(0) if e.session == sid and isinstance(e.body, s.TurnCompleted)]


async def wait_completed(eng, sid, count=1):
    async with asyncio.timeout(10):
        while len(completed(eng, sid)) < count:
            await asyncio.sleep(0.01)
        await asyncio.gather(*eng._tasks)
        while eng._tasks:  # gather of an already-done task need not yield on Python 3.12+
            await asyncio.sleep(0)


def assert_capped(eng, sid, provider):
    assert completed(eng, sid)[-1].stop_reason == "max_iterations"
    assert len(provider.calls) == 25
    assert all(c["opts"].get("tool_choice") != "none" for c in provider.calls[:24])
    assert provider.calls[-1]["opts"]["tool_choice"] == "none"
    assert provider.calls[-1]["tools"] == provider.calls[0]["tools"]
    assert "used all 24 tool rounds" in provider.calls[-1]["messages"][-1].text()
    calls = [e.body for e in eng.hub.since(0) if e.session == sid and isinstance(e.body, s.ModelCall)]
    assert [call.iteration for call in calls[-25:]] == list(range(1, 26))
    messages = eng.store.messages(sid)
    assert len([m for m in messages if m.role == "tool"
                and m.meta["turn_id"] == completed(eng, sid)[-1].turn_id]) == 24
    assert messages[-1].meta["wrap_up"] is True
    assert messages[-1].text() == "checked 24 pages; next: the totals"
    assert any(isinstance(e.body, s.Notice) and e.body.level == "warn"
               and e.body.text == "stopped after 24 tool rounds" for e in eng.hub.since(0))
    assert not eng.loop.state(sid).running and not eng._tasks
    assert eng.loop.max_iterations == 0


@pytest.mark.parametrize("source, watching", [("job", False), ("peer", False), ("bus", False), ("bus", True)])
@pytest.mark.parametrize("capped", [False, True])
@aio
async def test_ingress_wake_completes_with_a_report(tmp_path, source, watching, capped):
    eng = engine(tmp_path)
    sub = eng.hub.subscribe() if watching else None
    try:
        sid = (await eng.loop.create_session()).id
        provider = eng.loop.provider = RepeatingTools() if capped else FakeProvider([
            tool_turn("glob", {"pattern": "*.none"}), "finished"])
        path = (eng.jobs.path if source == "job" else BusTarget(eng).path if source == "bus"
                else IngressPath(eng._peer_target, lambda ing: ("auto", "test policy")))
        receipt = await path.submit(Ingress(source, "harness", "local", "check pages", session=sid,
                                           meta={"local": True, "job_id": "j1", "state": "done", "head": "finished"}))
        assert receipt.mode == "started"
        await wait_completed(eng, sid)
        if capped:
            assert_capped(eng, sid, provider)
        else:
            assert completed(eng, sid)[0].stop_reason == "end"
            assert len(provider.calls) == 2 and eng.store.messages(sid)[-1].text() == "finished"
            assert "wrap_up" not in eng.store.messages(sid)[-1].meta
    finally:
        if sub:
            sub.close()
        await eng.close()


@pytest.mark.parametrize("caller, author", [("principal:chat", "principal"), (CHANNEL, "local")])
@aio
async def test_principal_channel_turn_remains_unlimited_while_a_wake_runs(tmp_path, caller, author):
    eng = engine(tmp_path)
    try:
        wake = (await eng.loop.create_session()).id
        principal = (await eng.loop.create_session()).id
        # Each session gets its own script while sharing the engine loop and provider.
        providers = {wake: RepeatingTools(), principal: FakeProvider([
            *[tool_turn("glob", {"pattern": "*.none"}) for _ in range(30)], "finished"])}

        class PerSession:
            name = "fake"

            async def stream(self, messages, tools, **opts):
                sid = wake if any("wake pages" in m.text() for m in messages) else principal
                async for event in providers[sid].stream(messages, tools, **opts):
                    yield event

        eng.loop.provider = PerSession()
        eng.start_turn(wake, "wake pages", {"author": "job"})
        body = s.TurnSend(session=principal, text="principal pages")
        if caller == CHANNEL:
            assert (await ChannelLink(Dispatcher(eng)).call(body))["accepted"]
        else:
            assert (await Dispatcher(eng, caller=caller).run(s.Command.of(body))).ok
        await wait_completed(eng, wake)
        await wait_completed(eng, principal)
        assert_capped(eng, wake, providers[wake])
        assert completed(eng, principal)[0].stop_reason == "end"
        assert len(providers[principal].calls) == 31
        assert eng.store.messages(principal)[0].meta["author"] == author
        assert eng.store.messages(principal)[-1].text() == "finished"
    finally:
        await eng.close()


class LeavingTools(RepeatingTools):
    """The client watching this turn closes its event stream three rounds in: nobody can steer it now."""

    def __init__(self, sub):
        super().__init__()
        self.sub = sub

    async def stream(self, messages, tools, **opts):
        if len(self.calls) == 3:
            self.sub.close()
        async for event in super().stream(messages, tools, **opts):
            yield event


@aio
async def test_a_watched_turn_is_capped_when_its_client_leaves_mid_turn(tmp_path):
    eng = engine(tmp_path)
    sub = eng.hub.subscribe()
    try:
        sid = (await eng.loop.create_session()).id
        provider = eng.loop.provider = LeavingTools(sub)
        response = await Dispatcher(eng, caller="principal:tui").run(s.Command.of(s.TurnSend(session=sid, text="pages")))
        assert response.ok
        await wait_completed(eng, sid)
        assert_capped(eng, sid, provider)  # 24 rounds from the start, then a report
    finally:
        sub.close()
        await eng.close()


@aio
async def test_wait_completed_drains_a_finished_tasks_queued_cleanup(tmp_path):
    eng = engine(tmp_path)
    try:
        sid = (await eng.loop.create_session()).id
        eng.loop.provider = FakeProvider(["finished"])
        await eng.loop.send(sid, "pages")
        finished = asyncio.create_task(asyncio.sleep(0))
        await finished
        eng._tasks.add(finished)
        finished.add_done_callback(eng._tasks.discard)
        # Pin the race window: the task is done, but its engine cleanup is still queued.
        assert finished.done() and finished in eng._tasks
        await wait_completed(eng, sid)
        assert not eng._tasks
    finally:
        await eng.close()


@aio
async def test_a_principal_message_steered_into_a_wake_keeps_its_cap(tmp_path):
    eng = engine(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    try:
        async def wait(args, ctx):
            entered.set()
            await release.wait()
            return "ready"

        eng.registry.register(ToolSpec(name="wait", description="wait", toolset="core"), wait)
        sid = (await eng.loop.create_session()).id
        provider = eng.loop.provider = RepeatingTools()
        provider.script.insert(0, tool_turn("wait"))
        eng.start_turn(sid, "wake pages", {"author": "job"})
        await asyncio.wait_for(entered.wait(), 10)
        response = await Dispatcher(eng, caller="principal:tui").run(
            s.Command.of(s.TurnSend(session=sid, text="check these too", mode="steer")))
        assert response.ok and response.result["steered"]
        release.set()
        await wait_completed(eng, sid)
        assert_capped(eng, sid, provider)
        steer = next(m for m in eng.store.messages(sid) if m.text() == "check these too")
        assert steer.meta["author"] == "principal"
    finally:
        release.set()
        await eng.close()


@aio
async def test_interrupted_wake_wrap_up_records_the_actual_iteration(tmp_path):
    eng = engine(tmp_path)
    wrapping = asyncio.Event()
    try:
        class HeldWrap(RepeatingTools):
            async def stream(self, messages, tools, **opts):
                if opts.get("tool_choice") == "none":
                    wrapping.set()
                    await asyncio.Event().wait()
                async for event in super().stream(messages, tools, **opts):
                    yield event

        sid = (await eng.loop.create_session()).id
        eng.loop.provider = HeldWrap()
        eng.start_turn(sid, "wake pages", {"author": "job"})
        await asyncio.wait_for(wrapping.wait(), 10)
        assert eng.loop.interrupt(sid)
        await wait_completed(eng, sid)
        assert completed(eng, sid)[-1].stop_reason == "interrupted"
        calls = [e.body for e in eng.hub.since(0) if e.session == sid and isinstance(e.body, s.ModelCall)]
        assert [call.iteration for call in calls] == list(range(1, 26))
        assert calls[-1].stop_reason == "interrupted"
    finally:
        await eng.close()


@pytest.mark.parametrize("limit", [2, -1])
@aio
async def test_wake_preserves_a_stricter_explicit_loop_limit(tmp_path, limit):
    eng = engine(tmp_path)
    try:
        sid = (await eng.loop.create_session()).id
        eng.loop.max_iterations = limit
        provider = eng.loop.provider = FakeProvider([
            *[tool_turn("glob", {"pattern": "*.none"}) for _ in range(limit)], "report"])
        eng.start_turn(sid, "wake pages", {"author": "job"})
        await wait_completed(eng, sid)
        assert completed(eng, sid)[0].stop_reason == "max_iterations"
        assert len(provider.calls) == max(0, limit) + 1
        assert provider.calls[-1]["opts"]["tool_choice"] == "none"
        assert eng.loop.max_iterations == limit
    finally:
        await eng.close()


@aio
async def test_background_job_wakes_a_completed_scheduled_run_with_a_cap(tmp_path):
    eng = engine(tmp_path)
    try:
        eng.loop.provider = FakeProvider([
            tool_turn("bash", {"command": "while [ ! -f wake-release ]; do sleep 0.01; done; printf finished",
                               "background": True}), "started background work"])
        sch = eng.scheduler
        job = sch.add("every 1h", "make the report", title="Report")
        assert sch.launch(job)
        await asyncio.wait_for(sch.wait(), 10)
        sid = next(e.body.child_session for e in eng.hub.since(0)
                   if isinstance(e.body, s.TaskUpdated) and e.body.status == "running")
        assert eng.store.get_session(sid).kind == "scheduled"
        assert completed(eng, sid)[0].stop_reason == "end"
        assert sch.get(job["id"])["last_status"] == "done"
        provider = eng.loop.provider = RepeatingTools()
        (tmp_path / "wake-release").touch()
        await wait_completed(eng, sid, 2)
        assert_capped(eng, sid, provider)
        notice = next(m for m in eng.store.messages(sid) if m.meta.get("job"))
        assert notice.meta["author"] == "job" and "finished" in notice.text()
        assert not sch.running and not sch.loops
        eng.loop.provider = FakeProvider(["later report"])
        assert sch.launch(job)
        await asyncio.wait_for(sch.wait(), 10)
        assert sch.get(job["id"])["last_status"] == "done"
    finally:
        await eng.close()
