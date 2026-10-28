import json

import pytest
from nacl.signing import SigningKey

from exo import crypto as exo_crypto
from exo.bus import wire


def test_canonical_json_is_stable_and_shared_with_exo_crypto():
    a = {"z": 1, "a": [3, {"y": "é", "x": None}], "m": "日本"}
    b = dict(reversed(list(json.loads(json.dumps(a)).items())))
    assert wire.canonical(a) == wire.canonical(b) == exo_crypto.canonical(a)
    assert wire.canonical(a) == b'{"a":[3,{"x":null,"y":"\xc3\xa9"}],"m":"\xe6\x97\xa5\xe6\x9c\xac","z":1}'
    assert wire.canonical is exo_crypto.canonical and wire.sign_doc is exo_crypto.sign_doc


def test_sign_and_verify_envelope():
    key = SigningKey.generate()
    env = wire.finish_envelope(wire.make_envelope("x", "group", "hi", wire.GENESIS), key)
    assert set(env) == wire.ENVELOPE_KEYS
    assert wire.envelope_id(env) == env["id"]
    assert wire.verify_doc(env, wire.pubkey_b64(key))
    assert not wire.verify_doc(env, wire.pubkey_b64(SigningKey.generate()))
    tampered = {**env, "body": "bye"}
    assert not wire.verify_doc(tampered, wire.pubkey_b64(key))
    assert wire.envelope_id(tampered) != env["id"]


def test_encrypted_body_round_trip():
    alice, bob, eve = SigningKey.generate(), SigningKey.generate(), SigningKey.generate()
    body, enc = wire.encrypt_body("secret", alice, "bob", wire.pubkey_b64(bob))
    assert enc["alg"] == wire.ENC_ALG and enc["to"] == "bob"
    assert wire.decrypt_body(body, enc, bob, wire.pubkey_b64(alice)) == "secret"
    with pytest.raises(Exception):
        wire.decrypt_body(body, enc, eve, wire.pubkey_b64(alice))


def test_request_auth_and_replay_rejection():
    key, replay = SigningKey.generate(), wire.ReplayCache()
    pub = wire.pubkey_b64(key)
    headers = {k.lower(): v for k, v in wire.request_headers("s", key, "GET", "/v1/head").items()}
    assert set(headers) == {"x-divan-seat", "x-divan-ts", "x-divan-sig"}
    assert wire.verify_request(headers, "GET", "/v1/head", lambda s: pub, replay) == "s"
    for bad, msg in [(headers, "replayed"), ({**headers, "x-divan-sig": headers["x-divan-sig"][:-4] + "AAA="}, "bad"),
                     ({**headers, "x-divan-ts": "2020-01-01T00:00:00Z"}, "window"), ({}, "missing")]:
        with pytest.raises(ValueError, match=msg):
            wire.verify_request(bad, "GET", "/v1/head", lambda s: pub, replay)
    with pytest.raises(ValueError, match="unknown seat"):
        wire.verify_request(headers, "GET", "/v1/head", lambda s: None, wire.ReplayCache())


def test_query_string_is_bound_to_request_signature():
    # A signed read must not be replayable with different query parameters (issue 12).
    key, replay = SigningKey.generate(), wire.ReplayCache()
    pub = wire.pubkey_b64(key)
    headers = {k.lower(): v for k, v in wire.request_headers("s", key, "GET", "/v1/messages", query="since=0&to=x").items()}
    assert wire.verify_request(headers, "GET", "/v1/messages", lambda s: pub, replay, query="since=0&to=x") == "s"
    # Tampered parameters, or omitting the query, must not verify.
    with pytest.raises(ValueError, match="bad request signature"):
        wire.verify_request(headers, "GET", "/v1/messages", lambda s: pub, wire.ReplayCache(), query="since=1&to=x")
    with pytest.raises(ValueError, match="bad request signature"):
        wire.verify_request(headers, "GET", "/v1/messages", lambda s: pub, wire.ReplayCache())


def test_load_signing_key_accepts_file_or_inline_seed(tmp_path):
    key = SigningKey.generate()
    seed = wire.b64(bytes(key))
    (tmp_path / "k.key").write_text(seed + "\n")
    assert bytes(wire.load_signing_key(tmp_path / "k.key")) == bytes(key)
    assert bytes(wire.load_signing_key(seed)) == bytes(key)


def test_config_reads_exo_then_legacy_names(tmp_path):
    from exo.bus.config import ClientConfig, RelayConfig
    environ = {"EXO_HOME": str(tmp_path), "DIVAN_URL": "http://legacy:1", "DIVAN_SEAT": "old"}
    c = ClientConfig.from_env(environ)
    assert (c.url, c.seat, c.keys_dir) == ("http://legacy:1", "old", tmp_path / "keys")
    environ["EXO_BUS_URL"] = "http://new:2"
    assert ClientConfig.from_env(environ).url == "http://new:2"
    r = RelayConfig.from_env({"EXO_HOME": str(tmp_path)})
    assert r.db == tmp_path / "bus" / "relay.sqlite" and r.registry == tmp_path / "bus" / "registry.json"
    assert r.relay_key is None and r.leader_pubkey is None
