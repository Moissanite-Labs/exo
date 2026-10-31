"""A transport keeps one HTTP client per event loop and closes it on shutdown (#263, the HTTP half of W2-7).

Each stream used to open its own ``httpx.AsyncClient`` and close it after, so no call reused a connection or a TLS
session. A client's connections belong to the loop that opened them, so a call on another loop (``aux_sync``'s
thread, a command's ``asyncio.run``) gets its own client. Hermetic: every client runs on a mock transport.
"""

import asyncio
import gc
import threading
import weakref

import httpx
import pytest

from exo.core.engine import CoreEngine
from exo.core.models.decision import DecisionProvider
from exo.core.models.openai_chat import OpenAIChatProvider
from exo.core.models.router import Router
from exo.core.types import Message

SSE = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
       b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
NOUL = {"q": {"type": "noul", "instructions": "is it?"}}


def answer(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/systemone"):
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.9}}})
    return httpx.Response(200, content=SSE, headers={"content-type": "text/event-stream"})


@pytest.fixture
def made(monkeypatch):
    """Every client a transport makes for itself, each on a mock transport."""
    clients: list[httpx.AsyncClient] = []

    class Counted(httpx.AsyncClient):
        def __init__(self, **kw):
            super().__init__(transport=httpx.MockTransport(answer), **kw)
            clients.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", Counted)
    return clients


async def chat(provider) -> list:
    return [ev async for ev in provider.stream([Message.user("hi")], [])]


@pytest.fixture
def other_loop():
    """A second running loop in its own thread, as ``aux_sync`` keeps one."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


def on(loop, coro):
    return asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop))


def test_streams_on_one_loop_share_one_client_and_close_releases_it(made):
    provider = OpenAIChatProvider("https://chat.test/v1", "k", model="m")

    async def run():
        for _ in range(3):
            assert (await chat(provider))[-1].kind == "done"
        assert len(made) == 1 and not made[0].is_closed
        await provider.aclose()
        assert made[0].is_closed

    asyncio.run(run())


def test_decision_calls_share_one_client_and_close_releases_it(made):
    provider = DecisionProvider("https://jev.test/v1", "k")

    async def run():
        for _ in range(3):
            assert (await provider.decide("state", NOUL)).answers["q"].noul == 0.9
        assert len(made) == 1 and not made[0].is_closed
        await provider.aclose()
        assert made[0].is_closed

    asyncio.run(run())


def test_each_running_loop_keeps_its_own_client_and_close_reaches_both(made, other_loop):
    provider = OpenAIChatProvider("https://chat.test/v1", "k", model="m")

    async def run():
        for _ in range(2):  # the calls alternate between the loops, as turns and aux_sync calls do
            assert (await chat(provider))[-1].kind == "done"
            assert (await on(other_loop, chat(provider)))[-1].kind == "done"
        assert len(made) == 2 and not any(c.is_closed for c in made)
        await provider.aclose()  # the other loop's client is closed on that loop
        assert all(c.is_closed for c in made)

    asyncio.run(run())


def test_a_loop_that_ended_does_not_keep_its_client(made):
    provider = OpenAIChatProvider("https://chat.test/v1", "k", model="m")
    asyncio.run(chat(provider))
    asyncio.run(chat(provider))  # a new loop: the first loop's client cannot serve it
    assert len(made) == 2
    first = weakref.ref(made.pop(0))
    gc.collect()
    assert first() is None  # the transport let it go


def test_router_close_closes_every_client_it_made_also_after_a_key_reload(made):
    given = OpenAIChatProvider("https://given.test/v1", "k", model="m")  # a plugin's instance: not the router's
    router = Router.from_config({"providers": {"chat": {"base_url": "https://chat.test/v1"},
                                               "jev": {"transport": "decision", "base_url": "https://jev.test/v1",
                                                       "roles": ["decision"]}},
                                 "roles": {"main": ["chat:m"], "aux": ["given:m"]}}, env={},
                                instances={"given": given})

    async def run():
        for _ in range(2):
            assert (await chat(router.provider("main")))[-1].kind == "done"
        router.reload_credentials("chat")  # a key change: the next call gets a new transport
        assert (await chat(router.provider("main")))[-1].kind == "done"
        await router.credentials("jev")[0].provider.decide("state", NOUL)
        assert (await chat(router.provider("aux")))[-1].kind == "done"
        assert len(made) == 4 and not any(c.is_closed for c in made)
        await router.aclose()
        assert [c.is_closed for c in made] == [True, True, True, False]
        await given.aclose()

    asyncio.run(run())


def test_engine_close_closes_the_router(tmp_path):
    eng = CoreEngine(tmp_path / "home", {"fake": {}, "roles": {"aux": ["fake:x"]}}, cwd=str(tmp_path))
    closed = []

    async def aclose():
        closed.append(True)

    eng.router.aclose = aclose
    asyncio.run(eng.close())
    assert closed == [True]
