"""A live exo.bus relay on a free localhost port, with seat keys in a temp dir."""
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

import pytest
from nacl.signing import SigningKey

from exo.bus import wire

SEATS = {"alpha": ("lead", "leader"), "beta": ("core", "member"), "gamma": ("core", "member"),
         "delta": ("ops", "member")}


def http(method, url, body=None, headers=None, params=None):
    """Tiny stdlib HTTP helper. Returns (status, parsed json or None, raw text)."""
    if params:
        url += "?" + urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode()
    try:
        return status, json.loads(raw), raw
    except ValueError:
        return status, None, raw


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_port(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"nothing listening on {port}")


def write_keys(keys_dir, keys):
    keys_dir.mkdir(parents=True, exist_ok=True)
    for seat, key in keys.items():
        p = keys_dir / f"{seat}.key"
        p.write_text(wire.b64(bytes(key)) + "\n")
        p.chmod(0o600)


class Bus:
    """A live relay plus the seat keys in its registry."""

    def __init__(self, tmp_path, db_path=None, keys=None, relay_key=None):
        pytest.importorskip("fastapi")
        uvicorn = pytest.importorskip("uvicorn")
        from exo.bus.config import RelayConfig
        from exo.bus.relay import create_app
        from exo.bus.tools import make_registry

        self.keys = keys or {seat: SigningKey.generate() for seat in SEATS}
        self.relay_key = relay_key or SigningKey.generate()
        self.keys_dir = tmp_path / "relay-keys"
        write_keys(self.keys_dir, {"alpha": self.keys["alpha"]})
        seats = [f"{s},{t},test,{wire.pubkey_b64(self.keys[s])},{r},2026-10-11" for s, (t, r) in SEATS.items()]
        self.registry_path = tmp_path / "registry.json"
        if not self.registry_path.exists():
            make_registry(self.keys["alpha"], "alpha", self.registry_path, seats)
        self.db_path = db_path or tmp_path / "bus.sqlite"
        self.config = RelayConfig(db=self.db_path, registry=self.registry_path,
                                  relay_key=wire.b64(bytes(self.relay_key)), keys_dir=self.keys_dir,
                                  mcp=False, title="Test Bus")
        self.app = create_app(self.config)
        self.relay = self.app.state.relay
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            time.sleep(0.02)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)

    def envelope(self, seat, to, body, kind="msg", thread=None, enc=None, prev=None, key=None, ts=None):
        prev = prev if prev is not None else self.head()["head"]
        env = wire.make_envelope(seat, to, body, prev, kind, thread, enc, ts=ts)
        return wire.finish_envelope(env, key or self.keys[seat])

    def post(self, seat, to, body, **kw):
        return http("POST", f"{self.url}/v1/messages", self.envelope(seat, to, body, **kw))

    def get(self, seat, path, key=None, **params):
        query = urlencode(params) if params else ""
        return http("GET", f"{self.url}{path}", params=params,
                    headers=wire.request_headers(seat, key or self.keys[seat], "GET", path, query=query))

    def head(self):
        return http("GET", f"{self.url}/v1/head")[1]

    def inbox(self, seat, since=0):
        status, data, raw = self.get(seat, f"/v1/inbox/{seat}", since=since)
        assert status == 200, raw
        return [m["body"] for m in data]

    def client(self, seat):
        from exo.bus import Client
        return Client(self.url, seat, key=self.keys[seat])


@pytest.fixture
def bus(tmp_path):
    b = Bus(tmp_path)
    yield b
    b.stop()
