"""exo-bus plugin: settings, tool wrappers, channel routing, sign / verify / round trip (all offline)."""
import asyncio
import json

import httpx
import pytest
from nacl.signing import SigningKey

from exo.bus import wire
from exo.peerauth import MAX_FRAME
from exo.protocol.client import Client
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


def test_cursor_and_session_map_do_not_follow_a_symlink_at_the_old_temporary_name(tmp_path):
    """Both were written as `<name>.tmp` with `write_text`, which follows a symlink there (#273)."""
    import os
    import stat
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch\n")
    cur = settings.Cursor("beta", {"state_dir": str(tmp_path / "s")})
    chan = adapter.BusChannel(object(), {}, client=object())
    chan._map_path, chan._by_chat = cur.path.with_name("sessions-beta.json"), {"chat": "s1"}
    cur.path.parent.mkdir()
    for p in (cur.path, chan._map_path):
        p.with_suffix(".tmp").symlink_to(victim)
    cur.set(7)
    chan._save_map()
    assert victim.read_text() == "do not touch\n"
    umask = os.umask(0o022)
    os.umask(umask)
    for p, want in ((cur.path, {"since": 7}), (chan._map_path, {"chat": "s1"})):
        st = os.lstat(p)
        assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o666 & ~umask  # what write_text gave
        assert json.loads(p.read_text()) == want


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


def test_event_loop_reads_past_an_event_too_large_to_read():
    """The channel keeps its own cursor and calls ``events(since)`` without reconnect: a skipped event must not
    leave it asking for that event again, or every later reply is held back (D-060)."""
    def frame(seq, text):
        ev = {"id": f"e{seq}", "seq": seq, "session": None, "type": "notice", "ts": "", "body": {"text": text}}
        return f"id: {seq}\nevent: notice\ndata: {json.dumps(ev)}\n\n"
    log = [(1, frame(1, "before")), (2, frame(2, "x" * (MAX_FRAME + 10))), (3, frame(3, "after"))]
    asked, got = [], []
    link = Client(url="http://exod")
    chan = adapter.BusChannel(link, {}, client=object())

    def handler(request):
        since = int(request.url.params["since"])
        asked.append(since)
        if len(asked) > 4:  # stuck at the same cursor: stop the loop instead of hanging
            chan.running = False
        body = ": exod instance=i_1 heartbeat=15\n\n" + "".join(f for seq, f in log if seq > since)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body.encode())
    link._http = httpx.AsyncClient(base_url="http://exod", transport=httpx.MockTransport(handler))

    async def on_event(ev):
        got.append(ev.seq)
        if ev.seq == 3:
            chan.running = False
    chan._on_event = on_event

    async def main():
        chan.running = True
        try:
            await asyncio.wait_for(chan._event_loop(), 15)
        finally:
            await link.aclose()
    asyncio.run(main())
    assert got == [1, 3]
    assert asked == [0, 2]


def test_standalone_send_needs_config_then_posts(as_seat, relay, monkeypatch):
    out = asyncio.run(adapter._standalone_send("beta", "x"))
    assert out["success"] is False
    as_seat("alpha")
    monkeypatch.setattr(adapter, "make_client", settings.make_client)
    out = asyncio.run(adapter._standalone_send("beta", "ping"))
    assert out["success"] is True and out["message_id"] == relay.log[0]["id"]


# ---- a sealed body this seat cannot open

UNOPENED = "<encrypted body could not be opened>"


def _unopenable(case, alpha_key, beta_pub):
    """(body, enc) sealed from alpha to beta that beta cannot open. The relay checks only the alg and the seat."""
    n24, junk = wire.b64(b"n" * 24), wire.b64(b"x" * 40)
    if case == "old key":  # sealed to a key beta no longer holds (beta rotated)
        return wire.encrypt_body("hi", alpha_key, "beta", wire.pubkey_b64(SigningKey.generate()))
    if case == "not utf-8":
        sealed = wire._box(alpha_key, beta_pub).encrypt(b"\xff\xfe")
        return wire.b64(sealed.ciphertext), {"alg": wire.ENC_ALG, "to": "beta", "nonce": wire.b64(sealed.nonce)}
    body, nonce = {"garbage box": (junk, n24), "short nonce": (junk, wire.b64(b"n" * 5)), "no nonce": (junk, None),
                   "bad base64": ("not base64!", n24), "nonce not a string": (junk, 24)}[case]
    return body, {"alg": wire.ENC_ALG, "to": "beta", **({} if nonce is None else {"nonce": nonce})}


