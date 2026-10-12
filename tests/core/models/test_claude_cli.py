"""claude-cli provider against a fake ``claude`` script: no network, no real CLI."""
import asyncio
import json
import os
import stat
import sys
import time

from exo.core.models.fake import FakeProvider
from exo.core.models.router import Router
from exo.core.types import Message, ToolCall, ToolResult, ToolSpec
from exo.models_cmd import available_models

# A fake CLI speaking stream-json both ways. FAKE_MODE picks the behaviour, FAKE_OUT records what it saw.
FAKE = f"""#!{sys.executable}
import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "ok")
out = lambda d: print(json.dumps(d), flush=True)
if mode == "fail":
    sys.stderr.write("boom\\n")
    sys.exit(3)
if mode == "sleep":
    open(os.environ["FAKE_PID"], "w").write(str(os.getpid()))
    time.sleep(60)
frames = []
for line in sys.stdin:
    f = json.loads(line)
    frames.append(f)
    if f.get("shouldQuery") is False:
        out({{"type": "result", "subtype": "success", "num_turns": 0, "is_error": False}})
settings = json.load(open(sys.argv[sys.argv.index("--settings") + 1]))
json.dump({{"argv": sys.argv[1:], "frames": frames, "cwd": os.getcwd(), "env": dict(os.environ),
           "system": open(sys.argv[sys.argv.index("--system-prompt-file") + 1]).read(),
           "body": json.loads(settings["env"]["CLAUDE_CODE_EXTRA_BODY"])}}, open(os.environ["FAKE_OUT"], "w"))
if mode == "nologin":
    out({{"type": "assistant", "error": "authentication_failed", "message": {{"content": [{{"type": "text", "text": "Not logged in"}}]}}}})
    out({{"type": "result", "subtype": "success", "is_error": True}})
    sys.exit(1)
usage = None if mode == "nousage" else {{"input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": 5}}
if mode == "tool":
    blocks = [{{"type": "tool_use", "id": "tu_1", "name": "mcp__hermes__get_weather", "input": {{"city": "Paris"}}}}]
else:
    blocks = [{{"type": "text", "text": "hi there"}}]
    out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": "hi "}}}}}})
out({{"type": "stream_event", "event": {{"type": "message_stop"}}}})
out({{"type": "assistant", "message": {{"content": blocks}}}})
res = {{"type": "result", "subtype": "error_max_turns" if mode == "tool" else "success",
       "is_error": False, "usage": usage}}
out(res)
sys.exit(1 if mode == "tool" else 0)
"""
TOOL = ToolSpec(name="get_weather", description="weather", parameters={"type": "object", "properties": {"city": {"type": "string"}}})


def fake_claude(tmp_path):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    exe = d / "claude"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(d)


def router(tmp_path, mode="ok", **env):
    return Router(roles={"main": ["claude-cli:sonnet"]},
                  env={"PATH": fake_claude(tmp_path), "FAKE_MODE": mode, "FAKE_OUT": str(tmp_path / "out.json"),
                       "FAKE_PID": str(tmp_path / "pid"), **env})


def run(r, messages, tools=()):
    async def go():
        return [e async for e in r.stream("main", messages, list(tools))]
    return asyncio.run(go())


def seen(tmp_path):
    return json.loads((tmp_path / "out.json").read_text())


