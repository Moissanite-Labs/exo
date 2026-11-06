"""api.dispatch hands exactly the /api/swarm prefix to swarm_api.dispatch; every other path is unchanged.
Offline: a fake source, no core. The last tests use the real WebServer on loopback with a fake source only, to show
the server's token/Host guard still runs first and nothing in the swarm routes takes a socket, host or command."""
import asyncio
import json

import pytest

from exo.protocol import schema as s
from exo_command import api, swarm_api
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre
from test_csp_web import Src as BaseSrc, head
from exo_command.source import SourceError
from test_swarm_api import GID, GROUP, OPS, STATUS, Centre, msg, reply, sent

TOKEN = "page-token-for-tests-0123456789abcdef"


def run(centre, method, path, q=None, body=None):
    return asyncio.run(api.dispatch(centre, method, path, q or {}, body or {}))


def test_prefix_is_forwarded_and_replies_match_swarm_api_exactly():
    for method, path, q, body in (("GET", "/api/swarm/groups", None, None),
                                  ("GET", "/api/swarm/groups/" + GID, None, None),
                                  ("GET", "/api/swarm/groups/%s/messages" % GID, {"limit": ["1"]}, None),
                                  ("POST", "/api/swarm/groups/%s/send" % GID, None, {"text": "hi"}),
                                  ("GET", "/api/swarm/nope", None, None),
                                  ("DELETE", "/api/swarm/groups", None, None)):
        a, b = Centre({"groups": [GROUP]}), Centre({"groups": [GROUP]})
        assert run(a, method, path, q, body) == asyncio.run(swarm_api.dispatch(b, method, path, q or {}, body or {}))
        assert [c.args for c in a.calls] == [c.args for c in b.calls]


def test_list_through_api_dispatch_sends_one_group_command():
    c = Centre({"groups": [GROUP]})
    assert run(c, "GET", "/api/swarm/groups") == (200, {"groups": [GROUP]})
    assert len(c.calls) == 1 and isinstance(c.calls[0], s.CommandRun) and c.calls[0].name == "group"


def test_swarm_routing_is_forwarded_not_shadowed_by_the_chat_table():
    # api._route knows nothing about swarm: without the forward these would be None, not 404/405 replies.
    assert api._route("GET", ["swarm", "groups"], "swarm/groups") is None
    c = Centre()
    assert run(c, "GET", "/api/swarm")[0] == 404
    assert run(c, "GET", "/api/swarm/")[0] == 404
    assert run(c, "POST", "/api/swarm/groups")[0] == 405
    assert c.calls == []


@pytest.mark.parametrize("path", ["", "/", "/api", "/api/", "//api", "/apix/swarm/groups", "/api/swarmx",
                                  "/api/swarmx/groups", "/api/swarm2", "/api/Swarm/groups", "/api/chat/swarm/groups",
                                  "/x/api/swarm/groups", "/API/swarm/groups", "/swarm/groups", "api/"])
def test_boundary_and_lookalike_paths_are_not_swarm(path):
    c = Centre({"groups": []})
    assert run(c, "GET", path) is None
    assert c.calls == []


def test_non_swarm_routes_are_unchanged():
    c = Centre()
    assert run(c, "GET", "/api/nothing-here") is None
    assert run(c, "GET", "/api/chat/sessions/x/unknown") is None
    assert run(c, "DELETE", "/api/chat/sessions") == (405, {"error": "method not allowed"})
    assert run(c, "PATCH", "/api/tasks") == (405, {"error": "method not allowed"})
    assert c.calls == []


def test_swarm_never_reaches_the_chat_handlers(monkeypatch):
    def boom(*a):
        raise AssertionError("_route must not run for /api/swarm")
    monkeypatch.setattr(api, "_route", boom)
    assert run(Centre(), "GET", "/api/swarm/groups/a/b/c/d")[0] == 404
    assert run(Centre({"groups": []}), "GET", "/api/swarm/groups") == (200, {"groups": []})


@pytest.mark.parametrize("path", [
    "/api/swarm/groups/" + "a" * 129, "/api/swarm/groups/..", "/api/swarm/groups/%2e%2e", "/api/swarm/groups/a%2Fb",
    "/api/swarm/groups/-x", "/api/swarm/groups/g 1", "/api/swarm/groups/" + GID + "/messages/extra",
    "/api/swarm/groups/" + "/".join("x" * 5 for _ in range(500))])
def test_bad_or_overlong_paths_never_reach_the_core(path):
    c = Centre({"groups": []})
    st, data = run(c, "GET", path)
    assert st in (400, 404) and "error" in data and c.calls == []


