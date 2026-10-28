"""The existing command transport exposes fresh only to the live principal."""
import asyncio

from exo.core.engine import CoreEngine
from exo.core.types import Message
from exo.protocol import schema as s
from exo.server.app import Dispatcher


def test_fresh_command_is_principal_only_and_does_not_rewrite_the_transcript(tmp_path):
    async def exercise():
        engine = CoreEngine(tmp_path, {"roles": {"main": ["fake:fixture"]}})
        dispatcher = Dispatcher(engine, engine.hub)
        session = await engine.loop.create_session(agent=engine.agent)
        engine.store.append_message(session.id, Message.user("saved original input"))
        before = engine.store.last_seq(session.id)
        command = s.Command.of(s.CommandRun(name="fresh", session=session.id))
        refused = await dispatcher.run(command, "local")
        assert not refused.ok and refused.error.code == "forbidden"
        accepted = await dispatcher.run(command, "principal:tui")
        assert accepted.ok and accepted.result["output"]["reset"]
        assert engine.store.last_seq(session.id) == before
        assert not engine.loop.state(session.id).running
        repeated = await dispatcher.run(command, "principal:tui")
        assert repeated.ok and not repeated.result["output"]["reset"]
        commands = await dispatcher.run(s.Command.of(s.CommandsList()), "principal:tui")
        assert any(row["name"] == "fresh" for row in commands.result["commands"])
        await engine.close()
    asyncio.run(exercise())
