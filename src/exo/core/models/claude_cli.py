"""Claude subscription via the ``claude`` CLI: one subprocess per call, OAuth login, no API key.

Behavior follows the Hermes claude-subscription-directsdk plugin (MIT, Nous Research), reimplemented in
Exo's style. The CLI runs headless with its own tools, MCP servers, skills and settings switched off
and at most one turn. Exo keeps the tool loop: Exo's tools are advertised to the model through an
inert MCP server and the request body (``CLAUDE_CODE_EXTRA_BODY``), the model's tool_use comes back
in the stream, and the CLI stops at its one-turn limit so Exo runs the tool and calls again. History
is replayed as stream-json frames, so tool turns work.

Differences from Hermes: no local admission relay (so no cache-breakpoint control and no upstream
status capture, usage comes from the CLI's result line); native thinking blocks are not replayed;
conflicting API credentials are stripped from the child's environment instead of refused.

Compatibility evidence: ``tests/core/models/test_claude_cli.py`` pins a synthetic stream-json fixture contract
(v1), including ``shouldQuery: false`` replay and ``error_max_turns`` with exit 1. It does not certify a real
installed CLI version or authenticated wire exchange. See ``docs/PROVIDERS.md`` for the assumed envelopes.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import sys
import tempfile
from contextlib import aclosing
from pathlib import Path
from typing import Any, AsyncGenerator, AsyncIterator, Mapping, cast

import httpx

from exo.core.models.base import StreamState, error_event
from exo.core.models.anthropic import sanitize_tool_id
from exo.core.trace import scrub
from exo.core.types import Message, ProviderEvent, ToolCall, ToolSpec, Usage

PREFIX = "mcp__hermes__"  # the inert server is named "hermes" so tool names match the proven plugin
# Credentials and backend switches that would make the CLI bill an API account instead of the login.
DROP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_FOUNDRY_API_KEY",
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_EXTRA_BODY", "CLAUDE_CODE_EFFORT_LEVEL")
CHILD_ENV = {"ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_MAX_RETRIES": "0", "DISABLE_AUTO_COMPACT": "1",
             "DISABLE_COMPACT": "1", "CLAUDE_CODE_TOTAL_TOKENS_REMINDER": "off"}
NATIVE_ERROR_STATUS = {"rate_limit": 429, "billing_error": 402, "authentication_failed": 401,
                       "overloaded": 529, "server_error": 503}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MCP_SERVER = Path(__file__).with_name("claude_cli_mcp.py")
CLEANUP_TIMEOUT = 2.0
STDERR_BYTES = 4096


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The CLI's environment (D-039): an explicit minimal set from the caller's (``exo.envscrub.minimal_env``:
    PATH, HOME, locale, terminal, CLAUDE_CONFIG_DIR, proxies, CA bundles), never an API credential (OAuth login
    only, so nothing in DROP_ENV), plus fixed switches. The parent's other variables never reach the CLI."""
    from exo.envscrub import minimal_env

    out = {k: v for k, v in minimal_env(os.environ if env is None else env).items() if k not in DROP_ENV}
    out.update(CHILD_ENV)
    return out


