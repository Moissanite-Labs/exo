"""muse-cli tool route (muse serve, MSP over stdio) against a fake ``muse``: no network, no real CLI or keychain."""
import asyncio
import json
import os
import stat
import sys
import time

import pytest

from exo import providers_setup as ps
from exo.core.models import muse_serve
from exo.core.models.router import Router
from exo.core.types import Message, ToolCall, ToolResult, ToolSpec

CANARY = "canary-secret-value-123456"
FP = muse_serve.SCHEMA_FINGERPRINT

# A fake muse. `exec` answers "ok" (the provider test call); `serve` speaks enough MSP. fake.json beside it gives
# the mode and where to record what it saw (one JSON line per client message, written as they arrive).
FAKE = f"""#!{sys.executable}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(sys.argv[0]))
cfg = json.load(open(os.path.join(here, "fake.json")))
mode, a = cfg["mode"], sys.argv[1:]
if a[0] == "exec":
    print("ok")
    sys.exit(0)
rec = open(cfg["out"], "a")
def note(**kw):
    rec.write(json.dumps(kw) + "\\n"); rec.flush()
def out(**o):
    print(json.dumps({{"jsonrpc": "2.0", **o}}), flush=True)
note(argv=a, env=dict(os.environ), cwd=os.getcwd(), pid=os.getpid())
open(cfg["pid"], "w").write(str(os.getpid()))
SID, TID = "sess-1", "turn-1"
def completed(terminal="completed", **kw):
    out(method="turn/completed", params={{"sessionId": SID, "turnId": TID, "terminal": terminal, **kw}})
def text(*parts):
    out(method="item/started", params={{"item": {{"itemId": "i1", "kind": "agentMessage", "status": "inProgress"}}}})
    for p in parts:
        out(method="item/delta", params={{"itemId": "i1", "field": "text", "delta": p}})
usage = {{"inputTokens": 23000, "outputTokens": 7, "cachedTokens": 0, "reasoningTokens": 0}}
for line in sys.stdin:
    m = json.loads(line)
    me = m.get("method")
    note(method=me, params=m.get("params"), id=m.get("id"), reply=("result" in m or "error" in m))
    if me == "initialize":
        out(id=m["id"], result={{"serverInfo": {{"name": "muse", "version": "9"}}, "userAgent": "muse-build/9 (serve)",
            "schema": {{"version": 1, "fingerprint": cfg.get("fp", {FP!r})}}, "grantedCapabilities": ["sessionMcp"]}})
    elif me == "session/start":
        servers = m["params"]["config"]["mcpServers"]
        note(manifest=json.load(open(servers["exo"]["args"][-1])), servers=servers)
        out(id=m["id"], result={{"session": {{"sessionId": SID}}}})
    elif me == "turn/start":
        out(id=m["id"], result={{"turnId": TID, "status": "accepted"}})
        out(method="turn/started", params={{"turnId": TID}})
        if mode == "text":
            text("hi ", "there")
            out(method="session/tokenUsage", params={{"usage": usage}})
            completed()
        elif mode == "tool":
            out(method="session/tokenUsage", params={{"usage": usage}})
            for i in range(cfg.get("calls", 1)):
                out(id=100 + i, method="approval/request", params={{
                    "approvalId": f"ap{{i}}", "toolName": "mcp__exo__get_weather", "toolCallId": f"call_{{i}}",
                    "rawArgs": json.dumps({{"city": f"C{{i}}"}}), "turnId": TID}})
        elif mode == "native":
            out(id=200, method="approval/request", params={{
                "approvalId": "apN", "toolName": "mcp__muse__web_fetch", "toolCallId": "callN", "rawArgs": "{{}}",
                "availableChoices": [{{"choiceId": "allow", "decision": "approved"}},
                                    {{"choiceId": "deny", "decision": "denied"}}],
                "currentRequirementId": {{"approvalId": "apN", "sourceIndex": 0}}, "turnId": TID}})
        elif mode == "failed":
            completed("failed", error={{"kind": "authRequired", "message": "log in", "retryable": False}})
        elif mode == "sleep":
            time.sleep(60)
    elif me == "approval/decide":
        text("done without the native tool")
        completed()
    elif me == "turn/interrupt":
        out(id=m["id"], result={{}})
        completed("cancelled", reason="cancelled after tool result reconciliation")
"""
TOOL = ToolSpec(name="get_weather", description="weather",
                parameters={"type": "object", "properties": {"city": {"type": "string"}}})


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(muse_serve, "GRACE", 0.25)


