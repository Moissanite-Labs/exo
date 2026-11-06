"""Web swarm controls over a REAL Unix socket against a real CoreEngine with an injected GroupHost.

The path under test is ``exo_command.api.dispatch`` -> ``swarm_api`` -> ``ClientSource`` (principal ``web``) ->
the real ``exo.protocol.client.Client`` -> a real Unix-socket ``exo.server.app.serve`` -> the real ``CoreEngine`` ->
``group_commands`` -> the real ``GroupHost``. Nothing between the web API and the host is mocked. The one
substitution is the provider transport: ``RuleFake.stream`` blocks on an ``asyncio.Event`` so a member request stays
in flight, then lets the real offline fake answer.

Checked:
  * status, pause, resume and stop are accepted over the socket (HTTP 200, ``accepted: true``, ``settled: unknown``).
  * pause leaves the in-flight provider request running (not cancelled. the member reads ``pausing``).
  * stop cancels that request and the group settles (members ``stopped``, receipt ``settled``).
  * a standalone engine with no GroupHost refuses a control with 403.
  * an engine whose injected host belongs to another owner refuses a control with 403.

Needs the swarm core and this plugin on PYTHONPATH (skips otherwise), for example
``PYTHONPATH=<core>/src:<core>/plugins/exo-command``. Hermetic: temp EXO_HOME, EXO_NO_KEYCHAIN=1, no network, every
wait is bounded by BOUND seconds.

Limits: this is socket and authority evidence, not browser proof. It does not cover the HTTP server in
``exo_command.server``, the page token or CSRF. It covers one socratic group of two members with the one-shot
engine adapter, so "pause blocks the NEXT turn" is not covered. The provider call is held until resume or stop.
"""
import asyncio
import contextlib
import json
import shutil
import tempfile
import types
from pathlib import Path

import pytest

group_launch = pytest.importorskip('exo.group_launch')
group_host_mod = pytest.importorskip('exo.core.group_host')
serve_mod = pytest.importorskip('exo.server.app')
compose = pytest.importorskip('exo.compose')
fake_mod = pytest.importorskip('exo.core.models.fake')
groups_mod = pytest.importorskip('exo.core.groups')
exo_command_api = pytest.importorskip('exo_command.api')
source_mod = pytest.importorskip('exo_command.source')
swarm_api = pytest.importorskip('exo_command.swarm_api')

BOUND = 10
PROFILES = ['builder', 'checker']
OWNER = 'owner'
MODELS = {'fake': {'rules': [{'match': 'zzz-never', 'steps': ['UNUSED']}]}, 'roles': {'main': ['fake:x']}}


@pytest.fixture
def home(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix='exw', dir='/tmp'))      # short: AF_UNIX paths cap at ~104 bytes
    home = root / 'h'
    (home / 'seats').mkdir(parents=True)
    for name in (*PROFILES, OWNER):
        (home / 'seats' / f'{name}.toml').write_text(f'name = "{name}"\n', encoding='utf-8')
    (home / 'config.json').write_text(json.dumps({'models': MODELS}), encoding='utf-8')
    monkeypatch.setenv('EXO_HOME', str(home))
    monkeypatch.setenv('EXO_NO_KEYCHAIN', '1')
    monkeypatch.setenv('EXO_NO_SECRET_SERVICE', '1')
    try:
        yield home
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def held(monkeypatch):
    """A provider transport that blocks every member call on one event, counting entries and outcomes."""
    state = types.SimpleNamespace(gate=None, started=0, active=0, cancelled=0, finished=0)
    original = fake_mod.RuleFake.stream

    async def stream(this, messages, tools, **opts):
        if state.gate is None:
            state.gate = asyncio.Event()
        state.started += 1
        state.active += 1
        yielded, cancelled = 0, False
        try:
            await state.gate.wait()
            async for event in original(this, messages, tools, **opts):
                yielded += 1
                yield event
        except asyncio.CancelledError:
            cancelled = True
            state.cancelled += 1
            raise
        finally:
            state.finished += int(yielded > 0 and not cancelled)
            state.active -= 1

    monkeypatch.setattr(fake_mod.RuleFake, 'stream', stream)
    return state


async def until(pred, what):
    deadline = asyncio.get_running_loop().time() + BOUND
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f'timed out after {BOUND}s waiting for {what}')
        await asyncio.sleep(0.02)


async def start_core(engine, sock):
    """Run the real Unix-socket server for ``engine`` as a task. returns once it accepts connections."""
    ready = asyncio.Event()
    task = asyncio.create_task(serve_mod.serve(engine, socket=str(sock), ready=lambda _addr: ready.set()))
    waiter = asyncio.create_task(ready.wait())
    done, _ = await asyncio.wait({task, waiter}, timeout=BOUND, return_when=asyncio.FIRST_COMPLETED)
    waiter.cancel()
    if task in done:
        task.result()
        raise AssertionError('socket server stopped before it was ready')
    assert ready.is_set(), 'socket server did not become ready'
    return task


def web(sock):
    """The web client's side: a centre whose source is the real ClientSource (principal ``web``) on ``sock``."""
    return types.SimpleNamespace(source=source_mod.ClientSource(socket=str(sock)))


async def post(centre, gid, op, body=None):
    return await exo_command_api.dispatch(centre, 'POST', f'/api/swarm/groups/{gid}/{op}', {}, body or {})


