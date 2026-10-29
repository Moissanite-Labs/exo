"""The chat web API and the protocol bridge: fake source, no network, no real core except one in-process run."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path


from exo.protocol import schema as s
from exo_command import api
from exo_command.config import load_settings
from exo_command.server import STATIC, WebServer
from exo_command.service import CommandCentre
from exo_command.source import SourceError


class FakeSource:
    """Records commands; answers from a table keyed by command type; streams what the test feeds it."""

    def __init__(self, answers=None, fail=None):
        self.calls = []
        self.answers = answers or {}
        self.fail = fail or {}
        self.feed: asyncio.Queue = asyncio.Queue()

    async def call(self, body):
        t = type(body).TYPE
        self.calls.append(body)
        if t in self.fail:
            raise SourceError(self.fail[t], "nope")
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        return self.answers.get(t, {})

    async def stream(self, since=0):
        while True:
            ev = await self.feed.get()
            yield ev

    async def aclose(self):
        return None


def ev(seq, typ, body, sid="s1"):
    return {"seq": seq, "type": typ, "body": body, "session": sid, "ts": "2026-10-11T10:00:00Z"}


def centre(src=None):
    return CommandCentre(load_settings({}), src or FakeSource())


def run(coro):
    return asyncio.run(coro)


def call(c, method, path, body=None, q=None):
    return run(api.dispatch(c, method, path, q or {}, body or {}))


# ------------------------------------------------------------------ api routes


def test_session_routes_send_the_right_commands():
    src = FakeSource({"session.list": {"sessions": [{"id": "s1"}]}, "session.create": {"session": {"id": "s2"}},
                      "session.get": {"session": {"id": "s1"}, "messages": [{"role": "user"}]}})
    c = centre(src)
    assert call(c, "GET", "/api/chat/sessions") == (200, {"sessions": [{"id": "s1"}]})
    assert call(c, "POST", "/api/chat/sessions", {"title": "hi", "seat": "lead"})[1] == {"session": {"id": "s2"}}
    code, data = call(c, "GET", "/api/chat/sessions/s1")
    assert code == 200 and data["messages"] == [{"role": "user"}] and data["seq"] == 0
    assert call(c, "POST", "/api/chat/sessions/s1/send", {"text": "hello", "mode": "queue"})[0] == 200
    assert call(c, "POST", "/api/chat/sessions/s1/interrupt")[0] == 200
    kinds = [type(b).__name__ for b in src.calls]
    assert kinds == ["SessionList", "SessionCreate", "SessionGet", "TurnSend", "TurnInterrupt"]
    send = src.calls[3]
    assert (send.session, send.text, send.mode) == ("s1", "hello", "queue")
    assert src.calls[1].title == "hi" and src.calls[1].seat == "lead"


def test_bad_input_is_refused_before_any_command():
    src = FakeSource()
    c = centre(src)
    assert call(c, "POST", "/api/chat/sessions/s1/send", {"text": ""})[0] == 400
    assert call(c, "POST", "/api/chat/sessions/s1/send", {"text": "x", "mode": "shout"})[0] == 400
    assert call(c, "POST", "/api/chat/sessions/s1/send", {"text": 5})[0] == 400
    assert call(c, "POST", "/api/feedback", {"session": "s1", "verdict": "love"})[0] == 400
    assert call(c, "POST", "/api/feedback", {"session": "s1", "verdict": "edit"})[0] == 400
    assert call(c, "POST", "/api/feedback", {"verdict": "accept"})[0] == 400
    assert call(c, "POST", "/api/ui/respond", {"panel_id": "p", "action": "clicked"})[0] == 400
    assert call(c, "POST", "/api/ui/respond", {"panel_id": "p", "action": "acted", "value": "x"})[0] == 400
    assert call(c, "POST", "/api/schedule", {"action": "add", "when": "every 5m"})[0] == 400
    assert call(c, "POST", "/api/schedule", {"action": "remove"})[0] == 400
    assert call(c, "POST", "/api/schedule", {"action": "explode"})[0] == 400
    assert call(c, "GET", "/api/chat/sessions", q={"limit": ["x"]})[0] == 400
    assert src.calls == []


def test_session_list_limit_stays_between_1_and_500():
    """exo-desktop #55: only the top was capped, so limit=-1 reached SQL as LIMIT -1, which returns every row."""
    src = FakeSource({"session.list": {"sessions": []}})
    c = centre(src)
    for low in ("-1", "0", "-500"):
        assert call(c, "GET", "/api/chat/sessions", q={"limit": [low]})[0] == 400, low
    assert src.calls == []
    for asked, sent in (("1", 1), ("500", 500), ("9999", 500)):
        assert call(c, "GET", "/api/chat/sessions", q={"limit": [asked]})[0] == 200
        assert src.calls[-1].limit == sent, asked
    assert call(c, "GET", "/api/chat/sessions")[0] == 200   # no limit asked: 100
    assert src.calls[-1].limit == 100


