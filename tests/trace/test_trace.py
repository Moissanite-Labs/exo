"""Harness self-observation (D-025): recording, old-session derivation, anomaly rules, findings lifecycle, tools, CLI."""

from __future__ import annotations

import asyncio
import json

import pytest

from exo.core import trace
from exo.core.engine import CoreEngine
from exo.core.findings import Findings
from exo.core.models.fake import FakeProvider
from exo.core.models.router import Router
from exo.core.sessions import SessionStore
from exo.core.trace import Call, Flag, TaskRow, Thresholds, ToolRow, TurnTrace
from exo.core.types import ProviderEvent, ToolCall, Usage
from exo.protocol import schema as s

TH = Thresholds()


def run(coro):
    return asyncio.run(coro)


def engine(tmp_path, rules=None, **kw) -> CoreEngine:
    models = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"rules": rules or []}}
    return CoreEngine(tmp_path / "home", models, cwd=str(tmp_path), env={}, **kw)


def types(eng, sid=None):
    return [e for e in eng.hub.since(0) if sid is None or e.session == sid]


# ---------------------------------------------------------------- record


def test_turn_records_model_calls_tools_and_timing(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    eng = engine(tmp_path, [{"match": "go", "steps": [{"tool": "read_file", "args": {"path": "a.txt"}},
                                                      {"tool": "read_file", "args": {"path": "nope.txt"}}, "done"]}])

    async def go():
        info = await eng.loop.create_session(title="t")
        await eng.loop.send(info.id, "go")
        return info.id

    sid = run(go())
    evs = types(eng, sid)
    calls = [e.body for e in evs if isinstance(e.body, s.ModelCall)]
    assert len(calls) == 3 and calls[0].role == "main" and calls[0].requested == "fake:x"
    assert calls[0].route == "fake:x" and calls[0].ttft_ms is not None or calls[0].total_ms >= 0
    assert all(c.usage is not None for c in calls)
    done = [e.body for e in evs if isinstance(e.body, s.ToolCompleted)]
    assert [d.is_error for d in done] == [False, True] and all(d.duration_ms >= 0 for d in done)
    t = [e.body for e in evs if isinstance(e.body, s.TurnCompleted)][0].timing
    assert t["model_calls"] == 3 and t["tool_errors"] == 1 and t["fallbacks"] == 0
    assert set(t) == {"wall_ms", "model_ms", "tool_ms", "approval_ms", "model_calls", "fallbacks", "tool_errors"}
    # the turn end applied the rules and filed the tool error
    sigs = [f["signature"] for f in eng.findings.list()]
    assert any(x.startswith("tool.error:read_file") for x in sigs)


def test_router_reports_every_attempt_and_the_serving_route():
    down = FakeProvider([ProviderEvent(kind="error", error="ReadTimeout: slow", retryable=True)])
    ok = FakeProvider(["hello"])
    r = Router({"main": ["a:one", "b:two"]}, {"a": {}, "b": {}}, instances={"a": down, "b": ok}, env={})

    async def go():
        return [e async for e in r.stream("main", [], [])]

    evs = run(go())
    assert any(e.kind == "notice" for e in evs)
    done = evs[-1]
    assert done.kind == "done" and done.route == "b:two"
    assert [(a["route"], a["outcome"]) for a in done.attempts] == [("a:one", "timeout"), ("b:two", "ok")]
    assert "ReadTimeout" in done.attempts[0]["error"]


def test_router_terminal_failure_lists_attempts():
    bad = FakeProvider([ProviderEvent(kind="error", error="HTTP 500: boom", retryable=True, status=500)])
    r = Router({"main": ["a:one"]}, {"a": {}}, instances={"a": bad}, env={})

    async def go():
        return [e async for e in r.stream("main", [], [])]

    end = run(go())[-1]
    assert end.kind == "error" and [a["outcome"] for a in end.attempts] == ["error"]


def test_harness_errors_are_persisted_and_scrubbed(tmp_path):
    eng = engine(tmp_path)

    def bad_hook(**kw):
        raise RuntimeError("boom with sk-abcdefghijklmnop1234 and Bearer abcdef123456")

    eng.plugins.hooks.register("on_turn_start", bad_hook, plugin="p")

    async def go():
        info = await eng.loop.create_session(title="t")
        await eng.handle(s.Command.of(s.TurnSend(session=info.id, text="hi")), eng.hub)
        await asyncio.sleep(0.2)
        return info.id

    sid = run(go())
    errs = [e.body for e in types(eng, sid) if isinstance(e.body, s.HarnessError)]
    assert errs and errs[0].where == "hook on_turn_start (p)" and errs[0].error_type == "RuntimeError"
    text = json.dumps(vars(errs[0]))
    assert "sk-abc" not in text and "abcdef123456" not in text and "[redacted]" in text
    assert "RuntimeError" in errs[0].traceback
    assert any(f["signature"].startswith("harness.error:hook on_turn_start") for f in eng.findings.list())


def test_scrub_removes_secrets_and_cuts():
    out = trace.scrub("Authorization: Bearer abcdefghij api_key=hunter2hunter2 ok " + "x" * 600, 100)
    assert "abcdefghij" not in out and "hunter2" not in out and len(out) <= 100


def test_loop_memory_failure_is_recorded(tmp_path):
    eng = engine(tmp_path)

    class Mem:
        def sync_turn(self, *a, **k):
            raise ValueError("mem down")

    eng.loop.memory = Mem()

    async def go():
        info = await eng.loop.create_session(title="t")
        await eng.loop.send(info.id, "hi")
        return info.id

    sid = run(go())
    assert any(isinstance(e.body, s.HarnessError) and e.body.where == "memory.sync_turn" for e in types(eng, sid))


# ---------------------------------------------------------------- read: old sessions


def old_session(store: SessionStore, sid="s1"):
    """A pre D-025 session: no model.call, no timing, no is_error. Timestamps are set by hand."""
    store.create_session(id=sid, title="old")

    def put(ts, body):
        ev = store.append_event(body, sid)
        store._q("UPDATE events SET ts=? WHERE seq=?", (ts, ev.seq))

    call = ToolCall("c1", "bash", {"command": "pytest"})
    put("2026-10-12T04:00:00.000Z", s.TurnStarted(turn_id="t1", model="p:shown"))
    put("2026-10-12T04:00:40.000Z", s.MessageDelta(message_id="m", text="x"))
    put("2026-10-12T04:00:41.000Z", s.UsageUpdated(usage=Usage(input_tokens=10, output_tokens=2), model="p:other"))
    put("2026-10-12T04:00:42.000Z", s.ToolStarted(call=call))
    put("2026-10-12T04:01:12.000Z", s.ToolCompleted(result=s.ToolResult.of_text("c1", "bad", True)))
    put("2026-10-12T04:01:13.000Z", s.UsageUpdated(usage=Usage(input_tokens=20, output_tokens=3), model="p:other"))
    put("2026-10-12T04:01:20.000Z", s.TurnCompleted(turn_id="t1"))


def test_old_session_is_derived_from_timestamps():
    store = SessionStore()
    old_session(store)
    (t,) = trace.build(store, "s1")
    assert t.derived and t.wall_ms == 80_000 and len(t.calls) == 2
    assert t.calls[0].ttft_ms == 40_000 and t.calls[0].total_ms == 41_000
    assert t.tools[0].ms == 30_000 and t.tools[0].is_error
    assert (t.tokens_in, t.tokens_out) == (30, 5) and t.served == ["p:other"]
    sigs = {f.signature for f in t.flags}
    assert {"turn.slow", "model.wait:p:other", "model.mismatch"} <= sigs and "tool.error:bash:bad" in sigs
    assert "~" in trace.table([t])


def test_old_session_fallback_notice_counts():
    store = SessionStore()
    store.create_session(id="s2")
    for b in (s.TurnStarted(turn_id="t", model="a:x"),
              s.Notice(text="a:x did not answer in 60 s; trying b:y", level="warn"),
              s.UsageUpdated(usage=Usage(), model="b:y"), s.TurnCompleted(turn_id="t")):
        store.append_event(b, "s2")
    (t,) = trace.build(store, "s2")
    assert t.fallbacks == 1 and {"model.timeout:a:x"} <= {f.signature for f in t.flags}
    assert "model.mismatch" not in {f.signature for f in t.flags}  # a fallback explains it


def test_subagent_turns_roll_into_the_parent():
    store = SessionStore()
    store.create_session(id="p")
    store.create_session(id="k")
    store.append_event(s.TurnStarted(turn_id="t", model="m:a"), "p")
    store.append_event(s.TaskUpdated(task_id="k1", title="work", status="running", child_session="k"), "p")
    store.append_event(s.TurnStarted(turn_id="kt", model="m:a"), "k")
    for i in range(2):
        call = ToolCall(f"c{i}", "bash", {"command": "cd /x && pytest -q | tail -5", "timeout": 10 * (i + 1)})
        store.append_event(s.ToolStarted(call=call), "k")
        store.append_event(s.ToolCompleted(result=s.ToolResult.of_text(f"c{i}", "ok"), duration_ms=5), "k")
    store.append_event(s.TurnCompleted(turn_id="kt"), "k")
    store.append_event(s.TaskUpdated(task_id="k1", title="work", status="done", child_session="k"), "p")
    store.append_event(s.TurnCompleted(turn_id="t"), "p")
    (t,) = trace.build(store, "p")
    assert len(t.all_tools()) == 2 and t.summary()["tool_calls"] == 2
    assert "tool.repeat:bash" in {f.signature for f in t.flags}  # same suite, different wrappers


# ---------------------------------------------------------------- detect


def tt(**kw) -> TurnTrace:
    base = dict(session="s", turn_id="t", shown="p:a", served=["p:a"], complete=True)
    return TurnTrace(**{**base, **kw})


def sigs(t, **limits):
    return {f.signature for f in trace.detect(t, Thresholds(**limits) if limits else TH)}


def test_rule_turn_slow_and_threshold_from_config():
    assert "turn.slow" not in sigs(tt(wall_ms=59_000))
    assert "turn.slow" in sigs(tt(wall_ms=61_000))
    assert "turn.slow" not in sigs(tt(wall_ms=61_000), turn_wall_s=120)
    assert Thresholds.of({"turn_wall_s": 5, "bogus": 1, "model_wait_s": -1}).turn_wall_s == 5.0
    assert Thresholds.of({"model_wait_s": -1}).model_wait_s == 30.0


def test_rule_model_wait_prefers_first_token_time():
    slow = Call(route="p:a", ttft_ms=31_000, total_ms=40_000)
    quick = Call(route="p:a", ttft_ms=2_000, total_ms=40_000)  # a long answer is not a long wait
    assert "model.wait:p:a" in sigs(tt(calls=[slow]))
    assert "model.wait:p:a" not in sigs(tt(calls=[quick]))


def test_rule_fallback_timeout_and_mismatch():
    att = [{"route": "p:a", "outcome": "timeout", "error": "ReadTimeout", "ms": 60000},
           {"route": "p:b", "outcome": "ok", "error": "", "ms": 100}]
    got = sigs(tt(calls=[Call(route="p:b", attempts=att)], served=["p:b"], fallbacks=1))
    assert "model.timeout:p:a" in got and "model.mismatch" not in got
    att[0]["outcome"] = "fallback"
    assert "model.fallback:p:a" in sigs(tt(calls=[Call(route="p:b", attempts=att)], served=["p:b"], fallbacks=1))
    assert "model.mismatch" in sigs(tt(served=["p:z"]))
    assert "model.mismatch" not in sigs(tt(served=["p:a"]))


def test_rule_tool_error_repeat_and_harness_error():
    tools = [ToolRow("bash", "1", "k", 5, True, "timeout must be between 1 and 600 seconds"),
             ToolRow("bash", "2", "k", 5), ToolRow("bash", "3", "k", 5), ToolRow("grep", "4", "z", 5)]
    got = sigs(tt(tools=tools, errors=[{"where": "hook x", "type": "NameError", "message": "m"}]))
    assert "tool.error:bash:timeout must be between # and # seconds" in got
    assert "tool.repeat:bash" in got and "tool.repeat:grep" not in got
    assert "harness.error:hook x:NameError" in got
    assert not sigs(tt(tools=[ToolRow("bash", "1", "a"), ToolRow("bash", "2", "b")]))


def test_rule_subagent_slow():
    task = TaskRow("k", "title", "done", "c", 0, 100_000)
    assert "subagent.slow" in sigs(tt(tasks=[task]))
    assert "subagent.slow" not in sigs(tt(tasks=[TaskRow("k", "t", "done", "c", 0, 80_000)]))
    assert "subagent.slow" not in sigs(tt(tasks=[task]), subagent_s=200)


# ---------------------------------------------------------------- findings


def fnd(version="aaa111", **kw) -> Findings:
    return Findings(SessionStore(), Thresholds(**kw), version=version)


def test_findings_dedup_and_severity_upgrade():
    f = fnd()
    a = f.file("sig", "t", severity="papercut", session="s1")
    b = f.file("sig", "t2", severity="bug", session="s2", detail="later")
    assert a["id"] == b["id"] and b["count"] == 2 and b["severity"] == "bug"
    assert b["sessions"] == ["s1", "s2"] and b["detail"] == "later" and len(f.list()) == 1
    with pytest.raises(ValueError):
        f.file("x", "t", severity="nope")


def test_fixed_finding_reopens_on_a_harness_at_or_after_the_fix():
    f = fnd(version="abc1234")
    row = f.file("sig", "t")
    assert f.fix(row["id"][:6], "abc1234")["status"] == "fixed"
    again = f.file("sig", "t")
    assert again["status"] == "reopened" and again["count"] == 2 and f.open_count() == 1


def test_recurrence_on_an_older_harness_does_not_reopen(monkeypatch):
    f = fnd(version="old0001")
    row = f.file("sig", "t")
    f.fix(row["id"], "new0002")
    monkeypatch.setattr(trace, "at_or_after", lambda running, fixed: False)
    import exo.core.findings as mod

    monkeypatch.setattr(mod, "at_or_after", lambda running, fixed: False)
    again = f.file("sig", "t")
    assert again["status"] == "fixed" and again["count"] == 2
    assert f.sweep() == []  # quiet proves nothing when the fix is not running


def test_quiet_for_n_turns_verifies():
    f = fnd(version="abc1234", verify_turns=3)
    row = f.fix(f.file("sig", "t")["id"], "abc1234")
    assert f.sweep() == []
    for i in range(3):
        f.store.append_event(s.TurnCompleted(turn_id=f"t{i}"), "s")
    assert f.sweep() == [row["id"]] and f.get(row["id"])["status"] == "verified"
    assert f.file("sig", "t")["status"] == "reopened"  # verified findings are re-measured too


def test_quiet_for_days_verifies():
    f = fnd(version="abc1234")
    row = f.fix(f.file("sig", "t")["id"], "abc1234")
    f.store._q("UPDATE findings SET fixed_at='2026-01-01T00:00:00.000Z' WHERE id=?", (row["id"],))
    assert f.sweep() == [row["id"]]


def test_at_or_after_rules():
    assert trace.at_or_after("abc1234", "abc1234") and trace.at_or_after("abc1234", None)
    assert trace.at_or_after("exo-0.1.0", "abc1234")  # cannot tell: assume live


def test_export_groups_by_severity_newest_first():
    f = fnd()
    f.file("a", "minor", severity="papercut")
    f.file("b", "major", severity="bug", session="s9")
    md = f.export_md()
    assert md.index("## bug") < md.index("## papercut") and "s9" in md and "x1" in md


def test_loop_finding_cycle_through_the_engine(tmp_path):
    eng = engine(tmp_path, [{"match": "go", "steps": [{"tool": "read_file", "args": {"path": "missing"}}, "ok"]}],
                 trace_cfg={"verify_turns": 1})
    eng.findings.version = "abc1234"

    async def go():
        info = await eng.loop.create_session(title="t")
        await eng.loop.send(info.id, "go")
        return info.id

    sid = run(go())
    (row,) = [r for r in eng.findings.list() if r["signature"].startswith("tool.error:read_file")]
    assert row["source"] == "anomaly" and row["session"] == sid and row["status"] == "open"
    eng.findings.fix(row["id"], "abc1234")
    run(eng.loop.send(sid, "a quiet turn"))  # one clean turn after the fix is enough here
    assert eng.findings.get(row["id"])["status"] == "verified"
    sid2 = run(eng.loop.create_session(title="again")).id
    run(eng.loop.send(sid2, "go"))  # the same failure again: the fix did not hold
    again = eng.findings.get(row["id"])
    assert again["status"] == "reopened" and again["count"] == 2


def test_observation_can_be_switched_off(tmp_path):
    eng = engine(tmp_path, [{"match": "go", "steps": [{"tool": "read_file", "args": {"path": "missing"}}, "ok"]}],
                 trace_cfg={"enabled": False})

    async def go():
        info = await eng.loop.create_session(title="t")
        await eng.loop.send(info.id, "go")

    run(go())
    assert eng.findings.list() == []


# ---------------------------------------------------------------- tools, feedback, commands


def call(eng, name, args, sid):
    from exo.core.tools.registry import ToolContext

    return run(eng.registry.call(ToolCall("c", name, args), ToolContext(session=sid, meta={"turn_id": "t9"})))


def test_agent_tools(tmp_path):
    eng = engine(tmp_path)
    sid = run(eng.loop.create_session(title="t")).id
    r = call(eng, "exo_harness_report", {"title": "Shell is slow", "detail": "12 s per ls", "severity": "bug"}, sid)
    assert not r.is_error and "Filed finding" in r.text()
    call(eng, "exo_harness_report", {"title": "shell is SLOW!", "detail": "again"}, sid)
    (row,) = eng.findings.list()
    assert row["source"] == "agent" and row["count"] == 2 and row["severity"] == "bug" and row["turn"] == "t9"
    assert call(eng, "exo_harness_report", {"title": "x", "detail": "d", "severity": "huge"}, sid).is_error
    assert call(eng, "exo_harness_report", {"title": "x"}, sid).is_error
    out = call(eng, "exo_trace", {}, sid).text()
    assert "running on: fake:x" in out and "harness " in out and "open findings 1" in out


def test_trace_tools_are_not_for_subagents_and_seats_keep_them(tmp_path):
    from exo.core.agents import BLOCKED_TOOLS

    assert {"exo_trace", "exo_harness_report"} <= BLOCKED_TOOLS
    eng = engine(tmp_path)
    assert {"exo_trace", "exo_harness_report"} <= {t.name for t in eng._specs()}


def test_principal_reject_files_a_finding_and_accept_does_not(tmp_path):
    eng = engine(tmp_path)
    sid = run(eng.loop.create_session(title="t")).id
    run(eng._feedback_send(s.FeedbackSend(session=sid, verdict="accept")))
    assert eng.findings.list() == []
    run(eng._feedback_send(s.FeedbackSend(session=sid, verdict="reject", text="wrong file")))
    run(eng._feedback_send(s.FeedbackSend(session=sid, verdict="should_have_been", text="use X")))
    rows = eng.findings.list()
    assert len(rows) == 2 and {r["source"] for r in rows} == {"principal"}


def test_trace_and_findings_commands_and_client_error_report(tmp_path):
    eng = engine(tmp_path)

    async def go():
        sid = (await eng.loop.create_session(title="t")).id
        await eng.loop.send(sid, "hi")
        out = (await eng._command_run(s.CommandRun(name="trace", session=sid)))["output"]
        await eng._command_run(s.CommandRun(name="harness_error", session=sid, args=json.dumps(
            {"where": "tui event turn.started", "error_type": "NameError", "message": "x is not defined"})))
        n = (await eng._command_run(s.CommandRun(name="findings", args="count")))["output"]
        names = [c["name"] for c in (await eng._commands_list(s.CommandsList()))["commands"]]
        return out, n, names

    out, n, names = run(go())
    assert "running on:" in out and "1 turns" not in out and "wall" in out
    assert n == {"open": 1} and {"trace", "findings"} <= set(names)


# ---------------------------------------------------------------- CLI


def test_cli_trace_and_findings(tmp_path, monkeypatch, capsys):
    from exo import cli

    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    store = SessionStore(tmp_path / "sessions.sqlite")
    old_session(store)
    assert cli.main(["trace", "s1"]) == 0
    out = capsys.readouterr().out
    assert "turn.slow" in out and "model.mismatch" in out
    assert cli.main(["trace", "--last", "1", "--json"]) == 0  # no id: the newest session
    data = json.loads(capsys.readouterr().out)
    assert data["session"] == "s1" and data["turns"][0]["derived"] and data["turns"][0]["tool_errors"] == 1
    f = Findings(store, version="abc1234")
    row = f.file("sig.one", "Something broke", severity="bug", session="s1")
    assert cli.main(["findings", "--status", "open", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == row["id"]
    assert cli.main(["findings", "fix", row["id"], "--in", "abc1234"]) == 0
    assert "fixed in abc1234" in capsys.readouterr().out
    assert cli.main(["findings", "export", "--md"]) == 0
    assert "## bug" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["findings", "fix", row["id"]])
    with pytest.raises(SystemExit):
        cli.main(["trace", "nope"])


# ---------------------------------------------------------------- protocol


def test_protocol_has_the_new_events_and_they_roundtrip():
    assert "model.call" in s.EVENTS and "harness.error" in s.EVENTS and len(s.EVENTS) < 30
    e = s.Event.of(s.ModelCall(iteration=1, route="a:b", attempts=[{"route": "a:b", "outcome": "ok"}], ttft_ms=5,
                               usage=Usage(input_tokens=1)), seq=1, session="x")
    assert s.Event.from_json(e.to_json()) == e
    e = s.Event.of(s.TurnCompleted(turn_id="t", timing={"wall_ms": 5}), seq=2)
    assert s.Event.from_json(e.to_json()).body.timing == {"wall_ms": 5}
