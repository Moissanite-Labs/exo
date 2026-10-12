import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from exo.protocol import schema as s
from exo.protocol.client import Client
from exo.server.app import Dispatcher, HttpServer, load_engine, serve_lines
from exo.server.echo import EchoEngine
from exo.server.engine import Engine, Hub


@pytest.fixture
def sock_dir():
    # Unix socket paths are capped near 104 bytes on macOS; pytest tmp_path can exceed that.
    d = tempfile.mkdtemp(prefix="exo", dir="/tmp" if os.path.isdir("/tmp") else None)
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


async def _start(sock_dir, token=None, tcp=False, delay=0.0):
    srv = HttpServer(Dispatcher(EchoEngine(delay=delay)), token=token, heartbeat=0.2)
    if tcp:
        port = await srv.start_tcp("127.0.0.1", 0)
        return srv, {"url": f"http://127.0.0.1:{port}"}
    path = sock_dir / "exod.sock"
    await srv.start_unix(path)
    return srv, {"socket": str(path)}


async def _collect(client, since, until_completed, session=None):
    out, done = [], 0
    async for ev in client.events(since=since, session=session):
        out.append(ev)
        if ev.type == "turn.completed":
            done += 1
            if done == until_completed:
                return out
    return out


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


def test_echo_engine_satisfies_protocol():
    assert isinstance(EchoEngine(), Engine)
    assert isinstance(load_engine("echo"), EchoEngine)


def test_two_clients_see_same_ordered_events(sock_dir):
    async def main():
        srv, where = await _start(sock_dir, delay=0.001)
        try:
            async with Client(**where) as a, Client(**where) as b, Client(**where) as ctl:
                sid = (await ctl.call(s.SessionCreate(title="t")))["session"]["id"]
                ta = asyncio.create_task(_collect(a, 0, 2, session=sid))
                tb = asyncio.create_task(_collect(b, 0, 2))
                await asyncio.sleep(0.05)
                await ctl.call(s.TurnSend(session=sid, text="one two three"))
                await ctl.call(s.TurnSend(session=sid, text="four five", mode="queue"))
                ea, eb = await ta, await tb
            return ea, eb
        finally:
            await srv.close()

    ea, eb = _run(main())
    key = lambda evs: [(e.seq, e.type, e.id) for e in evs]
    assert key(ea) == key(eb)
    seqs = [e.seq for e in ea]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    types = [e.type for e in ea]
    assert types[0] == "session.created"
    # turns of one session never interleave: started, deltas, completed, then the next turn
    first_done = types.index("turn.completed")
    assert types.count("turn.started") == 2 and "turn.started" in types[first_done:]
    text = "".join(e.body.text for e in ea[:first_done] if e.type == "message.delta")
    assert text == "one two three"


def test_reconnect_with_since_replays(sock_dir):
    async def main():
        srv, where = await _start(sock_dir)
        try:
            async with Client(**where) as c:
                sid = (await c.call(s.SessionCreate()))["session"]["id"]
                await c.call(s.TurnSend(session=sid, text="alpha beta"))
                full = await _collect(c, 0, 1)
                cut = full[2].seq  # drop off part way through the turn
                await c.call(s.TurnSend(session=sid, text="gamma"))
                rest = await _collect(c, cut, 2)
                one_shot = await c.call(s.EventsSubscribe(since=cut))
            return full, cut, rest, one_shot
        finally:
            await srv.close()

    full, cut, rest, one_shot = _run(main())
    assert [e.seq for e in rest][: len(full) - 3] == [e.seq for e in full[3:]]
    assert rest[0].seq == cut + 1 and all(e.seq > cut for e in rest)
    assert one_shot["events"][0]["seq"] == cut + 1


def test_subscription_has_no_gap_or_repeat():
    async def main():
        hub = Hub()
        for i in range(3):
            hub.publish(s.Notice(text=str(i)))
        sub = hub.subscribe(since=1)
        hub.publish(s.Notice(text="3"))  # lands both in backlog-range and queue
        got = [await sub.next(timeout=0.1) for _ in range(3)]
        assert await sub.next(timeout=0.05) is None
        return [e.seq for e in got]

    assert asyncio.run(main()) == [2, 3, 4]


