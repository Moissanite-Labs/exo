"""Focused HTTP regression: malformed Content-Length on /api/approvals must answer 400."""
from __future__ import annotations

import asyncio
import json

from exo.core.approvals import Pending
from exo.core.engine import CoreEngine
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo_command.config import load_settings
from exo_command.server import MAX_BODY, WebServer
from exo_command.service import CommandCentre
from exo_command.source import DispatcherSource


def make(tmp_path):
    (tmp_path / ".git").mkdir(exist_ok=True)
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={})
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    return eng, Dispatcher(eng)


async def raw(port, content_length, body=b"", extra=None):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    hs = "X-Exo-Token: t0k\r\n"
    if extra:
        hs += "".join("{}: {}\r\n".format(k, v) for k, v in extra.items())
    w.write(
        "POST /api/approvals/r9 HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nContent-Length: {}\r\n{}\r\n".format(
            port, content_length, hs
        ).encode()
        + body
    )
    await w.drain()
    try:
        data = await asyncio.wait_for(r.read(), 5)
    except Exception:
        data = b""
    w.close()
    head = data.split(b"\r\n", 1)[0]
    status = int(head.split()[1]) if len(head.split()) >= 2 else 0
    return status, data


def test_approval_malformed_content_length_answers_400(tmp_path):
    eng, disp = make(tmp_path)

    async def main():
        centre = CommandCentre(load_settings(), DispatcherSource(disp))
        await centre.bootstrap()
        web = WebServer(centre, token="t0k")
        port = await web.start("127.0.0.1", 0)

        sid = (await centre.source.call(s.SessionCreate(title="g")) )["session"]["id"]
        fut = asyncio.get_running_loop().create_future()
        req = s.ApprovalRequested(request_id="r9", summary="write file")
        eng.approvals.open["r9"] = Pending(fut, sid, req)
        eng.hub.publish(req, sid)
        await centre.sync()

        # empty header must not drop the connection (pre-fix dropped it)
        status, _ = await raw(port, "", b'{"decision":"allow"}')
        assert status == 400, status
        assert not fut.done()

        # malformed non-numeric header must not drop the connection
        status, _ = await raw(port, "not-a-number", b'{"decision":"allow"}')
        assert status == 400, status
        assert not fut.done()

        # negative length must also be rejected (-1 already returned 400 on old source)
        status, _ = await raw(port, "-1", b"")
        assert status == 400, status
        assert not fut.done()

        # oversize length must be rejected (already returned 400 on old source)
        status, _ = await raw(port, str(MAX_BODY + 1), b"")
        assert status == 400, status
        assert not fut.done()

        # valid request still works
        body = json.dumps({"decision": "allow"}).encode()
        status, _ = await raw(port, str(len(body)), body)
        assert status == 200, status
        assert fut.result() == "allow"

        await web.close()

    asyncio.run(main())
