"""Core availability is distinct from the still-open browser event stream."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile

import httpx

from exo.protocol import schema as s
from exo.server.app import Dispatcher, HttpServer
from exo.server.echo import EchoEngine
from exo_command.config import Settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre
from exo_command.source import ClientSource


async def until(predicate):
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError('core availability transition did not arrive')


async def core_frame(lines, state):
    frame = []
    while True:
        line = await asyncio.wait_for(anext(lines), 2)
        if line:
            frame.append(line)
            continue
        if 'event: core-state' in frame:
            data = json.loads(next(line[6:] for line in frame if line.startswith('data: ')))
            assert set(data) == {'state'} and not any(line.startswith('id:') for line in frame)
            if data['state'] == state:
                return '\n'.join(frame)
        frame = []


class QuietCore:
    def __init__(self, error=False):
        self.ready = asyncio.Event()
        self.drop = asyncio.Event()
        self.error = error
    async def call(self, body):
        if isinstance(body, s.Hello):
            await self.ready.wait()
        return {'events': [], 'seq': 0}
    async def stream(self, since=0):
        await self.drop.wait()
        if self.error:
            raise RuntimeError('private-upstream-error-fixture')
        if False:
            yield {}


def test_quiet_core_connects_without_model_event_and_eof_or_exception_notifies_existing_sse():
    async def go(error):
        source = QuietCore(error)
        centre = CommandCentre(Settings(), source)
        server = WebServer(centre, token='core-state-fixture')
        port = await server.start()
        feed = asyncio.create_task(server._feed())
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', headers={'X-Exo-Token': 'core-state-fixture'}) as client:
                async with client.stream('GET', '/api/events?token=core-state-fixture') as response:
                    lines = response.aiter_lines()
                    await core_frame(lines, 'connecting')
                    assert centre.connected is False
                    source.ready.set()
                    await core_frame(lines, 'connected')
                    assert centre.connected and centre.state.seq == 0
                    # No model event exists; successful bootstrap is enough for a quiet idle core.
                    health = (await client.get('/api/health')).json()
                    assert health['connected'] and health['core_state'] == 'connected'
                    source.drop.set()
                    wire = await core_frame(lines, 'unavailable')
                    assert 'private-upstream-error-fixture' not in wire
                    health = (await client.get('/api/health')).json()
                    assert not health['connected'] and health['core_state'] == 'unavailable'
                    assert response.status_code == 200 and centre.state.seq == 0  # same SSE, no invented turn completion
        finally:
            feed.cancel()
            await asyncio.gather(feed, return_exceptions=True)
            await server.close()
    asyncio.run(go(False))
    asyncio.run(go(True))


def test_real_core_socket_failure_and_recovery_updates_web_sse_without_autostart():
    async def go(folder):
        socket = str(Path(folder) / 'core.sock')
        backend = HttpServer(Dispatcher(EchoEngine()), principal=True, heartbeat=0.02)
        await backend.start_unix(socket)
        source = ClientSource(socket=socket)
        centre = CommandCentre(Settings(socket=socket), source)
        def forbidden_autostart():
            raise AssertionError('attached clients must not start another core')
        centre.ensure_core = forbidden_autostart
        server = WebServer(centre, token='socket-fixture')
        port = await server.start()
        feed = asyncio.create_task(server._feed())
        replacement = None
        try:
            await until(lambda: centre.connected)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', headers={'X-Exo-Token': 'socket-fixture'}) as client:
                async with client.stream('GET', '/api/events?token=socket-fixture') as response:
                    lines = response.aiter_lines()
                    await core_frame(lines, 'connected')
                    await backend.close()
                    await core_frame(lines, 'unavailable')
                    assert centre.state.seq == 0 and centre.auto_core is False
                    replacement = HttpServer(Dispatcher(EchoEngine()), principal=True, heartbeat=0.02)
                    await replacement.start_unix(socket)  # test-owned replacement, never requested by the web client
                    await until(lambda: centre.connected)
                    await core_frame(lines, 'connected')
                    assert source.socket == socket and centre.state.seq == 0
        finally:
            feed.cancel()
            await asyncio.gather(feed, return_exceptions=True)
            await server.close()
            await source.aclose()
            await backend.close()
            if replacement:
                await replacement.close()
    with tempfile.TemporaryDirectory(prefix='exo-core-link-', dir='/tmp') as folder:
        asyncio.run(go(folder))


def test_browser_bus_requires_core_metadata_not_only_open_web_transport(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path
    import pytest
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    static = Path(__file__).parents[1] / 'exo_command' / 'static'
    for name in ('dom', 'bus'):
        (tmp_path / f'{name}.mjs').write_text((static / f'{name}.js').read_text().replace('.js"', '.mjs"'))
    script = tmp_path / 'test.mjs'
    script.write_text('''
import assert from "node:assert/strict";
const made=[]; class ES { constructor(){made.push(this);this.readyState=1;} close(){} addEventListener(t,f){this[t]=f;} }
globalThis.EventSource=ES;globalThis.sessionStorage={getItem:()=>"fake",removeItem(){}};
globalThis.fetch=async()=>({ok:true,status:200,json:async()=>({panels:[],seq:7})});
const bus=await import("./bus.mjs");let state;bus.onState(s=>state={...s});await bus.start();
const first=made[0];first.onopen();assert.equal(state.transport_connected,true);assert.equal(state.connected,false);
first['core-state']({data:'{"state":"connected"}'});assert.equal(state.connected,true);assert.equal(bus.lastSeq(),7);
first['core-state']({data:'null'});first['core-state']({data:'{"state":"private-invalid-error"}'});assert.equal(state.core_state,"connected");
first['core-state']({data:'{"state":"unavailable"}'});assert.equal(state.connected,false);assert.equal(state.transport_connected,true);assert.equal(bus.lastSeq(),7);
await bus.restart();first['core-state']({data:'{"state":"connected"}'});assert.equal(state.connected,false);
made[1].onopen();made[1]['core-state']({data:'{"state":"connected"}'});assert.equal(state.connected,true);
console.log("ok");
''')
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'


def test_chat_web_transport_loss_does_not_diagnose_core_failure(tmp_path, monkeypatch):
    import test_exo_command_chat_view as fixture
    monkeypatch.setattr(fixture, 'BUS', fixture.BUS.replace(
        'export const onState=f=>(f({synced:0}),()=>{});',
        'export const onState=f=>(globalThis.connectionState=f,f({synced:0}),()=>{});'))
    fixture._run(tmp_path, r'''
globalThis.connectionState({synced:0,connected:false,transport_connected:false,core_state:"connected"});
const status=nodes.filter(x=>(x.attrs.class||"").startsWith("xc-state")).at(-1).textContent;
assert.match(status,/web reconnecting.*last known idle/);
assert.ok(!status.includes("core unavailable"));
''')
