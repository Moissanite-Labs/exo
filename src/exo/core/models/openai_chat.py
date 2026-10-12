# Lifted from NousResearch/hermes-agent agent/transports/chat_completions.py @ dce1e9b37581dd62e480a9064dc04a709c2940d3 (MIT, Copyright (c) 2025 Nous Research). See PROVENANCE.toml.
"""OpenAI-compatible Chat Completions over httpx: OpenRouter, OpenAI, llama.cpp, Ollama,
LM Studio, vLLM. Upstream's near-identity conversion is kept; its provider quirk tables
(xAI aliasing, Gemini thinking config, Kimi efforts, cache-key routing) are dropped.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from exo.core.models.base import HttpTransport, StreamState, error_event, map_finish_reason, parse_json_args
from exo.core.types import Message, ProviderEvent, ToolCall, ToolSpec, Usage

_FINISH_MAP = {"stop": "end", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens",
               "content_filter": "end", "eos": "end"}
_WIRE_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "none")


def _content(m: Message) -> str | list[dict[str, Any]]:
    """Plain string unless the message carries images (strict local servers prefer strings)."""
    if not any(p.type == "image" for p in m.content):
        return m.text()
    parts: list[dict[str, Any]] = []
    for p in m.content:
        if p.type == "text" and p.text:
            parts.append({"type": "text", "text": p.text})
        elif p.type == "image":
            url = p.url or f"data:{p.mime or 'image/png'};base64,{p.data}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Exo messages to Chat Completions messages. A tool message with several results
    becomes one ``role: tool`` message per result. Reasoning is not replayed: strict
    providers 400 on unknown keys."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            for r in m.tool_results:
                text = r.text() or ("(error)" if r.is_error else "(no output)")
                out.append({"role": "tool", "tool_call_id": r.call_id, "content": text})
        elif m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.text() or None}
            if m.tool_calls:
                msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                      "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                                     for tc in m.tool_calls]
            elif msg["content"] is None:
                msg["content"] = ""
            out.append(msg)
        else:
            out.append({"role": m.role, "content": _content(m)})
    return out


def convert_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for t in tools or []:
        if t.name not in seen:
            seen.add(t.name)
            out.append({"type": "function", "function": {"name": t.name, "description": t.description,
                                                         "parameters": t.parameters}})
    return out


class OpenAIChatProvider(HttpTransport):
    """``POST {base_url}/chat/completions`` with ``stream: true``. ``base_url`` includes ``/v1``."""

    name = "openai"
    path = "/chat/completions"

    def headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    def build_body(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> dict[str, Any]:
        body: dict[str, Any] = {"model": opts.get("model") or self.model or "", "messages": convert_messages(messages),
                                "stream": True, "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = convert_tools(tools)
        if opts.get("max_tokens"):
            key = "max_completion_tokens" if "api.openai.com" in self.base_url else "max_tokens"
            body[key] = int(opts["max_tokens"])
        if opts.get("temperature") is not None:
            body["temperature"] = opts["temperature"]
        if opts.get("stop"):
            body["stop"] = list(opts["stop"])
        effort = opts.get("reasoning")
        if effort:
            effort = {"ultra": "max"}.get(effort, effort)
            if effort in _WIRE_EFFORTS:
                if "openrouter.ai" in self.base_url:
                    body["reasoning"] = {"effort": effort} if effort != "none" else {"enabled": False}
                else:
                    body["reasoning_effort"] = effort
        return body

    def handle(self, event: str, data: str, st: StreamState) -> Iterator[ProviderEvent]:
        if data.strip() == "[DONE]":
            yield from self._flush_tools(st)
            st.meta["_finished"] = True
            return
        try:
            d = json.loads(data)
        except ValueError:
            return
        if d.get("error"):
            err = d["error"] if isinstance(d["error"], dict) else {"message": str(d["error"])}
            code = err.get("code")
            status = code if isinstance(code, int) else None
            yield error_event(f"provider error: {err.get('message', err)}", status)
            return
        if u := d.get("usage"):
            st.usage = Usage(
                input_tokens=u.get("prompt_tokens") or 0, output_tokens=u.get("completion_tokens") or 0,
                cache_read_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
                reasoning_tokens=(u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0,
                cost_usd=u.get("cost") if isinstance(u.get("cost"), (int, float)) else None)
            yield ProviderEvent(kind="usage", usage=Usage(**vars(st.usage)))
        for choice in d.get("choices") or []:
            delta = choice.get("delta") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and (ev := st.add_text("reasoning", reasoning)) is not None:
                yield ev
            if isinstance(delta.get("content"), str) and (ev := st.add_text("text", delta["content"])) is not None:
                yield ev
            pending: dict[int, dict] = st.meta.setdefault("_tools", {})
            for tc in delta.get("tool_calls") or []:
                slot = pending.setdefault(tc.get("index", len(pending)), {"id": "", "name": "", "args": ""})
                fn = tc.get("function") or {}
                slot["id"] = slot["id"] or tc.get("id") or ""
                slot["name"] += fn.get("name") or ""
                slot["args"] += fn.get("arguments") or ""
            if choice.get("finish_reason"):
                st.stop_reason = map_finish_reason(choice["finish_reason"], _FINISH_MAP)
                yield from self._flush_tools(st)

    def _flush_tools(self, st: StreamState) -> Iterator[ProviderEvent]:
        for idx, slot in sorted(st.meta.pop("_tools", {}).items()):
            call_id = slot["id"] or f"call_{len(st.tool_calls)}_{idx}"
            yield st.add_tool_call(ToolCall(id=call_id, name=slot["name"], arguments=parse_json_args(slot["args"])))
