# Lifted from NousResearch/hermes-agent agent/anthropic_message_convert.py @ dce1e9b37581dd62e480a9064dc04a709c2940d3 (MIT, Copyright (c) 2025 Nous Research). See PROVENANCE.toml.
"""Anthropic Messages API over httpx: request conversion, streaming, tool use, caching.

Conversion keeps upstream's hard-won guards (sanitized tool ids, blank-text
scrubbing, orphaned tool_use/tool_result stripping, role alternation, a leading
user turn, top-level union removal in tool schemas, duplicate tool names) and
drops the parts tied to Hermes's dict history (sidecar block carriers, Kimi and
DeepSeek routes, screenshot eviction, Bedrock ids).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterator

from exo.core.models.base import HttpTransport, StreamState, error_event, map_finish_reason, parse_json_args
from exo.core.types import ContentPart, Message, ProviderEvent, ToolCall, ToolSpec, Usage

API_VERSION = "2023-06-01"
EPHEMERAL = {"type": "ephemeral"}
_INPUT_USAGE = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_PLACEHOLDER = "(empty)"
_STOP_MAP = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool_use", "max_tokens": "max_tokens",
             "pause_turn": "end", "refusal": "end", "model_context_window_exceeded": "max_tokens"}
# Claude families that predate adaptive thinking and take budget_tokens (upstream list, trimmed).
_LEGACY_THINKING = ("claude-3", "claude-opus-4-0", "claude-opus-4-1", "claude-sonnet-4-0", "claude-opus-4-2025",
                    "claude-sonnet-4-2025", "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5")
_NO_XHIGH = ("claude-opus-4-6", "claude-sonnet-4-6")
_BUDGET = {"low": 4000, "medium": 8000, "high": 16000, "xhigh": 32000, "max": 32000}
_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def normalize_model(model: str) -> str:
    """Strip an ``anthropic/`` prefix; Claude version dots become hyphens (claude-opus-4.6)."""
    if model.lower().startswith("anthropic/"):
        model = model[len("anthropic/"):]
    return model.replace(".", "-") if model.lower().startswith("claude-") else model


def is_legacy(model: str) -> bool:
    m = model.lower()
    return "claude" not in m or any(s in m for s in _LEGACY_THINKING)


_TOOL_ID_OK = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def sanitize_tool_id(tool_id: str) -> str:
    """Anthropic requires ids matching ^[a-zA-Z0-9_-]+$ (max 64). Valid ids pass through unchanged.

    Others (e.g. Kimi's ``functions.bash:0``) get bad chars replaced and a short sha1 of the original
    appended, so the mapping is deterministic (tool_use and tool_result pairs still match) and two
    different originals cannot collapse to the same id. Never empty.
    """
    if not tool_id:
        return "tool_0"
    if _TOOL_ID_OK.match(tool_id):
        return tool_id
    digest = hashlib.sha1(tool_id.encode("utf-8", "replace")).hexdigest()[:8]
    return re.sub(r"[^a-zA-Z0-9_-]", "_", tool_id)[:55] + "_" + digest


def _blank(b: dict) -> bool:
    return b.get("type") == "text" and not str(b.get("text") or "").strip()


def normalize_schema(schema: Any) -> dict[str, Any]:
    """Top-level oneOf/allOf/anyOf get a generic 400, so drop them for a plain object."""
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    out = {k: v for k, v in schema.items() if k not in ("oneOf", "allOf", "anyOf")}
    out.setdefault("type", "object")
    if out["type"] == "object" and not isinstance(out.get("properties"), dict):
        out["properties"] = {}
    return out


def convert_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """ToolSpecs to Anthropic tools; duplicate names dropped (Anthropic hard-400s on them)."""
    out, seen = [], set()
    for t in tools or []:
        if t.name in seen:
            continue
        seen.add(t.name)
        out.append({"name": t.name, "description": t.description, "input_schema": normalize_schema(t.parameters)})
    return out


def _part_block(p: ContentPart) -> dict[str, Any] | None:
    if p.type == "text":
        return {"type": "text", "text": p.text}
    if p.type == "image":
        if p.data:
            mime = "image/jpeg" if (p.mime or "").lower() in ("", "image/jpg") else p.mime
            return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": p.data}}
        if p.url:
            return {"type": "image", "source": {"type": "url", "url": p.url}}
    return None


def _assistant_blocks(m: Message, keep_thinking: bool) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for p in m.content:
        if p.type == "reasoning":
            # Signed thinking goes back unchanged; redacted thinking carries its payload in
            # ``data``; unsigned reasoning cannot be replayed and is dropped.
            if not keep_thinking:
                continue
            if p.signature:
                blocks.append({"type": "thinking", "thinking": p.text, "signature": p.signature})
            elif p.data:
                blocks.append({"type": "redacted_thinking", "data": p.data})
        elif (b := _part_block(p)) is not None and not _blank(b):
            blocks.append(b)
    for tc in m.tool_calls:
        blocks.append({"type": "tool_use", "id": sanitize_tool_id(tc.id), "name": tc.name, "input": tc.arguments})
    return blocks or [{"type": "text", "text": _PLACEHOLDER}]


def _tool_result_block(r) -> dict[str, Any]:
    inner = [b for p in r.content if (b := _part_block(p)) is not None and not _blank(b)]
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": sanitize_tool_id(r.call_id),
                             "content": inner or "(no output)"}
    if r.is_error:
        block["is_error"] = True
    return block


def _strip_orphans(result: list[dict[str, Any]]) -> None:
    """Drop tool_use without a tool_result in the next user turn, and vice versa."""
    for i, m in enumerate(result):
        if m["role"] != "assistant":
            continue
        uses = {b["id"] for b in m["content"] if b.get("type") == "tool_use"}
        if not uses:
            continue
        nxt = result[i + 1] if i + 1 < len(result) and result[i + 1]["role"] == "user" else None
        answered = {b.get("tool_use_id") for b in (nxt["content"] if nxt else []) if b.get("type") == "tool_result"}
        orphan = uses - answered
        if orphan:
            kept = [b for b in m["content"] if not (b.get("type") == "tool_use" and b["id"] in orphan)]
            if len(kept) != len(m["content"]):  # edited turn: its thinking signature is dead
                kept = [b for b in kept if b.get("type") not in ("thinking", "redacted_thinking")]
            m["content"] = kept or [{"type": "text", "text": "(tool call removed)"}]
    live = {b["id"] for m in result if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use"}
    for m in result:
        if m["role"] == "user":
            kept = [b for b in m["content"] if b.get("type") != "tool_result" or b.get("tool_use_id") in live]
            if len(kept) != len(m["content"]):
                m["content"] = kept or [{"type": "text", "text": "(tool result removed)"}]


def _merge_roles(result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive same-role turns. A merged assistant turn's thinking is dropped:
    it was signed against a different turn boundary."""
    fixed: list[dict[str, Any]] = []
    for m in result:
        if fixed and fixed[-1]["role"] == m["role"]:
            extra = m["content"]
            if m["role"] == "assistant":
                extra = [b for b in extra if b.get("type") not in ("thinking", "redacted_thinking")]
            fixed[-1]["content"] = fixed[-1]["content"] + extra
        else:
            fixed.append(m)
    return fixed


def convert_messages(messages: list[Message], keep_thinking: bool = True) -> tuple[list[dict], list[dict]]:
    """Exo messages to ``(system_blocks, messages)``. All system messages join the system param."""
    system: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            if m.text().strip():
                system.append({"type": "text", "text": m.text()})
        elif m.role == "assistant":
            result.append({"role": "assistant", "content": _assistant_blocks(m, keep_thinking)})
        elif m.role == "tool":
            blocks = [_tool_result_block(r) for r in m.tool_results]
            if result and result[-1]["role"] == "user" and result[-1]["content"] and \
                    result[-1]["content"][0].get("type") == "tool_result":
                result[-1]["content"].extend(blocks)
            elif blocks:
                result.append({"role": "user", "content": blocks})
        else:
            blocks = [b for p in m.content if (b := _part_block(p)) is not None and not _blank(b)]
            result.append({"role": "user", "content": blocks or [{"type": "text", "text": "(empty message)"}]})
    _strip_orphans(result)
    result = _merge_roles(result)
    if result and result[0]["role"] != "user":
        result.insert(0, {"role": "user", "content": [{"type": "text", "text": _PLACEHOLDER}]})
    return system, result


def apply_cache_control(system: list[dict], messages: list[dict], tools: list[dict]) -> None:
    """Prompt caching, at most 4 breakpoints: last tool, last system block, last two messages.
    Render order is tools, system, messages, so this caches the stable prefix plus history."""
    marks = 0
    if tools:
        tools[-1]["cache_control"] = dict(EPHEMERAL)
        marks += 1
    if system:
        system[-1]["cache_control"] = dict(EPHEMERAL)
        marks += 1
    for m in reversed(messages):
        if marks >= 4:
            break
        for b in reversed(m["content"]):
            if b.get("type") in ("text", "tool_use", "tool_result", "image"):
                b["cache_control"] = dict(EPHEMERAL)
                marks += 1
                break


def thinking_params(model: str, effort: str | None, max_tokens: int) -> dict[str, Any]:
    """Map Exo's ``reasoning`` effort to Anthropic params. Adaptive models get
    ``thinking: adaptive`` + ``output_config.effort``; legacy ones get a budget."""
    if not effort or effort == "none":
        return {}
    effort = {"minimal": "low", "ultra": "max"}.get(effort, effort)
    effort = effort if effort in _EFFORTS else "medium"
    if is_legacy(model):
        # The API wants 1024 <= budget_tokens < max_tokens, so thinking cannot fit in 1024 tokens or fewer.
        if ("haiku" in model.lower() and "4-5" not in model) or max_tokens <= 1024:
            return {}
        budget = min(_BUDGET[effort], max(1024, max_tokens - 1024))
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}
    if effort == "xhigh" and any(s in model for s in _NO_XHIGH):
        effort = "max"
    return {"thinking": {"type": "adaptive", "display": "summarized"}, "output_config": {"effort": effort}}


