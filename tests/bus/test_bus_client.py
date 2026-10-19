import json
import subprocess
import sys
from pathlib import Path

import pytest

from exo.bus import BusError, Client, wire

from .conftest import write_keys

SRC = str(Path(__file__).resolve().parents[2] / "src")


def run(bus, keys_dir, seat, *args):
    env = {"EXO_BUS_URL": bus.url, "EXO_BUS_SEAT": seat, "EXO_BUS_KEYS_DIR": str(keys_dir),
           "EXO_HOME": str(keys_dir.parent / "exo-home"), "PATH": "/usr/bin:/bin", "PYTHONPATH": SRC}
    return subprocess.run([sys.executable, "-m", "exo.bus", *args], capture_output=True, text=True, env=env)


def test_cli_end_to_end(bus, tmp_path):
    keys = tmp_path / "keys"
    write_keys(keys, {"beta": bus.keys["beta"], "gamma": bus.keys["gamma"]})
    r = run(bus, keys, "beta", "keygen", "new-seat")
    assert r.returncode == 0 and r.stdout.startswith("new-seat "), r.stderr
    assert oct((keys / "new-seat.key").stat().st_mode)[-3:] == "600" and (keys / "new-seat.pub").exists()
    assert run(bus, keys, "beta", "keygen", "new-seat").returncode == 2  # refuses to overwrite
    assert run(bus, keys, "beta", "pubkey").stdout.strip() == wire.pubkey_b64(bus.keys["beta"])
    r = run(bus, keys, "beta", "post", "--to", "team:core", "--thread", "T", "hello team")
    assert r.returncode == 0 and json.loads(r.stdout)["seq"] == 1, r.stderr
    r = run(bus, keys, "beta", "post", "--to", "gamma", "--enc-to", "gamma", "secret for gamma")
    assert r.returncode == 0, r.stderr
    out = run(bus, keys, "gamma", "inbox").stdout
    assert "hello team" in out and "secret for gamma" in out
    out = run(bus, keys, "beta", "inbox", "--json").stdout
    assert "secret for gamma" not in out and len(out.strip().splitlines()) == 1  # not addressed to beta
    assert "hello team" in run(bus, keys, "beta", "thread", "T").stdout
    assert json.loads(run(bus, keys, "beta", "head").stdout)["seq"] == 2
    r = run(bus, keys, "beta", "verify")
    assert r.returncode == 0 and json.loads(r.stdout)["checked"] == 2, r.stderr


def test_client_api_post_inbox_thread_verify(bus):
    beta, gamma = bus.client("beta"), bus.client("gamma")
    _first = beta.post("team:core", "opening", thread="t-1")
    beta.post(["gamma"], "reply", thread="t-1")
    gamma.post("beta", "sealed", enc_to="beta")
    assert [m["body"] for m in gamma.inbox()][:2] == ["opening", "reply"]
    assert [m["body"] for m in gamma.thread("t-1")] == ["opening", "reply"]
    sealed = beta.inbox(since=2)[0]
    assert sealed["enc"]["to"] == "beta" and beta.text(sealed) == "sealed"
    assert gamma.text(sealed) == "<encrypted to beta>"
    assert beta.verify() == {"ok": True, "checked": 3, "head": bus.head()["head"], "relay_head": bus.head()["head"]}
    with pytest.raises(BusError) as e:
        beta.post("nobody", "x")
    assert e.value.status == 422


def test_client_needs_seat_and_key(tmp_path):
    with pytest.raises(BusError, match="no seat"):
        Client("http://127.0.0.1:9", seat=None, keys_dir=tmp_path).key
    with pytest.raises(BusError, match="no key"):
        Client("http://127.0.0.1:9", seat="ghost", keys_dir=tmp_path).key
    with pytest.raises(BusError):
        Client("http://127.0.0.1:9", seat="ghost", keys_dir=tmp_path).head()  # nothing listening


def test_client_retries_on_prev_mismatch(bus, monkeypatch):
    cli = bus.client("beta")
    real, seen = cli.api, []

    def racing(method, path, body=None, auth=False):
        if method == "POST" and not seen:  # someone else lands a message between our head read and our post
            seen.append(body["prev"])
            bus.post("delta", "group", "sneaky")
        return real(method, path, body, auth)

    monkeypatch.setattr(cli, "api", racing)
    assert cli.post(["group"], "after the race")["seq"] == 2
    assert bus.inbox("beta") == ["sneaky", "after the race"]


def test_sym2p_body_rides_inside_the_envelope(bus):
    sym2p = pytest.importorskip("exo.protocols.sym2p")
    line = sym2p.encode_brief(id="b441", tk="mr1", cf=88, cv=70, scope=("sym-2p/1.0", 3, 5), rq="ack")
    beta = bus.client("beta")
    beta.post_sym2p("gamma", line, thread="t1")
    env = bus.client("gamma").inbox()[0]
    assert json.loads(env["body"]) == {"data": line, "fmt": "sym2p"}
    human = bus.client("gamma").text(env)
    assert human != env["body"] and "{" not in human[:1]
    assert bus.client("gamma").text(env, render=False) == env["body"]
    bus.relay.config.web_bodies = True  # the web view hides bodies unless the operator opts in
    page = __import__("urllib.request").request.urlopen(bus.url + "/").read().decode()
    assert '"fmt"' not in page and __import__("html").escape(human) in page  # the plain-English rendering
