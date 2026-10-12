import asyncio
import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).with_name("fixtures")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class Recorder:
    """httpx mock transport that replays canned responses and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body, headers = self.responses.pop(0)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, content=body, headers={"content-type": "text/event-stream", **headers})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def body(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content)


def collect(agen):
    async def run():
        return [ev async for ev in agen]

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real socket use fails the test."""
    import socket

    def guard(*a, **k):
        raise RuntimeError("network access in models tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
