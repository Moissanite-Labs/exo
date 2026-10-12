"""Fixtures for exo-bus tests. No network and no Divan checkout: `FakeRelay` is a small in-memory
relay that speaks the same JSON API as the real one, and `FakeClient` is the real `exo.bus.Client`
with only the HTTP transport swapped for it. Signing, chaining and sealing are the real code."""
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from nacl.signing import SigningKey

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from exo.bus import Client, wire  # noqa: E402

SEATS = ("alpha", "beta", "gamma")


class FakeRelay:
    """Append-only log with the checks the real relay makes: id, signature and chain link."""

    def __init__(self, keys):
        self.keys = keys
        self.log: list[dict] = []
        self.head = wire.GENESIS

    def handle(self, method, path, body=None):
        u = urlsplit(path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if method == "GET" and u.path == "/v1/head":
            return 200, {"head": self.head, "seq": len(self.log), "relay_pubkey": None}
        if method == "GET" and u.path == "/v1/registry":
            return 200, {"seats": [{"seat": s, "pubkey": wire.pubkey_b64(k)} for s, k in self.keys.items()]}
        since, limit = int(q.get("since", 0)), int(q.get("limit", 200))
        if method == "GET" and u.path == "/v1/messages":
            rows = [e for e in self.log if e["seq"] > since]
            if q.get("thread"):
                rows = [e for e in rows if e["thread"] == q["thread"]]
            return 200, rows[:limit]
        if method == "GET" and u.path.startswith("/v1/inbox/"):
            seat = u.path.rsplit("/", 1)[1]
            rows = [e for e in self.log if e["seq"] > since and (seat in e["to"] or "group" in e["to"])]
            return 200, rows[:limit]
        if method == "POST" and u.path == "/v1/messages":
            return self.accept(body)
        return 404, {"error": "not found"}

    def accept(self, env):
        pub = wire.pubkey_b64(self.keys[env["from"]]) if env.get("from") in self.keys else None
        if not pub or env["id"] != wire.envelope_id(wire.core(env)) or not wire.verify_doc(wire.core(env), pub):
            return 400, {"error": "bad id or signature"}
        if env["prev"] != self.head:
            return 409, {"head": self.head}
        self.head = wire.chain_hash(self.head, env["id"])
        self.log.append({**env, "seq": len(self.log) + 1})
        return 201, {"seq": len(self.log), "id": env["id"], "head": self.head}


class FakeClient(Client):
    def __init__(self, relay, seat):
        super().__init__("http://fake.invalid", seat, key=relay.keys[seat])
        self.relay = relay

    def api(self, method, path, body=None, auth=False):
        return self.relay.handle(method, path, body)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """No real keys file, no Keychain, no bus variables from the host."""
    import os
    for name in list(os.environ):
        if name.startswith(("EXO_BUS_", "DIVAN_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))
    monkeypatch.setenv("EXO_KEYS_FILE", str(tmp_path / "no-such-keys.env"))
    monkeypatch.setattr("exo.core.models.keys.keychain_get", lambda name: None)


@pytest.fixture
def relay():
    return FakeRelay({s: SigningKey.generate() for s in SEATS})


@pytest.fixture
def client_for(relay):
    return lambda seat: FakeClient(relay, seat)


@pytest.fixture
def as_seat(relay, tmp_path, monkeypatch):
    """Point the tool wrappers at the fake relay as `seat`; returns the seat's client."""
    import exo_bus_hermes.settings as settings
    import exo_bus_hermes.tools as tools

    def use(seat):
        monkeypatch.setenv("EXO_BUS_URL", "http://fake.invalid")
        monkeypatch.setenv("EXO_BUS_SEAT", seat)
        monkeypatch.setenv("EXO_BUS_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(tools, "make_client", lambda extra=None: FakeClient(relay, seat))
        monkeypatch.setattr(settings, "make_client", lambda extra=None: FakeClient(relay, seat))
        return FakeClient(relay, seat)
    return use
