"""The MCP auth gate verifies the query exactly as the app receives it, and refuses what it cannot verify."""

import asyncio

from fastapi import HTTPException
from nacl.signing import SigningKey

from exo.bus import wire
from exo.bus.mcp_server import AuthGate


class _Relay:
    """Verifies as Relay.authed_seat does, against one seat's key."""

    def __init__(self, pub):
        self.pub, self.replay = pub, wire.ReplayCache()

    def authed_seat(self, headers, method, path, query=""):
        try:
            return wire.verify_request(
                headers, method, path, lambda s: self.pub, self.replay, query=query
            )
        except ValueError as e:
            raise HTTPException(401, str(e)) from None


def _call(query_string: bytes, signed_query: str = "since=0&to=x"):
    key = SigningKey.generate()
    headers = wire.request_headers("s", key, "POST", "/mcp", query=signed_query)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "query_string": query_string,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    sent, reached = [], []

    async def app(scope, receive, send):
        reached.append(scope)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(AuthGate(app, _Relay(wire.pubkey_b64(key)))(scope, receive, send))
    return sent, reached


def test_a_signed_query_reaches_the_app():
    sent, reached = _call(b"since=0&to=x")
    assert len(reached) == 1 and sent == []


def test_a_fragment_suffix_is_refused_with_401_and_never_reaches_the_app():
    sent, reached = _call(b"since=0&to=x#&to=group")
    assert sent[0]["status"] == 401 and reached == []


def test_a_query_that_is_not_utf8_is_refused_with_401_and_never_reaches_the_app():
    sent, reached = _call(b"since=0&to=x\xff")
    assert sent[0]["status"] == 401 and reached == []


def test_a_stripped_query_is_refused_with_401_and_never_reaches_the_app():
    sent, reached = _call(b"")
    assert sent[0]["status"] == 401 and reached == []
