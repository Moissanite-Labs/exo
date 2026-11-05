"""First-party DeepSeek wire fixtures; never call the actual API or a credential store."""
import asyncio
import json

import httpx
import pytest

from exo.core.models.deepseek import DeepSeekProvider
from exo.core.models.router import Router, load_catalog
from exo.core.types import ContentPart, Message, ToolCall, ToolResult, ToolSpec
from exo.models_cmd import available_models

TOOL = ToolSpec('lookup', 'Synthetic lookup', {'type': 'object', 'properties': {}})


def reasoning_message(text, reasoning, calls=None):
    m = Message.assistant(text, calls)
    m.content.insert(0, ContentPart(type='reasoning', text=reasoning))
    return m


def test_tools_replay_all_reasoning_including_prior_final_answers_and_earlier_user_turns():
    history = [Message.user('earlier question'), reasoning_message('earlier final', 'earlier thinking'),
               Message.user('lookup'), reasoning_message('', 'tool thinking', [ToolCall('c', 'lookup', {'q': 'fixture'})]),
               Message.tool([ToolResult.of_text('c', 'synthetic result')])]
    provider = DeepSeekProvider('https://api.deepseek.com/v1', 'synthetic')
    body = provider.build_body(history, [TOOL], model='account-listed-model')
    assert body['messages'][1]['reasoning_content'] == 'earlier thinking'
    assert body['messages'][3]['reasoning_content'] == 'tool thinking'
    assert body['messages'][3]['tool_calls'][0]['id'] == body['messages'][4]['tool_call_id'] == 'c'
    assert body['messages'][4]['content'] == 'synthetic result'
    plain = provider.build_body(history, [], model='account-listed-model')
    assert all('reasoning_content' not in m for m in plain['messages'])
    assert 'thinking' not in body and 'reasoning_effort' not in body  # inherit server defaults


@pytest.mark.parametrize('requested,effective', [('minimal','low'),('low','low'),('medium','high'),('high','high'),
                                                ('xhigh','high'),('max','max'),('ultra','max'),('none',None)])
def test_official_thinking_effort_mapping(requested,effective):
    body = DeepSeekProvider('https://api.deepseek.com/v1').build_body([],[],model='fixture',reasoning=requested)
    assert body['thinking']['type'] == ('disabled' if effective is None else 'enabled')
    assert body.get('reasoning_effort') == effective


def test_account_discovery_and_credentials_do_not_change_default_roles(tmp_path):
    catalog = load_catalog()
    r = Router(env={'DEEPSEEK_API_KEY': 'synthetic'}, key_sources=False)
    assert r.roles == catalog['roles']
    assert isinstance(r.credentials('deepseek')[0].provider, DeepSeekProvider)
    seen=[]
    def get(url,**kw):
        seen.append((url,kw['headers']['authorization']))
        return httpx.Response(200,json={'data':[{'id':'authorized-z'},{'id':'authorized-a'}]},request=httpx.Request('GET',url))
    rows = [v for v in available_models(r,tmp_path,get=get) if v['provider']=='deepseek']
    assert [v['route'] for v in rows] == ['deepseek:authorized-a','deepseek:authorized-z']
    assert seen == [('https://api.deepseek.com/v1/models','Bearer synthetic')]
    assert not any(ref.startswith('deepseek:') for refs in catalog['roles'].values() for ref in refs)
    assert not any(ref.startswith('deepseek:') for ref in catalog['models'])


def test_actual_loop_retains_thinking_through_tool_result_and_serialized_history(tmp_path):
    from exo.core.loop import Loop
    from exo.core.sessions import SessionStore
    async def go():
        bodies=[];executed=[]
        async def respond(request):
            bodies.append(json.loads(request.content))
            delta = {'reasoning_content':'synthetic thinking','tool_calls':[{'index':0,'id':'c','type':'function',
                        'function':{'name':'lookup','arguments':'{"q":"fixture"}'}}]} if len(bodies)==1 else {'content':'done'}
            data={'choices':[{'delta':delta,'finish_reason':'tool_calls' if len(bodies)==1 else 'stop'}],
                  'usage':{'prompt_tokens':10,'prompt_tokens_details':{'cached_tokens':3},'completion_tokens':5}}
            return httpx.Response(200,content=('data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n').encode())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            r=Router(roles={'main':['deepseek:authorized-model']},env={'DEEPSEEK_API_KEY':'synthetic'},client=client)
            store=SessionStore(tmp_path/'messages.sqlite')
            async def tool(call,context):
                executed.append(call);return ToolResult.of_text(call.id,'synthetic tool result')
            loop=Loop(store,r.provider('main'),model='deepseek:authorized-model',tool_specs=[TOOL],run_tool=tool)
            sid=store.create_session().id
            result=await loop.send(sid,'use lookup')
            assert result.stop_reason=='end' and result.message.text()=='done' and len(executed)==1
            assert bodies[1]['messages'][-2]['reasoning_content']=='synthetic thinking'
            assert bodies[1]['messages'][-1]['tool_call_id']=='c'
            assert result.last_usage.input_tokens==7 and result.last_usage.cache_read_tokens==3
    asyncio.run(go())


@pytest.mark.parametrize('effort', ['invented',[]])
def test_unsupported_effort_is_rejected_without_echoing_the_value(effort):
    with pytest.raises(ValueError,match='Unsupported DeepSeek reasoning effort'):
        DeepSeekProvider('https://api.deepseek.com/v1').build_body([],[],reasoning=effort)