def test_success_argv_env_and_isolation(tmp_path):
    r = router(tmp_path, ANTHROPIC_API_KEY="sk-secret", ANTHROPIC_AUTH_TOKEN="tok", ANTHROPIC_BASE_URL="http://x")
    evs = run(r, [Message.system("be brief"), Message.user("hello")])
    assert "".join(e.text for e in evs if e.kind == "text") == "hi there"  # streamed delta plus the missed rest
    done = evs[-1]
    assert done.kind == "done" and done.route == "claude-cli:sonnet"
    assert (done.usage.input_tokens, done.usage.output_tokens, done.usage.cache_read_tokens) == (3, 2, 5)
    s = seen(tmp_path)
    a = s["argv"]
    assert a[a.index("--model") + 1] == "sonnet" and a[a.index("--tools") + 1] == ""
    assert a[a.index("--setting-sources") + 1] == "" and a[a.index("--max-turns") + 1] == "1"
    assert a[a.index("--permission-mode") + 1] == "dontAsk"
    for flag in ("-p", "--verbose", "--include-partial-messages", "--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence", "--mcp-config", "--system-prompt-file"):
        assert flag in a
    assert "--dangerously-skip-permissions" not in a
    assert [a[a.index("--input-format") + 1], a[a.index("--output-format") + 1]] == ["stream-json"] * 2
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        assert k not in s["env"]  # the OAuth login is used, never a key
    assert s["env"]["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert s["system"] == "be brief" and "claude-cli-cwd-" in s["cwd"]
    assert s["frames"] == [{"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}}]


def test_tool_call_mapped_and_history_replayed(tmp_path):
    r = router(tmp_path, "tool")
    hist = [Message.user("weather?")]
    evs = run(r, hist, [TOOL])
    calls = [e.tool_call for e in evs if e.kind == "tool_call"]
    assert calls == [ToolCall(id="tu_1", name="get_weather", arguments={"city": "Paris"})]
    assert evs[-1].kind == "done" and evs[-1].stop_reason == "tool_use"
    body = seen(tmp_path)["body"]
    assert body["tools"][0]["name"] == "mcp__hermes__get_weather"
    hist += [Message.assistant("", calls), Message(role="tool", tool_results=[ToolResult.of_text("tu_1", "12C")])]
    run(r, hist, [TOOL])
    fr = seen(tmp_path)["frames"]
    assert fr[1]["message"]["content"][0] == {"type": "tool_use", "id": "tu_1", "name": "mcp__hermes__get_weather",
                                             "input": {"city": "Paris"}}
    assert fr[2]["message"]["content"][0]["tool_use_id"] == "tu_1" and "shouldQuery" not in fr[2]
    assert fr[0]["shouldQuery"] is False


def test_nonzero_exit_is_error_and_falls_back(tmp_path):
    r = router(tmp_path, "fail")
    r.roles["main"] = ["claude-cli:sonnet", "b:m"]
    r.instances["b"] = FakeProvider(["fallback ok"], name="b")
    evs = run(r, [Message.user("hello")])
    assert any(e.kind == "notice" and "claude-cli:sonnet" in e.text for e in evs)
    assert evs[-1].kind == "done" and evs[-1].message.text() == "fallback ok"
    end = run(router(tmp_path, "fail"), [Message.user("hello")])[-1]
    assert end.kind == "error" and "exited 3" in end.error and "boom" in end.error


def test_not_logged_in_is_401_error_with_no_text(tmp_path):
    evs = run(router(tmp_path, "nologin"), [Message.user("hi")])
    assert not [e for e in evs if e.kind == "text"]  # the login message is an error, never an answer
    assert evs[-1].kind == "error" and "Not logged in" in evs[-1].error


def test_unknown_usage_is_none(tmp_path):
    done = run(router(tmp_path, "nousage"), [Message.user("hi")])[-1]
    assert done.kind == "done" and done.usage is None


def test_available_only_with_claude_on_path(tmp_path):
    empty = Router(env={"PATH": str(tmp_path / "empty")})
    assert not empty.credentials("claude-cli")
    r = router(tmp_path)
    assert r.credentials("claude-cli")
    assert [m["route"] for m in available_models(r, tmp_path / "home") if m["provider"] == "claude-cli"] == [
        "claude-cli:sonnet", "claude-cli:opus", "claude-cli:haiku"]
    assert not [m for m in available_models(empty, tmp_path / "home") if m["provider"] == "claude-cli"]


def _wait_dead(pid):
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_cancel_kills_process_group(tmp_path):
    r = router(tmp_path, "sleep")
    pidf = tmp_path / "pid"

    async def drain():
        return [e async for e in r.stream("main", [Message.user("hi")], [])]

    async def go():
        t = asyncio.create_task(drain())
        for _ in range(100):
            if pidf.exists() and pidf.read_text():
                break
            await asyncio.sleep(0.05)
        t.cancel()  # Esc
        try:
            await t
        except asyncio.CancelledError:
            pass
    asyncio.run(go())
    assert _wait_dead(int(pidf.read_text()))


def test_idle_timeout_kills_and_reports(tmp_path):
    r = router(tmp_path, "sleep")
    r.providers["claude-cli"] = {**r.providers["claude-cli"], "read_timeout": 0.5}
    end = run(r, [Message.user("hi")])[-1]
    assert end.kind == "error" and "ReadTimeout" in end.error
    assert _wait_dead(int((tmp_path / "pid").read_text()))


def test_foreign_tool_ids_are_sanitized_and_paired(tmp_path):
    import re
    r = router(tmp_path, "tool")
    bad = "functions.bash:0"
    other = "functions.bash_0"  # sanitizes to the same prefix but must stay distinct
    hist = [Message.user("go"),
            Message.assistant("", [ToolCall(id=bad, name="get_weather", arguments={}),
                                   ToolCall(id=other, name="get_weather", arguments={}),
                                   ToolCall(id="ok_1-x", name="get_weather", arguments={})]),
            Message(role="tool", tool_results=[ToolResult.of_text(bad, "a"), ToolResult.of_text(other, "b"),
                                               ToolResult.of_text("ok_1-x", "c")])]
    run(r, hist, [TOOL])
    fr = seen(tmp_path)["frames"]
    uses = [b["id"] for b in fr[1]["message"]["content"] if b["type"] == "tool_use"]
    results = [b["tool_use_id"] for b in fr[2]["message"]["content"] if b["type"] == "tool_result"]
    assert uses == results
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", i) for i in uses)
    assert len(set(uses)) == 3
    assert uses[2] == "ok_1-x"