def test_read_only_command_route():
    src = FakeSource({"command.run": {"output": "Next call for thread x"}})
    c = centre(src)
    assert call(c, "POST", "/api/chat/sessions/s1/command", {"name": "context"}) == (200, {"output": "Next call for thread x"})
    assert call(c, "POST", "/api/chat/sessions/s1/command", {"name": "findings", "args": "count"})[0] == 200
    assert call(c, "POST", "/api/chat/sessions/s1/command", {"name": "compact"})[0] == 400
    assert call(c, "GET", "/api/chat/sessions/s1/command")[0] == 405
    assert [(b.name, b.args, b.session) for b in src.calls] == [("context", "", "s1"), ("findings", "count", "s1")]


def test_session_model_route_is_scoped_and_preserves_core_refusals():
    src = FakeSource({"models.list": {"models": [{"route": "fake:x"}]}})
    c = centre(src)
    assert call(c, "GET", "/api/chat/models") == (200, {"models": [{"route": "fake:x"}]})
    assert call(c, "POST", "/api/chat/sessions/s1/model", {"model": "fake:y", "role": "deep", "session": "s2"})[0] == 200
    cmd = src.calls[-1]
    assert isinstance(cmd, s.ModelsSet) and (cmd.model, cmd.session, cmd.role) == ("fake:y", "s1", "main")
    assert call(c, "POST", "/api/chat/sessions/s1/model", {"model": ""})[0] == 400
    assert call(c, "GET", "/api/chat/sessions/s1/model")[0] == 405
    src.fail["models.set"] = "forbidden"
    assert call(c, "POST", "/api/chat/sessions/s1/model", {"model": "fake:y"})[0] == 403
    for code in ("bad_model", "bad_request"):   # the caller's mistake, as /api/providers/default says; 502 means the core is away
        src.fail["models.set"] = code
        assert call(c, "POST", "/api/chat/sessions/s1/model", {"model": "nosuch:m"}) == (400, {"error": "nope", "code": code})
    src.fail["session.get"] = "not_found"
    before = sum(isinstance(cmd, s.ModelsSet) for cmd in src.calls)
    assert call(c, "POST", "/api/chat/sessions/missing/model", {"model": "fake:y"})[0] == 404
    assert sum(isinstance(cmd, s.ModelsSet) for cmd in src.calls) == before
    assert not any(isinstance(cmd, s.TurnSend) for cmd in src.calls)


def test_model_change_http_requires_page_token_and_same_origin():
    async def go():
        src = FakeSource()
        web = WebServer(centre(src), token="model-test-token")
        port = await web.start("127.0.0.1", 0)
        async def request(headers):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            body = json.dumps({"model": "fake:y"})
            h = {"Host": f"127.0.0.1:{port}", "Content-Length": str(len(body)), **headers}
            w.write(("POST /api/chat/sessions/s1/model HTTP/1.1\r\n" +
                     "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n" + body).encode())
            await w.drain()
            response = await r.read()
            w.close()
            await w.wait_closed()
            return int(response.split(b" ", 2)[1])
        try:
            assert await request({}) == 403
            assert await request({"X-Exo-Token": "wrong"}) == 403
            assert await request({"X-Exo-Token": "model-test-token", "Origin": "https://evil.example"}) == 403
            assert src.calls == []
            assert await request({"X-Exo-Token": "model-test-token"}) == 200
            assert isinstance(src.calls[-1], s.ModelsSet) and src.calls[-1].session == "s1"
        finally:
            await web.close()
    run(go())


def test_methods_and_unknown_paths():
    c = centre()
    assert call(c, "GET", "/api/feedback")[0] == 405
    assert call(c, "POST", "/api/tasks")[0] == 405
    assert call(c, "DELETE", "/api/chat/sessions")[0] == 405
    assert call(c, "GET", "/api/nothing") is None
    assert call(c, "GET", "/index") is None


