"""exo-bus plugin: settings, tool wrappers, channel routing, sign / verify / round trip (all offline)."""
import asyncio
import json

import pytest

from exo.bus import wire
from exo_bus_hermes import adapter, settings, tools


# ---- settings

def test_setting_order_extra_then_exo_then_divan_then_default(monkeypatch):
    assert settings.setting("URL", default="d") == "d"
    monkeypatch.setenv("DIVAN_URL", "http://divan")
    assert settings.setting("URL") == "http://divan"
    monkeypatch.setenv("EXO_BUS_URL", "http://exo")
    assert settings.setting("URL") == "http://exo"
    assert settings.setting("URL", {"url": "http://extra"}) == "http://extra"


def test_configured_needs_url_and_seat(monkeypatch):
    assert settings.configured() is False
    monkeypatch.setenv("EXO_BUS_URL", "http://x")
    assert settings.configured() is False
    assert settings.configured({"seat": "beta"}) is True


def test_cursor_round_trip_and_bad_file(tmp_path):
    cur = settings.Cursor("beta", {"state_dir": str(tmp_path / "s")})
    assert cur.get() == 0
    cur.set(7)
    assert cur.get() == 7
    cur.path.write_text("not json")
    assert cur.get() == 0


# ---- sign, verify, round trip

def test_post_signs_and_relay_accepts(relay, client_for):
    res = client_for("alpha").post("beta", "hello", thread="t1")
    env = relay.log[0]
    assert res["seq"] == 1 and env["id"] == res["id"]
    assert wire.envelope_id(wire.core(env)) == env["id"]
    assert wire.verify_doc(wire.core(env), wire.pubkey_b64(relay.keys["alpha"]))
    assert not wire.verify_doc(wire.core(env), wire.pubkey_b64(relay.keys["beta"]))


def test_round_trip_post_then_inbox(relay, client_for):
    client_for("alpha").post(["beta", "gamma"], "to both")
    got = client_for("beta").inbox(0)
    assert [e["body"] for e in got] == ["to both"]
    assert client_for("gamma").inbox(0)[0]["id"] == got[0]["id"]
    assert client_for("alpha").inbox(0) == []


def test_verify_passes_on_a_good_chain_and_catches_tampering(relay, client_for):
    a = client_for("alpha")
    a.post("beta", "one")
    a.post("beta", "two")
    assert a.verify() == {"ok": True, "checked": 2, "head": relay.head, "relay_head": relay.head}
    relay.log[0]["body"] = "forged"
    bad = a.verify()
    assert bad["ok"] is False and bad["seq"] == 1


def test_sealed_body_reads_only_for_the_addressee(relay, client_for):
    client_for("alpha").post("beta", "secret", enc_to="beta")
    env = relay.log[0]
    assert env["body"] != "secret" and env["enc"]["to"] == "beta"
    assert client_for("beta").text(env) == "secret"
    assert client_for("gamma").text(env) == "<encrypted to beta>"


# ---- tool wrappers

def test_bus_post_needs_to_and_text(as_seat):
    as_seat("alpha")
    assert json.loads(tools.bus_post({"to": ["beta"]}))["ok"] is False
    assert json.loads(tools.bus_post({"text": "x"}))["ok"] is False


def test_bus_post_accepts_comma_string_and_body_alias(as_seat, relay):
    as_seat("alpha")
    out = json.loads(tools.bus_post({"to": "beta, gamma", "body": "hi"}))
    assert out["ok"] and out["seq"] == 1
    assert relay.log[0]["to"] == ["beta", "gamma"] and relay.log[0]["body"] == "hi"


def test_bus_post_reports_unknown_seat_as_error(as_seat):
    as_seat("alpha")
    out = json.loads(tools.bus_post({"to": ["beta"], "text": "x", "enc_to": "nobody"}))
    assert out["ok"] is False and "unknown seat" in out["error"]


def test_bus_inbox_moves_cursor_but_not_with_since(as_seat, client_for):
    client_for("alpha").post("beta", "one")
    as_seat("beta")
    first = json.loads(tools.bus_inbox({}))
    assert [m["text"] for m in first["messages"]] == ["one"]
    assert json.loads(tools.bus_inbox({}))["messages"] == []
    assert len(json.loads(tools.bus_inbox({"since": 0}))["messages"]) == 1
    assert json.loads(tools.bus_inbox({}))["messages"] == []


def test_bus_thread_and_verify(as_seat, client_for):
    a = client_for("alpha")
    a.post("beta", "a1", thread="t")
    a.post("beta", "other")
    a.post("beta", "a2", thread="t")
    as_seat("beta")
    assert json.loads(tools.bus_thread({}))["ok"] is False
    assert [m["text"] for m in json.loads(tools.bus_thread({"thread": "t"}))["messages"]] == ["a1", "a2"]
    assert json.loads(tools.bus_verify())["checked"] == 3


def test_tools_register_hidden_until_configured(monkeypatch):
    seen = {}

    class Ctx:
        def register_tool(self, name, toolset, handler, description, schema, emoji="", check_fn=None):
            seen[name] = (toolset, check_fn)

    tools.register_tools(Ctx())
    assert sorted(seen) == ["bus_inbox", "bus_post", "bus_thread", "bus_verify"]
    assert {t for t, _ in seen.values()} == {"exo-bus"}
    assert seen["bus_post"][1]() is False
    monkeypatch.setenv("EXO_BUS_URL", "http://x")
    monkeypatch.setenv("EXO_BUS_SEAT", "beta")
    assert seen["bus_post"][1]() is True


# ---- channel

def test_route_and_frame():
    dm = {"id": "i1", "from": "alpha", "to": ["beta"], "thread": None}
    assert adapter.route(dm, "beta") == ("alpha", "dm")
    team = {"id": "i2", "from": "alpha", "to": ["team:core"], "thread": "t"}
    assert adapter.route(team, "beta") == ("team:core", "group")
    assert adapter.route({**team, "to": ["group"]}, "beta") == ("group", "group")
    assert adapter.frame(dm, "yo", "dm").startswith("[Exo Bus direct message from seat alpha, thread i1]")


class RecordingLink:
    def __init__(self):
        self.calls = []

    async def call(self, body):
        self.calls.append(body)
        return {"session": {"id": "s1"}}


def test_poll_once_skips_own_messages_and_does_not_repeat(as_seat, client_for):
    me = as_seat("beta")
    client_for("alpha").post("beta", "from alpha")
    client_for("alpha").post("group", "to all")
    me.post("group", "my own")
    link = RecordingLink()
    chan = adapter.BusChannel(link, {"state_dir": str(me.keys_dir)}, client=me)
    chan._cursor = settings.Cursor("beta", {"state_dir": str(chan._extra["state_dir"])})
    chan._map_path = chan._cursor.path.with_name("sessions-beta.json")
    assert asyncio.run(chan.poll_once()) == 2
    texts = [getattr(c, "text", None) for c in link.calls if getattr(c, "text", None)]
    assert len(texts) == 2 and "from alpha" in texts[0] and "to all" in texts[1]
    assert chan._cursor.get() == 3
    assert asyncio.run(chan.poll_once()) == 0


def test_standalone_send_needs_config_then_posts(as_seat, relay, monkeypatch):
    out = asyncio.run(adapter._standalone_send("beta", "x"))
    assert out["success"] is False
    as_seat("alpha")
    monkeypatch.setattr(adapter, "make_client", settings.make_client)
    out = asyncio.run(adapter._standalone_send("beta", "ping"))
    assert out["success"] is True and out["message_id"] == relay.log[0]["id"]
