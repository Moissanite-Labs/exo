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


def test_joined_window_answers_an_approval_as_principal_and_sees_it_close(home):
    """D-045: the approval card of a window that joined the core works end to end over the shared socket with the
    principal credential, and the resolved event reaches that window's own stream."""
    async def go():
        one = connect()
        sid = (await one.call(s.SessionCreate(title="A")))["session"]["id"]
        two = connect()
        assert isinstance(two, SharedClient)
        seen: list[str] = []

        async def watch():
            async for ev in two.events(since=0):
                seen.append(ev.type)
                if ev.type == "approval.resolved":
                    return

        w = asyncio.ensure_future(watch())
        ask = asyncio.ensure_future(one.engine.ask(sid, "run it?", None))
        for _ in range(100):
            rows = (await two.call(s.ApprovalsList()))["approvals"]
            if rows:
                break
            await asyncio.sleep(0.02)
        assert rows, "the joined window sees the open approval"
        await two.call(s.ApprovalRespond(request_id=rows[0]["request_id"], decision="allow", client="tui"))
        assert await asyncio.wait_for(ask, 5) == "allow"
        await asyncio.wait_for(w, 5)
        assert "approval.requested" in seen and "approval.resolved" in seen
        assert (await two.call(s.ApprovalsList()))["approvals"] == []
        await one.aclose()
        await two.aclose()
    asyncio.run(go())
