"""Session labels must identify the loaded seat, not impersonate another profile."""

from __future__ import annotations

import asyncio

import pytest

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.core.seats import Seat
from exo.protocol import schema as s
from exo.server.app import Dispatcher

MODELS = {"fake": {"rules": []}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def _engine(tmp_path, name):
    return CoreEngine(
        tmp_path / "home", MODELS, plugins=PluginManager(), cwd=str(tmp_path),
        env={}, seat=Seat(name=name) if name else None,
    )


@pytest.mark.parametrize("loaded,payload,expected", [
    ("worker", {}, "worker"),
    ("worker", {"seat": ""}, "worker"),
    ("worker", {"seat": "worker"}, "worker"),
    (None, {"seat": "scout"}, "scout"),
    (None, {}, None),
])
def test_session_seat_binding(tmp_path, loaded, payload, expected):
    async def run():
        eng = _engine(tmp_path, loaded)
        try:
            response = await Dispatcher(eng).run(s.Command.of(s.SessionCreate(**payload)))
            assert response.ok, response.error
            info = response.result["session"]
            assert info["seat"] == expected
            assert eng.store.get_session(info["id"]).seat == expected
        finally:
            await eng.close()
    asyncio.run(run())


def test_different_seat_refused_without_creating_session(tmp_path):
    async def run():
        eng = _engine(tmp_path, "worker")
        try:
            before = len(eng.store.list_sessions())
            response = await Dispatcher(eng).run(s.Command.of(s.SessionCreate(seat="scout")))
            assert not response.ok
            assert response.error.code == "bad_request"
            assert "scout" in response.error.message and "worker" in response.error.message
            assert len(eng.store.list_sessions()) == before
        finally:
            await eng.close()
    asyncio.run(run())
