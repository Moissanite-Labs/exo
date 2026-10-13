"""The tool route of muse-cli: ``muse serve`` (MSP, JSON-RPC over stdio) with Exo's tools as an inert MCP server.

Why: ``muse exec`` runs Muse's own agent and cannot carry Exo's tools. ``muse serve`` lets a client register an MCP
server for one session. Exo registers the same inert server claude-cli uses (``claude_cli_mcp.py``, which lists
Exo's tools and never runs one). The model sees them as ``mcp__exo__<tool>``.

How one turn goes (one fresh ``muse serve`` process per request, ephemeral session, torn down after):

1. ``initialize`` with ``requestedCapabilities: ["sessionMcp"]``. The reply's schema version and fingerprint are
   checked against the pins below. A mismatch is an error (class ``version_mismatch``), never a guess.
2. ``session/start`` with ``config.mcpServers.exo``, then ``turn/start`` with the whole history flattened to text.
3. Text streams as ``item/delta``. When the model calls an Exo tool, Muse (approval mode ``promptUnmatched``) sends
   the server request ``approval/request``. Exo records the call (id, name, raw arguments), answers the request,
   and after a short quiet period (so parallel calls arrive together) sends one ``turn/interrupt``. The turn ends
   ``cancelled`` and the inert tool is never run. That is the one-turn cap: Exo emits ``tool_call`` events with
   ``stop_reason: tool_use`` and runs the tools itself.
4. An approval for any tool that is not ``mcp__exo__*`` (a native Muse tool) is denied.

Limits, said plainly:

- No native way to feed a tool result back (turn input parts are text, image, skill and video only). So every
  request replays the flattened history, tool calls and tool results included, as one text block in a fresh
  session. Tool results are marked in that text; the model reads them as quoted text, not as structured results.
- Quota cost: Muse's own system prompt is large. A trivial prompt measured about 23k input tokens, with no cache
  hits. Each tool round trip pays it again. Exo logs ``inputTokens`` per call (logger ``exo.muse``).
- ``muse serve`` cannot switch off web tools or reminder agents the way ``muse exec`` can; native tool approvals
  are denied, and the reminder agents add some tokens.
- Tied to one wire schema. A Muse update that changes it makes this route fail with ``version_mismatch`` until the
  pins are reviewed. ``[models] muse_serve = false`` turns the route off (tool turns then fall back, as before).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from exo.core.models import muse_cli
from exo.core.models.base import StreamState, error_event
from exo.core.models.claude_cli import _schema
from exo.core.types import Message, ProviderEvent, ToolCall, ToolSpec, Usage

log = logging.getLogger("exo.muse")

SCHEMA_VERSION = 1
SCHEMA_FINGERPRINT = "sha256:6de32db08bbbe0deb478d2bfcaba001e9fb490986bc80db3aceb4d996abf7df9"
USER_AGENT_PREFIX = "muse-build/"  # the full string carries the version, platform and build; only the product is pinned
SERVER = "exo"
PREFIX = f"mcp__{SERVER}__"
MCP_SERVER = Path(__file__).with_name("claude_cli_mcp.py")
GRACE = 0.6  # seconds of quiet after the first tool call before interrupting, so parallel calls arrive together
INTERRUPT_WAIT = 15.0  # how long to wait for the cancelled turn to settle after an interrupt


def uuid7() -> str:
    b = bytearray(int(time.time() * 1000).to_bytes(6, "big") + os.urandom(10))
    b[6] = (b[6] & 0x0F) | 0x70
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


def build_serve_prompt(messages: list[Message]) -> str:
    """Flatten system, user, assistant, tool_use and tool_result into one text block, with tool results marked."""
    system = [m.text() for m in messages if m.role == "system" and m.text()]
    names: dict[str, str] = {}
    lines: list[str] = []
    for m in messages:
        if m.role == "user" and m.text():
            lines.append(f"[user]\n{m.text()}")
        elif m.role == "assistant":
            if m.text():
                lines.append(f"[assistant]\n{m.text()}")
            for tc in m.tool_calls:
                names[tc.id] = tc.name
                lines.append(f"[assistant called tool {tc.name} (call {tc.id}) with arguments {json.dumps(tc.arguments)}]")
        elif m.role == "tool":
            for r in m.tool_results:
                err = " (the tool reported an error)" if r.is_error else ""
                lines.append(f"[result of tool {names.get(r.call_id, '?')} (call {r.call_id}){err}; quoted output follows]\n"
                             f"{r.text() or '(no output)'}\n[end of tool result]")
    if not lines:
        raise ValueError("history must contain at least one message")
    head = ("This is the conversation so far. Calls marked 'assistant called tool' were already run by the host and "
            "their results are quoted below them. Reply as the assistant to the last entry. To act, call a tool; "
            "to finish, answer in plain text.")
    tail = ""
    if messages and messages[-1].role == "tool":
        tail = "\n\nThe last entry holds tool results. Use them to answer, or call another tool if still needed."
    return "\n\n".join(([*system] if system else []) + [head, *lines]) + tail


class _Conn:
    """Newline-delimited JSON-RPC over a subprocess's pipes."""

    def __init__(self, proc: asyncio.subprocess.Process, read: float) -> None:
        self.proc, self.read, self._id = proc, read, 0

    async def send(self, obj: dict[str, Any]) -> None:
        assert self.proc.stdin
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", **obj}) + "\n").encode())
        await self.proc.stdin.drain()

    async def call(self, method: str, params: dict[str, Any]) -> int:
        self._id += 1
        await self.send({"id": self._id, "method": method, "params": params})
        return self._id

    async def recv(self, timeout: float | None = None) -> dict[str, Any] | None:
        assert self.proc.stdout
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout or self.read)
            if not line:
                return None
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # a banner or stray print
            if isinstance(obj, dict):
                return obj

    async def result(self, rid: int) -> dict[str, Any]:
        """The result of request ``rid``; notifications before it are skipped. Raises RuntimeError on an error reply."""
        while (m := await self.recv()) is not None:
            if m.get("id") == rid and "method" not in m:
                if "error" in m:
                    raise RuntimeError(f"error {((m['error'] or {}).get('code'))}")
                return m.get("result") or {}
            if "method" in m and "id" in m:  # a server request before the handshake ended: acknowledge it
                await self.send({"id": m["id"], "result": {}})
        raise RuntimeError("muse serve exited")


