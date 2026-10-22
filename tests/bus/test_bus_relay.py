import json
import sqlite3
import threading
import time

import pytest
from nacl.signing import SigningKey

from exo.bus import wire
from exo.bus.registry import RegistryError, build_registry, check_registry

from .conftest import http


def test_registry_refuses_unknown_and_non_leader_keys(bus):
    doc = json.loads(bus.registry_path.read_text())
    doc["seats"][0]["pubkey"] = wire.pubkey_b64(SigningKey.generate())  # swap a key under the signature
    with pytest.raises(RegistryError):
        check_registry(doc)
    stranger = SigningKey.generate()
    seats = [{"seat": "mallory", "team": "x", "platform": "x", "pubkey": wire.pubkey_b64(stranger),
              "role": "leader", "since": "2026-10-11"}]
    with pytest.raises(RegistryError):  # self-signed by a key that is not the pinned leader
        check_registry(build_registry(stranger, "mallory", seats), leader_pubkey=wire.pubkey_b64(bus.keys["alpha"]))
    assert bus.post("mallory", "group", "hi", key=stranger)[0] == 403
    assert bus.post("beta", "group", "hi", key=stranger)[0] == 401
    assert bus.get("beta", "/v1/messages", key=stranger)[0] == 401


def test_registry_versions_chain(tmp_path, bus):
    from exo.bus.tools import make_registry
    first = json.loads(bus.registry_path.read_text())
    new_key = SigningKey.generate()
    second = make_registry(bus.keys["alpha"], "alpha", tmp_path / "r2.json",
                           [f"epsilon,ops,test,{wire.pubkey_b64(new_key)}"], previous=bus.registry_path, remove=["delta"])
    assert second["version"] == 2 and second["prev_hash"] == wire.sha256_hex(wire.canonical(first))
    assert {s["seat"] for s in second["seats"]} == {"alpha", "beta", "gamma", "epsilon"}
    bus.registry_path.write_text(json.dumps(second))
    bus.relay.registry.reload()
    assert bus.relay.registry.seat("epsilon") and not bus.relay.registry.seat("delta")
    bus.registry_path.write_text(json.dumps(first))
    with pytest.raises(RegistryError, match="older"):
        bus.relay.registry.reload()


def test_prev_mismatch_409_and_retry(bus):
    head = bus.head()["head"]
    first, second = bus.envelope("beta", "group", "a", prev=head), bus.envelope("delta", "group", "b", prev=head)
    assert http("POST", f"{bus.url}/v1/messages", first)[0] == 201
    status, data, _ = http("POST", f"{bus.url}/v1/messages", second)
    assert status == 409 and data["seq"] == 1 and data["head"] == bus.head()["head"]
    retry = bus.envelope("delta", "group", "b", prev=data["head"])
    assert http("POST", f"{bus.url}/v1/messages", retry)[0] == 201
    assert bus.post("delta", "group", "c", ts="2026-01-01T00:00:00Z")[0] == 422
    assert bus.post("delta", "nobody", "c")[0] == 422
    assert bus.post("delta", "group", "c", kind="shout")[0] == 422


def test_chain_verification_detects_tampering(bus):
    for i in range(3):
        assert bus.post("beta", "group", f"m{i}")[0] == 201
    assert http("GET", f"{bus.url}/v1/verify")[1] == {"ok": True, "from": 1, "to": 3, "checked": 3,
                                                       "head": bus.head()["head"]}
    db = sqlite3.connect(bus.db_path)
    db.execute("UPDATE log SET body = 'forged' WHERE seq = 2")
    db.commit()
    r = http("GET", f"{bus.url}/v1/verify")[1]
    assert r["ok"] is False and r["seq"] == 2 and r["checked"] == 1
    assert http("GET", f"{bus.url}/v1/verify", params={"from": 3})[1]["ok"] is True


