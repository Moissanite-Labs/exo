"""Offline Responses/SIWC wire fixtures; no account, credentials or model requests."""
import asyncio
import json

import httpx
import pytest

from exo.core.models.responses import OpenAIResponsesProvider
from exo.core.models.router import Router, load_catalog
from exo.core.models.fake import FakeProvider
from exo.core.types import Message, ToolResult, ToolSpec, from_dict, to_dict
from exo.models_cmd import available_models

TOOLS = [ToolSpec('lookup', 'Synthetic lookup', {'type': 'object', 'properties': {}})]
USAGE = {'input_tokens': 20, 'input_tokens_details': {'cached_tokens': 5}, 'output_tokens': 7,
         'output_tokens_details': {'reasoning_tokens': 3}}
REASON = {'type': 'reasoning', 'id': 'rs_fixture', 'summary': [{'type': 'summary_text', 'text': 'A summary'}],
          'encrypted_content': 'opaque-fixture-not-a-token'}
CALL = {'type': 'function_call', 'id': 'fc_fixture', 'call_id': 'call_fixture', 'namespace': 'exo',
        'name': 'lookup', 'arguments': '{"query":"fixture"}'}
TEXT = {'type': 'message', 'id': 'msg_fixture', 'role': 'assistant',
        'content': [{'type': 'output_text', 'text': 'finished'}]}


def wire(*events):
    return ''.join('event: '+e['type']+'\r\ndata: '+json.dumps(e)+'\r\n\r\n' for e in events).encode()


def completed(output):
    return {'type': 'response.completed', 'response': {'status': 'completed', 'output': output, 'usage': USAGE}}


def test_full_subscription_tool_roundtrip_preserves_opaque_items_after_json_restart():
    async def go():
        requests = []
        async def respond(request):
            requests.append(request)
            first = len(requests) == 1
            content = wire({'type': 'response.reasoning_summary_text.delta', 'delta': 'A summary'},
                           {'type': 'response.output_item.done', 'item': CALL}, completed([REASON, CALL])) if first else wire(
                           {'type': 'response.output_text.delta', 'delta': 'finished'}, completed([TEXT]))
            return httpx.Response(200, content=content)
        headers_seen = []
        async def headers():
            value = 'Bearer synthetic-'+str(len(headers_seen)); headers_seen.append(value)
            return {'Authorization': value}
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = OpenAIResponsesProvider('https://api.openai.com/v1', 'must-not-use-api-key', name='chatgpt',
                         subscription=True, header_resolver=headers, continuation_scope=lambda:'account-a', client=client)
            first = [e async for e in provider.stream([Message.system('rules'), Message.user('lookup')], TOOLS,
                            model='authorized-model', max_tokens=10, temperature=0.5, reasoning='high')]
            assert first[-1].kind == 'done' and first[-1].stop_reason == 'tool_use'
            assert len([e for e in first if e.kind == 'tool_call']) == 1
            call = first[-1].message.tool_calls[0]
            assert (call.id, call.name, call.arguments) == ('call_fixture','lookup',{'query':'fixture'})
            restored = from_dict(Message, json.loads(json.dumps(to_dict(first[-1].message))))
            history = [Message.system('rules'), Message.user('lookup'), restored,
                       Message.tool([ToolResult.of_text(call.id, 'synthetic result')])]
            second = [e async for e in provider.stream(history, TOOLS, model='authorized-model')]
            assert second[-1].message.text() == 'finished'
            body, next_body = [json.loads(r.content) for r in requests]
            assert body['store'] is False and body['stream'] is True and body['input'][0]['role'] == 'developer'
            assert body['tools'][0]['type'] == 'namespace' and body['tools'][0]['tools'][0]['name'] == 'lookup'
            assert body['reasoning'] == {'effort':'high'}
            assert not {'temperature','max_output_tokens','previous_response_id','metadata'} & body.keys()
            assert next_body['input'][2:4] == [REASON, CALL]
            assert next_body['input'][4] == {'type':'function_call_output','call_id':'call_fixture','output':'synthetic result'}
            assert [r.headers['authorization'] for r in requests] == headers_seen
            usage = second[-1].usage
            assert (usage.input_tokens,usage.cache_read_tokens,usage.output_tokens,usage.reasoning_tokens) == (15,5,7,3)
            for opts in ({'model':'another-model'}, {'model':'authorized-model','_scope':'another-account'}):
                converted = provider.build_body(history, TOOLS, **opts)['input']
                assert not any(i.get('type') == 'reasoning' for i in converted)
    asyncio.run(go())


