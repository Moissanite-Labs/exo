"""Harness self-observation (D-025): recording, old-session derivation, anomaly rules, findings lifecycle, tools, CLI."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from exo.core import trace
from exo.core.engine import CoreEngine
from exo.core.findings import Findings
from exo.core.models.fake import FakeProvider
from exo.core.models.router import Router
from exo.core.sessions import SessionStore
from exo.core.trace import Call, TaskRow, Thresholds, ToolRow, TurnTrace
from exo.core.types import Message, ProviderEvent, ToolCall, Usage
from exo.protocol import schema as s

TH = Thresholds()


def run(coro):
    return asyncio.run(coro)


def engine(tmp_path, rules=None, **kw) -> CoreEngine:
    models = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"rules": rules or []}}
    eng = CoreEngine(tmp_path / "home", models, cwd=str(tmp_path), env={}, **kw)
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    return eng


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


def test_a_setup_state_is_not_a_harness_error(tmp_path, caplog):
    """A NotConfigured that reaches trace.report (from a hook, or a direct call) files nothing: no harness.error event,
    no finding and no notice. The hook runner still logs its own warning with the traceback, a real fault beside it is
    still an event and a finding, and a tool handler that raises it is still a tool error. Only the memory provider's
    initialize turns it into a notice (docs/TRACE.md; that path is tested in
    plugins/exo-memory/tests/test_exo_memory_no_principal.py)."""
    from exo.core.plugins import PluginManager, ToolEntry
    from exo.core.slots import NotConfigured
    from exo.core.types import ToolSpec

    def off(*args, **kw):  # a hook and a tool handler of a plugin that is off until set up
        raise NotConfigured("the relay is off: no relay address is set yet")

    pm = PluginManager(config={})
    pm.tools["relay_send"] = ToolEntry(ToolSpec(name="relay_send", description="send", parameters={"type": "object"}),
                                       off, plugin="relay")
    eng = engine(tmp_path, [{"match": "hi", "steps": [{"tool": "relay_send", "args": {}}, "done"]}], plugins=pm)

    def bad_hook(**kw):
        raise KeyError("real fault")

    eng.plugins.hooks.register("on_turn_start", off, plugin="setup")
    eng.plugins.hooks.register("on_turn_start", bad_hook, plugin="broken")
    assert issubclass(NotConfigured, RuntimeError)  # hosts that catch RuntimeError keep working

    async def go():
        info = await eng.loop.create_session(title="t")
        await eng.handle(s.Command.of(s.TurnSend(session=info.id, text="hi")), eng.hub)
        await asyncio.sleep(0.2)
        trace.report("direct caller", NotConfigured("off until set up"), info.id)
        return info.id

    caplog.set_level(logging.INFO, logger="exo")
    sid = run(go())
    errs = [e.body for e in types(eng, sid) if isinstance(e.body, s.HarnessError)]
    assert [e.where for e in errs] == ["hook on_turn_start (broken)"]
    assert not [e for e in types(eng, sid) if isinstance(e.body, s.Notice) and "relay" in e.body.text]
    assert [d.is_error for d in (e.body for e in types(eng, sid)) if isinstance(d, s.ToolCompleted)] == [True]
    sigs = [f["signature"] for f in eng.findings.list()]
    harness = [x for x in sigs if x.startswith("harness.error")]
    assert harness and all("(broken)" in x for x in harness), sigs
    assert [x for x in sigs if x.startswith("tool.error:relay_send") and "notconfigured" in x], sigs
    logged = [(r.levelno, r.getMessage(), bool(r.exc_info)) for r in caplog.records]
    assert (logging.WARNING, "hook on_turn_start from setup failed", True) in logged, logged  # the hook runner's own
    assert (logging.INFO, "direct caller is off until set up: off until set up", False) in logged, logged
    assert not [m for lv, m, _ in logged if lv >= logging.WARNING and m.startswith("harness error in hook on_turn_start "
                                                                                   "(setup)")], logged


def test_a_setup_state_in_a_tool_approval_hook_still_blocks(tmp_path):
    """A pre_tool_call hook that raises NotConfigured fails like any approval hook (fail closed): the call is blocked,
    with a tool.error finding for the block and no harness.error event or finding (docs/TRACE.md)."""
    from exo.core.slots import NotConfigured

    (tmp_path / "a.txt").write_text("x")
    eng = engine(tmp_path, [{"match": "go", "steps": [{"tool": "read_file", "args": {"path": "a.txt"}}, "done"]}])

    def off(**kw):
        raise NotConfigured("the audit hook is off: no audit sink is set yet")

    eng.plugins.hooks.register("pre_tool_call", off, plugin="audit")

    async def go():
        info = await eng.loop.create_session(title="t")
        await eng.loop.send(info.id, "go")
        return info.id

    sid = run(go())
    done = [e.body for e in types(eng, sid) if isinstance(e.body, s.ToolCompleted)]
    assert [d.is_error for d in done] == [True] and "Blocked: Tool approval hook failed" in done[0].result.text()
    assert not [e for e in types(eng, sid) if isinstance(e.body, s.HarnessError)]
    sigs = [f["signature"] for f in eng.findings.list()]
    assert [x for x in sigs if x.startswith("tool.error:read_file:blocked")], sigs
    assert not [x for x in sigs if x.startswith("harness.error")], sigs


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
    put("2026-10-12T04:02:10.000Z", s.TurnCompleted(turn_id="t1"))  # 130 s wall, 30 s of it in a tool


def test_old_session_is_derived_from_timestamps():
    store = SessionStore()
    old_session(store)
    (t,) = trace.build(store, "s1")
    assert t.derived and t.wall_ms == 130_000 and t.harness_ms == 58_000 and len(t.calls) == 2
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
    assert "turn.slow" not in sigs(tt(wall_ms=29_000))
    assert "turn.slow" in sigs(tt(wall_ms=31_000))
    assert "turn.slow" not in sigs(tt(wall_ms=31_000), turn_wall_s=120)
    assert Thresholds().turn_wall_s == 30.0 and Thresholds().model_call_s == 60.0


def test_turn_slow_measures_the_harness_not_the_work():
    """D-032, amended: wall minus tool time, approval waits and model-call time. A long build, a slow human or a
    slow provider does not trip it."""
    timing = {"wall_ms": 400_000, "tool_ms": 330_000, "approval_ms": 30_000, "model_ms": 30_000}
    assert "turn.slow" not in sigs(tt(wall_ms=400_000, timing=timing, approval_ms=30_000))
    slow_model = {"wall_ms": 170_000, "tool_ms": 10_000, "approval_ms": 0, "model_ms": 150_000}  # 6 slow calls
    t = tt(wall_ms=170_000, timing=slow_model)
    assert t.harness_ms == 10_000 and "turn.slow" not in sigs(t)
    overhead = {"wall_ms": 420_000, "tool_ms": 100_000, "approval_ms": 0, "model_ms": 100_000}
    t = tt(wall_ms=420_000, timing=overhead)
    assert t.harness_ms == 220_000 and "turn.slow" in sigs(t)
    assert next(f for f in trace.detect(t, TH) if f.signature == "turn.slow").severity == "bug"  # 5x the limit
    # no timing summary (old session): recorded tool and call durations stand in
    assert tt(wall_ms=100_000, tools=[ToolRow("bash", ms=40_000)], calls=[Call(total_ms=30_000)]).harness_ms == 30_000
    assert "turn.slow" not in sigs(tt(wall_ms=100_000, tools=[ToolRow("bash", ms=70_000)]))


def test_rule_model_slow_flags_the_route_per_call():
    fast = tt(calls=[Call(route="p:a", total_ms=25_000, attempts=[{"route": "p:a", "outcome": "ok", "ms": 25_000}])] * 6)
    assert not any(x.startswith("model.slow") for x in sigs(fast))  # six 25 s calls: a normal turn
    slow = tt(calls=[Call(route="p:b", total_ms=70_000, attempts=[{"route": "p:a", "outcome": "fallback", "ms": 3_000},
                                                                   {"route": "p:b", "outcome": "ok", "ms": 67_000}])])
    assert "model.slow:p:b" in sigs(slow) and "model.slow:p:a" not in sigs(slow)  # the slow try, not the whole call
    assert "model.slow:p:b" not in sigs(slow, model_call_s=90)
    assert "model.slow:p:b" not in sigs(slow, model_call_routes={"p:b": 120.0})  # per-route limit wins
    assert "model.slow:p:a" not in sigs(tt(calls=[Call(route="p:a", total_ms=20_000)], derived=True))
    big = tt(calls=[Call(route="p:a", total_ms=400_000)])  # no attempts recorded: the call total counts
    f = next(f for f in trace.detect(big, TH) if f.signature == "model.slow:p:a")
    assert f.severity == "bug" and "slow, not the harness" in f.detail
    assert Thresholds.of({"model_call_s": 45, "model_call_routes": {"p:a": 200, "p:z": -1}}).model_call_routes == {"p:a": 200.0}


def test_rule_model_slow_charges_no_route_for_a_call_whose_only_tries_timed_out():
    """A call stopped on the fallback route keeps only the timeout before it: model.timeout names that route, and
    the call's total (the timeout plus the fallback's stream) is not filed as model.slow against it."""
    stopped = tt(calls=[Call(route="", total_ms=130_000, ttft_ms=70_500,
                             attempts=[{"route": "p:a", "outcome": "timeout", "ms": 60_000}])])
    got = sigs(stopped)
    assert "model.timeout:p:a" in got and not [x for x in got if x.startswith("model.slow")]


def test_turn_total_wall_is_a_separate_high_cap():
    long = tt(wall_ms=2_000_000, timing={"wall_ms": 2_000_000, "tool_ms": 1_990_000, "approval_ms": 0})
    got = sigs(long)
    assert "turn.wall" in got and "turn.slow" not in got
    assert "turn.wall" not in sigs(long, turn_total_wall_s=3600)
    assert "turn.wall" not in sigs(tt(wall_ms=1_700_000, timing={"wall_ms": 1_700_000, "tool_ms": 1_700_000}))
    assert Thresholds().turn_total_wall_s == 1800.0


def test_seat_trace_table_overrides_config(tmp_path):
    from exo.core.seats import parse

    seat = parse({"name": "canary", "trace": {"turn_wall_s": 5, "turn_total_wall_s": 99, "model_call_s": 150}})
    assert seat.trace == {"turn_wall_s": 5, "turn_total_wall_s": 99, "model_call_s": 150}
    eng = engine(tmp_path, seat=seat, trace_cfg={"turn_wall_s": 45, "model_wait_s": 12, "model_call_s": 20})
    assert eng.thresholds.turn_wall_s == 5 and eng.thresholds.turn_total_wall_s == 99
    assert eng.thresholds.model_call_s == 150  # the seat's limit for a model call wins too
    assert eng.thresholds.model_wait_s == 12  # keys the seat does not set fall back to config
    assert engine(tmp_path / "x", trace_cfg={"turn_wall_s": 45}).thresholds.turn_wall_s == 45


def test_cli_trace_reads_the_sessions_seat_thresholds(tmp_path, monkeypatch):
    from exo import trace_cmd

    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    (tmp_path / "seats").mkdir()
    (tmp_path / "seats" / "canary.toml").write_text("[trace]\nturn_wall_s = 7\n")
    assert trace_cmd._thresholds("canary").turn_wall_s == 7.0
    assert trace_cmd._thresholds("nobody").turn_wall_s == 30.0
    assert trace_cmd._thresholds(None).turn_wall_s == 30.0


# ---------------------------------------------------------------- cost (D-032)


def _usage_call(route, cost, tin=100, tout=10, **kw):
    return Call(route=route, tokens_in=tin, tokens_out=tout, cost_usd=cost, **kw)


def test_cost_sums_priced_calls_and_never_reports_zero_for_unpriced():
    priced = tt(calls=[_usage_call("p:a", 0.01), _usage_call("p:a", 0.0025)])
    assert priced.cost() == (0.0125, 0) and trace.money(*priced.cost()) == "$0.0125"
    none = tt(calls=[_usage_call("p:a", None)])
    assert none.cost() == (None, 1) and trace.money(*none.cost()) == "unknown"
    mixed = tt(calls=[_usage_call("p:a", 0.02), _usage_call("p:b", None)])
    assert mixed.cost() == (0.02, 1) and trace.money(*mixed.cost()) == "$0.0200+"
    assert tt(calls=[]).cost() == (None, 0)  # no calls: unknown, not $0
    failed = tt(calls=[Call(route="", error="boom")])  # used no tokens: not an unpriced call
    assert failed.cost() == (None, 0)
    assert trace.money(1.5) == "$1.50"


def test_cost_includes_subagent_turns_and_shows_in_table_and_json():
    kid = tt(calls=[_usage_call("p:k", 0.5)])
    t = tt(calls=[_usage_call("p:a", 0.25, cache_read=900)], children=[kid], t0=1.0, wall_ms=1000)
    assert t.summary()["cost_usd"] == 0.75 and t.summary()["cost"] == "$0.7500"
    out = trace.table([t])
    assert "cost" in out.splitlines()[0] and "$0.7500" in out
    assert "calls in turn 1" in out and "cache r/w 900/0" in out
    d = trace.as_dict("s", [t])["turns"][0]
    assert d["cost_usd"] == 0.75 and {c["cost_usd"] for c in d["call_list"]} == {0.25, 0.5}
    u = tt(calls=[_usage_call("p:a", None)], t0=1.0)
    assert "unknown" in trace.table([u]) and trace.as_dict("s", [u])["turns"][0]["cost_usd"] is None


def test_cost_flows_from_catalog_price_through_router_and_trace(tmp_path):
    from exo.core.models.router import Route

    r = Router.from_config({"models": {"p:m": {"input": 2.0, "output": 10.0, "cache_read": 0.2}}}, env={})
    u = r.price(Route("p", "m"), Usage(input_tokens=1000, output_tokens=100, cache_read_tokens=5000))
    assert u.cost_usd == pytest.approx((1000 * 2.0 + 100 * 10.0 + 5000 * 0.2) / 1e6)  # cache read priced as such
    # input_tokens is the uncached input on every transport (D-080): the OpenAI transport splits the cached
    # tokens out of prompt_tokens, so the router never subtracts them (tests/core/models/test_router.py)
    assert r.price(Route("p", "nope"), Usage(input_tokens=5)).cost_usd is None  # no price: None, never 0


def test_recorded_session_cost_in_exo_trace_tool(tmp_path):
    eng = engine(tmp_path, [{"match": "go", "steps": ["ok"]}])
    eng.router.models["fake:x"] = {"input": 1_000_000.0, "output": 0.0}  # $1 per token: easy to read

    async def go():
        info = await eng.loop.create_session(title="t")
        await eng.loop.send(info.id, "go")
        return info.id

    sid = run(go())
    out = eng.trace_text(sid, 5)
    assert "$210.00" in out and "cache r/w 0/0  unknown" not in out  # the standing line names bus messages (D-040)
    assert "calls in turn 1" in out
    plain = engine(tmp_path / "np", [{"match": "go", "steps": ["ok"]}])
    async def go2():
        info = await plain.loop.create_session(title="t")
        await plain.loop.send(info.id, "go")
        return info.id
    assert "cache r/w 0/0  unknown" in plain.trace_text(run(go2()), 5)
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
    assert f.observe(tt(tools=[ToolRow("bash")]), set()) == []  # quiet proves nothing when the fix is not running


def bash_err(f, version="abc1234"):
    """A fixed ``tool.error:bash`` finding (fixed in the running harness) and a turn that ran bash cleanly."""
    return f.fix(f.file("tool.error:bash:exit-1", "t")["id"], version), tt(tools=[ToolRow("bash")])


def days(monkeypatch, *dates):
    import exo.core.findings as mod
    it = iter(dates)
    monkeypatch.setattr(mod, "now_ts", lambda: next(it) + "T12:00:00.000Z")


def test_quiet_for_n_eligible_turns_verifies():
    f = fnd(version="abc1234", verify_turns=3)
    row, ran_bash = bash_err(f)
    assert f.observe(ran_bash, set()) == [] and f.observe(ran_bash, set()) == []
    assert f.observe(ran_bash, set()) == [row["id"]] and f.get(row["id"])["status"] == "verified"
    assert f.file("tool.error:bash:exit-1", "t")["status"] == "reopened"  # verified findings are re-measured too


def test_unrelated_turns_do_not_count():
    """D-025 as ruled: a turn that could not have tripped the signature is no evidence the fix held."""
    f = fnd(version="abc1234", verify_turns=2)
    row, ran_bash = bash_err(f)
    for t in (tt(), tt(tools=[ToolRow("read_file")]), tt(calls=[Call(route="p:a")])) * 10:
        assert f.observe(t, set()) == []
    assert f.get(row["id"])["status"] == "fixed" and f.get(row["id"])["quiet"] is None
    assert f.observe(ran_bash, set()) == [] and f.observe(ran_bash, set()) == [row["id"]]


def test_a_tripped_or_not_live_turn_does_not_count(monkeypatch):
    f = fnd(version="abc1234", verify_turns=1)
    row, ran_bash = bash_err(f)
    assert f.observe(ran_bash, {"tool.error:bash:exit-1"}) == []
    import exo.core.findings as mod
    monkeypatch.setattr(mod, "at_or_after", lambda running, fixed: False)
    assert f.observe(ran_bash, set()) == [] and f.get(row["id"])["status"] == "fixed"


def test_quiet_on_three_days_of_activity_verifies(monkeypatch):
    f = fnd(version="abc1234")  # 20 turns, 3 days
    row, ran_bash = bash_err(f)
    days(monkeypatch, "2026-10-01", "2026-10-01", "2026-10-02", "2026-10-09", "2026-10-09")
    assert [f.observe(ran_bash, set()) for _ in range(3)] == [[], [], []]
    assert f.observe(ran_bash, set()) == [row["id"]]


def test_idle_days_do_not_count(monkeypatch):
    """A fix marked long ago with one quiet day of activity since is not three days of activity."""
    f = fnd(version="abc1234")
    row, ran_bash = bash_err(f)
    f.store._q("UPDATE findings SET fixed_at='2026-01-01T00:00:00.000Z' WHERE id=?", (row["id"],))
    days(monkeypatch, "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04")
    assert f.observe(tt(), set()) == [] and f.observe(tt(), set()) == []  # unrelated turns mark no day
    assert f.observe(ran_bash, set()) == [] and f.get(row["id"])["status"] == "fixed"


def test_a_report_without_a_rule_verifies_by_days_only(monkeypatch):
    f = fnd(version="abc1234", verify_turns=1)
    row = f.fix(f.file("agent:slow-startup", "t", source="agent")["id"], "abc1234")
    days(monkeypatch, "2026-10-01", "2026-10-01", "2026-10-02", "2026-10-03")
    assert [f.observe(tt(), set()) for _ in range(3)] == [[], [], []]  # turns alone never verify it
    assert f.observe(tt(), set()) == [row["id"]]


def test_an_older_harness_recurrence_restarts_the_clock(monkeypatch):
    f = fnd(version="abc1234", verify_turns=2)
    row, ran_bash = bash_err(f)
    assert f.observe(ran_bash, set()) == []
    import exo.core.findings as mod
    monkeypatch.setattr(mod, "at_or_after", lambda running, fixed: False)
    f.file("tool.error:bash:exit-1", "t")
    assert f.get(row["id"])["quiet"] is None
    monkeypatch.setattr(mod, "at_or_after", lambda running, fixed: True)
    assert f.observe(ran_bash, set()) == [] and f.observe(ran_bash, set()) == [row["id"]]


@pytest.mark.parametrize("sig, yes, no", [
    ("turn.slow", tt(), tt(complete=False)),
    ("turn.wall", tt(), tt(complete=False)),
    ("model.slow:p:a", tt(calls=[Call(route="p:a")]), tt(calls=[Call(route="p:b")])),
    ("model.wait:p:a", tt(calls=[Call(route="")]), tt()),  # no route recorded: the shown model is the route
    ("model.timeout:p:a", tt(calls=[Call(route="p:b", attempts=[{"route": "p:a", "outcome": "timeout"}])]),
     tt(calls=[Call(route="p:b")])),
    ("model.error:p:a", tt(calls=[Call(route="p:a")]), tt(calls=[Call(route="p:b")])),
    ("model.fallback:p:a", tt(children=[tt(calls=[Call(route="p:a")])]), tt(calls=[Call(route="p:b")])),
    ("model.mismatch", tt(), tt(served=[])),
    ("tool.error:read_file:not-found", tt(tools=[ToolRow("read_file")]), tt(tools=[ToolRow("bash")])),
    ("tool.repeat:bash", tt(children=[tt(tools=[ToolRow("bash")])]), tt(tools=[ToolRow("read_file")])),
    ("shell.masked_exit", tt(tools=[ToolRow("bash")]), tt()),
    ("subagent.slow", tt(tasks=[TaskRow("k1")]), tt()),
])
def test_eligibility_rule(sig, yes, no):
    assert trace.eligible(sig, yes) is True and trace.eligible(sig, no) is False


def test_no_rule_can_tell_for_errors_and_reports():
    assert trace.eligible("harness.error:hook:KeyError", tt()) is None
    assert trace.eligible("agent:slow-startup", tt()) is None and trace.eligible("manual:x", tt()) is None


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
    eng = engine(tmp_path, [{"match": "go", "steps": [{"tool": "read_file", "args": {"path": "missing"}}, "ok"]},
                            {"match": "read there", "steps": [{"tool": "read_file", "args": {"path": "there"}}, "ok"]}],
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
    run(eng.loop.send(sid, "a quiet turn"))  # no read_file call: it could not have tripped the finding
    assert eng.findings.get(row["id"])["status"] == "fixed"
    (tmp_path / "there").write_text("x")

    async def read():
        info = await eng.loop.create_session(title="read")
        await eng.loop.send(info.id, "read there")  # read_file ran and did not fail: with verify_turns 1, that verifies

    run(read())
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
    assert "running on: fake:x" in out and "harness " in out and "findings 1: 1 open, 0 fixed (awaiting re-verification)" in out


def test_trace_header_after_a_stop_names_the_route_that_served_the_last_call(tmp_path, monkeypatch):
    """A call cut short by a stop names no route (usage comes at the end of a stream); the header skips it."""
    eng = engine(tmp_path, [{"match": "go", "steps": ["ok"]}])
    fake = eng.router.credentials("fake")[0].provider

    async def stall(messages, tools, **opts):
        yield ProviderEvent(kind="text", text="long answer")
        await asyncio.Event().wait()

    async def go():
        sid = (await eng.loop.create_session(title="t")).id
        await eng.loop.send(sid, "go")
        monkeypatch.setattr(fake, "stream", stall)
        task = asyncio.create_task(eng.loop.send(sid, "go on"))
        while not any(e.type == "message.delta" and e.body.text == "long answer" for e in types(eng, sid)):
            await asyncio.sleep(0.01)
        assert eng.loop.interrupt(sid)
        assert (await asyncio.wait_for(task, 5)).stop_reason == "interrupted"
        return sid

    sid = run(go())
    calls = [e.body for e in types(eng, sid) if e.type == "model.call"]
    assert [(c.route, c.stop_reason) for c in calls] == [("fake:x", "end"), ("", "interrupted")]
    out = eng.trace_text(sid, 5)
    assert "last call served by fake:x" in out.splitlines()[0]
    assert "calls in turn 2" in out


def test_trace_tools_are_not_for_subagents_and_seats_keep_them(tmp_path):
    from exo.core.agents import BLOCKED_TOOLS

    assert {"exo_trace", "exo_harness_report"} <= BLOCKED_TOOLS
    eng = engine(tmp_path)
    assert {"exo_trace", "exo_harness_report"} <= {t.name for t in eng._specs()}


def test_principal_reject_files_a_finding_and_accept_does_not(tmp_path):
    eng = engine(tmp_path)
    sid = run(eng.loop.create_session(title="t")).id
    from exo.server.app import Dispatcher
    d = Dispatcher(eng, caller="principal:tui")
    run(d.run(s.Command.of(s.FeedbackSend(session=sid, verdict="accept"))))
    assert eng.findings.list() == []
    run(d.run(s.Command.of(s.FeedbackSend(session=sid, verdict="reject", text="wrong file"))))
    run(d.run(s.Command.of(s.FeedbackSend(session=sid, verdict="should_have_been", text="use X"))))
    rows = eng.findings.list()
    assert len(rows) == 2 and {r["source"] for r in rows} == {"principal"}


def test_principal_reject_in_a_private_thread_files_no_finding(tmp_path):
    """D-028: findings are global (exo_trace in every thread, /findings, /handoff), so a verdict on a private
    thread's answer files none: its note would carry the private thread into the others. The same holds for its
    delegate child and that child's child, which a client opens with /tasks and /open."""
    eng = engine(tmp_path)
    from exo.server.app import Dispatcher
    d = Dispatcher(eng, caller="principal:tui")
    priv = run(d.run(s.Command.of(s.SessionCreate(kind="private")))).result["session"]["id"]
    normal = run(d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
    kid = eng.store.create_session(title="researcher: zebracorn", parent=priv, agent=eng.agent, kind="subagent").id
    grandkid = eng.store.create_session(title="checker: zebracorn", parent=kid, agent=eng.agent, kind="subagent").id
    assert eng.store.thread(priv)[1] == "private"
    for sid in (priv, kid, grandkid):
        eng.store.append_message(sid, Message.assistant("Zebracorn: $40M, closes Friday."))
        for verdict in ("reject", "should_have_been"):
            out = run(d.run(s.Command.of(s.FeedbackSend(session=sid, verdict=verdict, text="zebracorn is $50M"))))
            assert out.ok and out.result["recorded"] is False
    assert eng.findings.list() == []
    assert "zebracorn" not in call(eng, "exo_trace", {}, normal).text()
    run(d.run(s.Command.of(s.FeedbackSend(session=normal, verdict="reject", text="wrong file"))))
    assert [r["session"] for r in eng.findings.list()] == [normal]  # a normal thread still files one


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
    assert n["open"] == 1 and n["by_state"]["open"] == 1 and {"trace", "findings"} <= set(names)


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


def test_repeat_counts_suite_runs_through_ulimit_tail_and_grep_wrappers():
    cmds = ["pytest -q tests", "ulimit -n 4096 && pytest -q tests | tail -5", "cd /x && pytest -q tests 2>&1 | grep -E 'passed|failed'",
            "ulimit -n 8192; timeout 300 pytest -q tests"]
    keys = [trace._args_key({"command": c}) for c in cmds]
    assert len(set(keys)) == 1
    tools = [ToolRow("bash", str(i), k, 5) for i, k in enumerate(keys)]
    (f,) = [f for f in trace.detect(tt(tools=tools), TH) if f.signature == "tool.repeat:bash"]
    assert "4 times" in f.title


def test_cli_findings_add_files_and_dedups(tmp_path, monkeypatch, capsys):
    from exo import cli

    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    SessionStore(tmp_path / "sessions.sqlite")
    assert cli.main(["findings", "add", "Tool X hangs", "--severity", "bug", "--detail", "run it twice"]) == 0
    assert "open x1 bug" in capsys.readouterr().out
    assert cli.main(["findings", "add", "Tool X hangs", "--severity", "bug"]) == 0  # same title: a recurrence
    assert "x2" in capsys.readouterr().out
    assert cli.main(["findings", "add", "Other", "--signature", "my.sig"]) == 0
    capsys.readouterr()
    cli.main(["findings", "--json"])
    rows = {r["signature"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["manual:tool-x-hangs"]["detail"] == "run it twice" and rows["manual:tool-x-hangs"]["source"] == "principal"
    assert rows["my.sig"]["severity"] == "papercut"
    with pytest.raises(SystemExit):
        cli.main(["findings", "add"])


def test_masked_pipeline_exit_is_flagged_from_the_stored_result():
    store = SessionStore()
    store.create_session(id="m")
    store.append_event(s.TurnStarted(turn_id="t", model="m:a"), "m")
    for cid, cmd, text in [("c1", "renew | head -1", "ok\nnote: pipeline stage 1 exited 1 (only the last stage's code "
                                                      "is reported as the result)\n[exit 0]"),
                           ("c2", "yes | head -1", "y\n[exit 0]")]:
        store.append_event(s.ToolStarted(call=ToolCall(cid, "bash", {"command": cmd})), "m")
        store.append_event(s.ToolCompleted(result=s.ToolResult.of_text(cid, text), duration_ms=5), "m")
    store.append_event(s.TurnCompleted(turn_id="t"), "m")
    (t,) = trace.build(store, "m")
    (f,) = [f for f in t.flags if f.signature == "shell.masked_exit"]
    assert "stage 1 exited 1" in f.detail


def _turn_with_calls(calls):
    store = SessionStore()
    store.create_session(id="r")
    store.append_event(s.TurnStarted(turn_id="t", model="m:a"), "r")
    for i, (name, args) in enumerate(calls):
        store.append_event(s.ToolStarted(call=ToolCall(f"c{i}", name, args)), "r")
        store.append_event(s.ToolCompleted(result=s.ToolResult.of_text(f"c{i}", "ok"), duration_ms=5), "r")
    store.append_event(s.TurnCompleted(turn_id="t"), "r")
    (t,) = trace.build(store, "r")
    return {f.signature for f in t.flags}


def test_rerunning_tests_after_an_edit_is_not_a_repeat():
    """f_3c19fb4f: the same command with a file change between is a retest."""
    run_tests = ("bash", {"command": "pytest -q tests"})
    for edit in (("edit_file", {"path": "a.py", "old_string": "x", "new_string": "y"}),
                 ("write_file", {"path": "a.py", "content": "y"}),
                 ("patch", {"patch": "*** Begin Patch"}),
                 ("bash", {"command": "sed -i 's/x/y/' a.py"}),
                 ("bash", {"command": "echo y > a.py"})):
        assert "tool.repeat:bash" not in _turn_with_calls([run_tests, edit, run_tests]), edit
    # still a repeat: nothing changed between, a read between, or a redirect to /dev/null
    assert "tool.repeat:bash" in _turn_with_calls([run_tests, run_tests])
    assert "tool.repeat:bash" in _turn_with_calls([run_tests, ("read_file", {"path": "a.py"}), run_tests])
    assert "tool.repeat:bash" in _turn_with_calls([run_tests, ("bash", {"command": "ls 2>/dev/null"}), run_tests])
    # three runs with one edit between: the first two are one epoch, so they still count
    assert "tool.repeat:bash" in _turn_with_calls([run_tests, run_tests, ("edit_file", {"path": "a"}), run_tests])


def test_findings_counts_by_state_reconcile_and_fixed_stay_listed():
    f = fnd(version="abc1234")
    a, b, c, d = (f.file(f"sig.{i}", f"Finding {i}", severity="bug")["id"] for i in range(4))
    f.fix(b, "abc1234")
    f.fix(c, "abc1234")
    f.store._q("UPDATE findings SET status='verified' WHERE id=?", (c,))
    f.store._q("UPDATE findings SET status='reopened' WHERE id=?", (d,))
    assert f.counts() == {"open": 1, "fixed": 1, "verified": 1, "reopened": 1, "total": 4}
    assert f.open_count() == 2  # open + reopened still need work
    assert f.summary() == "findings 4: 1 open, 1 fixed (awaiting re-verification), 1 verified, 1 reopened"
    text = f.text("all")
    assert text.splitlines()[0] == f.summary() and "fixed in abc1234" in text
    assert len(f.list("all")) == 4 == len(f.list())  # nothing dropped
    assert [r["id"] for r in f.list("fixed")] == [b]


def test_findings_changes_since_last_view():
    f = fnd(version="abc1234")
    a = f.file("sig.a", "Alpha")["id"]
    assert f.changes("cli", save=True) == []  # first view: no baseline
    b = f.file("sig.b", "Beta")["id"]
    f.fix(a, "abc1234")
    moved = f.changes("cli")
    assert any(m.startswith("open -> fixed") and a in m for m in moved) and any(m.startswith("new") and b in m for m in moved)
    assert f.changes("cli") == moved  # peeking does not move the baseline
    assert "since last view:" in f.text(viewer="cli")
    assert f.changes("cli") == [] and f.changes("other") == []


def test_cli_findings_status_all_and_trace_counts(tmp_path, monkeypatch, capsys):
    from exo import cli

    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    store = SessionStore(tmp_path / "sessions.sqlite")
    old_session(store)
    f = Findings(store, version="abc1234")
    one = f.file("sig.one", "One", severity="bug")
    f.file("sig.two", "Two", severity="bug")
    f.fix(one["id"], "abc1234")
    assert cli.main(["findings", "--status", "all"]) == 0
    out = capsys.readouterr().out
    assert "1 open, 1 fixed (awaiting re-verification)" in out and "fixed in abc1234" in out and out.count("sig.") == 2
    assert cli.main(["trace", "s1"]) == 0
    assert "findings 2:" in capsys.readouterr().out
    assert cli.main(["trace", "s1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["findings"]["fixed"] == 1


def test_trace_text_lists_open_findings_by_default(tmp_path):
    eng = engine(tmp_path)
    eng.findings.version = "abc1234"
    eng.findings.file("sig.open", "Open thing", severity="bug")
    fixed = eng.findings.file("sig.fixed", "Fixed thing", severity="bug")
    eng.findings.fix(fixed["id"], "abc1234")
    out = eng.trace_text(None)
    assert "open findings:" in out and "Open thing" in out
    assert "Fixed thing" not in out  # fixed is counted, not listed
    assert "1 open, 1 fixed" in out


@pytest.mark.parametrize('served', ['p:a', 'p:b'])
def test_recorded_error_then_ok_is_not_guessed_to_be_a_fallback(served):
    store = SessionStore()
    session = store.create_session().id
    attempts = [{'route': 'p:a', 'outcome': 'error', 'error': 'fixture failure'},
                {'route': served, 'outcome': 'ok'}]
    for body in (s.TurnStarted(turn_id='t', model='p:a'),
                 s.ModelCall(requested='p:a', route=served, attempts=attempts), s.TurnCompleted(turn_id='t')):
        store.append_event(body, session)
    turn = trace.last_turn(store, session)
    assert not turn.derived and turn.fallbacks == 0
    flags = trace.detect(turn, TH)
    assert 'model.error:p:a' in {f.signature for f in flags}
    assert not any(f.signature.startswith('model.fallback:') for f in flags)