def _schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Anthropic rejects top-level oneOf/allOf/anyOf and an object without properties."""
    out = {k: v for k, v in (schema or {}).items() if k not in ("oneOf", "allOf", "anyOf")}
    out.setdefault("type", "object")
    if out["type"] == "object" and not isinstance(out.get("properties"), dict):
        out["properties"] = {}
    return out


def _blocks(m: Message) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in m.content:
        if p.type == "text" and p.text:
            out.append({"type": "text", "text": p.text})
        elif p.type == "image":
            if p.url and not p.data:  # the CLI cannot fetch; a hint keeps the turn alive
                out.append({"type": "text", "text": f"[Image not attached: remote URL {p.url}]"})
            else:
                out.append({"type": "image", "source": {"type": "base64", "media_type": p.mime or "image/png",
                                                        "data": p.data or ""}})
    return out


def build_frames(messages: list[Message], names: set[str]) -> tuple[str, list[dict[str, Any]]]:
    """Exo messages to ``(system, stream-json input frames)``. Consecutive user turns merge."""
    system: list[str] = []
    frames: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system.append(m.text())
            continue
        if m.role == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": sanitize_tool_id(r.call_id), "is_error": r.is_error,
                       "content": r.text() or ("(error)" if r.is_error else "(no output)")} for r in m.tool_results]
        elif m.role == "assistant":
            role, blocks = "assistant", _blocks(m)  # thinking is not replayed (no signatures kept)
            blocks += [{"type": "tool_use", "id": sanitize_tool_id(tc.id), "name": PREFIX + tc.name if tc.name in names else tc.name,
                        "input": tc.arguments} for tc in m.tool_calls]
        else:
            role, blocks = "user", _blocks(m)
        if not blocks:  # an empty frame (a tool message with no results, a blank turn) carries nothing
            continue
        if frames and frames[-1]["type"] == role:
            frames[-1]["message"]["content"].extend(blocks)
        else:
            frames.append({"type": role, "message": {"role": role, "content": blocks}})
    if frames and frames[-1]["type"] == "assistant":  # a trailing assistant note: ask the model to go on
        frames.append({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "(continue)"}]}})
    if not frames:
        raise ValueError("history must contain at least one message")
    return "\n\n".join(system), frames


def _private_dir(path: str) -> bool:
    info = os.lstat(path)
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077


def workdir() -> str:
    """A stable private cwd for the CLI (it writes the cwd into the prompt, so a changing one breaks
    prompt caching). Our own 0700 directory in the tempdir; a fresh mkdtemp if that is not trustworthy."""
    path = os.path.join(tempfile.gettempdir(), f"claude-cli-cwd-{os.getuid()}")
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        if _private_dir(path):
            return path
    except OSError:
        pass
    return tempfile.mkdtemp(prefix="claude-cli-cwd-")


def kill_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the CLI's whole process group (node and MCP children); it leads its own session."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # gone already, or a zombie leader (macOS answers EPERM)
        pass


async def exited(proc: asyncio.subprocess.Process) -> int:
    """The leader's exit code. ``proc.wait()`` also waits for every pipe to close, so a descendant that keeps
    stderr open would hold it until the read timeout; the return code is set as soon as the leader exits."""
    while proc.returncode is None:
        await asyncio.sleep(0.02)
    return proc.returncode


async def stop_group(proc: asyncio.subprocess.Process) -> None:
    """The leader may have exited while a child still holds a pipe; always stop the owned group."""
    kill_group(proc)
    try:
        await asyncio.wait_for(proc.wait(), CLEANUP_TIMEOUT)
    except asyncio.TimeoutError:
        # CPython's Process owns this transport, but typeshed does not expose the private attribute.
        transport = cast(asyncio.SubprocessTransport, getattr(proc, "_transport"))
        transport.close()  # bound cleanup if a detached descendant retains the captured pipes


class ClaudeCliProvider:
    """Provider over a ``claude`` executable. ``command`` is its path; the model is an alias (sonnet, opus, haiku)."""

    name = "claude-cli"

    def __init__(self, command: str = "claude", *, name: str | None = None, model: str | None = None,
                 env: Mapping[str, str] | None = None, read_timeout: float | None = 60.0,
                 timeout: float = 600.0) -> None:
        self.command = command
        self.model = model
        self.env = env
        self._timeout = httpx.Timeout(timeout, read=read_timeout or timeout)  # the router quotes ``_timeout.read``
        self._read = read_timeout or timeout
        if name:
            self.name = name

    def _diagnostic(self, text: Any, limit: int = 300) -> str:
        # Scrub token/header patterns and credentials already in the caller's environment. Do not read a key
        # store just to redact diagnostics from a CLI that owns its own OAuth login.
        from exo.envscrub import is_secret_name
        from exo.core.models.keys import redact_secrets

        env = os.environ if self.env is None else self.env
        values = sorted((v for k, v in env.items() if v and is_secret_name(k)), key=len, reverse=True)
        return scrub(redact_secrets(str(text), values=values), limit)

    async def stream(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> AsyncIterator[ProviderEvent]:
        names = {t.name for t in tools or []}
        try:
            system, frames = build_frames(messages, names)
        except ValueError as exc:
            yield error_event(f"claude-cli: {exc}", 400, retryable=False)
            return
        manifest = [{"name": t.name, "description": t.description, "inputSchema": _schema(t.parameters)}
                    for t in tools or []]
        body: dict[str, Any] = {"tools": [{"name": PREFIX + t["name"], "description": t["description"],
                                           "input_schema": t["inputSchema"]} for t in manifest]}
        env = child_env(self.env)
        if opts.get("tool_choice") == "none" and body["tools"]:  # merged into the CLI's API request (D-064)
            body["tool_choice"] = {"type": "none"}
        if opts.get("max_tokens"):
            body["max_tokens"] = int(opts["max_tokens"])
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(int(opts["max_tokens"]))
        effort = {"ultra": "max"}.get(opts.get("reasoning"), opts.get("reasoning"))
        if effort in EFFORTS:
            body["output_config"] = {"effort": effort}
        st = StreamState()
        proc = None
        with tempfile.TemporaryDirectory(prefix="claude-cli-") as tmp:
            root = Path(tmp)
            (root / "tools.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "system.md").write_text(system, encoding="utf-8")
            # Settings apply env inside the CLI, which avoids execve's argument size limit for big tool schemas.
            (root / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps(body)}}),
                                                encoding="utf-8")
            mcp = {"mcpServers": {"hermes": {"command": sys.executable, "args": [str(MCP_SERVER), str(root / "tools.json")]}}}
            argv = [self.command, "-p", "--model", str(opts.get("model") or self.model or "sonnet"),
                    "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                    "--include-partial-messages", "--tools", "", "--system-prompt-file", str(root / "system.md"),
                    "--settings", str(root / "settings.json"), "--setting-sources", "", "--strict-mcp-config",
                    "--disable-slash-commands", "--max-turns", "1", "--permission-mode", "dontAsk",
                    "--no-session-persistence", "--mcp-config", json.dumps(mcp)]
            if effort in EFFORTS:
                argv += ["--effort", effort]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=env, cwd=workdir(), start_new_session=True,
                    limit=1 << 24)  # one stream-json line can carry a whole message
            except OSError as exc:
                yield error_event(f"cannot run {self.command}: {self._diagnostic(exc)}", None, retryable=True)
                return
            terminal = None
            try:
                async with aclosing(self._run(proc, frames, names, st)) as events:
                    async for ev in events:
                        if ev.kind in ("done", "error"):
                            terminal = ev
                            break
                        yield ev
            finally:  # also runs on cancel (a stop) and on a closed generator: leave no claude behind
                await asyncio.shield(stop_group(proc))
            if terminal is not None:
                yield terminal  # the router sees terminal output only after reader/process cleanup

    async def _read_line(self, proc: asyncio.subprocess.Process) -> bytes:
        """One stdout line; a stall longer than the read timeout raises TimeoutError (the clock restarts per line)."""
        assert proc.stdout
        return await asyncio.wait_for(proc.stdout.readline(), self._read)

    async def _run(self, proc: asyncio.subprocess.Process, frames: list[dict[str, Any]], names: set[str],
                   st: StreamState) -> AsyncGenerator[ProviderEvent, None]:
        stderr = proc.stderr
        assert stderr
        tail = bytearray()

        async def drain_stderr() -> None:
            while chunk := await stderr.read(STDERR_BYTES):
                tail.extend(chunk)
                del tail[:-STDERR_BYTES]

        stderr_task = asyncio.create_task(drain_stderr())
        try:
            async with aclosing(self._exchange(proc, frames, names, st, tail)) as events:
                async for ev in events:
                    yield ev
        finally:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

    async def _exchange(self, proc: asyncio.subprocess.Process, frames: list[dict[str, Any]], names: set[str],
                        st: StreamState, stderr_tail: bytearray) -> AsyncGenerator[ProviderEvent, None]:
        assert proc.stdin and proc.stdout and proc.stderr
        timeout_msg = f"ReadTimeout: claude sent nothing for {self._read:.0f} s"

        def tail() -> str:
            text = self._diagnostic(stderr_tail.decode("utf-8", "replace").strip(), STDERR_BYTES)[-300:]
            return f": {text}" if text else ""

        async def event() -> dict[str, Any] | None:
            while True:
                line = await self._read_line(proc)
                if not line:
                    return None
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # a banner or stray print: not an event
                if isinstance(row, dict):
                    return row

        try:
            for i, frame in enumerate(frames):
                last = i == len(frames) - 1
                if frame["type"] == "user" and not last:
                    frame = {**frame, "shouldQuery": False}  # replay history without a model call
                proc.stdin.write((json.dumps(frame) + "\n").encode())
                await proc.stdin.drain()
                if frame.get("shouldQuery") is False:
                    while True:
                        ack = await event()
                        if ack is None:
                            yield error_event("claude exited before acknowledging replayed history", None, retryable=True)
                            return
                        if ack.get("type") == "result":
                            if ack.get("num_turns") != 0 or ack.get("is_error"):
                                yield error_event("claude did not accept replayed history", None, retryable=True)
                                return
                            break
            proc.stdin.close()
            assistants: list[dict[str, Any]] = []
            results: list[dict[str, Any]] = []
            emitted, stopped, native_error, native_code = "", False, None, None
            while (ev := await event()) is not None:
                kind = ev.get("type")
                if kind == "assistant":
                    if ev.get("error") or (ev.get("message") or {}).get("error"):
                        native_error = "\n".join(b.get("text", "") for b in (ev.get("message") or {}).get("content", [])
                                                 if b.get("type") == "text")
                        native_code = ev.get("error")
                    else:
                        assistants.append(ev["message"])
                elif kind == "result":
                    results.append(ev)
                elif kind == "stream_event":
                    native = ev.get("event") or {}
                    stopped = stopped or native.get("type") == "message_stop"
                    delta = native.get("delta") or {}
                    if delta.get("type") == "text_delta" and (out := st.add_text("text", delta.get("text", ""))):
                        emitted += delta["text"]
                        yield out
                    elif delta.get("type") == "thinking_delta" and (out := st.add_text("reasoning", delta.get("thinking", ""))):
                        yield out
        except asyncio.TimeoutError:
            yield error_event(timeout_msg + tail(), None, retryable=True)
            return
        except (OSError, ConnectionError) as exc:
            yield error_event(f"claude pipe failed: {self._diagnostic(exc)}" + tail(), None, retryable=True)
            return
        try:
            rc = await asyncio.wait_for(exited(proc), self._read)
        except asyncio.TimeoutError:
            yield error_event(timeout_msg + tail(), None, retryable=True)
            return
        await asyncio.sleep(0)  # let the reader collect already-available bytes; never wait for a child's EOF
        if native_error is not None:
            status = NATIVE_ERROR_STATUS.get(str(native_code))
            hint = " (run `claude` and log in)" if status == 401 else ""
            yield error_event(f"claude: {self._diagnostic(native_error or native_code, 500)}{hint}", status, retryable=True)
            return
        if len(results) != 1 or not assistants or not stopped:
            yield error_event(f"claude exited {rc} with an incomplete response" + tail(), None, retryable=True)
            return
        final = results[0]
        blocks = [b for a in assistants for b in a.get("content") or []]
        calls = [ToolCall(id=b["id"], name=b["name"].removeprefix(PREFIX), arguments=b.get("input") or {})
                 for b in blocks if b.get("type") == "tool_use"]
        # With one turn allowed, a model that calls a tool ends the run at the limit: exit 1, error_max_turns.
        boundary = bool(calls) and final.get("subtype") == "error_max_turns" and rc == 1
        if not boundary and (rc != 0 or final.get("is_error") or final.get("subtype") != "success"):
            yield error_event(f"claude request failed ({final.get('subtype')}, exit {rc})"
                              + tail(), None, retryable=True)
            return
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if text.startswith(emitted) and (rest := text[len(emitted):]):
            if out := st.add_text("text", rest):  # whatever the partial stream missed
                yield out
        for call in calls:
            yield st.add_tool_call(call)
        u = final.get("usage")
        if isinstance(u, dict) and isinstance(u.get("input_tokens"), int) and isinstance(u.get("output_tokens"), int):
            st.usage = Usage(input_tokens=u["input_tokens"], output_tokens=u["output_tokens"],
                             cache_read_tokens=u.get("cache_read_input_tokens") or 0,
                             cache_write_tokens=u.get("cache_creation_input_tokens") or 0,
                             reasoning_tokens=(u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
            yield ProviderEvent(kind="usage", usage=st.usage)
            yield st.done()
        else:  # no counts reported: unknown, never 0
            done = st.done()
            done.usage = None
            yield done
