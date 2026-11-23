"""#740: approval POSTs reject non-object JSON with an HTTP response, before reaching the core."""
import asyncio
import json

import pytest

from exo.protocol import schema as s
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre

TOKEN = "synthetic-approval-page-token"


class Source:
    def __init__(self):
        self.calls = []

    async def call(self, body):
        self.calls.append(body)
        return {}


async def request(port, path, data, token=TOKEN):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write((f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                  f"X-Exo-Token: {token}\r\nContent-Type: application/json\r\n"
                  f"Content-Length: {len(data)}\r\n\r\n").encode() + data)
    await writer.drain()
    try:
        response = await asyncio.wait_for(reader.read(), 2)
    finally:
        writer.close()
        await writer.wait_closed()
    assert response, "server closed the connection without an HTTP response"
    head, _, body = response.partition(b"\r\n\r\n")
    assert b"Content-Type: application/json\r\n" in head + b"\r\n"
    return int(head.split()[1]), json.loads(body)


def post(data, path="/api/approvals/r1", token=TOKEN):
    async def go():
        source = Source()
        web = WebServer(CommandCentre(load_settings({}), source), token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        try:
            return await request(port, path, data, token), source.calls
        finally:
            await web.close()
    return asyncio.run(go())


@pytest.mark.parametrize("value", [[], [{"decision": "allow"}], None, "allow", "", 1, True, False])
@pytest.mark.parametrize("path", ["/api/approvals/r1", "/api/chat/sessions"])
def test_non_object_json_returns_400_without_a_core_command(value, path):
    reply, calls = post(json.dumps(value).encode(), path)
    assert reply == (400, {"error": "body must be a json object"})
    assert calls == []


@pytest.mark.parametrize("data", [b"{", b"not json"])
def test_malformed_approval_json_keeps_its_error_response(data):
    reply, calls = post(data)
    assert reply == (400, {"error": "bad json"})
    assert calls == []


@pytest.mark.parametrize("decision", ["allow", "deny", "allow_always"])
def test_object_approval_reaches_the_core_with_its_note(decision):
    reply, calls = post(json.dumps({"decision": decision, "note": "test note"}).encode())
    assert reply == (200, {"ok": True})
    assert calls == [s.ApprovalRespond(request_id="r1", decision=decision, note="test note")]


@pytest.mark.parametrize("data", [b"", b"{}", b'{"decision":"maybe"}'])
def test_object_without_a_valid_decision_does_not_reach_the_core(data):
    reply, calls = post(data)
    assert reply[0] == 400 and "decision must be one of" in reply[1]["error"]
    assert calls == []


@pytest.mark.parametrize("token", ["", "wrong-token"])
def test_page_token_is_required_before_parsing_the_approval(token):
    reply, calls = post(b"null", token=token)
    assert reply[0] == 403 and "page token" in reply[1]["error"]
    assert calls == []