@pytest.mark.parametrize('terminal', ['response.failed','response.incomplete','error','eof','bad_json','missing_usage'])
def test_unsuccessful_or_truncated_stream_is_never_done(terminal):
    async def go():
        end = {'type':terminal,'response':{'error':{'code':'subscription_sharing_usage_limit_exceeded'},'usage':USAGE}}
        tail = b'' if terminal == 'eof' else b'data: not-json\n\n' if terminal == 'bad_json' else wire(end)
        if terminal == 'missing_usage':tail=wire({'type':'response.completed','response':{'output':[TEXT]}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=wire(
                {'type':'response.output_text.delta','delta':'partial'})+tail))) as client:
            p=OpenAIResponsesProvider('https://fixture.invalid/v1','synthetic',client=client)
            events=[e async for e in p.stream([Message.user('hi')],[],model='fixture')]
            assert events[0].text == 'partial' and events[-1].kind == 'error'
            assert not any(e.kind == 'done' for e in events)
    asyncio.run(go())


def test_generic_meta_responses_options_and_auth_failure_are_request_local():
    p=OpenAIResponsesProvider('https://api.meta.ai/v1','synthetic',name='meta')
    body=p.build_body([Message.system('rules')],TOOLS,model='catalog-choice',max_tokens=80,temperature=0.2)
    assert body['max_output_tokens']==80 and body['temperature']==0.2 and body['input'][0]['role']=='system'
    assert body['tools'][0]['type']=='function' and body['include']==['reasoning.encrypted_content']
    async def go():
        async def bad():raise RuntimeError('synthetic-secret-must-not-escape')
        p=OpenAIResponsesProvider('https://api.openai.com/v1',header_resolver=bad,subscription=True)
        events=[e async for e in p.stream([],[],model='fixture')]
        assert events[-1].kind=='error' and 'synthetic-secret' not in events[-1].error
    asyncio.run(go())


@pytest.mark.parametrize('connected', [False,True])
def test_selected_subscription_never_falls_through_to_paid_api_or_env_key(connected):
    async def go():
        paid=FakeProvider(['must not run'])
        async def bad():raise RuntimeError('expired fake credential')
        r=Router(roles={'main':['chatgpt:authorized','paid:fixture']},catalog={'providers':{
            'chatgpt':{'transport':'responses','auth':'chatgpt','base_url':'https://api.openai.com/v1'}}},
            instances={'paid':paid},env={'OPENAI_API_KEY':'must-not-use'},provider_auth={'chatgpt':(lambda:connected,bad)})
        events=[e async for e in r.stream('main',[Message.user('hi')],[])]
        assert events[-1].kind=='error' and paid.calls==[]
        assert all(c.key is None for c in r.credentials('chatgpt'))
    asyncio.run(go())


def test_subscription_discovery_preserves_authorized_server_order_and_account_switch(tmp_path):
    account=['a'];calls=[]
    async def headers():return {'Authorization':'Bearer fake-'+account[0]}
    r=Router(roles={},providers={},env={},provider_auth={'chatgpt':(lambda:True,headers,lambda:account[0])})
    def get(url,**kw):
        calls.append((url,kw['headers']))
        return httpx.Response(200,json={'models':[{'slug':'z-'+account[0],'display_name':'First','visibility':'list'},
                {'slug':'hidden','visibility':'hide'},{'slug':'a-'+account[0],'display_name':'Second','visibility':'list'}]},
                request=httpx.Request('GET',url))
    first=[v for v in available_models(r,tmp_path,get=get) if v['provider']=='chatgpt']
    account[0]='b'
    second=[v for v in available_models(r,tmp_path,get=get) if v['provider']=='chatgpt']
    assert [v['route'] for v in first]==['chatgpt:z-a','chatgpt:a-a']
    assert [v['route'] for v in second]==['chatgpt:z-b','chatgpt:a-b']
    assert first[0]['display_name']=='First' and len(calls)==2 and not (tmp_path/'cache/models/chatgpt.json').exists()
    assert load_catalog()['roles']==Router(env={}).roles  # no default role/account change


