import asyncio

from exo_tui.app import ExoApp
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
        async with app.run_test(size=(120, 40)) as _pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            return app.session, app.transcript_text()

    sid = asyncio.run(first())
    got, text = asyncio.run(second(sid))
    assert got == sid and "Hello **there**" in text and "resumed" in text and sid in text


def launch(tmp_path, resume=False, text="", new=False):
    """One `exo` run on a shared home: optionally say something or open a /new thread, then quit."""
    async def main():
        app = ExoApp(FakeServerClient(make_engine(tmp_path)), resume=resume, seat="tester")
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            if text:
                await send(app, pilot, text)
                await until(lambda: "Hello **there**" in app.transcript_text() and not app.busy, pilot)
            if new:
                first = app.session
                await send(app, pilot, "/new")
                await until(lambda: app.session != first, pilot)
            return app.session, app.transcript_text()

    return asyncio.run(main())


def test_resume_skips_threads_with_no_messages(tmp_path):
    """Every launch opens a thread, so a look-and-quit or an unused /new left an empty one on top of the list, and
    `exo --resume` showed it instead of the work."""
    worked, _ = launch(tmp_path, text="hello")
    launch(tmp_path, new=True)  # a plain launch and a /new, both left empty
    got, text = launch(tmp_path, resume=True)
    assert got == worked
    assert "Hello **there**" in text and f"({worked}, 2 messages)" in text


def test_resume_with_only_empty_threads_starts_a_new_one(tmp_path):
    empty, _ = launch(tmp_path)
    got, text = launch(tmp_path, resume=True)
    assert got != empty and "resumed" not in text
