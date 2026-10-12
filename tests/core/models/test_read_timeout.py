"""A provider that accepts a request but never answers must time out and let the router fall back."""

import asyncio

import httpx

from exo.core.models.router import Router
from exo.core.types import ContentPart, Message


def test_hanging_route_falls_back_to_next(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hang.test":
            raise httpx.ReadTimeout("no bytes", request=request)  # what httpx raises when read= expires
        body = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = {"providers": {"hang": {"base_url": "https://hang.test/v1", "key_env": "HANG_KEY", "read_timeout": 5},
                         "ok": {"base_url": "https://ok.test/v1", "key_env": "OK_KEY"}},
           "roles": {"main": ["hang:m", "ok:m"]}}
    r = Router.from_config(cfg, env={"HANG_KEY": "x", "OK_KEY": "y"}, client=client)

    async def run():
        return [ev async for ev in r.provider("main").stream(
            [Message(role="user", content=[ContentPart(type="text", text="hi")])], [])]

    evs = asyncio.run(run())
    done = [e for e in evs if e.kind == "done"]
    assert done and done[0].route == "ok:m" and not [e for e in evs if e.kind == "error"]
    prov = r.credentials("hang")[0].provider
    assert prov._timeout.read == 5.0 and prov._timeout.connect == 15.0


def test_fallback_publishes_notice():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hang.test":
            raise httpx.ReadTimeout("no bytes", request=request)
        body = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = {"providers": {"hang": {"base_url": "https://hang.test/v1", "key_env": "HANG_KEY", "read_timeout": 60},
                         "ok": {"base_url": "https://ok.test/v1", "key_env": "OK_KEY"}},
           "roles": {"main": ["hang:m", "ok:m"]}}
    r = Router.from_config(cfg, env={"HANG_KEY": "x", "OK_KEY": "y"}, client=client)

    async def run():
        return [ev async for ev in r.provider("main").stream(
            [Message(role="user", content=[ContentPart(type="text", text="hi")])], [])]

    evs = asyncio.run(run())
    notes = [e for e in evs if e.kind == "notice"]
    assert [n.text for n in notes] == ["hang:m did not answer in 60 s; trying ok:m"]
    assert evs.index(notes[0]) < [e.kind for e in evs].index("text")