def test_inbox_routes_to_seat_team_and_group(bus):
    assert bus.post("alpha", "beta", "to-beta")[0] == 201
    assert bus.post("alpha", "team:core", "to-core")[0] == 201
    assert bus.post("alpha", "group", "to-all")[0] == 201
    assert bus.post("delta", ["gamma", "team:ops"], "to-gamma-and-ops")[0] == 201
    assert bus.inbox("beta") == ["to-beta", "to-core", "to-all"]
    assert bus.inbox("gamma") == ["to-core", "to-all", "to-gamma-and-ops"]
    assert bus.inbox("delta") == ["to-all", "to-gamma-and-ops"]
    assert bus.inbox("delta", since=3) == ["to-gamma-and-ops"]
    assert bus.get("delta", "/v1/inbox/beta")[0] == 403
    assert [m["body"] for m in bus.get("alpha", "/v1/messages", to="group")[1]] == ["to-all"]


def test_encrypted_body_round_trip_over_the_bus(bus):
    beta, gamma = bus.keys["beta"], bus.keys["gamma"]
    body, enc = wire.encrypt_body("for gamma only", beta, "gamma", wire.pubkey_b64(gamma))
    assert bus.post("beta", "team:core", body, enc=enc)[0] == 201
    env = bus.get("gamma", "/v1/inbox/gamma")[1][0]
    assert wire.decrypt_body(env["body"], env["enc"], gamma, wire.pubkey_b64(beta)) == "for gamma only"
    with pytest.raises(Exception):
        wire.decrypt_body(env["body"], env["enc"], bus.keys["delta"], wire.pubkey_b64(beta))
    page = http("GET", f"{bus.url}/")[2]
    assert "Test Bus" in page and "encrypted to gamma" in page and "for gamma only" not in page


def test_read_auth_replay_rejected_over_http(bus):
    headers = wire.request_headers("beta", bus.keys["beta"], "GET", "/v1/messages")
    assert http("GET", f"{bus.url}/v1/messages", headers=headers)[0] == 200
    status, data, _ = http("GET", f"{bus.url}/v1/messages", headers=headers)
    assert status == 401 and "replay" in data["error"]
    assert http("GET", f"{bus.url}/v1/messages")[0] == 401
    assert http("GET", f"{bus.url}/v1/inbox/beta", headers=headers)[0] == 401  # path differs


def test_websocket_stream_pushes_addressed_messages(bus):
    got = []

    def reader():
        for env in bus.client("beta").tail():
            got.append(env)
            break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    end = time.time() + 5
    while not bus.relay.subscribers.get("beta") and time.time() < end:
        time.sleep(0.02)
    bus.post("delta", "team:ops", "not for beta")
    bus.post("delta", "team:core", "for beta")
    t.join(5)
    assert got and got[0]["body"] == "for beta" and got[0]["seq"] == 2


def test_compose_and_post_signs_with_seat_or_relay_key(bus):
    import asyncio
    r1 = asyncio.run(bus.relay.compose_and_post("alpha", "group", "seat key"))  # relay holds alpha's key file
    r2 = asyncio.run(bus.relay.compose_and_post("beta", "group", "relay key"))  # falls back to the relay key
    msgs = bus.get("gamma", "/v1/messages")[1]
    assert [m["seq"] for m in msgs] == [r1["seq"], r2["seq"]]
    assert wire.verify_doc(wire.core(msgs[0]), wire.pubkey_b64(bus.keys["alpha"]))
    assert wire.verify_doc(wire.core(msgs[1]), wire.pubkey_b64(bus.relay_key))
    assert http("GET", f"{bus.url}/v1/verify")[1]["ok"] is True


def test_checkpoint_tool_writes_signed_checkpoint(bus, tmp_path):
    from exo.bus.tools import checkpoint, verify_checkpoint
    bus.post("alpha", "group", "x")
    lines = []
    doc, path = checkpoint(bus.db_path, wire.b64(bytes(bus.relay_key)), tmp_path / "cp",
                           {"alpha": str(bus.keys_dir / "alpha.key")}, stamp=True, log=lines.append)
    assert json.loads(path.read_text()) == doc
    assert doc["seq"] == 1 and doc["head"] == bus.head()["head"]
    assert verify_checkpoint(doc, {"relay": wire.pubkey_b64(bus.relay_key), "alpha": wire.pubkey_b64(bus.keys["alpha"])})
    assert not verify_checkpoint({**doc, "head": "0" * 64})
    assert http("GET", f"{bus.url}/v1/checkpoints")[1][0]["head"] == doc["head"]
    assert any("ots" in line or "OpenTimestamps" in line for line in lines)


