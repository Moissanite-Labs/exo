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


def test_stop_on_the_fallback_route_keeps_the_timeout_of_the_route_before(tmp_path):
    """hang:m times out, ok:m starts streaming, the user stops: the recorded call still shows hang:m's timeout."""
    from exo.core import trace
    from exo.core.loop import Loop
    from exo.core.sessions import EventBus, SessionStore

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hang.test":
            raise httpx.ReadTimeout("no bytes", request=request)

        async def body():
            yield b'data: {"choices":[{"delta":{"content":"hi "}}]}\n\n'
            await asyncio.Event().wait()  # the fallback streams, then stalls until the stop
        return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = {"providers": {"hang": {"base_url": "https://hang.test/v1", "key_env": "HANG_KEY", "read_timeout": 60},
                         "ok": {"base_url": "https://ok.test/v1", "key_env": "OK_KEY"}},
           "roles": {"main": ["hang:m", "ok:m"]}}
    r = Router.from_config(cfg, env={"HANG_KEY": "x", "OK_KEY": "y"}, client=client)
    store = SessionStore(tmp_path / "s.sqlite")
    bus = EventBus(store)
    seen = []
    bus.subscribe(seen.append)
    loop = Loop(store, r.provider("main"), bus=bus, retry_delay=0, model="hang:m")

    async def go():
        s = await loop.create_session()
        task = asyncio.create_task(loop.send(s.id, "go"))
        while not any(e.type == "message.delta" for e in seen):
            await asyncio.sleep(0.01)
        assert loop.interrupt(s.id)
        return s, await asyncio.wait_for(task, 5)

    s, res = asyncio.run(go())
    assert res.stop_reason == "interrupted"
    (call,) = [e.body for e in seen if e.type == "model.call"]
    assert call.stop_reason == "interrupted" and [(a["route"], a["outcome"]) for a in call.attempts] == [("hang:m", "timeout")]
    assert next(e.body.timing for e in seen if e.type == "turn.completed")["fallbacks"] == 0
    t = trace.last_turn(store, s.id)
    assert not t.derived and t.fallbacks == 0  # interruption never confirmed a served fallback route
    assert "model.timeout:hang:m" in [f.signature for f in trace.detect(t, trace.Thresholds())]
    # The stop came on ok:m: neither hang:m's wait nor ok:m's stream is charged to hang:m as a slow call.
    scaled = [f.signature for f in trace.detect(t, trace.Thresholds(model_call_s=0.001))]
    assert "model.timeout:hang:m" in scaled and not [x for x in scaled if x.startswith("model.slow")]
