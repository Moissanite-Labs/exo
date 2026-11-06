"""Offline tests for exo_command.swarm_api: a fake source proves the exact command payloads, that bad input never
reaches the core, how refusals and failures map, and that replies are validated. No socket, no core."""
import asyncio
import json

import pytest

from exo.protocol import schema as s
from exo_command import swarm_api as sw
from exo_command.source import SourceError

GID = "g_0123456789abcdef"
GROUP = {"id": GID, "owner": "kai", "mode": "socratic", "conductor": None, "created": "2026-01-01T00:00:00Z",
         "members": [{"profile": "lead", "session": "s_1"}, {"profile": "one", "session": "s_2"}]}


def msg(seq=1, text="hi", audience=("lead", "one"), kind="human"):
    return {"seq": seq, "id": "m_%d" % seq, "sender": "kai", "sender_kind": kind, "audience": list(audience),
            "text": text, "created": "2026-01-01T00:00:00Z"}


class Centre:
    def __init__(self, output=None, fail=None):
        self.calls, self.output, self.fail = [], output, fail
        self.source = self

    async def call(self, body):
        self.calls.append(body)
        if self.fail is not None:
            raise self.fail
        return {"output": self.output}


def run(centre, method, path, q=None, body=None):
    return asyncio.run(sw.dispatch(centre, method, path, q or {}, body or {}))


def sent(c):
    assert len(c.calls) == 1
    cmd = c.calls[0]
    assert isinstance(cmd, s.CommandRun) and cmd.name == "group" and cmd.session is None
    return cmd.args


def test_not_ours_returns_none_and_unknown_paths_are_rejected():
    c = Centre()
    assert run(c, "GET", "/api/chat/sessions") is None
    assert run(c, "GET", "/index.html") is None
    for path in ("/api/swarm", "/api/swarm/x", "/api/swarm/groups/" + GID + "/delete", "/api/swarm/groups/a/b/c/d",
                 "/api/swarm/groups/" + GID + "/stop/now", "/api/swarm/command", "/api/swarm/socket"):
        assert run(c, "GET", path)[0] == 404
    assert run(c, "POST", "/api/swarm/groups")[0] == 405
    assert run(c, "DELETE", "/api/swarm/groups/" + GID)[0] == 405
    assert run(c, "GET", "/api/swarm/groups/" + GID + "/send")[0] == 405
    for tail in ("send", "pause", "resume", "stop"):
        assert run(c, "GET", "/api/swarm/groups/%s/%s" % (GID, tail))[0] == 405
    for tail in ("status", "messages", ""):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            assert run(c, method, ("/api/swarm/groups/%s/%s" % (GID, tail)).rstrip("/"))[0] == 405
    for tail in ("pause", "resume", "stop"):
        for method in ("GET", "PUT", "DELETE", "PATCH"):
            if method != "GET":
                assert run(c, method, "/api/swarm/groups/%s/%s" % (GID, tail))[0] == 405
    assert c.calls == []


def test_list_inspect_exact_payloads_and_reply():
    c = Centre({"groups": [GROUP]})
    assert run(c, "GET", "/api/swarm/groups") == (200, {"groups": [GROUP]})
    assert sent(c) == "list"
    c = Centre(GROUP)
    assert run(c, "GET", "/api/swarm/groups/" + GID) == (200, GROUP)
    assert sent(c) == "inspect " + json.dumps({"id": GID})


def test_inspect_rejects_other_id_and_extra_core_fields_are_dropped():
    other = dict(GROUP, id="g_other")
    assert run(Centre(other), "GET", "/api/swarm/groups/" + GID)[1]["code"] == "bad_reply"
    assert run(Centre(dict(GROUP, secret="x")), "GET", "/api/swarm/groups/" + GID) == (200, GROUP)


def test_observe_exact_payloads():
    c = Centre({"messages": [msg(5), msg(6)], "next_after": 6})
    st, data = run(c, "GET", "/api/swarm/groups/%s/messages" % GID,
                   {"member": ["lead"], "after": ["4"], "limit": ["2"]})
    assert st == 200 and data["next_after"] == 6 and [m["seq"] for m in data["messages"]] == [5, 6]
    assert sent(c) == "observe " + json.dumps({"id": GID, "member": "lead", "after": 4, "limit": 2})
    c = Centre({"messages": [], "next_after": 0})
    assert run(c, "GET", "/api/swarm/groups/%s/messages" % GID) == (200, {"messages": [], "next_after": 0})
    assert sent(c) == "observe " + json.dumps({"id": GID})


