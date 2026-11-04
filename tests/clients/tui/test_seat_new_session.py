"""`/new` on a seated core keeps that core's seat after a thread with another seat label is resumed."""

from __future__ import annotations

import asyncio

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.core.seats import Seat
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient

MODELS = {"fake": {"rules": []}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def _engine(home, cwd, name):
    return CoreEngine(
        home, MODELS, plugins=PluginManager(), cwd=str(cwd), env={},
        seat=Seat(name=name) if name else None,
    )


def test_new_after_resuming_another_seat_uses_this_core(tmp_path):
    async def run():
        home = tmp_path / "home"
        bare = _engine(home, tmp_path, None)
        try:
            # The worker owns this history; its display label is not its owner.
            scout_id = bare.store.create_session(agent="worker", seat="scout", title="scout work").id
        finally:
            await bare.close()

        worker = _engine(home, tmp_path, "worker")
        app = ExoApp(LocalClient(worker), seat=None)
        try:
            async with app.run_test(size=(120, 40)) as _pilot:
                await asyncio.wait_for(app.ready.wait(), 5)
                assert app.launch_seat is None
                await app.load_session(scout_id)
                assert app.seat == "scout"
                previous = app.session
                await app.new_session()
                assert app.session and app.session != previous
                assert worker.store.get_session(app.session).seat == "worker"
                assert "bad_request" not in app.transcript_text()
        finally:
            await worker.close()

    asyncio.run(run())


def test_window_started_for_another_seat_joins_this_core(tmp_path):
    async def run():
        worker = _engine(tmp_path / "home", tmp_path, "worker")
        app = ExoApp(LocalClient(worker), seat="scout")
        try:
            async with app.run_test(size=(120, 40)) as _pilot:
                await asyncio.wait_for(app.ready.wait(), 5)
                assert app.launch_seat == "scout"
                assert app.session
                assert worker.store.get_session(app.session).seat == "worker"
                assert "joined worker" in app.transcript_text()
        finally:
            await worker.close()

    asyncio.run(run())