@pytest.mark.parametrize("path,q,body", [
    ("/api/swarm/groups/%s/messages" % GID, {"after": ["-1"]}, None),
    ("/api/swarm/groups/%s/messages" % GID, {"limit": ["1001"]}, None),
    ("/api/swarm/groups/%s/messages" % GID, {"after": ["1", "2"]}, None),
    ("/api/swarm/groups/%s/messages" % GID, {"socket": ["/tmp/x.sock"]}, None),
    ("/api/swarm/groups/%s/messages" % GID, {"host": ["evil.example"]}, None),
    ("/api/swarm/groups", {"host": ["evil.example"]}, None),
    ("/api/swarm/groups/" + GID, {"url": ["http://evil.example"]}, None)])
def test_query_validation_rejects_before_any_call_and_no_socket_or_host_is_accepted(path, q, body):
    c = Centre({"groups": []})
    st, data = run(c, "GET", path, q, body)
    assert st == 400 and data["code"] == "bad_request" and c.calls == []


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "x", "socket": "/tmp/s"}, {"text": "x", "host": "h"},
                                  {"text": "x", "sender_kind": "member"}, {"text": "x", "name": "stop"},
                                  {"text": "x" * 50_001}, {"text": "x", "audience": "lead"}, {"text": 5}, [], "x"])
def test_send_body_validation_rejects_before_any_call(body):
    c = Centre(msg())
    st, data = run(c, "POST", "/api/swarm/groups/%s/send" % GID, None, body)
    assert st == 400 and data["code"] == "bad_request" and c.calls == []


def test_send_reply_says_unknown_processed_and_woken_independently():
    c = Centre(msg(7, "hello", audience=("lead",)))
    st, data = run(c, "POST", "/api/swarm/groups/%s/send" % GID, None, {"text": "hello", "audience": ["lead"]})
    assert st == 200
    assert data["persisted"] is True and data["processed"] == "unknown" and data["woken"] == "unknown"
    assert data["processed"] not in (True, False, "yes", "no") and "does not mean" in data["note"]
    assert set(data) == {"message", "persisted", "processed", "woken", "note"}


def test_error_responses_are_sanitized_static_json():
    secret = "token=SECRET /home/x/.sock"
    for exc in (RuntimeError(secret), OSError(secret), SourceError_("unsupported", secret)):
        c = Centre(fail=exc)
        for st, data in (run(c, "GET", "/api/swarm/groups"), run(c, "POST", "/api/swarm/groups/%s/send" % GID, None, {"text": "x"})):
            assert st in (501, 502) and "SECRET" not in json.dumps(data) and ".sock" not in json.dumps(data)
    st, data = run(Centre(fail=RuntimeError(secret)), "POST", "/api/swarm/groups/%s/send" % GID, None, {"text": "x"})
    assert st == 502 and data["persisted"] == "unknown"
    assert run(Centre({"groups": "bad"}), "GET", "/api/swarm/groups")[1]["code"] == "bad_reply"


def SourceError_(code, message):
    from exo_command.source import SourceError
    return SourceError(code, message) if _takes_two(SourceError) else SourceError(code)


def _takes_two(cls):
    import inspect
    return len(inspect.signature(cls.__init__).parameters) >= 3


# ---------------------------------------------------------------- lifecycle routes through api.dispatch
LIFE = "/api/swarm/groups/%s/" % GID


def test_status_through_api_dispatch_sends_exact_payload_and_forwards_reply():
    c = Centre(STATUS)
    assert run(c, "GET", LIFE + "status") == (200, STATUS)
    assert sent(c) == "status " + json.dumps({"id": GID})


@pytest.mark.parametrize("op,flag", OPS)
@pytest.mark.parametrize("member", [None, "lead"])
def test_control_through_api_dispatch_sends_exact_payload(op, flag, member):
    c = Centre(reply(flag, member))
    body = {} if member is None else {"member": member}
    st, data = run(c, "POST", LIFE + op, None, body)
    assert st == 200 and data["id"] == GID and data["action"] == op and data["member"] == member
    assert data["accepted"] is True and data["settled"] == "unknown"
    payload = {"id": GID} if member is None else {"id": GID, "member": member}
    assert sent(c) == "%s %s" % (op, json.dumps(payload))
    assert asyncio.run(swarm_api.dispatch(Centre(reply(flag, member)), "POST", LIFE + op, {}, body)) == (st, data)


@pytest.mark.parametrize("op,flag", OPS)
def test_control_with_null_member_sends_no_member_key(op, flag):
    c = Centre(reply(flag))
    assert run(c, "POST", LIFE + op, None, {"member": None})[0] == 200
    assert sent(c) == "%s %s" % (op, json.dumps({"id": GID}))


