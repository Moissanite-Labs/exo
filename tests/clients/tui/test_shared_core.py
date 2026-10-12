"""Two windows of one seat share one core (D-028): the second joins the first's socket; when the first closes,
the second takes over and keeps the same memory."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from exo.protocol import schema as s
from exo_tui.conn import LocalClient, SharedClient, connect

CONFIG = {"models": {"fake": {"rules": [{"match": "hello", "steps": ["hi there"]}]},
                     "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
          "core_plugins": [], "learn": {"enabled": False}, "schedule": False, "peers": {"enabled": False}}


@pytest.fixture
def home(monkeypatch):
    d = Path(tempfile.mkdtemp(dir="/tmp", prefix="exo-"))  # unix socket paths must stay short
    (d / "config.json").write_text(json.dumps(CONFIG))
    monkeypatch.setenv("EXO_HOME", str(d))
    monkeypatch.delenv("EXO_SEAT", raising=False)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_second_window_joins_then_takes_over(home):
    async def go():
        one = connect()
        assert isinstance(one, LocalClient)
        a = (await one.call(s.SessionCreate(title="A")))["session"]  # starts serving the agent socket
        two = connect()
        assert isinstance(two, SharedClient)
        b = (await two.call(s.SessionCreate(title="B")))["session"]
        assert b["agent"] == "default"
        listed = {r["id"] for r in (await two.call(s.SessionList()))["sessions"]}
        assert {a["id"], b["id"]} <= listed  # one core: both windows see both threads
        await one.aclose()  # the window holding the core closes
        again = (await two.call(s.SessionList()))["sessions"]  # the second window takes over
        assert {a["id"], b["id"]} <= {r["id"] for r in again}
        assert isinstance(two.inner, LocalClient)
        await two.aclose()
    asyncio.run(go())
