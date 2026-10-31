"""Stateless Responses over HTTP/SSE, with opaque reasoning/tool continuation.

Wire contract: official OpenAI Responses streaming and SIWC preview documentation.
Authentication may be resolved freshly for each request; OAuth never falls back to an API key.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterator

from exo.core.models.base import HttpTransport, StreamState, error_event
from exo.core.types import Message, ProviderEvent, ToolCall, ToolSpec, Usage


def merge_output_item(previous: dict, current: dict) -> dict:
    for key in ('type', 'id', 'call_id', 'name', 'namespace', 'encrypted_content'):
        if previous.get(key) and current.get(key) and previous[key] != current[key]:
            raise ValueError('Inconsistent Responses output history')
    if previous.get('arguments') and current.get('arguments') and json.loads(previous['arguments']) != json.loads(current['arguments']):
        raise ValueError('Inconsistent Responses output history')
    return {**current, **previous, **{key: value for key, value in current.items() if value not in (None, '', [])}}


def convert_input(messages: list[Message], origin: str, *, subscription: bool = False) -> list[dict]:
    out = []
    for message in messages:
        if message.role == 'tool':
            out.extend({'type': 'function_call_output', 'call_id': r.call_id,
                        'output': r.text() or ('(error)' if r.is_error else '(no output)')}
                       for r in message.tool_results)
            continue
        replay = message.meta.get('responses_output')
        replaying = bool(origin and message.role == 'assistant' and message.meta.get('responses_origin') == origin and isinstance(replay, list))
        present = {}
        if replaying:
            for item in replay:
                if isinstance(item, dict) and item.get('type') == 'function_call':
                    cid = item.get('call_id')
                    if cid in present:
                        raise ValueError('Inconsistent Responses tool history')
                    present[cid] = item
            for call in message.tool_calls:
                if call.id in present:
                    item = present[call.id]
                    if item.get('name') != call.name or json.loads(item.get('arguments') or '{}') != call.arguments:
                        raise ValueError('Inconsistent Responses tool history')
            out.extend(copy.deepcopy(item) for item in replay if isinstance(item, dict) and
                       item.get('type') in ('reasoning', 'message', 'function_call'))
        missing_calls = [call for call in message.tool_calls if call.id not in present]
        role = 'developer' if subscription and message.role == 'system' else message.role
        content = []
        for part in ([] if replaying and any(isinstance(item, dict) and item.get('type') == 'message' for item in replay) else message.content):
            if part.type == 'text' and part.text:
                content.append({'type': 'output_text' if role == 'assistant' else 'input_text', 'text': part.text})
            elif part.type == 'image' and role != 'assistant':
                content.append({'type': 'input_image', 'image_url': part.url or f'data:{part.mime or "image/png"};base64,{part.data}'})
        if content:
            out.append({'type': 'message', 'role': role, 'content': content})
        for call in missing_calls:
            item = {'type': 'function_call', 'call_id': call.id, 'name': call.name,
                    'arguments': json.dumps(call.arguments)}
            if subscription:
                item['namespace'] = 'exo'
            out.append(item)
    return out


class OpenAIResponsesProvider(HttpTransport):
    name = 'responses'
    path = '/responses'
    stop_at_terminal = True

    def __init__(self, *args, header_resolver=None, subscription=False, continuation_scope=None, prompt_cache_retention=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.header_resolver, self.subscription = header_resolver, subscription
        self.continuation_scope = continuation_scope
        self.prompt_cache_retention = prompt_cache_retention
        self.origin = self.name + '@' + self.base_url
        if subscription:
            if self.base_url != 'https://api.openai.com/v1' or header_resolver is None:
                raise ValueError('ChatGPT requires its authorized public Responses endpoint')
            self.api_key = None
            self.extra_headers = {}  # configured headers cannot replace the selected OAuth identity

    def headers(self):
        return {'content-type': 'application/json', **({'authorization': 'Bearer ' + self.api_key} if self.api_key else {})}

    async def request_headers(self):
        if self.header_resolver is None:
            return self.headers()
        supplied = await self.header_resolver()
        bearer = next((v for k, v in supplied.items() if k.lower() == 'authorization'), None)
        if not isinstance(bearer, str) or not bearer.startswith('Bearer ') or not re.fullmatch(r'[A-Za-z0-9._~+/=\-]+', bearer[7:]):
            raise ValueError('Provider authentication unavailable')
        return {'content-type': 'application/json', 'authorization': bearer}

    def scope(self):
        return self.continuation_scope() if self.continuation_scope else ('' if self.subscription else 'api')

    async def stream(self, messages, tools, **opts):
        try:
            scope = self.scope()
            headers = await self.request_headers()
            if self.scope() != scope:
                raise ValueError('Selected account changed')
        except Exception:
            yield error_event('Provider authorization or request preparation unavailable', 401, retryable=False)
            return
        try:
            body = self.build_body(messages, tools, **{**opts, '_scope': scope})
        except Exception:
            yield error_event('Responses request history or options are inconsistent', 400, retryable=False)
            return
        origin = self.origin + '#' + body['model'] + '#' + scope if scope or not self.subscription else ''
        async for event in self.stream_prepared(body, session_id=opts.get('session_id'), resolved_headers=headers,
                                                state_meta={'_request_origin': origin}):
            yield event

    def error_text(self, body):
        if self.subscription:
            try:
                error = json.loads(body).get('error')
                code = error.get('code') if isinstance(error, dict) else None
            except (ValueError, AttributeError):
                code = None
            messages = {
                'subscription_sharing_usage_limit_exceeded': 'ChatGPT plan usage limit reached. Review ChatGPT settings > Usage.',
                'subscription_sharing_usage_unavailable': 'ChatGPT plan usage is unavailable. Retry later or review ChatGPT settings.',
                'context_length_exceeded': 'ChatGPT request exceeds the model context limit.',
            }
            return messages.get(code if isinstance(code, str) else None,
                                'ChatGPT request refused. Check the connection and request compatibility. A refusal alone does not establish a usage limit.')
        return super().error_text(body)

    def build_body(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> dict[str, Any]:
        model = opts.get('model') or self.model or ''
        scope = opts.get('_scope', self.scope())
        origin = self.origin + '#' + model + '#' + scope if scope or not self.subscription else ''
        body = {'model': model, 'store': False, 'stream': True,
                'input': convert_input(messages, origin, subscription=self.subscription),
                'include': ['reasoning.encrypted_content']}
        functions = [{'type': 'function', 'name': t.name, 'description': t.description,
                      'parameters': t.parameters, 'strict': False} for t in tools]
        if functions:
            body['tools'] = ([{'type': 'namespace', 'name': 'exo', 'description': 'Local Exo tools', 'tools': functions}]
                             if self.subscription else functions)
            if opts.get('tool_choice'):
                choice = opts['tool_choice']
                if isinstance(choice, dict) and choice.get('type') == 'function':
                    name = choice.get('name') or (choice.get('function') or {}).get('name')
                    choice = {'type': 'function', 'name': name}
                    if self.subscription:
                        choice['namespace'] = 'exo'
                body['tool_choice'] = choice
        if not self.subscription:
            if opts.get('max_tokens'):
                body['max_output_tokens'] = int(opts['max_tokens'])
            if opts.get('temperature') is not None:
                body['temperature'] = opts['temperature']
            retention = opts.get('prompt_cache_retention', self.prompt_cache_retention)
            if retention in ('in_memory', '24h'):
                body['prompt_cache_retention'] = retention
        if opts.get('reasoning'):
            body['reasoning'] = dict(opts['reasoning']) if isinstance(opts['reasoning'], dict) else {'effort': opts['reasoning']}
        return body

    def _usage(self, raw: dict, st: StreamState) -> Iterator[ProviderEvent]:
        if not isinstance(raw, dict) or not raw:
            return
        prompt, output = raw.get('input_tokens'), raw.get('output_tokens')
        details = raw.get('input_tokens_details') or {}
        output_details = raw.get('output_tokens_details') or {}
        if not isinstance(details, dict) or not isinstance(output_details, dict):
            return
        cached = details.get('cached_tokens', 0)
        reasoning = output_details.get('reasoning_tokens', 0)
        valid = all(type(n) is int and n >= 0 for n in (prompt, output, cached, reasoning)) and cached <= prompt
        st.meta['_usage_complete'] = valid
        if valid:
            st.usage = Usage(input_tokens=prompt - cached, cache_read_tokens=cached, output_tokens=output,
                             reasoning_tokens=reasoning)
            yield ProviderEvent(kind='usage', usage=Usage(**vars(st.usage)))

    def _item(self, item: dict, st: StreamState) -> Iterator[ProviderEvent]:
        if not isinstance(item, dict):
            yield error_event('Invalid Responses output item', 400, retryable=False)
            return
        if item.get('type') != 'function_call':
            return
        cid, name = item.get('call_id'), item.get('name')
        seen = st.meta.setdefault('_emitted_calls', set())
        if not isinstance(cid, str) or not cid:
            yield error_event('Invalid Responses function call', 400, retryable=False)
            return
        try:
            arguments = json.loads(item.get('arguments') or '{}')
            if not isinstance(arguments, dict) or not isinstance(cid, str) or not cid or not isinstance(name, str) or not name:
                raise ValueError()
            if self.subscription and item.get('namespace') not in (None, 'exo'):
                raise ValueError()
        except (ValueError, TypeError):
            yield error_event('Invalid Responses function call', 400, retryable=False)
            return
        if cid in seen:
            previous = next(call for call in st.tool_calls if call.id == cid)
            if previous.name != name or previous.arguments != arguments:
                yield error_event('Inconsistent Responses function call', 400, retryable=False)
            return
        seen.add(cid)
        yield st.add_tool_call(ToolCall(id=cid, name=name, arguments=arguments))

    def handle(self, event: str, data: str, st: StreamState) -> Iterator[ProviderEvent]:
        try:
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError()
        except ValueError:
            yield error_event('Invalid Responses stream event', None, retryable=False)
            return
        kind = payload.get('type') or event
        if kind in ('response.output_text.delta', 'response.reasoning_summary_text.delta', 'response.reasoning_text.delta'):
            text = payload.get('delta')
            if isinstance(text, str):
                ev = st.add_text('text' if kind == 'response.output_text.delta' else 'reasoning', text)
                if ev:
                    yield ev
        elif kind == 'response.output_item.done':
            item = payload.get('item') or {}
            yield from self._item(item, st)
            if isinstance(item, dict):
                observed = st.meta.setdefault('_completed_output', {})
                index = payload.get('output_index', len(observed))
                if type(index) is int and index >= 0:
                    try:
                        observed[index] = copy.deepcopy(merge_output_item(observed.get(index, {}), item))
                    except (ValueError, TypeError):
                        yield error_event('Inconsistent Responses output history', 400, retryable=False)
                        return
        elif kind in ('response.completed', 'response.failed', 'response.incomplete', 'error'):
            response = payload.get('response') or {}
            if not isinstance(response, dict):
                yield error_event('Invalid Responses terminal event', None, retryable=False)
                return
            yield from self._usage(response.get('usage'), st)
            if kind != 'response.completed':
                err = response.get('error') or payload.get('error') or payload
                code = err.get('code', 'provider_error') if isinstance(err, dict) else 'provider_error'
                if not isinstance(code, str) or not re.fullmatch('[a-z0-9_]{1,80}', code):
                    code = 'provider_error'
                if kind == 'response.incomplete':
                    code = 'response_incomplete'
                messages = {'subscription_sharing_usage_limit_exceeded': 'ChatGPT plan usage limit reached. Review ChatGPT settings > Usage.',
                            'subscription_sharing_usage_unavailable': 'ChatGPT plan usage is unavailable. Retry later or review ChatGPT settings.',
                            'context_length_exceeded': 'Responses request exceeds the model context limit.',
                            'response_incomplete': 'Responses request was incomplete.'}
                yield error_event(messages.get(code, 'Responses request failed.'),
                                  429 if code == 'subscription_sharing_usage_limit_exceeded' else None,
                                  retryable=code in ('subscription_sharing_usage_unavailable', 'server_error'))
                return
            if not st.meta.get('_usage_complete') or response.get('status') not in (None, 'completed'):
                yield error_event('Completed Responses event lacks valid completion or usage', None, retryable=False)
                return
            output = response.get('output')
            observed = st.meta.pop('_completed_output', {})
            if output is None and observed:
                output = []
            if not isinstance(output, list):
                yield error_event('Completed Responses event has no output', None, retryable=False)
                return
            output = copy.deepcopy(output)
            for index, item in sorted(observed.items()):
                identity = item.get('call_id') if item.get('type') == 'function_call' else item.get('id')
                match = next((i for i, existing in enumerate(output) if existing == item or (identity and isinstance(existing, dict) and
                              (existing.get('call_id') if existing.get('type') == 'function_call' else existing.get('id')) == identity)), None)
                if match is None:
                    output.insert(min(index, len(output)), item)
                else:
                    try:
                        output[match] = merge_output_item(item, output[match])
                    except (ValueError, TypeError):
                        yield error_event('Inconsistent Responses output history', 400, retryable=False)
                        return
            for item in output:
                if not isinstance(item, dict):
                    yield error_event('Invalid Responses output item', None, retryable=False)
                    return
                yield from self._item(item, st)
            if not any(p.type == 'text' for p in st.parts):
                for item in output:
                    if item.get('type') == 'message':
                        for part in item.get('content') or []:
                            if isinstance(part, dict) and part.get('type') == 'output_text' and (ev := st.add_text('text', part.get('text', ''))):
                                yield ev
            st.meta['responses_output'] = copy.deepcopy(output)
            st.meta['responses_origin'] = st.meta.pop('_request_origin', self.origin + '#' + (self.model or '') + '#' + self.scope())
            st.meta.pop('_emitted_calls', None)
            st.meta['_finished'] = True
            st.stop_reason = 'tool_use' if st.tool_calls else 'end'