@pytest.mark.parametrize("op,flag", OPS)
@pytest.mark.parametrize("body", [{"owner": "kai"}, {"member": "lead", "principal": "x"}, {"member": "bad name"},
                                  {"member": 5}, {"id": "g_other"}, {"socket": "/tmp/s"}])
def test_control_bad_body_through_api_dispatch_never_reaches_core(op, flag, body):
    c = Centre(reply(flag))
    st, data = run(c, "POST", LIFE + op, None, body)
    assert st == 400 and data["code"] == "bad_request" and c.calls == []


@pytest.mark.parametrize("path,method", [(LIFE + "status", "POST"), (LIFE + "pause", "GET"), (LIFE + "resume", "PUT"),
                                         (LIFE + "stop", "DELETE"), (LIFE + "stop", "PATCH")])
def test_lifecycle_wrong_method_through_api_dispatch_is_405_without_call(path, method):
    c = Centre(STATUS)
    st, data = run(c, method, path)
    assert st == 405 and data["code"] == "method_not_allowed" and c.calls == []


def test_lifecycle_unknown_tail_through_api_dispatch_is_404_without_call():
    c = Centre(STATUS)
    for tail in ("start", "stop/now", "Stop"):
        assert run(c, "POST", LIFE + tail)[0] == 404
    assert c.calls == []


@pytest.mark.parametrize("op,flag", OPS)
@pytest.mark.parametrize("code,status", [("forbidden", 403), ("conflict", 409), ("bad_request", 400),
                                         ("unsupported", 501)])
def test_control_refusals_forwarded_through_api_dispatch_are_definite(op, flag, code, status):
    c = Centre(fail=SourceError(code, "SECRET /tmp/exo.sock"))
    st, data = run(c, "POST", LIFE + op)
    assert st == status and data["code"] == code and "outcome" not in data and "SECRET" not in json.dumps(data)
    assert len(c.calls) == 1


@pytest.mark.parametrize("code,status", [("forbidden", 403), ("conflict", 409)])
def test_status_refusals_forwarded_through_api_dispatch(code, status):
    c = Centre(fail=SourceError(code, "SECRET"))
    st, data = run(c, "GET", LIFE + "status")
    assert st == status and data["code"] == code and "SECRET" not in json.dumps(data) and len(c.calls) == 1


@pytest.mark.parametrize("op,flag", OPS)
def test_control_failure_and_bad_reply_through_api_dispatch_are_unknown_outcome_not_retried(op, flag):
    for c in (Centre(fail=OSError("SECRET /x.sock")), Centre(reply(flag, "other")), Centre("junk")):
        st, data = run(c, "POST", LIFE + op, None, {"member": "lead"})
        assert st == 502 and data["outcome"] == "unknown" and "SECRET" not in json.dumps(data)
        assert len(c.calls) == 1


# ---------------------------------------------------------------- the server guard is unchanged and runs first
class CountingSrc(BaseSrc):
    def __init__(self):
        self.commands = []

    async def call(self, body):
        if isinstance(body, s.CommandRun):
            self.commands.append(body)
            return {"output": {"groups": []}}
        return await super().call(body)


def _served(go):
    src = CountingSrc()

    async def main():
        web = WebServer(CommandCentre(load_settings({}), src), token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        try:
            return await go(port)
        finally:
            await web.close()
    return asyncio.run(main()), src


def test_server_guard_still_refuses_swarm_routes_without_token_or_with_a_foreign_host_or_origin():
    async def go(port):
        out = []
        for hdrs in ({}, {"X-Exo-Token": "wrong"}, {"X-Exo-Token": TOKEN, "Host": "evil.example:80"},
                      {"X-Exo-Token": TOKEN, "Origin": "http://evil.example"},
                      {"X-Exo-Token": TOKEN, "Sec-Fetch-Site": "cross-site"}):
            for method, path in (("GET", "/api/swarm/groups"), ("POST", "/api/swarm/groups/%s/send" % GID)):
                out.append((await head(port, method, path, hdrs))[0])
        return out
    codes, src = _served(go)
    assert codes == [403] * 10 and src.commands == []


def test_server_with_token_reaches_swarm_and_other_api_paths_still_404():
    async def go(port):
        auth = {"X-Exo-Token": TOKEN}
        return [(await head(port, "GET", "/api/swarm/groups", auth))[0],
                (await head(port, "GET", "/api/swarm/nothing", auth))[0],
                (await head(port, "POST", "/api/swarm/groups", auth))[0],
                (await head(port, "GET", "/api/swarmx", auth))[0],
                (await head(port, "GET", "/api/nothing-here", auth))[0]]
    codes, src = _served(go)
    assert codes == [200, 404, 405, 404, 404] and len(src.commands) == 1
