"""After the page's event stream ends (the core ends a stream that falls behind, D-079, or the core restarts), the
re-bootstrap resumes from the last event the page applied, so nothing published in between is lost."""
from __future__ import annotations

import asyncio

from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo.server.echo import EchoEngine
from exo.server.engine import Hub
from exo_command.config import load_settings
from exo_command.service import REPLAY_WINDOW, RESUME_GAP, CommandCentre
from exo_command.source import DispatcherSource


class Recording(DispatcherSource):
    def __init__(self, dispatcher):
        super().__init__(dispatcher)
        self.since: list[int] = []

    async def call(self, body):
        if isinstance(body, s.EventsSubscribe):
            self.since.append(body.since)
        return await super().call(body)


def test_rebootstrap_resumes_from_the_page_so_an_approval_older_than_the_window_shows():
    """A reconnect replays from the page's own seq, so an approval asked more than a replay window ago still shows.
    A newer core whose log is behind the page (a fresh EXO_HOME) cannot resume it and replays the window instead."""
    async def go():
        hub = Hub()
        disp = Dispatcher(EchoEngine(), hub)

        async def run(body):
            return (await disp.run(s.Command.of(body), "principal:web")).result

        a = (await run(s.SessionCreate(title="A")))["session"]["id"]
        for i in range(REPLAY_WINDOW + 10):  # history from before the page started
            hub.publish(s.Notice(text=f"old {i}"))
        src = Recording(disp)
        c = CommandCentre(load_settings({}), src)
        await c.bootstrap()
        first = hub.seq
        assert src.since == [first - REPLAY_WINDOW] and c.state.seq == first  # a first start replays the window
        # The stream is down: thread A asks for approval, then more than a window of other events follows.
        await run(s.TurnSend(session=a, text="approve: deploy"))
        while not (await run(s.ApprovalsList()))["approvals"]:
            await asyncio.sleep(0.01)
        live = {x["request_id"] for x in (await run(s.ApprovalsList()))["approvals"]}
        for i in range(REPLAY_WINDOW + 10):
            hub.publish(s.ToolOutput(call_id="c", text=f"{i}\n"), a)
        await c.bootstrap()  # what the feed loop does once the stream has ended
        assert src.since[-1] == first  # resumed where the page stopped, not at the newest window
        assert live <= set(c.state.approvals) and c.state.seq == hub.seq
        fresh = Hub()  # a newer core with a fresh log, far behind the page
        fresh.publish(s.Notice(text="new core"))
        c.source = src = Recording(Dispatcher(EchoEngine(), fresh))
        await c.bootstrap()
        assert src.since == [0]

    asyncio.run(go())


def test_rebootstrap_after_a_gap_above_resume_gap_replays_the_window():
    """A gap far past the stream limit (a long outage while the core kept working) is not replayed in one response.
    Up to RESUME_GAP missed events the page resumes where it stopped; past that it replays the newest window, as a
    first start does."""
    async def go():
        hub = Hub()
        src = Recording(Dispatcher(EchoEngine(), hub))
        c = CommandCentre(load_settings({}), src)
        hub.publish(s.Notice(text="start"))
        await c.bootstrap()
        first = hub.seq
        for i in range(RESUME_GAP):
            hub.publish(s.Notice(text=f"n {i}"))
        await c.bootstrap()  # exactly RESUME_GAP behind: every missed event replays
        assert src.since[-1] == first and c.state.seq == hub.seq
        for i in range(RESUME_GAP + 1):
            hub.publish(s.Notice(text=f"m {i}"))
        await c.bootstrap()
        assert src.since[-1] == hub.seq - REPLAY_WINDOW and c.state.seq == hub.seq

    asyncio.run(go())