@pytest.mark.parametrize("q", [{"after": ["-1"]}, {"after": ["1.5"]}, {"after": ["x"]}, {"after": [str(2 ** 63)]},
                               {"limit": ["0"]}, {"limit": ["1001"]}, {"limit": ["1", "2"]}, {"limit": [""]},
                               {"member": ["bad name"]}, {"member": [""]}, {"member": ["a" * 65]},
                               {"owner": ["x"]}, {"socket": ["/tmp/x"]}, {"host": ["h"]}])
def test_observe_bad_query_never_reaches_core(q):
    c = Centre({"messages": [], "next_after": 0})
    st, data = run(c, "GET", "/api/swarm/groups/%s/messages" % GID, q)
    assert st == 400 and data["code"] == "bad_request" and c.calls == []


@pytest.mark.parametrize("out", [{"messages": [msg(1), msg(1)], "next_after": 1},      # not increasing
                                 {"messages": [msg(3)], "next_after": 2},               # cursor mismatch
                                 {"messages": [msg(3)], "next_after": 3},               # not above after=5
                                 {"messages": [msg(6, kind="bot")], "next_after": 6},
                                 {"messages": [msg(6)], "next_after": True},
                                 {"messages": [dict(msg(6), seq=True)], "next_after": 6},
                                 {"messages": "x", "next_after": 0},
                                 {"messages": [msg(6), msg(7)], "next_after": 7}])      # more than limit=1
def test_observe_reply_validation(out):
    st, data = run(Centre(out), "GET", "/api/swarm/groups/%s/messages" % GID, {"after": ["5"], "limit": ["1"]})
    assert st == 502 and data["code"] == "bad_reply"


def test_send_exact_payload_and_persisted_only_response():
    c = Centre(msg(1, "change direction", ["lead"]))
    st, data = run(c, "POST", "/api/swarm/groups/%s/send" % GID, body={"text": "change direction", "audience": ["lead"]})
    assert st == 200
    assert sent(c) == "send " + json.dumps({"id": GID, "text": "change direction", "audience": ["lead"]})
    assert data["persisted"] is True and data["processed"] == "unknown" and data["woken"] == "unknown"
    assert "not mean" in data["note"] and data["message"]["seq"] == 1


def test_send_without_audience_leaves_the_core_default_and_makes_one_call_only():
    c = Centre(msg(2, "hello"))
    assert run(c, "POST", "/api/swarm/groups/%s/send" % GID, body={"text": "hello"})[0] == 200
    assert sent(c) == "send " + json.dumps({"id": GID, "text": "hello"})
    c = Centre(msg(2, "hello"))     # an explicit null audience is the same as omitted
    assert run(c, "POST", "/api/swarm/groups/%s/send" % GID, body={"text": "hello", "audience": None})[0] == 200
    assert sent(c) == "send " + json.dumps({"id": GID, "text": "hello"})


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "   "}, {"text": 5}, {"text": "a" * (sw.MAX_TEXT + 1)},
                                  {"text": "a\x00b"}, {"text": "x", "audience": []}, {"text": "x", "audience": "lead"},
                                  {"text": "x", "audience": [1]}, {"text": "x", "audience": ["a", "a"]},
                                  {"text": "x", "audience": ["bad name"]}, {"text": "x", "audience": ["a"] * 65},
                                  {"text": "x", "owner": "other"}, {"text": "x", "session": "s_1"},
                                  {"text": "x", "host": "h"}, {"text": "x", "socket": "/tmp/s"},
                                  {"text": "x", "sender": "lead"}, {"text": "x", "wake": True}, {"text": "x", "id": "g_1"}])
def test_send_bad_input_never_reaches_core(body):
    c = Centre(msg(1, "x"))
    st, data = run(c, "POST", "/api/swarm/groups/%s/send" % GID, body=body)
    assert st == 400 and data["code"] == "bad_request" and c.calls == []


@pytest.mark.parametrize("gid", ["a b", "-x", "x" * 129, "g%2F1", "a;b", ".hidden"])
def test_bad_group_id_never_reaches_core(gid):
    c = Centre(GROUP)
    for method, tail, body in (("GET", "", None), ("GET", "/messages", None), ("POST", "/send", {"text": "x"})):
        assert run(c, method, "/api/swarm/groups/%s%s" % (gid, tail), body=body)[0] == 400
    assert c.calls == []


def test_non_object_body_and_post_query_rejected():
    c = Centre(msg(1, "x"))
    assert asyncio.run(sw.dispatch(c, "POST", "/api/swarm/groups/%s/send" % GID, {}, ["x"]))[0] == 400
    assert run(c, "POST", "/api/swarm/groups/%s/send" % GID, q={"owner": ["x"]}, body={"text": "x"})[0] == 400
    assert run(c, "GET", "/api/swarm/groups", body={"x": 1})[0] == 400
    assert c.calls == []