class AnthropicProvider(HttpTransport):
    """Anthropic Messages API (``POST /v1/messages``, ``stream: true``)."""

    name = "anthropic"
    path = "/v1/messages"

    def __init__(self, base_url: str = "https://api.anthropic.com", api_key: str | None = None, *,
                 cache: bool = True, **kw: Any) -> None:
        super().__init__(base_url, api_key, **kw)
        self.cache = cache

    def headers(self) -> dict[str, str]:
        h = {"anthropic-version": API_VERSION, "content-type": "application/json"}
        if self.api_key:
            h["x-api-key"] = self.api_key
        return h

    def build_body(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> dict[str, Any]:
        model = normalize_model(opts.get("model") or self.model or "")
        max_tokens = int(opts.get("max_tokens") or 32000)
        # Thinking signatures are proprietary; other Anthropic-compatible hosts reject them.
        first_party = "anthropic.com" in self.base_url
        system, msgs = convert_messages(messages, keep_thinking=first_party)
        wire_tools = convert_tools(tools)
        if self.cache and opts.get("cache", True):
            apply_cache_control(system, msgs, wire_tools)
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": msgs, "stream": True}
        if system:
            body["system"] = system
        if wire_tools:
            body["tools"] = wire_tools
            if opts.get("tool_choice"):  # "none": tools stay declared (removing them is a prompt edit), use is off
                body["tool_choice"] = {"type": opts["tool_choice"]}
        body.update(thinking_params(model, opts.get("reasoning"), max_tokens))
        if (opts.get("temperature") is not None and (is_legacy(model) or any(s in model for s in _NO_XHIGH))
                and "thinking" not in body):
            body["temperature"] = opts["temperature"]
        if opts.get("stop"):
            body["stop_sequences"] = list(opts["stop"])
        return body

    def handle(self, event: str, data: str, st: StreamState) -> Iterator[ProviderEvent]:
        try:
            d = json.loads(data)
        except ValueError:
            return
        kind = d.get("type", event)
        blocks: dict[int, dict] = st.meta.setdefault("_blocks", {})
        if kind == "message_start":
            u = (d.get("message") or {}).get("usage") or {}
            st.meta["_usage_complete"] = False
            st.meta["_usage_input"] = ("_usage_input" not in st.meta and
                all(type(u.get(k)) is int and u[k] >= 0 for k in (*_INPUT_USAGE, "output_tokens")))
            st.usage = Usage(input_tokens=u.get("input_tokens") or 0, output_tokens=u.get("output_tokens") or 0,
                             cache_read_tokens=u.get("cache_read_input_tokens") or 0,
                             cache_write_tokens=u.get("cache_creation_input_tokens") or 0)
        elif kind == "content_block_start":
            cb = dict(d.get("content_block") or {})
            cb["_json"] = ""
            blocks[d.get("index", 0)] = cb
            if cb.get("type") == "redacted_thinking":
                st.parts.append(ContentPart(type="reasoning", data=cb.get("data")))
            elif cb.get("type") == "thinking":
                st.parts.append(ContentPart(type="reasoning", text=cb.get("thinking") or ""))
                cb["_part"] = st.parts[-1]
            elif cb.get("type") == "text" and cb.get("text"):
                if (ev := st.add_text("text", cb["text"])) is not None:
                    yield ev
        elif kind == "content_block_delta":
            cb, delta = blocks.get(d.get("index", 0), {}), d.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta":
                if (ev := st.add_text("text", delta.get("text", ""))) is not None:
                    yield ev
            elif dt == "thinking_delta" and cb.get("_part") is not None:
                cb["_part"].text += delta.get("thinking", "")
                if delta.get("thinking"):
                    yield ProviderEvent(kind="reasoning", text=delta["thinking"])
            elif dt == "signature_delta" and cb.get("_part") is not None:
                cb["_part"].signature = (cb["_part"].signature or "") + delta.get("signature", "")
            elif dt == "input_json_delta":
                cb["_json"] = cb.get("_json", "") + delta.get("partial_json", "")
        elif kind == "content_block_stop":
            cb = blocks.pop(d.get("index", 0), {})
            if cb.get("type") in ("tool_use", "server_tool_use"):
                args = parse_json_args(cb["_json"]) if cb["_json"] else dict(cb.get("input") or {})
                yield st.add_tool_call(ToolCall(id=cb.get("id") or "", name=cb.get("name") or "", arguments=args))
        elif kind == "message_delta":
            u = d.get("usage") or {}
            # Input/cache totals are fixed at start; cumulative output cannot decrease. Invalidity is sticky.
            st.meta["_usage_complete"] = st.meta["_usage_input"] = (st.meta.get("_usage_input", False)
                and all(k not in u or (type(u[k]) is int and u[k] == count) for k, count in zip(
                    _INPUT_USAGE, (st.usage.input_tokens, st.usage.cache_read_tokens, st.usage.cache_write_tokens)))
                and type(u.get("output_tokens")) is int and u["output_tokens"] >= st.usage.output_tokens)
            reason = (d.get("delta") or {}).get("stop_reason")
            if reason:
                st.stop_reason = map_finish_reason(reason, _STOP_MAP)
                if reason == "refusal":
                    st.meta["refusal"] = True
            if u.get("output_tokens") is not None:
                st.usage.output_tokens = u["output_tokens"]
            yield ProviderEvent(kind="usage", usage=Usage(**vars(st.usage)))
        elif kind == "message_stop":
            st.meta.pop("_blocks", None)
            st.meta["_finished"] = True
        elif kind == "error":
            err = d.get("error") or {}
            etype = err.get("type", "error")
            status = {"overloaded_error": 529, "rate_limit_error": 429, "api_error": 500}.get(etype)
            yield error_event(f"{etype}: {err.get('message', '')}", status,
                              retryable=status is not None)
