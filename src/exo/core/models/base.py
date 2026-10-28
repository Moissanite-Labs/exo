# Lifted from NousResearch/hermes-agent agent/transports/types.py, agent/transports/base.py @ dce1e9b37581dd62e480a9064dc04a709c2940d3 (MIT, Copyright (c) 2025 Nous Research). See PROVENANCE.toml.
"""Shared transport base: HTTP + SSE plumbing and stream assembly.

Upstream split a transport into convert_messages, convert_tools, build_kwargs and
normalize_response, with streaming and retries left on the agent. Exo keeps that
split but owns the stream here: a transport builds a request body and turns SSE
events into ``ProviderEvent`` items; this base does the HTTP call, error mapping
and assembly of the final assistant ``Message``. Retries and fallback belong to
the router, not the transport.
"""

from __future__ import annotations

import json
import time
from email.utils import parsedate_to_datetime
from typing import Any, AsyncIterator, Iterator

import httpx

from exo.core.types import ContentPart, Message, ProviderEvent, StopReason, ToolCall, ToolSpec, Usage

# HTTP statuses that may succeed on retry or on another route.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def map_finish_reason(reason: str | None, mapping: dict[str, StopReason]) -> StopReason:
    """Translate a provider stop reason via ``mapping``; unknown or None -> ``end``."""
    return "end" if reason is None else mapping.get(reason, "end")


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Seconds from a Retry-After header (delta seconds or HTTP date), or None."""
    if not value:
        return None
    try:
        secs = float(value)
    except ValueError:
        try:
            secs = parsedate_to_datetime(value).timestamp() - (now or time.time())
        except (TypeError, ValueError):
            return None
    return secs if secs > 0 else None


def error_event(text: str, status: int | None = None, retryable: bool | None = None,
                retry_after: float | None = None) -> ProviderEvent:
    """Build the terminal error event; ``retry_after`` lets the router honor provider resets."""
    if retryable is None:
        retryable = status is None or status in RETRYABLE_STATUS
    return ProviderEvent(kind="error", error=text, status=status, retryable=retryable,
                         retry_after=retry_after or None)


def iter_sse(lines: Iterator[str] | list[str]) -> Iterator[tuple[str, str]]:
    """Yield ``(event, data)`` pairs from SSE lines (sync; used by tests and the async reader)."""
    event, data = "", []
    for line in lines:
        if line == "":
            if data:
                yield event or "message", "\n".join(data)
            event, data = "", []
        elif not line.startswith(":"):
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
    if data:
        yield event or "message", "\n".join(data)


class StreamState:
    """Assembles deltas into the final assistant Message."""

    def __init__(self) -> None:
        self.parts: list[ContentPart] = []
        self.tool_calls: list[ToolCall] = []
        self.usage = Usage()
        self.stop_reason: StopReason | None = None
        self.meta: dict[str, Any] = {}

    def add_text(self, kind: str, text: str) -> ProviderEvent | None:
        if not text:
            return None
        ptype = "reasoning" if kind == "reasoning" else "text"
        last = self.parts[-1] if self.parts else None
        if last is not None and last.type == ptype and last.signature is None and last.data is None:
            last.text += text
        else:
            self.parts.append(ContentPart(type=ptype, text=text))
        return ProviderEvent(kind="reasoning" if ptype == "reasoning" else "text", text=text)

    def add_tool_call(self, call: ToolCall) -> ProviderEvent:
        self.tool_calls.append(call)
        return ProviderEvent(kind="tool_call", tool_call=call)

    def done(self) -> ProviderEvent:
        stop = self.stop_reason or ("tool_use" if self.tool_calls else "end")
        if stop == "end" and self.tool_calls:
            stop = "tool_use"
        msg = Message(role="assistant", content=self.parts, tool_calls=self.tool_calls, meta=self.meta)
        return ProviderEvent(kind="done", stop_reason=stop, message=msg, usage=self.usage)


def parse_json_args(raw: str) -> dict[str, Any]:
    """Decode tool arguments; empty -> {}, bad JSON or non-object -> {} (upstream behavior)."""
    if not raw or not raw.strip():
        return {}
    try:
        val = json.loads(raw)
    except ValueError:
        return {}
    return val if isinstance(val, dict) else {}


USER_AGENT = "exo/0.1"  # some providers (OpenCode Go) ask clients to name themselves, not their HTTP library


class HttpTransport:
    """Base for HTTP streaming providers. Subclasses implement the four hooks."""

    name = "http"
    path = ""

    def __init__(self, base_url: str, api_key: str | None = None, *, name: str | None = None,
                 model: str | None = None, client: httpx.AsyncClient | None = None,
                 timeout: float = 600.0, headers: dict[str, str] | None = None,
                 session_header: str | None = None, read_timeout: float | None = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_headers = dict(headers or {})
        self.session_header = session_header
        self._client = client
        # A provider that accepts the request but never sends a byte must fail fast, so the router can fall
        # back (seen live: a provider hanging every chat call while its model list answered in 0.2 s).
        self._timeout = httpx.Timeout(timeout, connect=15.0, read=read_timeout or timeout)
        if name:
            self.name = name

    # --- hooks -------------------------------------------------------------
    def headers(self) -> dict[str, str]:
        raise NotImplementedError

    async def request_headers(self) -> dict[str, str]:
        return self.headers()

    def build_body(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> dict[str, Any]:
        raise NotImplementedError

    def handle(self, event: str, data: str, st: StreamState) -> Iterator[ProviderEvent]:
        """Turn one SSE event into ProviderEvents. Set ``st.meta['_finished']`` when
        the provider signals the end; yield an ``error`` event to abort."""
        raise NotImplementedError

    def error_text(self, body: str) -> str:
        try:
            err = json.loads(body).get("error")
        except (ValueError, AttributeError):
            return body[:500]
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or err)[:500]
        return str(err or body)[:500]

    def explain_error(self, status: int, text: str) -> str:
        """Hook: rewrite a provider's raw HTTP error into something a user can act on."""
        return text

    def explain_request_error(self, status: int, text: str, *, model: str | None) -> str:
        """Request-local error context. The default preserves existing two-argument provider hooks."""
        return self.explain_error(status, text)

    def upstream_down(self, status: int, text: str) -> bool:
        """Hook: True when an HTTP error (even a 400) means the provider's own upstream is unavailable, so the call
        is retryable on another route and counts toward the route circuit breaker."""
        return False

    # --- stream ------------------------------------------------------------
    async def stream(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> AsyncIterator[ProviderEvent]:
        session_id = opts.pop("session_id", None)
        body = self.build_body(messages, tools, **opts)
        async for event in self.stream_prepared(body, session_id=session_id):
            yield event

    async def stream_prepared(self, body: dict[str, Any], *, session_id: str | None = None,
                              require_usage: bool = False, resolved_headers: dict[str, str] | None = None,
                              state_meta: dict | None = None) -> AsyncIterator[ProviderEvent]:
        """Send exactly the prepared body. HTTPX's default transport does not retry."""
        url = self.base_url + self.path
        try:
            sent = {"user-agent": USER_AGENT, **(resolved_headers if resolved_headers is not None else await self.request_headers()), **self.extra_headers}
        except Exception:
            yield error_event("Provider authentication unavailable", 401, retryable=False)
            return
        if self.session_header and session_id:
            sent[self.session_header] = str(session_id)
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        st = StreamState()
        st.meta.update(state_meta or {})
        try:
            async with client.stream("POST", url, json=body, headers=sent, timeout=self._timeout) as resp:
                if resp.status_code >= 400:
                    raw = (await resp.aread()).decode("utf-8", "replace")
                    msg = self.error_text(raw)
                    explained = self.explain_request_error(resp.status_code, msg, model=body.get("model") or self.model)
                    yield error_event(f"HTTP {resp.status_code}: {explained}", resp.status_code,
                                      retryable=True if self.upstream_down(resp.status_code, msg) else None,
                                      retry_after=parse_retry_after(resp.headers.get("retry-after")))
                    return
                buf: list[str] = []
                async for line in resp.aiter_lines():
                    buf.append(line)
                    if line != "":
                        continue
                    for ev, payload in iter_sse(buf):
                        for out in self.handle(ev, payload, st):
                            yield out
                            if out.kind == "error":
                                return
                    buf = []
                    if getattr(self, "stop_at_terminal", False) and st.meta.get("_finished"):
                        break
                for ev, payload in iter_sse(buf):
                    for out in self.handle(ev, payload, st):
                        yield out
                        if out.kind == "error":
                            return
        except httpx.HTTPError as exc:
            yield error_event(f"{type(exc).__name__}: {exc}", None, retryable=True)
            return
        finally:
            if self._client is None:
                await client.aclose()
        finished = st.meta.pop("_finished", False) or st.stop_reason is not None
        if not finished:  # closed cleanly but cut short: no finish event and no stop reason; never a normal end
            yield error_event("stream ended without a response", None, retryable=True)
            return
        usage_complete = st.meta.pop("_usage_complete", False)
        st.meta.pop("_usage_input", None)
        if require_usage and not usage_complete:
            yield error_event("provider did not return complete token usage", None, retryable=False)
            return
        if st.usage != Usage():
            yield ProviderEvent(kind="usage", usage=st.usage)
        yield st.done()