def _post_unopenable(relay, case):
    body, enc = _unopenable(case, relay.keys["alpha"], wire.pubkey_b64(relay.keys["beta"]))
    env = wire.make_envelope("alpha", ["beta"], body, relay.head, "msg", None, enc, None)
    assert relay.accept(wire.finish_envelope(env, relay.keys["alpha"]))[0] == 201


CASES = ["garbage box", "short nonce", "no nonce", "bad base64", "nonce not a string", "not utf-8", "old key"]


@pytest.mark.parametrize("case", CASES)
def test_an_unopenable_sealed_body_reads_as_a_placeholder(relay, client_for, case):
    _post_unopenable(relay, case)
    assert client_for("beta").text(relay.log[0]) == UNOPENED
    assert client_for("gamma").text(relay.log[0]) == "<encrypted to beta>"


@pytest.mark.parametrize("case", ["garbage box", "old key"])
def test_poll_once_moves_past_an_unopenable_sealed_envelope(as_seat, relay, client_for, case):
    """An unreadable envelope advances the cursor without creating a placeholder turn."""
    me = as_seat("beta")
    _post_unopenable(relay, case)
    client_for("alpha").post("beta", "hello after")
    link = RecordingLink()
    chan = adapter.BusChannel(link, {"state_dir": str(me.keys_dir)}, client=me)
    chan._cursor = settings.Cursor("beta", {"state_dir": str(chan._extra["state_dir"])})
    chan._map_path = chan._cursor.path.with_name("sessions-beta.json")
    assert asyncio.run(chan.poll_once()) == 1
    texts = [c.text for c in link.calls if getattr(c, "action", "") == "bus"]
    assert len(texts) == 1 and "hello after" in texts[0]
    assert chan._cursor.get() == len(relay.log) == 2
    assert asyncio.run(chan.poll_once()) == 0


@pytest.mark.parametrize("recipient, expected", [("beta", 1), ("gamma", 0)])
def test_group_encryption_only_dispatches_to_the_sealed_recipient(as_seat, client_for, recipient, expected):
    me = as_seat(recipient)
    client_for("alpha").post("group", "private group body", enc_to="beta")
    link = RecordingLink()
    chan = adapter.BusChannel(link, {"state_dir": str(me.keys_dir)}, client=me)
    chan._cursor = settings.Cursor(recipient, {"state_dir": str(chan._extra["state_dir"])})
    chan._map_path = chan._cursor.path.with_name(f"sessions-{recipient}.json")
    assert asyncio.run(chan.poll_once()) == expected
    turns = [c for c in link.calls if getattr(c, "action", "") == "bus"]
    assert len(turns) == expected
    if expected:
        assert turns[0].text == "private group body"
    else:
        assert link.calls == []  # no session allocation or core command for an unreadable envelope
    assert chan._cursor.get() == 1
    assert asyncio.run(chan.poll_once()) == 0


def test_poll_once_keeps_the_cursor_when_the_core_call_fails(as_seat, client_for):
    """Regression guard: only a body that cannot be opened is passed over. A core that is restarting or busy
    leaves the cursor where it is, so the message is tried again."""
    me = as_seat("beta")
    client_for("alpha").post("beta", "hello once the core is back")

    class FlakyLink(RecordingLink):
        down = True

        async def call(self, body):
            if getattr(body, "action", "") == "bus" and self.down:
                self.down = False
                raise RuntimeError("core restarting")
            return await super().call(body)

    link = FlakyLink()
    chan = adapter.BusChannel(link, {"state_dir": str(me.keys_dir)}, client=me)
    chan._cursor = settings.Cursor("beta", {"state_dir": str(chan._extra["state_dir"])})
    chan._map_path = chan._cursor.path.with_name("sessions-beta.json")
    with pytest.raises(RuntimeError):
        asyncio.run(chan.poll_once())
    assert chan._cursor.get() == 0
    assert asyncio.run(chan.poll_once()) == 1 and chan._cursor.get() == 1