def test_the_relay_key_signs_only_marked_relay_posts_and_never_counts_as_the_seat(bus):
    """D-040: an envelope signed with the relay key and posted over HTTP as a seat is refused. The relay's own MCP
    posts carry a relay marker in attach, and a reader never attributes them to the seat."""
    status, _, raw = bus.post("beta", "group", "pretending to be beta", key=bus.relay_key)
    assert status == 401, raw
    import asyncio
    asyncio.run(bus.relay.compose_and_post("beta", "group", "composed by the relay"))
    [msg] = bus.get("gamma", "/v1/messages")[1]
    assert {"relay": {"composed_for": "beta"}} in msg["attach"]
    cli = bus.client("gamma")
    reg, head = cli.registry(), cli.head()
    assert cli.signer_of(msg, reg, head["relay_pubkey"]) is None
    assert cli.verify()["ok"] is True  # the log itself still verifies: the relay vouched for the post


def test_web_view_hides_plaintext_bodies_unless_the_operator_opts_in(bus):
    """`/` takes no signed headers, so by default it shows who wrote to whom but never a plaintext body: the API
    gives bodies only to signed seats. EXO_BUS_WEB_BODIES=1 (web_bodies) shows them; sealed bodies never show."""
    assert bus.post("beta", "gamma", "dm-marker-beta-to-gamma")[0] == 201
    body, enc = wire.encrypt_body("sealed-marker", bus.keys["beta"], "gamma", wire.pubkey_b64(bus.keys["gamma"]))
    assert bus.post("beta", "gamma", body, enc=enc)[0] == 201
    assert http("GET", f"{bus.url}/v1/messages")[0] == 401
    status, _, page = http("GET", f"{bus.url}/")
    assert status == 200 and "dm-marker-beta-to-gamma" not in page and "sealed-marker" not in page
    assert "#1 <b>beta</b> → gamma" in page and "body hidden" in page and "bodies hidden" in page
    assert "encrypted to gamma" in page
    bus.relay.config.web_bodies = True
    page = http("GET", f"{bus.url}/")[2]
    assert "dm-marker-beta-to-gamma" in page and "sealed-marker" not in page and "hidden" not in page


def test_web_bodies_is_off_unless_explicitly_turned_on():
    from exo.bus.config import RelayConfig

    def on(**environ):
        return RelayConfig.from_env({"EXO_HOME": "/nonexistent", **environ}).web_bodies

    assert on() is False
    assert on(EXO_BUS_WEB_BODIES="1") is on(EXO_BUS_WEB_BODIES="True") is on(DIVAN_WEB_BODIES="yes") is True
    for value in ("0", "no", "false", "off", "ture", ""):
        assert on(EXO_BUS_WEB_BODIES=value) is False, value


def test_relay_refuses_envelopes_that_would_not_store_as_signed(bus):
    """The log stores v as the int 1 and the text fields as TEXT. An envelope signed with v=True, v=1.0 or a
    non-string thread would be stored in another form than the one its id and signature cover, and /v1/verify would
    fail at that row forever. Wrong types that used to crash the handler (500) get the same 422."""
    def post_with(**override):
        env = wire.make_envelope("beta", "group", "x", bus.head()["head"])
        env.update(override)
        return http("POST", f"{bus.url}/v1/messages", wire.finish_envelope(env, bus.keys["beta"]))

    for override in ({"v": True}, {"v": 1.0}, {"v": "1"}, {"thread": 7}, {"thread": 7.5}, {"thread": True},
                     {"thread": {}}, {"thread": []}, {"kind": ["msg"]}, {"from": ["beta"]}, {"body": 5},
                     {"ts": 1700000000}):
        status, data, raw = post_with(**override)
        assert status == 422, (override, raw)
    assert bus.head()["seq"] == 0
    assert bus.post("beta", "group", "ok")[0] == 201
    assert bus.post("beta", "group", "threaded", thread="t1")[0] == 201
    assert post_with(thread="")[0] == 201
    assert http("GET", f"{bus.url}/v1/verify")[1] == {"ok": True, "from": 1, "to": 3, "checked": 3,
                                                       "head": bus.head()["head"]}
