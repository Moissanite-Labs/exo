"""Observe active fake turns without real tools, inference, credentials or live state."""
import asyncio

import pytest

from exo.core import trace
from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.types import ToolResult, ToolSpec
from exo.protocol import schema as s


class HeldProvider(FakeProvider):
    def __init__(self, calls):
        super().__init__([*calls, 'done'])
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.before_final = len(calls)

    async def stream(self, *args, **kwargs):
        if len(self.calls) == self.before_final:
            self.entered.set()
            await self.release.wait()
        async for event in super().stream(*args, **kwargs):
            yield event


def repeated(n, name='read_file', arguments=None):
    calls = []
    for i in range(n):
        message = tool_turn(name, arguments or {'path': 'fixture.txt'}, call_id=f'c{i}')
        calls.append(message)
    return calls


def make_engine(tmp_path, monkeypatch, calls, *, enabled=True):
    monkeypatch.setenv('EXO_NO_KEYCHAIN', '1')
    monkeypatch.setenv('EXO_NO_SECRET_SERVICE', '1')
    monkeypatch.setenv('EXO_KEYS_FILE', str(tmp_path / 'unused-keys'))
    model = HeldProvider(calls)
    engine = CoreEngine(tmp_path / 'home', {'roles': {'main': ['fake:x'], 'aux': ['fake:x']}},
                        cwd=str(tmp_path), env={}, trace_cfg={'enabled': enabled})
    engine.loop.provider = model
    engine.loop.max_iterations = 20  # the observer must preserve the configured user limit
    engine.loop.tool_specs = [ToolSpec(name=name, description='fixture', parameters={})
                              for name in ('read_file', 'write_file', 'bash')]
    completed = []
    async def stub(call, context):
        completed.append(call)
        return ToolResult(call_id=call.id, content=[])
    engine.loop.run_tool = stub
    return engine, model, completed


def warnings(engine, sid):
    return [e for e in engine.store.events_since(0, session=sid)
            if e.type == 'notice' and 'Repeated call warning' in e.body.text]


@pytest.mark.parametrize('cancel', [False, True])
def test_warning_and_single_finding_appear_before_terminal_without_stopping_the_turn(tmp_path, monkeypatch, cancel):
    async def run():
        engine, model, completed = make_engine(tmp_path, monkeypatch, repeated(8))
        try:
            session = engine.store.create_session(agent=engine.agent)
            scans = []
            original_scan = trace.last_turn
            def count_scan(*args, **kwargs):
                scans.append(1)
                return original_scan(*args, **kwargs)
            monkeypatch.setattr(trace, 'last_turn', count_scan)
            task = asyncio.create_task(engine.loop.send(session.id, 'read the fixture'))
            await asyncio.wait_for(model.entered.wait(), 2)
            assert len(completed) == 8 and not task.done() and engine.loop.max_iterations == 20
            events = engine.store.events_since(0, session=session.id)
            assert not any(e.type == 'turn.completed' for e in events)
            assert len(warnings(engine, session.id)) == 1 and scans == []
            [finding] = engine.findings.list()
            assert finding['signature'] == 'tool.repeat:read_file' and finding['count'] == 1
            assert finding['turn'] == next(e.body.turn_id for e in events if e.type == 'turn.started')
            assert finding['session'] == session.id
            if cancel:
                engine.loop.interrupt(session.id)
            else:
                model.release.set()
            result = await asyncio.wait_for(task, 2)
            assert result.stop_reason == ('interrupted' if cancel else 'end')
            assert len(warnings(engine, session.id)) == 1 and engine.findings.list()[0]['count'] == 1
            assert not engine.repeat_watch.turns and not engine.repeat_watch.completed
            assert len(scans) == 1
        finally:
            await engine.close()
    asyncio.run(run())


def test_successful_edit_resets_repeat_epoch_and_short_normal_turn_does_not_warn(tmp_path, monkeypatch):
    async def run():
        calls = repeated(1) + repeated(1, 'write_file', {'path': 'fixture.txt', 'content': 'change one'}) + repeated(1) + repeated(1, 'write_file', {'path': 'fixture.txt', 'content': 'change two'}) + repeated(1)
        for i, message in enumerate(calls):
            message.tool_calls[0].id = f'c{i}'
        engine, model, completed = make_engine(tmp_path, monkeypatch, calls)
        try:
            sid = engine.store.create_session(agent=engine.agent).id
            task = asyncio.create_task(engine.loop.send(sid, 'read, edit and retest'))
            await asyncio.wait_for(model.entered.wait(), 2)
            assert len(completed) == 5 and not warnings(engine, sid) and not engine.findings.list()
            model.release.set(); await asyncio.wait_for(task, 2)
            assert not warnings(engine, sid)
            assert not any(f['signature'].startswith('tool.repeat:') for f in engine.findings.list())
        finally:
            await engine.close()
    asyncio.run(run())