def test_connect_disconnect_availability_never_caches_an_empty_credential_set():
    async def go():
        connected=[False]; headers_seen=[]
        async def headers():
            headers_seen.append(True)
            return {'Authorization':'Bearer fake-current'}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=wire(completed([TEXT]))))) as client:
            r=Router(roles={'main':['chatgpt:authorized']},env={},client=client,
                     provider_auth={'chatgpt':(lambda:connected[0],headers,lambda:'profile')})
            assert r.credentials('chatgpt')==[]
            connected[0]=True
            events=[e async for e in r.stream('main',[Message.user('fixture')],[])]
            assert events[-1].kind=='done' and headers_seen==[True]
            connected[0]=False
            assert r.credentials('chatgpt')==[]
            events=[e async for e in r.stream('main',[Message.user('fixture')],[])]
            assert events[-1].kind=='error' and headers_seen==[True]
    asyncio.run(go())


def test_actual_loop_executes_tool_and_replays_reasoning_before_second_response(tmp_path):
    from exo.core.loop import Loop
    from exo.core.sessions import SessionStore
    async def go():
        bodies=[]; executed=[]
        async def respond(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200,content=wire(completed([REASON,{k:v for k,v in CALL.items() if k!='namespace'}] if len(bodies)==1 else [TEXT])))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            r=Router(roles={'main':['meta:catalog-choice']},env={'META_API_KEY':'synthetic-only'},client=client)
            store=SessionStore(tmp_path/'messages.sqlite')
            async def tool(call,context):
                executed.append(call)
                return ToolResult.of_text(call.id,'synthetic lookup result')
            loop=Loop(store,r.provider('main'),model='meta:catalog-choice',tool_specs=TOOLS,run_tool=tool)
            sid=store.create_session().id
            result=await loop.send(sid,'use lookup')
            assert result.stop_reason=='end' and result.message.text()=='finished' and len(executed)==1
            assert len(bodies)==2 and REASON in bodies[1]['input']
            assert any(i.get('type')=='function_call_output' and i['call_id']=='call_fixture' for i in bodies[1]['input'])
            assert bodies[0]['prompt_cache_retention']=='24h' and bodies[0]['store'] is False
    asyncio.run(go())


def test_completed_event_stops_consumption_without_waiting_for_stream_eof():
    class NeverEndingAfterCompletion(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield wire(completed([TEXT]))
            raise AssertionError('must not read beyond terminal completion')
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=NeverEndingAfterCompletion()))) as client:
            p=OpenAIResponsesProvider('https://fixture.invalid/v1',client=client)
            events=[e async for e in p.stream([],[],model='fixture')]
            assert events[-1].kind=='done'
    asyncio.run(go())


def test_catalog_from_previous_account_cannot_paint_current_account(tmp_path):
    account=['a']
    async def headers():return {'Authorization':'Bearer fake-a'}
    r=Router(roles={},env={},provider_auth={'chatgpt':(lambda:True,headers,lambda:account[0])})
    def switched(url,**kw):
        account[0]='b'
        return httpx.Response(200,json={'models':[{'slug':'account-a-model','visibility':'list'}]},
                              request=httpx.Request('GET',url))
    assert not [v for v in available_models(r,tmp_path,get=switched) if v['provider']=='chatgpt']


@pytest.mark.parametrize('same_profile', [False,True])
def test_new_account_or_authorization_generation_does_not_inherit_old_refusal_cooldown(same_profile):
    async def go():
        scope=['account-a/grant-one'];seen=[]
        async def headers():return {'Authorization':'Bearer '+scope[0].replace('/','-')}
        async def respond(request):
            seen.append(request.headers['authorization'])
            return httpx.Response(401,json={'error':{'message':'untrusted credential detail'}}) if len(seen)==1 else httpx.Response(200,content=wire(completed([TEXT])))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            r=Router(roles={'main':['chatgpt:authorized']},env={},client=client,
                     provider_auth={'chatgpt':(lambda:True,headers,lambda:scope[0])})
            first=[e async for e in r.stream('main',[Message.user('fixture')],[])]
            assert first[-1].kind=='error' and 'untrusted credential detail' not in first[-1].error
            scope[0]='account-a/grant-two' if same_profile else 'account-b/grant-one'
            second=[e async for e in r.stream('main',[Message.user('fixture')],[])]
            assert second[-1].kind=='done' and len(seen)==2
    asyncio.run(go())


