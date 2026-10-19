"""The window's event feed: it starts whatever boot does after hello, it outlives an event this window cannot read,
and when it dies the window says so. Without it a turn is accepted and no reply or approval card ever shows."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from exo.protocol import schema as s
from exo.protocol.client import Client
from exo_tui.app import ExoApp
from exo_tui.widgets import ApprovalCard
from test_tui import FakeServerClient, make_engine, send, until


def boot(tmp_path, scenario, client_cls=FakeServerClient, **kw):
    eng = make_engine(tmp_path)
    client = client_cls(eng)

    async def main():
        app = ExoApp(client, seat="tester", **kw)
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await scenario(app, pilot, client, eng)

    asyncio.run(main())


def pumping(app) -> bool:
    return any(w.name == "pump" for w in app.workers)


def test_unknown_session_opens_a_new_thread_that_streams(tmp_path):
    async def sc(app, pilot, client, eng):
        assert pumping(app)
        assert app.session and app.session != "s_gone"
        text = app.transcript_text()
        assert "no thread s_gone; started a new one" in text and "cannot start" not in text
        await send(app, pilot, "hello")
        await until(lambda: "Hello **there**, friend" in app.transcript_text() and not app.busy, pilot)

    boot(tmp_path, sc, session="s_gone")


class RefusesOnce(FakeServerClient):
    """The first command of type ``refuse`` fails with a refusal that is not not_found."""

    refuse: type = s.SessionResume

    def __init__(self, engine):
        super().__init__(engine)
        self.refused = False

    async def send(self, body):
        if isinstance(body, self.refuse) and not self.refused:
            self.refused = True
            return s.Response(id="x", ok=False, error=s.ErrorInfo(code="forbidden", message="refused"))
        return await super().send(body)


@pytest.mark.parametrize("refuse,kw", [(s.SessionResume, {"session": "s_other"}),
                                       (s.SessionList, {"resume": True}),
                                       (s.SessionCreate, {})], ids=["resume", "list", "create"])
def test_boot_failure_after_hello_still_starts_the_feed(tmp_path, refuse, kw):
    cls = type("Refuses", (RefusesOnce,), {"refuse": refuse})

    async def sc(app, pilot, client, eng):
        assert "cannot start: ClientError: forbidden: refused" in app.transcript_text()
        assert pumping(app)
        before = app.session
        await send(app, pilot, "/new")
        await until(lambda: app.session not in (None, before), pilot)
        await send(app, pilot, "ask")  # the approval card arrives through the feed
        await until(lambda: isinstance(app.card, ApprovalCard), pilot)
        await pilot.press("ctrl+f")
        await pilot.press("o")
        await until(lambda: "after ask" in app.transcript_text() and not app.busy, pilot)

    boot(tmp_path, sc, client_cls=cls, **kw)


BASE = 10_000  # past anything the in-process engine has published


def newer_core(request: httpx.Request) -> httpx.Response:
    frames = [
        {"id": "e1", "seq": BASE + 1, "type": "ui.panel",
         "body": {"panel_id": "p1", "action": "open", "kind": "gauge", "title": "G", "text": "gauge text"}},
        {"id": "e2", "seq": BASE + 2, "type": "notice", "body": {"level": "info"}},  # no text: cannot be read
        {"id": "e3", "seq": BASE + 3, "type": "notice", "body": {"text": "NOTICE-AFTER"}},
    ]
    since = int(request.url.params.get("since", 0))
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames if f["seq"] > since)
    return httpx.Response(200, content=body.encode())


class JoinedToNewerCore(FakeServerClient):
    """Commands go to the in-process engine; events come over HTTP and SSE through the real protocol Client, as when
    the window is joined to exod or to another window's core, here one newer than this window."""

    def __init__(self, engine):
        super().__init__(engine)
        self.remote = Client(url="http://exod")
        self.remote._http = httpx.AsyncClient(base_url="http://exod", transport=httpx.MockTransport(newer_core))

    async def events(self, since=0, session=None, reconnect=False, backoff=0.5):
        async for ev in self.remote.events(since=BASE, session=session, reconnect=reconnect, backoff=backoff):
            yield ev


def test_feed_outlives_an_event_this_window_cannot_read(tmp_path):
    async def sc(app, pilot, client, eng):
        await until(lambda: "NOTICE-AFTER" in app.transcript_text(), pilot)
        assert "p1" in app.panels  # the newer kind is shown through its text fallback
        assert pumping(app)
        assert "event feed stopped" not in app.transcript_text()

    boot(tmp_path, sc, client_cls=JoinedToNewerCore)


def test_a_dead_feed_is_reported_not_silent(tmp_path):
    class DeadFeed(FakeServerClient):
        def __init__(self, engine):
            super().__init__(engine)
            self.ran: list[str] = []

        async def send(self, body):
            if isinstance(body, s.CommandRun):
                self.ran.append(body.name)
            return await super().send(body)

        async def events(self, since=0, session=None, reconnect=False, backoff=0.5):
            raise RuntimeError("feed broke")
            yield  # an async generator, like the real ones

    async def sc(app, pilot, client, eng):
        await until(lambda: "event feed stopped: RuntimeError: feed broke" in app.transcript_text(), pilot)
        assert "/restart" in app.transcript_text()
        await until(lambda: "harness_error" in client.ran, pilot)  # the harness is told (D-025)

    boot(tmp_path, sc, client_cls=DeadFeed)