def test_feedback_and_ui_respond_carry_the_schema_fields():
    src = FakeSource()
    c = centre(src)
    c.panels["p1"] = {"panel_id": "p1", "seq": 1}
    assert call(c, "POST", "/api/feedback", {"session": "s1", "verdict": "edit", "message_id": "m1", "text": "better"})[0] == 200
    assert call(c, "POST", "/api/feedback", {"session": "s1", "verdict": "accept"})[0] == 200
    fb1, fb2 = src.calls
    assert (fb1.session, fb1.verdict, fb1.message_id, fb1.text) == ("s1", "edit", "m1", "better")
    assert fb2.message_id is None
    assert call(c, "POST", "/api/ui/respond", {"panel_id": "p1", "action": "acted", "value": {"choice": "b"}})[0] == 200
    assert "p1" in c.panels
    assert call(c, "POST", "/api/ui/respond", {"panel_id": "p1", "action": "dismissed"})[0] == 200
    assert "p1" not in c.panels
    r1, r2 = src.calls[2:]
    assert (r1.panel_id, r1.action, r1.value) == ("p1", "acted", {"choice": "b"}) and r2.action == "dismissed"


def test_task_open_steer_stop_routes_use_the_subagent_builtin():
    src = FakeSource({"command.run": {"output": {"task_id": "k_1", "steerable": True}}})
    c = centre(src)
    assert call(c, "GET", "/api/tasks/k_1") == (200, {"task_id": "k_1", "steerable": True})
    assert call(c, "POST", "/api/tasks/k_1/steer", {"text": "use the other file"})[0] == 200
    assert call(c, "POST", "/api/tasks/k_1/stop")[0] == 200
    assert [(b.name, b.args) for b in src.calls] == [("subagent", "open k_1"), ("subagent", "send k_1 use the other file"),
                                                     ("subagent", "stop k_1")]
    assert call(c, "POST", "/api/tasks/k_1/steer", {})[0] == 400          # no text
    assert call(c, "POST", "/api/tasks/bad id/stop")[0] == 400            # a ref cannot smuggle arguments
    assert call(c, "GET", "/api/tasks/k_1/steer")[0] == 405
    assert len(src.calls) == 3


def test_task_routes_map_core_refusals():
    for code, status in (("forbidden", 403), ("conflict", 409), ("not_found", 404)):
        c = centre(FakeSource(fail={"command.run": code}))
        assert call(c, "POST", "/api/tasks/k_1/stop")[0] == status


def test_schedule_and_tasks():
    src = FakeSource({"schedule.edit": {"jobs": [{"job_id": "j1", "when": "every 1m"}]},
                      "tasks.list": {"tasks": [{"task_id": "t1"}]}})
    c = centre(src)
    assert call(c, "GET", "/api/schedule")[1] == {"jobs": [{"job_id": "j1", "when": "every 1m"}]}
    assert call(c, "GET", "/api/tasks", q={"session": ["s1"]})[1] == {"tasks": [{"task_id": "t1"}]}
    assert call(c, "POST", "/api/schedule", {"action": "add", "when": "0 9 * * *", "prompt": "digest",
                                             "title": "morning", "tier": "standard"})[0] == 200
    for action in ("pause", "resume", "remove", "run_now"):
        assert call(c, "POST", "/api/schedule", {"action": action, "job_id": "j1"})[0] == 200
    cmds = [b for b in src.calls if isinstance(b, s.ScheduleEdit)]
    assert [b.action for b in cmds] == ["list", "add", "pause", "resume", "remove", "run_now"]
    add = cmds[1]
    assert (add.when, add.prompt, add.title, add.tier, add.deliver) == ("0 9 * * *", "digest", "morning", "standard", "notice")
    assert src.calls[1].session == "s1"
    assert call(c, "POST", "/api/schedule", {"action": "add", "when": "x", "prompt": "y", "tier": "huge"})[0] == 400


def test_core_errors_map_to_http_codes():
    c = centre(FakeSource(fail={"feedback.send": "unsupported", "session.get": "not_found", "turn.send": "boom"}))
    assert call(c, "POST", "/api/feedback", {"session": "s", "verdict": "accept"})[0] == 501
    assert call(c, "GET", "/api/chat/sessions/zzz")[0] == 404
    code, data = call(c, "POST", "/api/chat/sessions/s/send", {"text": "x"})
    assert code == 502 and data["code"] == "boom"


# ------------------------------------------------------------------ the bridge


