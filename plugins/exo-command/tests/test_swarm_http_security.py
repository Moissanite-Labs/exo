"""The /api/swarm status, pause, resume and stop routes behind the real exo_command.server HTTP boundary.

These tests start the real WebServer on a loopback port the operating system picks, with a synthetic page token and
a fake source in place of the core. That is different from test_swarm_controls_socket.py, which talks to a real
Unix socket. Here the fake source only records the commands it is handed. Everything before it is real: the HTTP
parser, the Host, Origin and token checks, the body reader, api.dispatch and swarm_api.

Three things are shown. A request with no token, a wrong token, a hostile Origin or a hostile Host is refused with 403
before api.dispatch runs, so nothing reaches the core. A request that passes reaches the real api.dispatch and
hands the fake source one fixed group command with only the group id and the optional member. Extra fields that try to
name an owner, a socket or a path are rejected or ignored and never change that command."""
import asyncio
import json

import pytest

from exo.protocol import schema as s
from exo_command import api
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre
from test_csp_web import Src as BaseSrc
from test_swarm_api import GID, OPS, STATUS, reply
from test_web_auth import raw

TOKEN = "page-token-for-tests-0123456789abcdef"
LIFE = "/api/swarm/groups/%s/" % GID
AUTH = {"X-Exo-Token": TOKEN}
ACTIONS = [("GET", "status"), ("POST", "pause"), ("POST", "resume"), ("POST", "stop")]
FLAGS = dict(OPS)


class RecordingSrc(BaseSrc):
    """A fake source. It records every command and answers like a healthy core."""

    def __init__(self):
        self.commands = []

    async def call(self, body):
        if isinstance(body, s.CommandRun):
            self.commands.append(body)
            verb, _, rest = body.args.partition(" ")
            if verb == "status":
                return {"output": STATUS}
            member = json.loads(rest).get("member") if rest else None
            return {"output": reply(FLAGS[verb], member)}
        return await super().call(body)