def router(tmp_path, mode="text", serve=True, **extra):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    exe = d / "muse"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    (d / "fake.json").write_text(json.dumps({"mode": mode, "out": str(tmp_path / "out.jsonl"),
                                             "pid": str(tmp_path / "pid"), **extra}))
    home = tmp_path / "home"
    (home / ".config" / "muse").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "muse" / "auth.json").write_text(json.dumps(
        {"providers": {"meta": {"mechanism": "oauth", "subs_active": True, "user_email": CANARY}}}))
    r = Router.from_config({"muse_serve": serve, "roles": {"main": ["muse-cli:muse-spark-1.3"]}},
                           env={"PATH": str(d), "HOME": str(home), "META_API_KEY": "mk-secret", "LANG": "C"})
    return r


def run(r, messages, tools=(TOOL,), **opts):
    async def go():
        return [e async for e in r.stream("main", messages, list(tools), **opts)]
    return asyncio.run(go())


def rec(tmp_path):
    return [json.loads(x) for x in (tmp_path / "out.jsonl").read_text().splitlines()]


def methods(tmp_path):
    return [x["method"] for x in rec(tmp_path) if x.get("method")]


def test_text_turn_streams_and_reports_usage(tmp_path):
    evs = run(router(tmp_path, "text"), [Message.system("be brief"), Message.user("hello")])
    assert "".join(e.text for e in evs if e.kind == "text") == "hi there"
    done = evs[-1]
    assert done.kind == "done" and done.stop_reason == "end" and done.route == "muse-cli:muse-spark-1.3"
    assert (done.usage.input_tokens, done.usage.output_tokens) == (23000, 7)
    r = rec(tmp_path)
    argv = r[0]["argv"]
    assert argv[:2] == ["serve", "--provider"] and "--no-session-log" in argv and "--disable-shell" in argv
    assert argv[argv.index("--model") + 1] == "muse-spark-1.3"
    env = r[0]["env"]
    assert "META_API_KEY" not in env and env["LANG"] == "C"
    turn = next(x for x in r if x.get("method") == "turn/start")["params"]
    assert turn["reasoningEffort"] == "low"  # the quiet default
    assert turn["input"][0]["type"] == "text" and "be brief" in turn["input"][0]["text"]
    assert len(turn["commandId"]) == 36 and turn["commandId"][14] == "7"  # a UUIDv7


def test_tool_call_is_emitted_and_the_turn_is_interrupted_once(tmp_path):
    evs = run(router(tmp_path, "tool"), [Message.user("weather in Paris?")], reasoning="high")
    calls = [e.tool_call for e in evs if e.kind == "tool_call"]
    assert calls == [ToolCall(id="call_0", name="get_weather", arguments={"city": "C0"})]  # prefix stripped
    assert evs[-1].kind == "done" and evs[-1].stop_reason == "tool_use"
    assert evs[-1].usage.input_tokens == 23000
    assert methods(tmp_path).count("turn/interrupt") == 1
    r = rec(tmp_path)
    assert next(x for x in r if x.get("method") == "turn/start")["params"]["reasoningEffort"] == "high"
    sess = next(x for x in r if x.get("method") == "session/start")["params"]
    exo = sess["config"]["mcpServers"]["exo"]
    assert exo["transport"] == "stdio" and exo["framing"] == "lineDelimitedJson" and exo["args"][0] == "-I"
    manifest = next(x for x in r if "manifest" in x)["manifest"]
    assert [t["name"] for t in manifest] == ["get_weather"] and manifest[0]["inputSchema"]["type"] == "object"
    assert any(x.get("method") == "initialize" and x["params"]["capabilities"]["requestedCapabilities"] == ["sessionMcp"]
               for x in r)
    assert sum(1 for x in r if x.get("reply") and x.get("id", 0) >= 100) == 1  # the approval request was answered


def test_parallel_calls_are_all_emitted_then_one_interrupt(tmp_path):
    evs = run(router(tmp_path, "tool", calls=3), [Message.user("three cities")])
    calls = [e.tool_call for e in evs if e.kind == "tool_call"]
    assert [(c.id, c.arguments["city"]) for c in calls] == [("call_0", "C0"), ("call_1", "C1"), ("call_2", "C2")]
    assert evs[-1].stop_reason == "tool_use"
    assert methods(tmp_path).count("turn/interrupt") == 1


def test_history_with_tool_call_and_result_is_replayed_as_marked_text(tmp_path):
    hist = [Message.system("sys"), Message.user("weather?"),
            Message.assistant("let me check", [ToolCall(id="c9", name="get_weather", arguments={"city": "Paris"})]),
            Message(role="tool", tool_results=[ToolResult.of_text("c9", "12C and sunny")])]
    evs = run(router(tmp_path, "text"), hist)
    assert evs[-1].kind == "done"
    text = next(x for x in rec(tmp_path) if x.get("method") == "turn/start")["params"]["input"][0]["text"]
    assert text.startswith("sys\n\n")
    assert "[user]\nweather?" in text and "[assistant]\nlet me check" in text
    assert 'assistant called tool get_weather (call c9) with arguments {"city": "Paris"}' in text
    assert "result of tool get_weather (call c9)" in text and "12C and sunny" in text and "[end of tool result]" in text
    assert text.rstrip().endswith("or call another tool if still needed.")
    assert methods(tmp_path).count("session/start") == 1  # one fresh session for the request, no reuse


