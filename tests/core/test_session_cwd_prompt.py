"""The system text names the session's real cwd on every route (readiness blocker: on the claude-cli route the agent
reported the CLI's private folder). One line per session, the same on every call, so the prompt cache holds."""
from __future__ import annotations

import asyncio

from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider
from exo.protocol import schema as s
from exo.server.app import Dispatcher


class Recorder:
    """Records the system text of every main call, then answers."""

    name = "fake"

    def __init__(self):
        self.systems: list[str] = []

    async def stream(self, messages, tools, **opts):
        self.systems.append(messages[0].text() if messages and messages[0].role == "system" else "")
        async for ev in FakeProvider(["done"]).stream(messages, tools):
            yield ev


def _eng(tmp_path):
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={})
    eng.loop.provider = rec = Recorder()
    return eng, rec


async def _turn(d, eng, sid, text):
    await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))
    while eng._tasks:
        await asyncio.sleep(0.01)


def test_each_session_names_its_own_cwd_on_every_call(tmp_path):
    eng, rec = _eng(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    async def main():
        d = Dispatcher(eng)
        a = (await d.run(s.Command.of(s.SessionCreate(cwd=str(tmp_path / "a"))))).result["session"]
        b = (await d.run(s.Command.of(s.SessionCreate(cwd=str(tmp_path / "b"))))).result["session"]
        for sid in (a["id"], a["id"], b["id"]):
            await _turn(d, eng, sid, "where am I?")
        return a["cwd"], b["cwd"], await eng.loop.base_system(a["id"])

    cwd_a, cwd_b, base = asyncio.run(main())
    first, second, other = rec.systems
    assert f"Your working directory is {cwd_a}." in first and cwd_b not in first
    assert "overrides any other working directory" in first  # it outranks a CLI's own environment note
    assert first == second  # byte-stable within the session: the prompt cache holds
    assert f"Your working directory is {cwd_b}." in other and cwd_a not in other
    assert f"Your working directory is {cwd_a}." in base  # /context and /compact count the same line


def test_a_session_without_a_cwd_gets_no_line(tmp_path):
    eng, rec = _eng(tmp_path)

    async def main():
        bare = await eng.loop.create_session(title="bare")  # a plain loop session: no cwd on record
        await eng.loop.send(bare.id, "hi")
        d = Dispatcher(eng)
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]  # the engine's default
        await _turn(d, eng, sid, "hi")
        return eng.store.get_session(sid).cwd

    cwd = asyncio.run(main())
    assert "Your working directory is" not in rec.systems[0]
    assert f"Your working directory is {cwd}." in rec.systems[1]