def test_normalized_shell_repeats_warn_once_but_explicit_trace_disable_stays_off(tmp_path, monkeypatch):
    async def run(enabled):
        calls = [tool_turn('bash', {'command': command}, call_id=f'c{i}') for i, command in enumerate(
            ['cd fixture && pytest -q', 'pytest', 'timeout 20 pytest -q', 'pytest'])]
        engine, model, _ = make_engine(tmp_path / str(enabled), monkeypatch, calls, enabled=enabled)
        try:
            sid = engine.store.create_session(agent=engine.agent).id
            task = asyncio.create_task(engine.loop.send(sid, 'fixture tests'))
            await asyncio.wait_for(model.entered.wait(), 2)
            assert len(warnings(engine, sid)) == int(enabled)
            assert len(engine.findings.list()) == int(enabled)
            model.release.set(); await asyncio.wait_for(task, 2)
        finally:
            await engine.close()
    asyncio.run(run(True)); asyncio.run(run(False))


def test_watch_bounds_recent_keys_and_forgets_completed_turns():
    watch = trace.RepeatWatch()
    watch.feed(s.Event.of(s.TurnStarted(turn_id='t'), 0, session='s'))
    for i in range(1000):
        call = s.ToolCall(id=str(i), name='read_file', arguments={'path': str(i)})
        watch.feed(s.Event.of(s.ToolStarted(call=call), 0, session='s'))
        assert watch.feed(s.Event.of(s.ToolCompleted(result=ToolResult(call_id=str(i), content=[])), 0, session='s')) is None
    assert len(watch.turns['s']['counts']) <= watch.LIMIT
    watch.feed(s.Event.of(s.TurnCompleted(turn_id='old-turn'), 0, session='s'))
    assert 's' in watch.turns  # a stale completion must not clear the current turn
    watch.feed(s.Event.of(s.TurnCompleted(turn_id='t'), 0, session='s'))
    assert not watch.turns
    watch.finish('s', 't')
    assert not watch.completed



def test_short_turn_does_not_warn_and_next_turn_gets_its_own_deduplicated_warning(tmp_path, monkeypatch):
    async def run():
        engine, model, _ = make_engine(tmp_path, monkeypatch, repeated(1))
        try:
            sid = engine.store.create_session(agent=engine.agent).id
            task = asyncio.create_task(engine.loop.send(sid, 'one normal read'))
            await asyncio.wait_for(model.entered.wait(), 2)
            assert not warnings(engine, sid) and not engine.findings.list()
            model.release.set(); await asyncio.wait_for(task, 2)
            for expected in (1, 2):
                model = HeldProvider(repeated(4)); engine.loop.provider = model
                task = asyncio.create_task(engine.loop.send(sid, 'repeat fixture'))
                await asyncio.wait_for(model.entered.wait(), 2)
                assert len(warnings(engine, sid)) == expected
                assert engine.findings.list()[0]['count'] == expected
                model.release.set(); await asyncio.wait_for(task, 2)
                assert engine.findings.list()[0]['count'] == expected
        finally:
            await engine.close()
    asyncio.run(run())


def test_finding_store_failure_still_warns_and_never_stops_the_turn(tmp_path, monkeypatch):
    async def run():
        engine, model, completed = make_engine(tmp_path, monkeypatch, repeated(4))
        def unavailable(*args, **kwargs):
            raise RuntimeError('fixture observer unavailable')
        monkeypatch.setattr(engine.findings, 'file_flag', unavailable)
        try:
            sid = engine.store.create_session(agent=engine.agent).id
            task = asyncio.create_task(engine.loop.send(sid, 'fixture repeats'))
            await asyncio.wait_for(model.entered.wait(), 2)
            assert len(warnings(engine, sid)) == 1 and len(completed) == 4 and not task.done()
            model.release.set()
            assert (await asyncio.wait_for(task, 2)).stop_reason == 'end'
            assert not engine.repeat_watch.turns and not engine.repeat_watch.completed
        finally:
            await engine.close()
    asyncio.run(run())
