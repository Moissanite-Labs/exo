"""First-party DeepSeek Chat Completions, including tool-history thinking replay.

Contract: https://api-docs.deepseek.com/guides/thinking_mode/ . Requests carrying
functions retain all prior assistant reasoning, including earlier final-answer turns.
"""
from __future__ import annotations

from exo.core.models.openai_chat import OpenAIChatProvider, convert_messages

EFFORTS = {'minimal': 'low', 'low': 'low', 'medium': 'high', 'high': 'high',
           'xhigh': 'high', 'max': 'max', 'ultra': 'max'}


class DeepSeekProvider(OpenAIChatProvider):
    name = 'deepseek'

    def build_body(self, messages, tools, **opts):
        body = super().build_body(messages, tools, **opts)
        if tools:
            history = []
            for message in messages:
                rows = convert_messages([message])
                if message.role == 'assistant':
                    rows[0]['reasoning_content'] = ''.join(p.text or '' for p in message.content if p.type == 'reasoning')
                history.extend(rows)
            body['messages'] = history
        effort = opts.get('reasoning')
        if effort is not None:
            if effort == 'none':
                body['thinking'] = {'type': 'disabled'}
                body.pop('reasoning_effort', None)
            elif isinstance(effort, str) and effort in EFFORTS:
                body['thinking'] = {'type': 'enabled'}
                body['reasoning_effort'] = EFFORTS[effort]
            else:
                raise ValueError('Unsupported DeepSeek reasoning effort')
        return body