@pytest.mark.parametrize("out", [msg(1, "OTHER"), msg(1, "x", kind="member"), msg(1, "x", ["lead", "one"]),
                                 dict(msg(1, "x"), seq=0), dict(msg(1, "x"), id=""), {"messages": []}, None, "ok", [msg(1)]])
def test_send_reply_validation(out):
    c = Centre(out)
    st, data = run(c, "POST", "/api/swarm/groups/%s/send" % GID, body={"text": "x", "audience": ["lead"]})
    assert st == 502 and data["code"] == "bad_reply" and data["persisted"] == "unknown"
    assert "may or may not" in data["error"]


@pytest.mark.parametrize("code,status", [("forbidden", 403), ("bad_request", 400), ("not_found", 501),
                                         ("unsupported", 501), ("unknown_command", 501), ("conflict", 409)])
def test_core_refusals_map_to_static_text(code, status):
    for method, path, body in (("GET", "/api/swarm/groups", None), ("POST", "/api/swarm/groups/%s/send" % GID, {"text": "x"})):
        c = Centre(fail=SourceError(code, "SECRET /tmp/exo.sock token=abc"))
        st, data = run(c, method, path, body=body)
        assert st == status and data["code"] == code
        assert "SECRET" not in json.dumps(data) and "persisted" not in data


@pytest.mark.parametrize("exc", [SourceError("internal", "boom /tmp/exo.sock"), SourceError("unauthorized", "tok"),
                                 ConnectionResetError("/tmp/exo.sock"), OSError("secret"), RuntimeError("secret"),
                                 asyncio.TimeoutError()])
def test_transport_failures_are_sanitized_and_send_says_unknown(exc):
    st, data = run(Centre(fail=exc), "GET", "/api/swarm/groups")
    assert st == 502 and data == {"error": sw.UNAVAILABLE, "code": "unavailable"}
    st, data = run(Centre(fail=exc), "POST", "/api/swarm/groups/%s/send" % GID, body={"text": "x"})
    assert st == 502 and data["persisted"] == "unknown" and "secret" not in json.dumps(data).lower()
    assert "/tmp" not in json.dumps(data) and "boom" not in json.dumps(data)


@pytest.mark.parametrize("out", [None, "x", [], {"groups": "x"}, {"groups": [{"id": GID}]},
                                 {"groups": [dict(GROUP, mode="other")]}, {"groups": [dict(GROUP, members=[])]},
                                 {"groups": [dict(GROUP, members=[{"profile": "a"}])]}])
def test_list_reply_validation(out):
    st, data = run(Centre(out), "GET", "/api/swarm/groups")
    assert st == 502 and data["code"] == "bad_reply" and "persisted" not in data


def test_module_has_no_generic_surface():
    assert set(sw.ROUTES) == {("GET", ()), ("GET", ("<id>",)), ("GET", ("<id>", "messages")), ("POST", ("<id>", "send")),
                              ("GET", ("<id>", "status")), ("POST", ("<id>", "pause")), ("POST", ("<id>", "resume")),
                              ("POST", ("<id>", "stop"))}
    src = open(sw.__file__).read()
    assert src.count("s.CommandRun(") == 1 and src.count('name="group"') == 2   # one call site (+ the docstring)
    assert "getenv" not in src and "environ" not in src and "import socket" not in src


# ------------------------------------------------------------------ lifecycle
OPS = (("pause", "paused"), ("resume", "resumed"), ("stop", "stopped"))
URL = "/api/swarm/groups/%s/" % GID
STATUS = {"id": GID, "owner": "kai", "local_runtime": True, "reconciliation_required": False,
          "members": [{"profile": "lead", "state": "running"}, {"profile": "one", "state": "pausing"}],
          "receipt": {"id": "gx_1", "state": "active", "created": "2026-01-01T00:00:00Z", "finished": None,
                      "members": {"lead": "running", "one": "queued"}}}


def reply(flag, member=None, gid=GID):
    return {"id": gid, flag: True, "member": member}


def test_status_exact_payload_and_reply():
    c = Centre(STATUS)
    assert run(c, "GET", URL + "status") == (200, STATUS)
    assert sent(c) == "status " + json.dumps({"id": GID})
    c = Centre(dict(STATUS, receipt=None, local_runtime=False, reconciliation_required=True, extra="x"))
    st, data = run(c, "GET", URL + "status")
    assert st == 200 and data["receipt"] is None and data["local_runtime"] is False and "extra" not in data
    fin = dict(STATUS["receipt"], state="settled", finished="2026-01-01T00:01:00Z", members={"lead": "completed"})
    assert run(Centre(dict(STATUS, receipt=fin)), "GET", URL + "status")[1]["receipt"] == fin