async def status(centre, gid):
    code, data = await exo_command_api.dispatch(centre, 'GET', f'/api/swarm/groups/{gid}/status', {}, {})
    assert code == 200, (code, data)
    return data


def states(data):
    return {m['profile']: m['state'] for m in data['members']}


async def stop_server(task):
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(task, BOUND)


def run(coro):
    asyncio.run(asyncio.wait_for(coro, 4 * BOUND))


def test_web_controls_over_real_socket_pause_keeps_request_stop_cancels(home, held):
    async def scenario():
        sock = home / 'run' / 'exod.sock'
        sock.parent.mkdir(parents=True)
        launch = group_launch.create(OWNER, profiles=PROFILES, mode='socratic')
        gid, task, centre = launch.group.id, None, None
        try:
            launch.start()
            await until(lambda: held.active >= 1, 'a pending provider call')
            task = await start_core(launch.engine, sock)
            centre = web(sock)

            first = await status(centre, gid)
            assert first['owner'] == OWNER and first['local_runtime'] is True
            assert sorted(states(first)) == sorted(PROFILES)
            assert first['receipt']['state'] == 'active' and not {'completed', 'stopped', 'failed'} & set(states(first).values())

            expect = lambda op: (200, {'id': gid, 'action': op, 'member': None, 'accepted': True, 'settled': 'unknown',
                                       'note': swarm_api.ACCEPTED_ONLY})
            assert await post(centre, gid, 'pause') == expect('pause')
            paused = await status(centre, gid)
            assert set(states(paused).values()) == {'pausing'}      # gate closed, in-flight turn not at a checkpoint
            assert held.active >= 1 and held.cancelled == 0 and held.finished == 0   # pause did not cancel the request

            assert await post(centre, gid, 'resume') == expect('resume')
            resumed = await status(centre, gid)
            assert not {'pausing', 'paused', 'completed', 'stopped'} & set(states(resumed).values())
            assert held.active >= 1 and held.cancelled == 0 and held.finished == 0   # still pending after resume

            assert await post(centre, gid, 'stop') == expect('stop')
            await until(lambda: held.cancelled >= 1 and held.active == 0, 'the held provider call to be cancelled')
            deadline = asyncio.get_running_loop().time() + BOUND
            final = await status(centre, gid)
            while set(states(final).values()) != {'stopped'} and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
                final = await status(centre, gid)
            assert set(states(final).values()) == {'stopped'}, final
            assert final['receipt']['state'] == 'settled' and final['reconciliation_required'] is False
            assert held.finished == 0                                # stop cancelled it. it never completed
        finally:
            if held.gate is not None:
                held.gate.set()
            if centre is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(centre.source.aclose(), BOUND)
            if task is not None:
                await stop_server(task)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(launch.aclose(), BOUND)

    run(scenario())


def test_standalone_engine_without_group_host_refuses_controls_403(home, held):
    async def scenario():
        sock = home / 'run' / 'exod.sock'
        sock.parent.mkdir(parents=True)
        engine = compose.create(OWNER)                              # real CoreEngine, no group_host
        assert getattr(engine, '_group_host', None) is None
        task, centre = None, None
        try:
            task = await start_core(engine, sock)
            centre = web(sock)
            for op in ('pause', 'resume', 'stop'):
                code, data = await post(centre, 'g-none', op)
                assert (code, data['code']) == (403, 'forbidden'), (op, code, data)
                assert 'accepted' not in data
            code, data = await exo_command_api.dispatch(centre, 'GET', '/api/swarm/groups/g-none/status', {}, {})
            assert (code, data['code']) == (403, 'forbidden')
        finally:
            if centre is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(centre.source.aclose(), BOUND)
            if task is not None:
                await stop_server(task)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(engine.close(), BOUND)

    run(scenario())


def test_wrong_owner_group_host_refuses_controls_403_and_leaves_group_untouched(home, held):
    async def scenario():
        sock = home / 'run' / 'exod.sock'
        sock.parent.mkdir(parents=True)
        launch = group_launch.create(OWNER, profiles=PROFILES, mode='socratic')
        gid, task, centre, real = launch.group.id, None, None, None
        try:
            launch.start()
            await until(lambda: held.active >= 1, 'a pending provider call')
            # Rewire the real owner engine to a real GroupHost owned by someone else, over the same store. The
            # running group stays on its own host (kept as ``real``), so a leaked control would be visible on it.
            real = launch.engine._group_host
            launch.engine._group_host = group_host_mod.GroupHost(
                groups_mod.GroupStore(launch.store), owner='intruder', known_profiles=frozenset(PROFILES),
                engine_factory=lambda *a, **k: pytest.fail('the foreign host must never build an engine'))
            assert launch.engine._group_host.owner != launch.engine.agent
            task = await start_core(launch.engine, sock)
            centre = web(sock)
            for op in ('pause', 'resume', 'stop'):
                code, data = await post(centre, gid, op)
                assert (code, data['code']) == (403, 'forbidden'), (op, code, data)
                assert 'accepted' not in data
            assert held.active >= 1 and held.cancelled == 0      # the refused stop touched nothing
            assert {m.state for m in real.status(gid)['members']} <= {'queued', 'running'}
        finally:
            if held.gate is not None:
                held.gate.set()
            if centre is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(centre.source.aclose(), BOUND)
            if task is not None:
                await stop_server(task)
            if real is not None:
                launch.engine._group_host = real
            with contextlib.suppress(Exception):
                await asyncio.wait_for(launch.aclose(), BOUND)

    run(scenario())
