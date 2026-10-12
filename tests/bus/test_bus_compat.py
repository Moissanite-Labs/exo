"""Wire compatibility with Divan Bus v0.1.

1. Golden: the legacy client's signing and header code (copied verbatim below) talks to the exo relay.
2. Live: if a Divan Bus checkout with its venv is present (EXO_BUS_LEGACY_SRC, else ~/divan-bus), it is
   copied to a temp dir and its relay runs there. The exo client posts to it, the legacy client reads
   from the exo relay, and the exo relay reopens the legacy database and verifies the chain.
   The checkout is only read; bytecode writing is off.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from nacl.signing import SigningKey, VerifyKey

from exo.bus import Client, wire

from .conftest import SEATS, Bus, free_port, http, wait_port, write_keys


# --- verbatim from divan-bus clients/divan.py (v0.1) --------------------------------------
def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def digest_without_sig(doc: dict) -> bytes:
    return hashlib.sha256(canonical({k: v for k, v in doc.items() if k != "sig"})).digest()


def finish_envelope(env: dict, key: SigningKey) -> dict:
    env = {k: v for k, v in env.items() if k != "sig"}
    env["id"] = hashlib.sha256(canonical({k: v for k, v in env.items() if k != "id"})).hexdigest()
    env["sig"] = b64(key.sign(digest_without_sig(env)).signature)
    return env


def verify_doc(doc: dict, pub_b64: str) -> bool:
    try:
        VerifyKey(base64.b64decode(pub_b64)).verify(digest_without_sig(doc), base64.b64decode(doc["sig"]))
        return True
    except Exception:
        return False


def request_headers(seat: str, key: SigningKey, method: str, path: str) -> dict:
    ts = now_ts()
    sig = key.sign(f"{method}\n{path}\n{ts}".encode()).signature
    return {"X-Divan-Seat": seat, "X-Divan-Ts": ts, "X-Divan-Sig": b64(sig)}
# --- end verbatim ---------------------------------------------------------------------------


def test_legacy_client_envelopes_and_headers_work_on_exo_relay(bus):
    key = bus.keys["beta"]
    env = {"v": 1, "ts": now_ts(), "from": "beta", "to": ["group"], "thread": "legacy", "kind": "msg",
           "body": "from a legacy seat", "attach": [], "enc": None, "prev": bus.head()["head"]}
    legacy = finish_envelope(env, key)
    assert legacy == wire.finish_envelope(env, key)  # Ed25519 is deterministic: byte-identical envelopes
    assert http("POST", f"{bus.url}/v1/messages", legacy)[0] == 201
    status, msgs, _ = http("GET", f"{bus.url}/v1/messages",
                           headers=request_headers("gamma", bus.keys["gamma"], "GET", "/v1/messages"))
    assert status == 200 and msgs[0]["body"] == "from a legacy seat"
    bus.client("gamma").post("group", "from an exo seat")
    status, msgs, _ = http("GET", f"{bus.url}/v1/inbox/beta",
                           headers=request_headers("beta", key, "GET", "/v1/inbox/beta"))
    exo_env = msgs[1]
    assert verify_doc({k: v for k, v in exo_env.items() if k not in ("seq", "chain")},
                      wire.pubkey_b64(bus.keys["gamma"]))
    assert set(http("GET", f"{bus.url}/v1/head")[1]) == {"seq", "head", "relay_pubkey"}


def legacy_src() -> Path | None:
    src = Path(os.path.expanduser(os.environ.get("EXO_BUS_LEGACY_SRC", "~/divan-bus")))
    py = src / ".venv" / "bin" / "python"
    if (src / "relay" / "app.py").exists() and (src / "clients" / "divan.py").exists() and py.exists():
        return src
    return None


# Resolved once at collection, before the hermetic fixture moves HOME: this one test is an opt-in check against
# a real checkout, and the only place a test is allowed to look outside its temp dir.
LEGACY_SRC = legacy_src()


@pytest.mark.skipif(LEGACY_SRC is None, reason="no Divan Bus checkout with a venv to test against")
def test_live_round_trip_with_legacy_relay_and_client(tmp_path):
    src = LEGACY_SRC
    py = str(src / ".venv" / "bin" / "python")
    work = tmp_path / "legacy"
    for part in ("relay", "clients"):
        shutil.copytree(src / part, work / part, ignore=shutil.ignore_patterns("__pycache__"))
    keys = {seat: SigningKey.generate() for seat in SEATS}
    relay_key = SigningKey.generate()
    from exo.bus.tools import make_registry  # an exo-built registry must load in the legacy relay
    make_registry(keys["alpha"], "alpha", tmp_path / "registry.json",
                  [f"{s},{t},test,{wire.pubkey_b64(keys[s])},{r},2026-10-11" for s, (t, r) in SEATS.items()])
    db = tmp_path / "shared.sqlite"
    port = free_port()
    base_env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(tmp_path / "home")}
    relay_env = {**base_env, "DIVAN_DB": str(db), "DIVAN_REGISTRY": str(tmp_path / "registry.json"),
                 "DIVAN_RELAY_KEY": wire.b64(bytes(relay_key)), "DIVAN_KEYS_DIR": str(tmp_path / "nokeys")}
    proc = subprocess.Popen([py, "-c", "import uvicorn; from relay.app import create_app; "
                             f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, log_level='warning')"],
                            cwd=work, env=relay_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        wait_port(port)
        url = f"http://127.0.0.1:{port}"
        beta, gamma = Client(url, "beta", key=keys["beta"]), Client(url, "gamma", key=keys["gamma"])
        beta.post("team:core", "exo client to legacy relay", thread="c1")
        gamma.post("beta", "sealed via legacy relay", enc_to="beta")
        assert [m["body"] for m in gamma.inbox()] == ["exo client to legacy relay"]
        assert beta.text(beta.inbox()[-1]) == "sealed via legacy relay"
        assert beta.verify()["ok"] is True
        assert http("GET", f"{url}/v1/verify")[1]["ok"] is True
    finally:
        proc.terminate()
        proc.wait(10)

    # the exo relay reopens the legacy database; the legacy client talks to the exo relay
    bus = Bus(tmp_path, db_path=db, keys=keys, relay_key=relay_key)
    try:
        assert http("GET", f"{bus.url}/v1/verify")[1] == {"ok": True, "from": 1, "to": 2, "checked": 2,
                                                           "head": bus.head()["head"]}
        home = tmp_path / "home"
        write_keys(home / ".divan", {"gamma": keys["gamma"], "delta": keys["delta"]})

        def legacy(seat, *args):
            env = {**base_env, "DIVAN_URL": bus.url, "DIVAN_SEAT": seat}
            return subprocess.run([py, str(work / "clients" / "divan.py"), *args],
                                  capture_output=True, text=True, env=env, timeout=60)

        r = legacy("delta", "post", "--to", "team:core", "--thread", "c1", "legacy client to exo relay")
        assert r.returncode == 0, r.stderr
        out = legacy("gamma", "inbox").stdout
        assert "exo client to legacy relay" in out and "legacy client to exo relay" in out
        r = legacy("gamma", "verify")
        assert r.returncode == 0 and json.loads(r.stdout)["checked"] == 3, r.stdout + r.stderr
        assert Client(bus.url, "beta", key=keys["beta"]).verify()["ok"] is True
    finally:
        bus.stop()