@pytest.mark.parametrize("out", [None, "x", [], {}, dict(STATUS, id="g_other"), dict(STATUS, id=None),
                                 dict(STATUS, owner=""), dict(STATUS, members=[]), dict(STATUS, members="x"),
                                 dict(STATUS, members=[{"profile": "lead", "state": "flying"}]),
                                 dict(STATUS, members=[{"profile": "lead"}]),
                                 dict(STATUS, members=[{"profile": "bad name", "state": "idle"}]),
                                 dict(STATUS, members=[{"profile": "a", "state": "idle"}, {"profile": "a", "state": "idle"}]),
                                 dict(STATUS, local_runtime="true"), dict(STATUS, local_runtime=1),
                                 dict(STATUS, local_runtime=None), dict(STATUS, reconciliation_required="false"),
                                 dict(STATUS, reconciliation_required=0), {k: v for k, v in STATUS.items() if k != "receipt"} | {"owner": 5},
                                 dict(STATUS, receipt="x"), dict(STATUS, receipt={}),
                                 dict(STATUS, receipt=dict(STATUS["receipt"], members=[])),
                                 dict(STATUS, receipt=dict(STATUS["receipt"], members={"lead": "weird"})),
                                 dict(STATUS, receipt=dict(STATUS["receipt"], finished=5)),
                                 dict(STATUS, receipt=dict(STATUS["receipt"], finished="")),
                                 dict(STATUS, receipt=dict(STATUS["receipt"], id="")),
                                 dict(STATUS, receipt=dict(STATUS["receipt"], created=None))])
def test_status_reply_validation(out):
    st, data = run(Centre(out), "GET", URL + "status")
    assert st == 502 and data == {"error": sw.BAD_REPLY, "code": "bad_reply"}


@pytest.mark.parametrize("kw", [{"q": {"member": ["lead"]}}, {"q": {"owner": ["x"]}}, {"body": {"member": "lead"}}])
def test_status_bad_input_never_reaches_core(kw):
    c = Centre(STATUS)
    assert run(c, "GET", URL + "status", **kw)[0] == 400 and c.calls == []


@pytest.mark.parametrize("op,flag", OPS)
def test_control_exact_payloads_group_wide_and_member(op, flag):
    for body in (None, {}, {"member": None}):
        c = Centre(reply(flag))
        st, data = run(c, "POST", URL + op, body=body)
        assert sent(c) == op + " " + json.dumps({"id": GID})        # no member key at all
        assert st == 200 and data == {"id": GID, "action": op, "member": None, "accepted": True,
                                      "settled": "unknown", "note": sw.ACCEPTED_ONLY}
    c = Centre(reply(flag, "lead"))
    st, data = run(c, "POST", URL + op, body={"member": "lead"})
    assert sent(c) == op + " " + json.dumps({"id": GID, "member": "lead"})
    assert st == 200 and data["member"] == "lead" and data["accepted"] is True and data["settled"] == "unknown"
    assert "does not mean" in data["note"]


@pytest.mark.parametrize("op,flag", OPS)
@pytest.mark.parametrize("body", [{"member": ""}, {"member": "bad name"}, {"member": 5}, {"member": ["lead"]},
                                  {"member": True}, {"member": "a" * 65}, {"member": "lead", "owner": "x"},
                                  {"owner": "x"}, {"socket": "/tmp/s"}, {"host": "h"}, {"session": "s_1"},
                                  {"id": "g_1"}, {"op": "stop"}, {"name": "group"}, {"force": True}, {"all": True}])
def test_control_bad_body_never_reaches_core(op, flag, body):
    c = Centre(reply(flag))
    st, data = run(c, "POST", URL + op, body=body)
    assert st == 400 and data["code"] == "bad_request" and c.calls == []


@pytest.mark.parametrize("op,flag", OPS)
def test_control_bad_query_id_and_non_object_body_never_reach_core(op, flag):
    c = Centre(reply(flag))
    assert run(c, "POST", URL + op, q={"member": ["lead"]})[0] == 400
    assert run(c, "POST", URL + op, q={"socket": ["/x"]}, body={"member": "lead"})[0] == 400
    assert asyncio.run(sw.dispatch(c, "POST", URL + op, {}, ["lead"]))[0] == 400
    assert asyncio.run(sw.dispatch(c, "POST", URL + op, {}, "lead"))[0] == 400
    for gid in ("a b", "-x", "x" * 129, ".hidden", "g%2F1"):
        assert run(c, "POST", "/api/swarm/groups/%s/%s" % (gid, op))[0] == 400
    assert c.calls == []