def test_record_keeps_ring_and_folds_panels():
    c = centre()
    c.record(ev(1, "message.delta", {"message_id": "m", "text": "a"}))
    c.record(ev(2, "ui.panel", {"panel_id": "p", "action": "open", "kind": "diff", "title": "T", "text": "t", "data": {"diff": "+x"}}))
    c.record(ev(3, "ui.panel", {"panel_id": "p", "action": "update", "kind": "diff", "title": "", "text": "t2", "data": {}}))
    c.record(ev(3, "ui.panel", {"panel_id": "q", "action": "open", "kind": "table"}))       # replay: ignored
    assert [e["seq"] for e in c.ring] == [1, 2, 3]
    p = c.panels["p"]
    assert p["title"] == "T" and p["text"] == "t2" and p["data"] == {"diff": "+x"} and p["session"] == "s1"
    assert "q" not in c.panels
    c.record(ev(4, "ui.panel", {"panel_id": "p", "action": "close"}))
    assert c.panels == {}
    assert [e["seq"] for e in c.events_since(1)] == [2, 3, 4]
    c.record(ev(5, "notice", {"text": "hi"}, sid=None))
    c.record(ev(6, "message.delta", {"message_id": "x", "text": "b"}, sid="other"))
    assert [e["seq"] for e in c.events_since(4, "s1")] == [5]


def test_follow_and_sync_feed_the_ring():
    async def main():
        src = FakeSource()
        c = centre(src)
        await c.bootstrap()
        task = asyncio.create_task(c.follow())
        src.feed.put_nowait(ev(1, "ui.panel", {"panel_id": "p", "action": "open", "kind": "markdown", "text": "hello"}))
        for _ in range(100):
            if c.panels:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        assert c.panels["p"]["text"] == "hello" and [e["seq"] for e in c.ring] == [1]

    run(main())


def test_slow_event_subscriber_is_cut_off_not_blocking():
    c = centre()
    q = c.subscribe_events()
    for i in range(1, 1100):
        c.record(ev(i, "notice", {"text": "x"}, sid=None))
    assert q not in c._event_subs
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items[-1] is None


# ------------------------------------------------------------------ over http


async def http(port, method, path, body=None, headers=None):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else b""
    hs = "".join("{}: {}\r\n".format(k, v) for k, v in dict({"X-Exo-Token": "t0k"}, **(headers or {})).items())
    w.write("{} {} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nContent-Length: {}\r\n{}\r\n".format(
        method, path, port, len(data), hs).encode() + data)
    await w.drain()
    raw = await r.read()
    w.close()
    head, _, payload = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), payload


def test_http_routes_origin_and_event_stream():
    async def main():
        src = FakeSource({"session.create": {"session": {"id": "s9"}}})
        c = centre(src)
        c.record(ev(1, "message.delta", {"message_id": "m1", "text": "old"}))
        c.record(ev(2, "ui.panel", {"panel_id": "p", "action": "open", "kind": "chart", "text": "t"}, sid=None))
        web = WebServer(c, token="t0k")
        port = await web.start("127.0.0.1", 0)
        code, body = await http(port, "POST", "/api/chat/sessions", {"title": "x"})
        assert code == 200 and json.loads(body)["session"]["id"] == "s9"
        # a page on another origin cannot send, give feedback, or edit the schedule
        for path, b in (("/api/chat/sessions/s9/send", {"text": "x"}), ("/api/feedback", {"session": "s9", "verdict": "accept"}),
                        ("/api/schedule", {"action": "list"}), ("/api/ui/respond", {"panel_id": "p", "action": "opened"})):
            assert (await http(port, "POST", path, b, {"Origin": "http://evil.example"}))[0] == 403
        assert not any(isinstance(x, (s.TurnSend, s.FeedbackSend, s.UiRespond)) for x in src.calls)
        assert (await http(port, "POST", "/api/feedback", {"session": "s9", "verdict": "bogus"}))[0] == 400
        raw = await http(port, "POST", "/api/feedback", {"session": "s9", "verdict": "reject"})
        assert raw[0] == 200
        code, body = await http(port, "GET", "/api/chat/panels")
        assert json.loads(body)["panels"][0]["panel_id"] == "p"

        # the stream replays from ?since and then goes live
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write("GET /api/events?since=1&session=s9&token=t0k HTTP/1.1\r\nHost: 127.0.0.1:{}\r\n\r\n".format(port).encode())
        await w.drain()
        await asyncio.wait_for(r.readuntil(b"\n\n"), 2)                 # headers + comment
        first = await asyncio.wait_for(r.readuntil(b"\n\n"), 2)
        assert first.startswith(b"id: 2\ndata: ")
        assert (await asyncio.wait_for(r.readuntil(b"\n\n"), 2)).startswith(b"event: synced")
        c.record(ev(3, "message.delta", {"message_id": "m2", "text": "live"}, sid="s9"))
        c.record(ev(4, "message.delta", {"message_id": "m3", "text": "other"}, sid="s0"))
        c.record(ev(5, "task.updated", {"task_id": "t"}, sid=None))
        got = []
        for _ in range(2):
            frame = await asyncio.wait_for(r.readuntil(b"\n\n"), 2)
            got.append(json.loads(frame.split(b"data: ", 1)[1]))
        assert [e["seq"] for e in got] == [3, 5] and got[0]["body"]["text"] == "live"
        w.close()
        # Last-Event-ID resumes too
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write("GET /api/events?token=t0k HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nLast-Event-ID: 4\r\n\r\n".format(port).encode())
        await w.drain()
        await asyncio.wait_for(r.readuntil(b"\n\n"), 2)
        frame = await asyncio.wait_for(r.readuntil(b"\n\n"), 2)
        assert frame.startswith(b"id: 5\n")
        w.close()
        await web.close()

    run(main())


