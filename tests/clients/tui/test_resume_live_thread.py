"""Opening a thread whose turn is still running (/resume, /sessions, --session, --resume) writes no false 'not run'
result into that turn: session.resume leaves a running turn's open tool call alone (its approval is still pending)."""

import asyncio

import pytest

from exo.protocol import schema as s
from exo_tui.app import ExoApp
from test_tui import FakeServerClient, make_engine, run, send, until


def results(eng, sid):
    return [(r.call_id, r.text()) for m in eng.store.messages(sid) for r in m.tool_results]


async def allow_and_finish(eng, sid, pilot):
    rid = next(iter(eng.approvals.open))
    eng.approvals.open[rid].future.set_result("allow")
    await until(lambda: not eng.loop.state(sid).running, pilot)
    (got,) = results(eng, sid)  # one result for the one call, and it is the real one
    assert "asked" in got[1] and "not run" not in got[1]


def test_resume_back_into_a_thread_waiting_on_an_approval(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        a = app.session
        await send(app, pilot, "/new")
        await until(lambda: app.session != a, pilot)
        await send(app, pilot, f"/resume {a}")
        await until(lambda: app.session == a, pilot)
        assert results(eng, a) == []  # the call that waits on the approval is still open
        await allow_and_finish(eng, a, pilot)

    run(tmp_path, sc)


@pytest.mark.parametrize("how", ["session", "resume"])
def test_a_window_started_on_a_running_thread(tmp_path, how):
    """``--session <id>`` and ``--resume`` (the newest thread) while another window's turn waits on an approval."""
    eng = make_engine(tmp_path)
    client = FakeServerClient(eng)

    async def main():
        a = (await client.call(s.SessionCreate(title="busy", seat="tester")))["session"]["id"]
        await client.call(s.TurnSend(session=a, text="ask"))
        while not eng.approvals.open:
            await asyncio.sleep(0.02)
        app = ExoApp(client, seat="tester", **({"session": a} if how == "session" else {"resume": True}))
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            assert app.session == a and results(eng, a) == []
            await allow_and_finish(eng, a, pilot)

    asyncio.run(main())
