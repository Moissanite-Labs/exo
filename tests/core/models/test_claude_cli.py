"""Synthetic Claude stream-json fixture contract v1: no network, real CLI or vendor wire certification."""
import asyncio
import json
import os
import stat
import subprocess
import signal
import sys
import time

import pytest

from exo.core.models import claude_cli
from exo.core.models.base import StreamState

from exo.core.models.fake import FakeProvider
from exo.core.models.router import Router
from exo.core.types import Message, ToolCall, ToolResult, ToolSpec
from exo.models_cmd import available_models

# A fake CLI speaking stream-json both ways. fake.json beside it (the CLI gets a minimal env, D-039) gives the mode
# (the behaviour), out (where it records what it saw) and pid.
FAKE = f"""#!{sys.executable}
import json, os, sys, time
cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "fake.json")))
mode = cfg["mode"]
out = lambda d: print(json.dumps(d), flush=True)
if mode == "chattyfail":
    sys.stderr.write("Z" * 10000 + "\\nboom token=sk-cli-fixture-secret-123456789\\n")
    sys.exit(3)
if mode in ("orphan", "orphanfail", "orphansleep"):
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL)
    open(cfg["pid"], "w").write(str(child.pid))
    out({{"type": "system", "subtype": "fixture_ready"}})
    if mode == "orphanfail":
        sys.stderr.write("boom\\n"); sys.exit(3)
    if mode == "orphansleep":
        time.sleep(60)
if mode == "fail":
    sys.stderr.write("boom\\n")
    sys.exit(3)
if mode == "sleep":
    open(cfg["pid"], "w").write(str(os.getpid()))
    out({{"type": "system", "subtype": "fixture_ready"}})
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
           "body": json.loads(settings["env"]["CLAUDE_CODE_EXTRA_BODY"])}}, open(cfg["out"], "w"))
if mode == "nologin":
    out({{"type": "assistant", "error": "authentication_failed", "message": {{"content": [{{"type": "text", "text": "Not logged in"}}]}}}})
    out({{"type": "result", "subtype": "success", "is_error": True}})
    sys.exit(1)
usage = None if mode == "nousage" else {{"input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": 5}}
if mode in ("tool", "tool_error", "tool_wrong_exit"):
    blocks = [{{"type": "tool_use", "id": "tu_1", "name": "mcp__hermes__get_weather", "input": {{"city": "Paris"}}}}]
else:
    blocks = [{{"type": "text", "text": "hi there"}}]
    out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": "hi "}}}}}})
out({{"type": "stream_event", "event": {{"type": "message_stop"}}}})
out({{"type": "assistant", "message": {{"content": blocks}}}})
res = {{"type": "result", "subtype": "error_max_turns" if mode in ("tool", "tool_error", "tool_wrong_exit", "limit_without_tools") else "success",
       "is_error": mode == "tool_error", "usage": usage}}
out(res)
sys.exit(1 if mode in ("tool", "tool_error", "limit_without_tools") else 0)
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
    path = fake_claude(tmp_path)
    (tmp_path / "bin" / "fake.json").write_text(json.dumps({"mode": mode, "out": str(tmp_path / "out.json"),
                                                            "pid": str(tmp_path / "pid")}))
    return Router(roles={"main": ["claude-cli:sonnet"]}, env={"PATH": path, **env})


def run(r, messages, tools=()):
    async def go():
        return [e async for e in r.stream("main", messages, list(tools))]
    return asyncio.run(go())


def seen(tmp_path):
    return json.loads((tmp_path / "out.json").read_text())


def test_success_argv_env_and_isolation(tmp_path):
    r = router(tmp_path, ANTHROPIC_API_KEY="sk-secret", ANTHROPIC_AUTH_TOKEN="tok", ANTHROPIC_BASE_URL="http://x",
               SOME_PARENT_VAR="x", AWS_ACCESS_KEY_ID="a", LANG="C")
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
    assert "SOME_PARENT_VAR" not in s["env"] and "AWS_ACCESS_KEY_ID" not in s["env"]  # D-039: a minimal env
    assert s["env"]["LANG"] == "C"
    assert s["env"]["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert s["system"] == "be brief" and "claude-cli-cwd-" in s["cwd"]
    assert s["frames"] == [{"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}}]


@pytest.mark.parametrize("mode", ["tool", "tool_error"])
def test_tool_call_mapped_and_history_replayed(tmp_path, mode):
    r = router(tmp_path, mode)
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


@pytest.mark.parametrize("mode", ["tool_wrong_exit", "limit_without_tools"])
def test_only_the_pinned_tool_boundary_is_accepted(tmp_path, mode):
    events = run(router(tmp_path, mode), [Message.user("hi")], [TOOL])
    assert events[-1].kind == "error" and not [e for e in events if e.kind == "tool_call"]


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


def test_idle_timeout_kills_and_reports(tmp_path, monkeypatch):
    r = router(tmp_path, "sleep")
    r.providers["claude-cli"] = {**r.providers["claude-cli"], "read_timeout": 0.5}
    provider = r.credentials("claude-cli")[0].provider
    read = provider._read_line
    first = True
    async def ready_then_read(proc):
        nonlocal first
        if first:
            first = False
            line = await asyncio.wait_for(proc.stdout.readline(), 5)  # startup is not the idle-timeout assertion
            assert json.loads(line)["subtype"] == "fixture_ready"
            return line
        return await read(proc)
    monkeypatch.setattr(provider, "_read_line", ready_then_read)
    end = run(r, [Message.user("hi")])[-1]
    assert end.kind == "error" and "ReadTimeout" in end.error
    assert _wait_dead(int((tmp_path / "pid").read_text()))


@pytest.mark.parametrize("mode", ["normal", "timeout", "pipe", "cancel", "close"])
def test_stderr_reader_is_cancelled_and_awaited_on_every_exchange_exit(mode):
    async def go():
        entered, closed = asyncio.Event(), asyncio.Event()
        class Reader:
            async def read(self, *args):
                entered.set()
                try:
                    await asyncio.Event().wait()  # a descendant can hold stderr open after leader exit
                finally:
                    closed.set()
        class Writer:
            def write(self, data):
                pass
            async def drain(self):
                await entered.wait()
                if mode == "pipe":
                    raise OSError("broken pipe token=sk-cli-fixture-secret-123456789")
            def close(self):
                pass
        class Proc:
            def __init__(self):
                self.stdin, self.stderr, self.stdout = Writer(), Reader(), asyncio.StreamReader()
                if mode in ("normal", "close"):
                    frames = [{"type": "stream_event", "event": {"type": "message_stop"}},
                              {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
                              {"type": "result", "subtype": "success", "is_error": False}]
                    self.stdout.feed_data("".join(json.dumps(f) + "\n" for f in frames).encode())
                    self.stdout.feed_eof()
            returncode = 0  # the leader has exited
            async def wait(self):
                await asyncio.Event().wait()  # asyncio's wait() also waits for the pipe the descendant holds
        p = claude_cli.ClaudeCliProvider(env={}, read_timeout=.02 if mode == "timeout" else 5)
        frames = [{"type": "user", "message": {"role": "user", "content": []}}]
        stream = p._run(Proc(), frames, set(), StreamState())
        async def consume():
            return [e async for e in stream]
        if mode == "cancel":
            task = asyncio.create_task(consume())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif mode == "close":
            await anext(stream)
            await stream.aclose()
        else:
            events = await asyncio.wait_for(consume(), .5)
            assert events[-1].kind == ("done" if mode == "normal" else "error")
            assert "sk-cli-fixture-secret-123456789" not in (events[-1].error or "")
        assert closed.is_set()  # before asyncio.run's shutdown can hide a leaked reader
    asyncio.run(go())


@pytest.mark.parametrize("mode", ["orphan", "orphanfail"])
def test_descendant_stderr_does_not_hold_terminal_output_open_and_is_killed(tmp_path, mode):
    r = router(tmp_path, mode)
    pidf = tmp_path / "pid"
    try:
        async def go():
            return await asyncio.wait_for(_collect(r.stream("main", [Message.user("hi")], [])), 5)
        events = asyncio.run(go())
        assert events[-1].kind == ("done" if mode == "orphan" else "error")
        assert _wait_dead(int(pidf.read_text()))
    finally:
        if pidf.exists():
            try:
                os.kill(int(pidf.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_cancelling_cli_stops_its_descendant(tmp_path):
    r = router(tmp_path, "orphansleep")
    pidf = tmp_path / "pid"
    async def go():
        task = asyncio.create_task(_collect(r.stream("main", [Message.user("hi")], [])))
        async def ready():
            while not pidf.exists() or not pidf.read_text():
                await asyncio.sleep(.01)
        await asyncio.wait_for(ready(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _wait_dead(int(pidf.read_text()))
    try:
        asyncio.run(go())
    finally:
        if pidf.exists():
            try:
                os.kill(int(pidf.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_cleanup_timeout_closes_transport_when_pipe_stays_open(monkeypatch):
    closed, killed = [], []
    class Transport:
        def close(self):
            closed.append(True)
    class Proc:
        _transport = Transport()
        async def wait(self):
            await asyncio.Event().wait()
    proc = Proc()
    monkeypatch.setattr(claude_cli, "kill_group", lambda child: killed.append(child))
    monkeypatch.setattr(claude_cli, "CLEANUP_TIMEOUT", .01)
    asyncio.run(claude_cli.stop_group(proc))
    assert killed == [proc] and closed == [True]


async def _collect(stream):
    return [e async for e in stream]


def test_stderr_tail_is_bounded_useful_and_scrubbed(tmp_path):
    end = run(router(tmp_path, "chattyfail"), [Message.user("hi")])[-1]
    assert end.kind == "error" and "boom" in end.error and "[redacted]" in end.error
    assert "sk-cli-fixture-secret-123456789" not in end.error and len(end.error) < 500


def test_inert_mcp_survives_blank_malformed_and_invalid_input(tmp_path):
    manifest = tmp_path / "tools.json"
    manifest.write_text(json.dumps([{"name": "fixture_tool", "inputSchema": {"type": "object"}}]))
    rows = ["\n", "  \n", "not json token=sk-cli-fixture-secret-123456789\n", "[]\n", "null\n",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "unknown"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": "bad"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "fixture_tool"}}]
    payload = "".join(r if isinstance(r, str) else json.dumps(r) + "\n" for r in rows)
    result = subprocess.run([sys.executable, str(claude_cli.MCP_SERVER), str(manifest)], input=payload,
                            capture_output=True, text=True, timeout=5,
                            env={"HOME": str(tmp_path / "empty-home"), "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0 and not result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["error"]["code"] for r in replies if "error" in r] == [-32700, -32600, -32600, -32601, -32602]
    assert [r["id"] for r in replies if "result" in r] == [1, 4, 5]
    assert replies[-2]["result"]["tools"][0]["name"] == "fixture_tool"
    assert replies[-1]["result"]["isError"] is True
    assert "sk-cli-fixture-secret-123456789" not in result.stdout


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