def test_chat_through_a_real_core_streams_text():
    """session.create, turn.send and the event stream against an in-process core with a fake model."""
    from test_protocol import make
    from exo_command.source import DispatcherSource

    eng, disp = make(_tmp(), [("hello", ["streamed answer"])])

    async def main():
        c = CommandCentre(load_settings(), DispatcherSource(disp))
        await c.bootstrap()
        follow = asyncio.create_task(c.follow())
        web = WebServer(c, token="t0k")
        port = await web.start("127.0.0.1", 0)
        code, body = await http(port, "POST", "/api/chat/sessions", {"title": "t"})
        sid = json.loads(body)["session"]["id"]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write("GET /api/events?session={}&token=t0k HTTP/1.1\r\nHost: 127.0.0.1:{}\r\n\r\n".format(sid, port).encode())
        await w.drain()
        code, _ = await http(port, "POST", "/api/chat/sessions/{}/send".format(sid), {"text": "hello there"})
        assert code == 200
        seen = []
        while "turn.completed" not in seen:
            frame = await asyncio.wait_for(r.readuntil(b"\n\n"), 5)
            if frame.startswith(b"id: "):
                seen.append(json.loads(frame.split(b"data: ", 1)[1])["type"])
        assert "message.completed" in seen and "turn.started" in seen
        code, body = await http(port, "GET", "/api/chat/sessions/" + sid)
        roles = [m["role"] for m in json.loads(body)["messages"]]
        assert roles == ["user", "assistant"]
        # the core does not handle these yet: the web layer says so instead of failing
        code, _ = await http(port, "POST", "/api/feedback", {"session": sid, "verdict": "accept"})
        assert code in (200, 501)
        w.close()
        follow.cancel()
        await web.close()

    run(main())


_dirs = []


def _tmp():
    import tempfile
    d = tempfile.TemporaryDirectory()
    _dirs.append(d)
    return Path(d.name)


# ------------------------------------------------------------------ the page


def test_static_files_are_local_and_complete():
    page = (STATIC / "index.html").read_text()
    assert 'type="module" src="/static/app.js"' in page
    mods = {p.name for p in STATIC.glob("*.js")}
    assert {"app.js", "chat.js", "panels.js", "tasks.js", "schedule.js", "bus.js", "dom.js", "markdown.js", "command.js"} <= mods
    for p in list(STATIC.glob("*")):
        text = p.read_text()
        assert not re.search(r"(src|href)=[\"']https?://", text) and "@import" not in text, p.name
        assert not re.search(r"""(import\s.*from|fetch\()\s*["']https?://""", text), p.name
        for m in re.finditer(r"""from\s+["']\./([\w.]+)["']""", text):
            assert (STATIC / m.group(1)).is_file(), (p.name, m.group(1))


def test_panel_kinds_match_the_schema():
    import typing
    schema_kinds = set(typing.get_args(typing.get_type_hints(s.UiPanel)["kind"]))
    text = (STATIC / "panels.js").read_text()
    names = set(re.findall(r'"(\w+)"', re.search(r"KINDS = \[(.*?)\]", text).group(1)))
    assert names == schema_kinds
    for k in names - {"markdown"}:
        assert re.search(r"\b{}\b".format(k), text.split("const RENDER")[1].split("\n")[0]), k


def test_no_personal_data_in_the_ui_files():
    for p in list(STATIC.glob("*")) + [Path(api.__file__)]:
        assert "/Users/" not in p.read_text(), p.name