def test_subscription_function_choice_and_unsupported_cache_option():
    async def headers():return {'authorization':'Bearer synthetic'}
    p=OpenAIResponsesProvider('https://api.openai.com/v1',subscription=True,header_resolver=headers)
    body=p.build_body([],TOOLS,model='authorized',tool_choice={'type':'function','function':{'name':'lookup'}},
                      prompt_cache_retention='24h',temperature=1,max_tokens=2)
    assert body['tool_choice']=={'type':'function','name':'lookup','namespace':'exo'}
    assert not {'prompt_cache_retention','temperature','max_output_tokens'} & body.keys()


@pytest.mark.parametrize('status', [429,503])
@pytest.mark.parametrize('same_profile', [False,True])
def test_new_grant_clears_old_quota_and_route_breaker_without_clock_advancing(status,same_profile):
    async def go():
        scope=['account-a/grant-one'];healthy=[False];seen=[]
        async def headers():return {'Authorization':'Bearer '+scope[0].replace('/','-')}
        async def respond(request):
            seen.append(request.headers['authorization'])
            return httpx.Response(status,json={'error':{'message':'fixture'}}) if not healthy[0] else httpx.Response(200,content=wire(completed([TEXT])))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            r=Router(roles={'main':['chatgpt:authorized']},env={},client=client,clock=lambda:100,
                     breaker={'failures':1},provider_auth={'chatgpt':(lambda:True,headers,lambda:scope[0])})
            first=[e async for e in r.stream('main',[Message.user('fixture')],[])]
            assert first[-1].kind=='error' and r.cooldowns
            r.cooldowns['unrelated']=object()  # resetting ChatGPT must not erase another provider's state
            scope[0]='account-a/grant-two' if same_profile else 'account-b/grant-one';healthy[0]=True
            second=[e async for e in r.stream('main',[Message.user('fixture')],[])]
            assert second[-1].kind=='done' and len(seen)==2 and 'unrelated' in r.cooldowns
    asyncio.run(go())


@pytest.mark.parametrize('old_fails', [False,True])
def test_late_old_grant_outcome_cannot_mutate_new_grant_breaker(old_fails):
    async def go():
        scope=['a/grant'];entered=asyncio.Event();release=asyncio.Event();b_healthy=[old_fails]
        async def headers():return {'Authorization':'Bearer '+scope[0].replace('/','-')}
        class HeldOld(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield wire({'type':'response.output_text.delta','delta':'old account partial'})
                entered.set();await release.wait()
                yield wire({'type':'response.failed','response':{'error':{'code':'server_error'}}}) if old_fails else wire(completed([TEXT]))
        async def respond(request):
            if request.headers['authorization']=='Bearer a-grant':return httpx.Response(200,stream=HeldOld())
            return httpx.Response(200,content=wire(completed([TEXT]))) if b_healthy[0] else httpx.Response(503,json={'error':{'message':'fixture'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            r=Router(roles={'main':['chatgpt:authorized']},env={},client=client,clock=lambda:100,
                     breaker={'failures':1},provider_auth={'chatgpt':(lambda:True,headers,lambda:scope[0])})
            async def call():return [e async for e in r.stream('main',[Message.user('fixture')],[])]
            old=asyncio.create_task(call());await asyncio.wait_for(entered.wait(),2)
            scope[0]='b/grant';new=await call()
            assert new[-1].kind==('done' if old_fails else 'error')
            if not old_fails:assert r.breakers['chatgpt:authorized'].level==1
            outcomes=dict(r.cooldowns);release.set();older=await asyncio.wait_for(old,2)
            assert older[0].text=='old account partial' and older[-1].kind==('error' if old_fails else 'done')
            assert r.cooldowns==outcomes
            if old_fails:
                assert 'chatgpt:authorized' not in r.breakers
                b_healthy[0]=False;assert (await call())[-1].kind=='error'
            assert r.breakers['chatgpt:authorized'].level==1
    asyncio.run(go())