def check_handshake(res: Mapping[str, Any]) -> str:
    """'' when the server speaks the pinned wire schema, else a short reason."""
    schema = res.get("schema") or {}
    if schema.get("version") != SCHEMA_VERSION or schema.get("fingerprint") != SCHEMA_FINGERPRINT:
        return "schema changed"
    if not str(res.get("userAgent") or "").startswith(USER_AGENT_PREFIX):
        return "unexpected server"
    if "sessionMcp" not in (res.get("grantedCapabilities") or []):
        return "sessionMcp not granted"
    return ""


def serve_argv(exe: str, model: str) -> list[str]:
    return [exe, "serve", "--provider", "meta", "--model", model, "--no-session-log", "--disable-shell",
            "--disable-write"]


def _initialize_params() -> dict[str, Any]:
    return {"clientInfo": {"name": "exo", "version": "0"}, "capabilities": {"requestedCapabilities": ["sessionMcp"]}}


def _usage(u: Mapping[str, Any]) -> Usage | None:
    if isinstance(u.get("inputTokens"), int) and isinstance(u.get("outputTokens"), int):
        return Usage(input_tokens=u["inputTokens"], output_tokens=u["outputTokens"],
                     cache_read_tokens=u.get("cacheReadTokens") or u.get("cachedTokens") or 0,
                     cache_write_tokens=u.get("cacheWriteTokens") or 0, reasoning_tokens=u.get("reasoningTokens") or 0)
    return None


