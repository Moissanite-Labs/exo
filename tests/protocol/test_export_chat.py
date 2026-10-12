import asyncio
import argparse
import json

from exo import cli_chat
from exo.protocol import export
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo.server.echo import EchoEngine
from exo.server.engine import Hub


def test_schema_export_stable_and_checked_in():
    assert export.render() == export.render()
    for name, text in export.render().items():
        assert (export.OUT_DIR / name).read_text() == text, f"{name} stale: run python -m exo.protocol.export"
    assert export.main(["--check"]) == 0


def test_json_schema_covers_every_type():
    js = json.loads(export.render()[export.JSON_NAME])
    assert set(js["commands"]) == set(s.COMMANDS) and set(js["events"]) == set(s.EVENTS)
    turn = js["$defs"]["TurnSend"]
    assert turn["required"] == ["session"] and turn["additionalProperties"] is False
    assert turn["properties"]["mode"] == {"enum": ["send", "queue", "steer"], "default": "send"}
    assert turn["properties"]["parts"]["items"] == {"$ref": "#/$defs/ContentPart"}
    for ref in json.dumps(js).split('"$ref": "#/$defs/')[1:]:
        assert ref.split('"')[0] in js["$defs"]


def test_typescript_shapes():
    ts = export.render()[export.TS_NAME]
    assert "export interface TurnSend {\n  session: string;" in ts
    assert '  mode?: "send" | "queue" | "steer";' in ts
    assert '  "turn.send": TurnSend;' in ts and "export type AnyEvent" in ts
    assert chr(0x2014) not in ts


def test_register_hooks():
    ap = argparse.ArgumentParser()
    common = argparse.ArgumentParser(add_help=False)
    sp = ap.add_subparsers(dest="cmd")
    cli_chat.register(sp, common)
    a = ap.parse_args(["serve", "--tcp", "127.0.0.1:0", "--engine", "echo"])
    assert a.func is cli_chat.cmd_serve and a.tcp == "127.0.0.1:0"
    a = ap.parse_args(["chat", "--resume"])
    assert a.func is cli_chat.cmd_chat and a.resume


class LocalClient:
    """In-process stand-in for protocol.client.Client, to test the chat loop."""

    def __init__(self, d: Dispatcher):
        self.d = d

    async def send(self, body):
        return await self.d.run(s.Command.of(body))

    async def call(self, body):
        r = await self.send(body)
        assert r.ok, r.error
        return r.result

    async def events(self, since=0, session=None, reconnect=False):
        async for ev in self.d.hub.subscribe(since, session):
            yield ev


def test_chat_loop_runs_turns_and_approvals():
    async def main():
        d = Dispatcher(EchoEngine(), Hub())
        inputs = ["hello there", "approve: go", "y", "/help", "/quit"]
        out: list[str] = []

        async def read_line(prompt):
            await asyncio.sleep(0.01)
            return inputs.pop(0) if inputs else None

        rc = await cli_chat.chat(LocalClient(d), read_line, out.append, title="t")
        return rc, "".join(out)

    rc, text = asyncio.run(asyncio.wait_for(main(), 10))
    assert rc == 0
    assert "hello there\n" in text and "approve? " in text and "approve: go\n" in text
    assert "error: unsupported" in text  # /help goes to command.run, which echo does not implement
