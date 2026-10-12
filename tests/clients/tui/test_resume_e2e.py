import asyncio

from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient
from test_tui import FakeServerClient, make_engine, send, until


def test_second_process_resumes_same_session(tmp_path):
    async def first():
        app = ExoApp(FakeServerClient(make_engine(tmp_path)), seat="tester")
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await send(app, pilot, "hello")
            await until(lambda: "Hello **there**" in app.transcript_text() and not app.busy, pilot)
            return app.session

    async def second(sid):
        app = ExoApp(FakeServerClient(make_engine(tmp_path)), session=sid, seat="tester")
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            return app.session, app.transcript_text()

    sid = asyncio.run(first())
    got, text = asyncio.run(second(sid))
    assert got == sid and "Hello **there**" in text and "resumed" in text and sid in text