@pytest.mark.parametrize("op,flag", OPS)
@pytest.mark.parametrize("member,out", [
    (None, reply("x")), (None, {}), (None, None), (None, "ok"), (None, [reply("x")]),
    (None, {"id": GID, "member": None}), (None, reply("OTHERFLAG")), (None, "FLAGFALSE"), (None, "FLAGSTR"),
    (None, "FLAGONE"), (None, "WRONGID"), (None, "MEMBERSET"), ("lead", "MEMBERNONE"), ("lead", "MEMBEROTHER"),
    ("lead", "WRONGID")])
def test_control_mismatched_or_malformed_reply_is_unknown_outcome(op, flag, member, out):
    flagged = {"FLAGFALSE": dict(reply(flag), **{flag: False}), "FLAGSTR": dict(reply(flag), **{flag: "true"}),
               "FLAGONE": dict(reply(flag), **{flag: 1}), "WRONGID": reply(flag, member, gid="g_other"),
               "MEMBERSET": reply(flag, "lead"), "MEMBERNONE": reply(flag, None), "MEMBEROTHER": reply(flag, "one")}
    if isinstance(out, str) and out in flagged:
        out = flagged[out]
    body = {"member": member} if member else None
    c = Centre(out)
    st, data = run(c, "POST", URL + op, body=body)
    assert st == 502 and data["code"] == "bad_reply" and data["outcome"] == "unknown"
    assert "accepted" not in data and "not retried automatically" in data["error"] and len(c.calls) == 1


def test_control_reply_for_another_op_is_rejected():
    c = Centre(reply("stopped"))
    st, data = run(c, "POST", URL + "pause")
    assert st == 502 and data["outcome"] == "unknown"


@pytest.mark.parametrize("op,flag", OPS)
@pytest.mark.parametrize("exc", [SourceError("internal", "boom /tmp/exo.sock"), SourceError("unauthorized", "tok"),
                                 ConnectionResetError("/tmp/exo.sock"), OSError("secret"), RuntimeError("secret"),
                                 asyncio.TimeoutError()])
def test_control_transport_failure_is_sanitized_unknown_and_not_retried(op, flag, exc):
    c = Centre(fail=exc)
    st, data = run(c, "POST", URL + op, body={"member": "lead"})
    assert st == 502 and data["code"] == "unavailable" and data["outcome"] == "unknown"
    assert "persisted" not in data and "accepted" not in data
    text = json.dumps(data)
    assert "/tmp" not in text and "boom" not in text and "secret" not in text.lower() and "tok" not in data["error"].split()
    assert len(c.calls) == 1                                    # never retried


@pytest.mark.parametrize("op,flag", OPS)
@pytest.mark.parametrize("code,status", [("forbidden", 403), ("bad_request", 400), ("not_found", 501),
                                         ("unsupported", 501), ("unknown_command", 501), ("conflict", 409)])
def test_control_core_refusals_are_definite_and_static(op, flag, code, status):
    c = Centre(fail=SourceError(code, "SECRET /tmp/exo.sock token=abc"))
    st, data = run(c, "POST", URL + op)
    assert st == status and data["code"] == code and "outcome" not in data and "SECRET" not in json.dumps(data)
    c = Centre(fail=SourceError(code, "SECRET /tmp/exo.sock"))
    st, data = run(c, "GET", URL + "status")
    assert st == status and "SECRET" not in json.dumps(data) and len(c.calls) == 1


@pytest.mark.parametrize("exc", [SourceError("internal", "boom /tmp/x"), OSError("secret"), asyncio.TimeoutError()])
def test_status_transport_failure_is_sanitized_and_not_unknown_outcome(exc):
    st, data = run(Centre(fail=exc), "GET", URL + "status")
    assert (st, data) == (502, {"error": sw.UNAVAILABLE, "code": "unavailable"})


def test_only_fixed_ops_are_ever_sent():
    seen = []
    for op, flag in OPS:
        c = Centre(reply(flag, "lead"))
        run(c, "POST", URL + op, body={"member": "lead"})
        seen.append(sent(c).split(" ", 1)[0])
    c = Centre(STATUS)
    run(c, "GET", URL + "status")
    seen.append(sent(c).split(" ", 1)[0])
    assert seen == ["pause", "resume", "stop", "status"]