def test_unknown_command_rejected(sock_dir):
    async def main():
        srv, where = await _start(sock_dir)
        try:
            async with Client(**where) as c:
                bad = await c.send_raw(json.dumps({"id": "c1", "type": "session.nuke", "body": {}}))
                junk = await c.send_raw("not json")
                field = await c.send_raw(json.dumps({"id": "c2", "type": "hello", "body": {"x": 1}}))
                unsupported = await c.send(s.SeatList())
                hello = await c.call(s.Hello(client="t"))
            return bad, junk, field, unsupported, hello
        finally:
            await srv.close()

    bad, junk, field, unsupported, hello = _run(main())
    assert not bad.ok and bad.id == "c1" and bad.error.code == "unknown_command"
    assert junk.error.code == "bad_json"
    assert field.error.code == "bad_body" and field.id == "c2"
    assert unsupported.error.code == "unsupported"
    assert hello["protocol"] == s.PROTOCOL_VERSION


def test_tcp_requires_token(sock_dir):
    with pytest.raises(ValueError):
        asyncio.run(HttpServer(Dispatcher(EchoEngine())).start_tcp("127.0.0.1", 0))

    async def main():
        srv, where = await _start(sock_dir, token="sekrit", tcp=True)
        try:
            async with Client(**where) as anon, Client(**where, token="wrong") as wrong, \
                    Client(**where, token="sekrit") as ok:
                r1 = await anon.send(s.Hello())
                r2 = await wrong.send(s.Hello())
                r3 = await ok.send(s.Hello())
                with pytest.raises(Exception):
                    async for _ in anon.events():
                        break
            return r1, r2, r3
        finally:
            await srv.close()

    r1, r2, r3 = _run(main())
    assert r1.error.code == r2.error.code == "http_401"
    assert r3.ok


def test_socket_is_private(sock_dir):
    async def main():
        srv, where = await _start(sock_dir)
        mode = os.stat(where["socket"]).st_mode & 0o777
        await srv.close()
        return mode

    assert asyncio.run(main()) == 0o600


def test_approval_round_trip(sock_dir):
    async def main():
        srv, where = await _start(sock_dir)
        try:
            async with Client(**where) as c:
                sid = (await c.call(s.SessionCreate()))["session"]["id"]
                await c.call(s.TurnSend(session=sid, text="approve: rm"))
                async for ev in c.events(session=sid):
                    if ev.type == "approval.requested":
                        await c.call(s.ApprovalRespond(request_id=ev.body.request_id, decision="deny"))
                    if ev.type == "message.completed":
                        return ev.body.message.text()
        finally:
            await srv.close()

    assert _run(main()) == "denied"


def test_stdio_lines():
    async def main():
        d = Dispatcher(EchoEngine())
        lines = [
            s.Command.of(s.EventsSubscribe(), id="c0").to_json(),
            s.Command.of(s.SessionCreate(title="x"), id="c1").to_json(),
            json.dumps({"id": "c2", "type": "nope", "body": {}}),
        ]
        out: list[str] = []

        async def read():
            await asyncio.sleep(0.01)
            return lines.pop(0) if lines else None

        await serve_lines(d, read, out.append)
        return [json.loads(x) for x in out]

    out = asyncio.run(main())
    responses = {o["id"]: o for o in out if "ok" in o}
    events = [o for o in out if "seq" in o]
    assert responses["c0"]["result"]["subscribed"] is True
    assert responses["c1"]["ok"] and responses["c2"]["error"]["code"] == "unknown_command"
    assert events[0]["type"] == "session.created"


def test_unix_socket_path_too_long_is_a_clear_error(tmp_path):
    srv = HttpServer(None)
    long_path = tmp_path / ("d" * 120) / "exod.sock"
    with pytest.raises(ValueError, match="socket path too long"):
        asyncio.run(srv.start_unix(long_path))
