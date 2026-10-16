"""exo-bus Hermes plugin: tools against a live relay; platform adapter against a Hermes checkout."""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from .conftest import write_keys

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "exo-bus"


def load_plugin():
    spec = importlib.util.spec_from_file_location("exo_bus_plugin", PLUGIN / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["exo_bus_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


class Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, toolset, handler, description, schema, emoji="", check_fn=None):
        assert schema["name"] == name and schema["parameters"]["type"] == "object"
        self.tools[name] = (handler, check_fn, toolset)


@pytest.fixture
def seat_env(bus, tmp_path, monkeypatch):
    keys = tmp_path / "seat-keys"
    write_keys(keys, {"beta": bus.keys["beta"]})
    for k, v in {"EXO_BUS_URL": bus.url, "EXO_BUS_SEAT": "beta", "EXO_BUS_KEYS_DIR": str(keys),
                 "EXO_BUS_STATE_DIR": str(tmp_path / "state")}.items():
        monkeypatch.setenv(k, v)
    return tmp_path


def test_manifest_lists_the_registered_tools():
    import re
    manifest = (PLUGIN / "plugin.yaml").read_text()
    declared = re.findall(r"^\s+- (bus_\w+)$", manifest, re.M)
    ctx = Ctx()
    load_plugin().register(ctx)  # no register_platform on this ctx: tools only
    assert sorted(ctx.tools) == sorted(declared) == ["bus_inbox", "bus_post", "bus_thread", "bus_verify"]
    assert {t[2] for t in ctx.tools.values()} == {"exo-bus"}


def test_tools_hidden_until_configured(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("EXO_BUS_", "DIVAN_")):
            monkeypatch.delenv(k)
    ctx = Ctx()
    load_plugin().register(ctx)
    assert ctx.tools["bus_post"][1]() is False
    assert json.loads(ctx.tools["bus_post"][0]({"to": ["group"], "text": "x"}))["ok"] is False


def test_tools_post_read_thread_verify(bus, seat_env):
    ctx = Ctx()
    load_plugin().register(ctx)
    call = {name: t[0] for name, t in ctx.tools.items()}
    assert ctx.tools["bus_inbox"][1]() is True
    bus.post("gamma", "team:core", "hello beta", thread="t9")
    r = json.loads(call["bus_post"]({"to": ["gamma"], "text": "hi gamma", "thread": "t9"}))
    assert r["ok"] and r["seq"] == 2
    first = json.loads(call["bus_inbox"]({}))
    assert [m["text"] for m in first["messages"]] == ["hello beta"]
    assert json.loads(call["bus_inbox"]({}))["messages"] == []  # cursor advanced
    assert len(json.loads(call["bus_inbox"]({"since": 0}))["messages"]) == 1
    assert [m["text"] for m in json.loads(call["bus_thread"]({"thread": "t9"}))["messages"]] == ["hello beta", "hi gamma"]
    assert json.loads(call["bus_verify"]({}))["checked"] == 2
    assert json.loads(call["bus_post"]({"to": "nobody", "text": "x"}))["ok"] is False


def test_tools_sym2p_post_renders_for_readers(bus, seat_env):
    sym2p = pytest.importorskip("exo.protocols.sym2p")
    ctx = Ctx()
    load_plugin().register(ctx)
    line = sym2p.encode_brief(id="b441", tk="mr1", cf=88, cv=70, scope=("sym-2p/1.0", 3, 5), rq="ack")
    assert json.loads(ctx.tools["bus_post"][0]({"to": ["gamma"], "text": line, "sym2p": True}))["ok"]
    assert json.loads(ctx.tools["bus_post"][0]({"to": ["gamma"], "text": "not a packet", "sym2p": True}))["ok"] is False
    env = bus.client("gamma").inbox()[0]
    assert "Scope:" in bus.client("gamma").text(env)


class StubLink:
    """A protocol link that records commands and replays scripted events."""

    def __init__(self):
        self.calls = []
        self.q = None

    async def call(self, body):
        self.calls.append(body)
        if type(body).__name__ == "SessionCreate":
            return {"session": {"id": f"s_{len(self.calls)}"}}
        if type(body).__name__ == "Peers":  # D-040: the core's ingress path; this stub's policy delivers everything
            return {"msg_id": "bm_x", "state": "delivered", "session": body.session}
        return {"accepted": True}

    async def events(self, since=0, session=None):
        import asyncio
        self.q = self.q or asyncio.Queue()
        while True:
            yield await self.q.get()


def test_channel_routes_inbox_to_turns_and_replies_signed(bus, tmp_path):
    import asyncio
    from exo.protocol import schema as s
    from exo.core.types import Message
    keys = tmp_path / "seat-keys"
    write_keys(keys, {"beta": bus.keys["beta"]})
    bus.post("gamma", "beta", "direct to beta", thread="d1")
    bus.post("delta", "team:core", "to the core team")
    bus.post("beta", "group", "my own message")
    extra = {"url": bus.url, "seat": "beta", "keys_dir": str(keys), "state_dir": str(tmp_path / "state")}
    from exo_bus_plugin.exo_bus_hermes.adapter import BusChannel, route
    load_plugin()

    async def main():
        link = StubLink()
        ch = BusChannel(link, extra)
        assert await ch.start()
        await asyncio.sleep(0.5)  # the poll loop picks the inbox up on start
        n = len([c for c in link.calls if type(c).__name__ == "Peers" and c.action == "bus"])
        again = await ch.poll_once()  # nothing new: cursor and dedup hold
        sessions = dict(ch._by_chat)
        sid = sessions["gamma"]
        ev = lambda body, seq: s.Event(seq=seq, type="x", body=body, session=sid)
        await link.q.put(ev(s.MessageCompleted(message=Message.assistant("reply from exo")), 1))
        await link.q.put(ev(s.TurnCompleted(turn_id="t1"), 2))
        await asyncio.sleep(0.3)
        await ch.stop()
        return n, again, sessions, link.calls

    n, again, sessions, calls = asyncio.run(main())
    assert (n, again) == (2, 0) and set(sessions) == {"gamma", "team:core"}
    sends = [c for c in calls if type(c).__name__ == "Peers"]
    assert sends[0].text == "direct to beta" and sends[0].envelope["from"] == "gamma" and sends[0].pub
    assert not [c for c in calls if type(c).__name__ == "TurnSend"]  # never a principal turn
    reply = bus.client("gamma").inbox()[-1]
    assert reply["body"] == "reply from exo" and reply["from"] == "beta" and reply["thread"] == "d1"
    assert route({"to": ["team:core"], "from": "delta"}, "beta") == ("team:core", "group")