def test_native_muse_tool_is_denied_not_run(tmp_path):
    evs = run(router(tmp_path, "native"), [Message.user("fetch a page")])
    assert "".join(e.text for e in evs if e.kind == "text") == "done without the native tool"
    assert not [e for e in evs if e.kind == "tool_call"]
    dec = next(x for x in rec(tmp_path) if x.get("method") == "approval/decide")["params"]
    assert dec["choiceId"] == "deny" and dec["approvalId"] == "apN"


def test_schema_mismatch_fails_with_a_clear_class_and_never_starts_a_session(tmp_path):
    evs = run(router(tmp_path, "text", fp="sha256:0000"), [Message.user("hi")])
    end = evs[-1]
    assert end.kind == "error" and "version_mismatch" in end.error and end.retryable
    assert "session/start" not in methods(tmp_path)


def test_failed_turn_maps_auth_required(tmp_path):
    end = run(router(tmp_path, "failed"), [Message.user("hi")])[-1]
    assert end.kind == "error" and end.status == 401 and "muse login" in end.error and "authRequired" in end.error


def test_flag_off_refuses_tools_and_falls_back(tmp_path):
    from exo.core.models.fake import FakeProvider
    r = router(tmp_path, "text", serve=False)
    r.roles["main"] = ["muse-cli:muse-spark-1.3", "b:m"]
    r.instances["b"] = FakeProvider(["fallback ok"], name="b")
    evs = run(r, [Message.user("hi")])
    assert evs[-1].kind == "done" and evs[-1].message.text() == "fallback ok"
    assert not (tmp_path / "out.jsonl").exists()  # muse was never started
    only = router(tmp_path, "text", serve=False)
    end = run(only, [Message.user("hi")])[-1]
    assert end.kind == "error" and "muse_serve = false" in end.error
    # without tools the exec path is still used
    last = run(router(tmp_path, "text", serve=False), [Message.user("hi")], tools=())[-1]
    assert last.kind == "done", last.error


def _dead(pid):
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_process_is_gone_after_the_turn(tmp_path):
    run(router(tmp_path, "tool"), [Message.user("x")])
    assert _dead(int((tmp_path / "pid").read_text()))


def test_idle_timeout_and_cancel_kill_the_process(tmp_path):
    r = router(tmp_path, "sleep")
    r.providers["muse-cli"] = {**r.providers["muse-cli"], "read_timeout": 0.7}
    end = run(r, [Message.user("x")])[-1]
    assert end.kind == "error" and "ReadTimeout" in end.error
    assert _dead(int((tmp_path / "pid").read_text()))

    r2 = router(tmp_path, "sleep")
    (tmp_path / "pid").unlink()

    async def go():
        async def drain():
            return [e async for e in r2.stream("main", [Message.user("x")], [TOOL])]
        t = asyncio.create_task(drain())
        for _ in range(100):
            if (tmp_path / "pid").exists() and (tmp_path / "pid").read_text():
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    asyncio.run(go())
    assert _dead(int((tmp_path / "pid").read_text()))


def test_handshake_probe_and_provider_test(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))
    r = router(tmp_path, "text")
    exe = str(tmp_path / "bin" / "muse")
    assert asyncio.run(muse_serve.probe_handshake(exe, r.env)) == ""
    assert ps.test_provider("muse-cli", router=r)["ok"]
    bad = router(tmp_path, "text", fp="sha256:0000")
    res = ps.test_provider("muse-cli", router=bad)
    assert res["error_class"] == "version_mismatch" and not res["ok"]
    off = router(tmp_path, "text", fp="sha256:0000", serve=False)
    assert ps.test_provider("muse-cli", router=off)["ok"]  # the handshake is skipped when the route is off


def test_build_serve_prompt_rejects_empty_history():
    with pytest.raises(ValueError):
        muse_serve.build_serve_prompt([Message.system("only system")])


def test_handshake_pins():
    ok = {"schema": {"version": 1, "fingerprint": FP}, "userAgent": "muse-build/1.4.7 (x)",
          "grantedCapabilities": ["sessionMcp"]}
    assert muse_serve.check_handshake(ok) == ""
    assert muse_serve.check_handshake({**ok, "schema": {"version": 2, "fingerprint": FP}}) == "schema changed"
    assert muse_serve.check_handshake({**ok, "userAgent": "other/1"}) == "unexpected server"
    assert muse_serve.check_handshake({**ok, "grantedCapabilities": []}) == "sessionMcp not granted"