def served(go, monkeypatch):
    """Run go(port, seen) against a live loopback server. seen lists each call that reached the real api.dispatch."""
    seen = []
    real = api.dispatch

    async def spy(centre, method, path, q, body):
        seen.append((method, path, q, body))
        return await real(centre, method, path, q, body)

    monkeypatch.setattr(api, "dispatch", spy)
    src = RecordingSrc()

    async def main():
        web = WebServer(CommandCentre(load_settings({}), src), token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        try:
            return await asyncio.wait_for(go(port), 10)
        finally:
            await web.close()
    out = asyncio.run(main())
    return out, seen, src.commands


def _expected_args(op, member=None):
    payload = {"id": GID} if member is None else {"id": GID, "member": member}
    return "%s %s" % (op, json.dumps(payload))


def _hostile(port):
    own = "127.0.0.1:%d" % port
    return [
        ("no token", {}),
        ("empty token", {"X-Exo-Token": ""}),
        ("wrong token", {"X-Exo-Token": "wrong"}),
        ("token with one char changed", {"X-Exo-Token": TOKEN[:-1] + "X"}),
        ("token truncated", {"X-Exo-Token": TOKEN[:-1]}),
        ("token longer", {"X-Exo-Token": TOKEN + "x"}),
        ("hostile Origin", dict(AUTH, Origin="http://evil.example")),
        ("Origin null", dict(AUTH, Origin="null")),
        ("Origin https same host", dict(AUTH, Origin="https://" + own)),
        ("Origin other port", dict(AUTH, Origin="http://127.0.0.1:%d" % (port + 1))),
        ("Origin without token", {"Origin": "http://evil.example"}),
        ("Sec-Fetch-Site cross-site", dict(AUTH, **{"Sec-Fetch-Site": "cross-site"})),
        ("Sec-Fetch-Site same-site", dict(AUTH, **{"Sec-Fetch-Site": "same-site"})),
        ("hostile Host", dict(AUTH, Host="evil.example")),
        ("hostile Host with port", dict(AUTH, Host="evil.example:%d" % port)),
        ("rebound Host", dict(AUTH, Host="rebind.example:%d" % port)),
        ("Host other port", dict(AUTH, Host="127.0.0.1:%d" % (port + 1))),
        ("Host without port", dict(AUTH, Host="127.0.0.1")),
        ("empty Host", dict(AUTH, Host="")),
        ("missing Host", dict(AUTH, Host=None)),
    ]


@pytest.mark.parametrize("method,op", ACTIONS)
def test_missing_wrong_token_and_hostile_origin_or_host_fail_closed_before_dispatch(monkeypatch, method, op):
    async def go(port):
        out = {}
        for label, hdrs in _hostile(port):
            out[label] = await raw(port, method, LIFE + op, hdrs, {} if method == "POST" else None)
        # The token alone in the query string is not accepted on a plain route. Only the two event streams take it.
        out["token in query only"] = await raw(port, method, LIFE + op + "?token=" + TOKEN, None,
                                               {} if method == "POST" else None)
        return out
    out, seen, commands = served(go, monkeypatch)
    assert {k: v[0] for k, v in out.items()} == {k: 403 for k in out}
    assert all(TOKEN.encode() not in v[1] for v in out.values())
    assert seen == [] and commands == []


@pytest.mark.parametrize("path", ["/api/swarm/groups", "/api/swarm/groups/" + GID, "/api/swarm/groups/%s/messages" % GID,
                                  "/api/swarm/groups/%s/send" % GID])
def test_refusal_covers_the_other_swarm_routes_too(monkeypatch, path):
    async def go(port):
        method = "POST" if path.endswith("/send") else "GET"
        body = {"text": "x"} if method == "POST" else None
        return [(await raw(port, method, path, h, body))[0] for _, h in _hostile(port)]
    codes, seen, commands = served(go, monkeypatch)
    assert set(codes) == {403} and seen == [] and commands == []


@pytest.mark.parametrize("method,op", ACTIONS)
def test_unauthorized_post_with_owner_socket_body_is_refused_and_never_dispatched(monkeypatch, method, op):
    async def go(port):
        hostile = {"owner": "kai", "socket": "/tmp/s.sock", "member": "lead"}
        return [(await raw(port, "POST", LIFE + op, h, hostile))[0]
                for _, h in _hostile(port)] + [(await raw(port, "POST", LIFE + op, {"X-Exo-Token": "x"}, hostile))[0]]
    codes, seen, commands = served(go, monkeypatch)
    assert set(codes) == {403} and seen == [] and commands == []


def test_valid_status_reaches_real_dispatch_and_sends_one_fixed_group_command(monkeypatch):
    async def go(port):
        return await raw(port, "GET", LIFE + "status", AUTH)
    (status, data), seen, commands = served(go, monkeypatch)
    assert status == 200 and json.loads(data) == STATUS
    assert seen == [("GET", LIFE + "status", {}, {})]
    assert len(commands) == 1 and isinstance(commands[0], s.CommandRun)
    assert commands[0].name == "group" and commands[0].session is None
    assert commands[0].args == _expected_args("status")


@pytest.mark.parametrize("op", ["pause", "resume", "stop"])
@pytest.mark.parametrize("member", [None, "lead"])
def test_valid_control_reaches_real_dispatch_and_sends_one_fixed_group_command(monkeypatch, op, member):
    body = {} if member is None else {"member": member}

    async def go(port):
        return await raw(port, "POST", LIFE + op, AUTH, body)
    (status, data), seen, commands = served(go, monkeypatch)
    got = json.loads(data)
    assert status == 200 and got["id"] == GID and got["action"] == op and got["member"] == member
    assert got["accepted"] is True and got["settled"] == "unknown"
    assert seen == [("POST", LIFE + op, {}, body)]
    assert len(commands) == 1 and isinstance(commands[0], s.CommandRun)
    assert commands[0].name == "group" and commands[0].session is None
    assert commands[0].args == _expected_args(op, member)


def test_valid_same_origin_and_localhost_host_are_accepted(monkeypatch):
    async def go(port):
        own = "127.0.0.1:%d" % port
        return [(await raw(port, "GET", LIFE + "status", dict(AUTH, Origin="http://" + own)))[0],
                (await raw(port, "GET", LIFE + "status", dict(AUTH, **{"Sec-Fetch-Site": "same-origin"})))[0],
                (await raw(port, "POST", LIFE + "pause", dict(AUTH, Origin="http://" + own), {}))[0],
                (await raw(port, "GET", LIFE + "status", dict(AUTH, Host="localhost:%d" % port)))[0]]
    codes, seen, commands = served(go, monkeypatch)
    assert codes == [200, 200, 200, 200] and len(seen) == 4 and len(commands) == 4


# Fields that try to name an owner, a socket, a path or another group. None may change the command sent to the core.
INJECT_BODIES = [{"owner": "mallory"}, {"principal": "mallory"}, {"socket": "/tmp/evil.sock"},
                 {"sock": "/tmp/evil.sock"}, {"path": "/etc/passwd"}, {"host": "evil.example"},
                 {"url": "http://evil.example"}, {"id": "g_other"}, {"name": "stop"}, {"args": "stop {}"},
                 {"command": "group stop"}, {"session": "s_1"},
                 {"member": "lead", "owner": "mallory"}, {"member": "lead", "socket": "/tmp/evil.sock"},
                 {"member": "../../etc"}, {"member": "lead/../x"}, {"member": "a" * 500}, {"member": ["lead"]}]


@pytest.mark.parametrize("op", ["pause", "resume", "stop"])
def test_extra_owner_socket_path_authority_in_a_valid_request_body_is_rejected(monkeypatch, op):
    async def go(port):
        out = []
        for body in INJECT_BODIES:
            st, data = await raw(port, "POST", LIFE + op, AUTH, body)
            out.append((st, json.loads(data).get("code")))
        return out
    out, seen, commands = served(go, monkeypatch)
    assert out == [(400, "bad_request")] * len(INJECT_BODIES)
    assert len(seen) == len(INJECT_BODIES)   # authorized, so each one did reach api.dispatch and was refused there
    assert commands == []


@pytest.mark.parametrize("query", ["socket=/tmp/evil.sock", "owner=mallory", "path=/etc/passwd", "host=evil.example",
                                   "url=http://evil.example", "id=g_other", "member=lead"])
def test_extra_authority_in_the_query_string_is_rejected_for_status(monkeypatch, query):
    async def go(port):
        return await raw(port, "GET", LIFE + "status?" + query, AUTH)
    (status, data), seen, commands = served(go, monkeypatch)
    assert status == 400 and json.loads(data)["code"] == "bad_request" and len(seen) == 1 and commands == []


def test_extra_authority_in_headers_or_the_url_path_never_changes_the_command(monkeypatch):
    async def go(port):
        extra = dict(AUTH, **{"X-Exo-Owner": "mallory", "X-Exo-Socket": "/tmp/evil.sock", "X-Forwarded-Host": "evil.example",
                              "X-Owner": "mallory", "Socket": "/tmp/evil.sock"})
        ok = await raw(port, "POST", LIFE + "stop", extra, {})
        paths = [(await raw(port, "POST", p, AUTH, {}))[0]
                 for p in ("/api/swarm/socket", "/api/swarm/command", "/api/swarm/owner/mallory/stop",
                           "/api/swarm/groups/%s/stop/tmp/evil.sock" % GID, "/api/swarm/groups/%2e%2e/stop",
                           "/api/swarm/groups/%s/stop/../send" % GID)]
        return ok[0], paths
    (ok, paths), seen, commands = served(go, monkeypatch)
    assert ok == 200 and paths == [404, 404, 404, 404, 400, 404]
    assert len(commands) == 1 and commands[0].name == "group" and commands[0].args == _expected_args("stop")


def test_wrong_method_and_unknown_tail_over_http_never_reach_the_core(monkeypatch):
    async def go(port):
        return [(await raw(port, m, LIFE + t, AUTH, {} if m != "GET" else None))[0]
                for m, t in (("POST", "status"), ("GET", "pause"), ("GET", "stop"), ("PUT", "resume"),
                             ("DELETE", "stop"), ("PATCH", "stop"), ("POST", "start"), ("POST", "stop/now"))]
    codes, seen, commands = served(go, monkeypatch)
    assert codes == [405, 405, 405, 405, 405, 405, 404, 404] and commands == []