async def probe_handshake(exe: str, env: Mapping[str, str] | None, timeout: float = 30.0) -> str:
    """Start ``muse serve``, run ``initialize`` only (no model call, no tokens) and return '' or a reason."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *serve_argv(exe, muse_cli.DEFAULT_MODEL), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=muse_cli.child_env(env), start_new_session=True, limit=1 << 24)
    except OSError:
        return "could not start muse serve"
    try:
        conn = _Conn(proc, timeout)
        return check_handshake(await conn.result(await conn.call("initialize", _initialize_params())))
    except (asyncio.TimeoutError, RuntimeError, OSError):
        return "no answer from muse serve"
    finally:
        await asyncio.shield(muse_cli.stop_async(proc))


async def run(command: str, env: Mapping[str, str] | None, messages: list[Message], tools: list[ToolSpec], *,
              model: str, effort: str, read: float) -> AsyncIterator[ProviderEvent]:
    """One request through a fresh ``muse serve``. Yields Exo provider events, ending in done or error."""
    try:
        prompt = build_serve_prompt(messages)
    except ValueError as exc:
        yield error_event(f"muse-cli: {exc}", 400, retryable=False)
        return
    manifest = [{"name": t.name, "description": t.description, "inputSchema": _schema(t.parameters)} for t in tools]
    known = {t.name for t in tools}
    with tempfile.TemporaryDirectory(prefix="muse-serve-") as tmp:
        (Path(tmp) / "tools.json").write_text(json.dumps(manifest), encoding="utf-8")
        ws = Path(tmp) / "ws"
        ws.mkdir()
        try:
            proc = await asyncio.create_subprocess_exec(
                *serve_argv(command, model), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=muse_cli.child_env(env), cwd=str(ws), start_new_session=True,
                limit=1 << 24)
        except OSError as exc:
            yield error_event(f"cannot run {command}: {exc.strerror or type(exc).__name__}", None, retryable=True)
            return
        try:
            async for ev in _drive(_Conn(proc, read), tmp, str(ws), prompt, known, model, effort):
                yield ev
        finally:  # also on cancel (a stop): leave no muse serve behind, nor its children
            await asyncio.shield(muse_cli.stop_async(proc))


async def _drive(conn: _Conn, tmp: str, ws: str, prompt: str, known: set[str], model: str,
                 effort: str) -> AsyncIterator[ProviderEvent]:
    st = StreamState()
    try:
        init = await conn.result(await conn.call("initialize", _initialize_params()))
        if why := check_handshake(init):
            yield error_event(f"muse-cli: version_mismatch ({why}); the serve route is pinned to schema "
                              f"{SCHEMA_VERSION} {SCHEMA_FINGERPRINT[:15]}", None, retryable=True)
            return
        await conn.send({"method": "initialized", "params": {}})
        sess = await conn.result(await conn.call("session/start", {
            "commandId": uuid7(), "workspaceRoot": ws, "modelId": model,
            "config": {"mcpServers": {SERVER: {"transport": "stdio", "command": sys.executable,
                                               "args": ["-I", str(MCP_SERVER), str(Path(tmp) / "tools.json")],
                                               "framing": "lineDelimitedJson"}}}}))
        sid = (sess.get("session") or {}).get("sessionId")
        if not sid:
            yield error_event("muse-cli: session/start returned no session", None, retryable=True)
            return
        turn_rid = await conn.call("turn/start", {"commandId": uuid7(), "sessionId": sid, "reasoningEffort": effort,
                                                  "input": [{"type": "text", "text": prompt}]})
    except asyncio.TimeoutError:
        yield error_event(f"ReadTimeout: muse sent nothing for {conn.read:.0f} s", None, retryable=True)
        return
    except (RuntimeError, OSError, ConnectionError) as exc:
        yield error_event(f"muse-cli: serve failed ({exc})", None, retryable=True)
        return

    turn_id: str | None = None
    kinds: dict[str, str] = {}
    calls: list[ToolCall] = []
    seen_calls: set[str] = set()
    tokens: list[Usage] = []
    completed: dict[str, Any] | None = None
    first_call_at: float | None = None
    interrupted = False
    native_denied = ""

    async def interrupt() -> None:
        nonlocal interrupted
        if interrupted:
            return
        interrupted = True
        params = {"commandId": uuid7(), "sessionId": sid}
        if turn_id:
            params["turnId"] = turn_id
        await conn.call("turn/interrupt", params)

    try:
        while completed is None:
            if first_call_at is not None and not interrupted and time.monotonic() - first_call_at >= GRACE:
                await interrupt()
            wait = conn.read
            if first_call_at is not None and not interrupted:
                wait = max(0.01, GRACE - (time.monotonic() - first_call_at))
            elif interrupted:
                wait = min(conn.read, INTERRUPT_WAIT)
            try:
                m = await conn.recv(wait)
            except asyncio.TimeoutError:
                if first_call_at is not None and not interrupted:
                    await interrupt()  # quiet: no more parallel calls are coming
                    continue
                if interrupted:
                    yield error_event("muse-cli: the turn did not settle after an interrupt", None, retryable=True)
                    return
                yield error_event(f"ReadTimeout: muse sent nothing for {conn.read:.0f} s", None, retryable=True)
                return
            if m is None:
                yield error_event("muse serve exited before the turn finished", None, retryable=True)
                return
            method, params = m.get("method"), m.get("params") or {}
            if m.get("id") == turn_rid and "method" not in m:
                if "error" in m:
                    yield error_event("muse-cli: turn/start was refused", None, retryable=True)
                    return
                turn_id = (m.get("result") or {}).get("turnId") or turn_id
            elif method == "approval/request":
                tool = str(params.get("toolName") or "")
                await conn.send({"id": m["id"], "result": {}})  # a presentation receipt; the decision is separate
                if tool.startswith(PREFIX):
                    cid = str(params.get("toolCallId") or uuid7())
                    name = tool[len(PREFIX):]
                    if cid not in seen_calls and (name in known or not known):
                        seen_calls.add(cid)
                        try:
                            args = json.loads(params.get("rawArgs") or "{}")
                        except ValueError:
                            args = {}
                        calls.append(ToolCall(id=cid, name=name, arguments=args if isinstance(args, dict) else {}))
                        first_call_at = first_call_at or time.monotonic()
                else:  # a native Muse tool: Exo never lets it run
                    native_denied = tool
                    choice = next((c for c in params.get("availableChoices") or []
                                   if isinstance(c, dict) and c.get("decision") in ("denied", "abort")), None)
                    req = params.get("currentRequirementId") or {}
                    if choice and params.get("approvalId") and req:
                        await conn.call("approval/decide", {
                            "commandId": uuid7(), "sessionId": sid, "approvalId": params["approvalId"],
                            "choiceId": choice.get("choiceId"), "requirementId": req})
                    else:
                        await interrupt()
            elif method == "userInput/request":
                await conn.send({"id": m["id"], "result": {}})
                native_denied = native_denied or "userInput"
                await interrupt()
            elif method == "turn/started":
                turn_id = params.get("turnId") or turn_id
            elif method == "item/started":
                item = params.get("item") or {}
                kinds[str(item.get("itemId"))] = str(item.get("kind") or "")
            elif method == "item/delta" and params.get("field") == "text":
                kind = kinds.get(str(params.get("itemId")), "")
                if kind == "agentMessage":
                    if ev := st.add_text("text", params.get("delta") or ""):
                        yield ev
                elif "reasoning" in kind.lower():
                    if ev := st.add_text("reasoning", params.get("delta") or ""):
                        yield ev
            elif method == "session/tokenUsage":
                if u := _usage(params.get("usage") or {}):
                    tokens.append(u)
            elif method == "turn/completed":
                completed = params
            elif "method" in m and "id" in m:  # any other server request: acknowledge, never act
                await conn.send({"id": m["id"], "result": {}})
    except (OSError, ConnectionError) as exc:
        yield error_event(f"muse serve pipe failed: {type(exc).__name__}", None, retryable=True)
        return

    usage = _usage(completed.get("usage") or {}) or (
        Usage(input_tokens=sum(t.input_tokens for t in tokens), output_tokens=sum(t.output_tokens for t in tokens),
              cache_read_tokens=sum(t.cache_read_tokens for t in tokens),
              reasoning_tokens=sum(t.reasoning_tokens for t in tokens)) if tokens else None)
    if usage:
        log.info("muse serve call: input_tokens=%d output_tokens=%d cached=%d", usage.input_tokens,
                 usage.output_tokens, usage.cache_read_tokens)
    terminal = completed.get("terminal")
    err = completed.get("error") or {}
    if terminal == "failed":
        kind = str(err.get("kind") or "unknown")
        status = 401 if kind == "authRequired" else None
        hint = " (run `muse login`)" if kind == "authRequired" else ""
        yield error_event(f"muse serve turn failed ({kind}){hint}", status, retryable=bool(err.get("retryable", True)))
        return
    if native_denied and not calls:
        yield error_event("muse-cli: the model tried a native Muse tool, which Exo does not allow", None, retryable=True)
        return
    if terminal not in ("completed", "cancelled") or (terminal == "cancelled" and not calls):
        yield error_event(f"muse serve turn ended {terminal}", None, retryable=True)
        return
    for call in calls:
        yield st.add_tool_call(call)
    if usage:
        st.usage = usage
        yield ProviderEvent(kind="usage", usage=usage)
        yield st.done()
    else:  # no counts reported: unknown, never 0
        done = st.done()
        done.usage = None
        yield done

