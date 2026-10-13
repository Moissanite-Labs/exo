"""Offline admission tests. Mock HTTP at the real adapter boundary; no provider keys."""
import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from exo.core.models.router import Router, aux
from exo.core.models.spend import SpendDenied, SpendJournal, configured
from exo.core.models.decision import ask_choice
from exo.core.types import Message, Usage
from .conftest import collect


@pytest.fixture(autouse=True)
def synthetic_native_contract(monkeypatch):
    # This fake-price model exists only in test code. Production config cannot add native bounds.
    from exo.core.models import spend
    monkeypatch.setattr(spend, 'NATIVE_BOUNDS', {
        **spend.NATIVE_BOUNDS,
        ('openai', 'https://api.openai.com/v1', 'm'): (1000, 1000),
        ('anthropic', 'https://api.anthropic.com', 'claude-opus-5-5'): (8000, 128000),
    })


def policy(tmp_path, **overrides):
    path = tmp_path / 'spend.sqlite'
    identity = SpendJournal.provision(path, 'alice')
    return dict(journal=str(path), journal_id=identity, principal='alice', daily_usd='0.005',
                monthly_usd='0.01', routes={'test:m': dict(contract='text-tokens-v1',
                base_url='https://api.openai.com/v1', model='m', transport='openai',
                input_bound=1000, max_output=100, input=1, output=10, cache_read=1, cache_write=1)},
                **overrides)


def response(input=100, output=10, cost=99, tool=False):
    delta = {'tool_calls': [{'index': 0, 'id': 't1', 'function': {'name': 'run', 'arguments': '{}'}}]} if tool else {'content': 'hi'}
    data = {'choices': [{'delta': delta, 'finish_reason': 'tool_calls' if tool else 'stop'}],
            'usage': {'prompt_tokens': input, 'completion_tokens': output, 'cost': cost}}
    return httpx.Response(200, text='data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n')


def router(cfg, handler):
    return Router.from_config({'spend': cfg, 'roles': {'main': ['test:m'], 'aux': ['test:m'], 'decision': ['test:m']},
                              'providers': {'test': {'transport': 'openai', 'base_url': 'https://api.openai.com/v1'}}},
                              catalog={}, env={}, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def call(r, **opts):
    return collect(r.stream('main', [Message.user('hi')], [], **opts))


def rows(cfg):
    with sqlite3.connect(cfg['journal']) as db:
        return db.execute('SELECT reserved, actual FROM calls').fetchall()


def test_reserve_before_send_exact_body_and_immutable_price(tmp_path):
    cfg = policy(tmp_path)
    seen = []
    def handle(request):
        seen.append(json.loads(request.content))
        assert rows(cfg) == [(2_000_000, None)]
        r.models['test:m'] = {'input': 0, 'output': 0}
        cfg['routes']['test:m']['output'] = 0
        return response()
    r = router(cfg, handle)
    events = call(r)
    assert seen[0]['max_completion_tokens'] == 100
    assert events[-1].kind == 'done' and events[-1].usage.cost_usd == .0002
    assert rows(cfg) == [(2_000_000, 200_000)]


@pytest.mark.parametrize('field', ['daily_usd', 'monthly_usd'])
def test_cap_denial_zero_calls_no_fallback(tmp_path, field):
    cfg = policy(tmp_path); cfg[field] = '.001'
    seen = []
    r = router(cfg, lambda req: seen.append(req) or response())
    r.roles['main'].append('test:backup')
    event = call(r)[-1]
    assert event.kind == 'error' and not event.retryable and not seen and rows(cfg) == []
    assert 'principal' in event.error


@pytest.mark.parametrize('change', ['unknown', 'max_output', 'endpoint', 'wire_model', 'image'])
def test_unbounded_requests_fail_before_http(tmp_path, change):
    from exo.core.types import ContentPart
    cfg = policy(tmp_path); seen = []
    if change == 'unknown': cfg['routes'] = {}
    if change == 'endpoint': cfg['routes']['test:m']['base_url'] = 'https://elsewhere'
    if change == 'wire_model': cfg['routes']['test:m']['model'] = 'different'
    r = router(cfg, lambda req: seen.append(req) or response())
    if change == 'image':
        ev = collect(r.stream('main', [Message(role='user', content=[ContentPart(type='image', url='https://image')])], []))
    else:
        ev = call(r, **({'max_tokens': 101} if change == 'max_output' else {}))
    assert ev[-1].kind == 'error' and not seen


def test_concurrent_process_connections_cannot_overreserve(tmp_path):
    cfg = policy(tmp_path)
    def take(_):
        j = SpendJournal(cfg); p = j.prices['test:m']
        try: return j.reserve('test:m', '', p.maximum(100), p)
        except SpendDenied: return None
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(take, range(20)))
    assert sum(x is not None for x in results) == 2
    assert sum(r[0] for r in rows(cfg)) == 4_000_000


def test_pending_survives_restart_and_utc_rollover(tmp_path):
    cfg = policy(tmp_path); cfg['daily_usd'] = '.002'
    j = SpendJournal(cfg, clock=lambda: 0); p = j.prices['test:m']
    j.reserve('test:m', '', p.maximum(100), p)
    resumed = SpendJournal(cfg, clock=lambda: 86400 * 400)
    with pytest.raises(SpendDenied): resumed.reserve('test:m', '', 1, p)


@pytest.mark.parametrize('mode', ['error', 'missing', 'exceeded', 'exception'])
def test_uncertain_usage_keeps_reservation_freezes_and_never_retries(tmp_path, mode):
    cfg = policy(tmp_path); seen = []
    def handle(req):
        seen.append(req)
        if mode == 'exception': raise httpx.ReadTimeout('offline')
        if mode == 'error': return httpx.Response(500)
        if mode == 'exceeded': return response(output=101)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"x"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    r = router(cfg, handle)
    assert call(r)[-1].kind == 'error'
    assert len(seen) == 1 and rows(cfg) == [(2_000_000, None)]
    assert call(r)[-1].kind == 'error' and len(seen) == 1
    with pytest.raises(SpendDenied): SpendJournal(cfg)


def test_cancellation_retains_durable_reservation(tmp_path):
    cfg = policy(tmp_path)
    async def run():
        entered = asyncio.Event()
        async def handle(req):
            entered.set()
            await asyncio.Future()
        r = router(cfg, handle)
        async def consume():
            return [e async for e in r.stream('main', [Message.user('hi')], [])]
        task = asyncio.create_task(consume())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        await asyncio.sleep(0)
    asyncio.run(run())
    assert rows(cfg) == [(2_000_000, None)]
    with pytest.raises(SpendDenied): SpendJournal(cfg)


def test_missing_replaced_corrupt_journals_never_initialize(tmp_path):
    cfg = policy(tmp_path)
    from pathlib import Path
    path = Path(cfg['journal']); path.unlink()
    with pytest.raises(sqlite3.OperationalError): SpendJournal(cfg)
    assert not path.exists()
    SpendJournal.provision(path, 'alice')
    with pytest.raises(SpendDenied): SpendJournal(cfg)
    path.write_text('corrupt')
    with pytest.raises(sqlite3.DatabaseError): SpendJournal(cfg)
    with pytest.raises(FileExistsError): SpendJournal.provision(path, 'alice')


def test_invalid_policy_and_required_missing_fail_closed(tmp_path):
    with pytest.raises(SpendDenied): configured(None, required=True)
    cfg = policy(tmp_path)
    for value in ['NaN', '-1', 'Infinity']:
        cfg['daily_usd'] = value
        with pytest.raises(ValueError): SpendJournal(cfg)


def test_aux_and_generative_decisions_share_journal(tmp_path):
    cfg = policy(tmp_path)
    cfg['routes']['test:m']['max_output'] = 200
    r = router(cfg, lambda req: response())
    assert asyncio.run(aux(r, 'summarize')) == 'hi'
    assert len(rows(cfg)) == 1
    # Generative decision parses after admission; both calls remain accounted.
    with pytest.raises(Exception): asyncio.run(ask_choice(r, 's', 'q', ['a', 'b']))
    assert len(rows(cfg)) >= 2


def test_typed_decision_and_live_setup_probe_refused(tmp_path):
    from exo.providers_setup import test_provider as probe
    cfg = policy(tmp_path); seen = []
    r = router(cfg, lambda req: seen.append(req) or response())
    r.providers['typed'] = {'transport': 'decision', 'base_url': 'https://typed'}
    r.roles['decision'] = ['typed:m']
    with pytest.raises(SpendDenied): asyncio.run(ask_choice(r, 's', 'q', ['a', 'b']))
    assert probe('test', router=r, save=False)['error_class'] == 'spend_boundary'
    assert not seen


def test_cache_and_reasoning_bound_in_pinned_price(tmp_path):
    cfg = policy(tmp_path); p = SpendJournal(cfg).prices['test:m']
    assert p.actual(Usage(input_tokens=100, output_tokens=10, reasoning_tokens=5, cache_read_tokens=50), 100, True) == 200_000
    with pytest.raises(SpendDenied): p.actual(Usage(input_tokens=1, output_tokens=1, reasoning_tokens=2), 100, True)
    with pytest.raises(SpendDenied): p.actual(Usage(input_tokens=1, output_tokens=1, cache_read_tokens=2), 100, True)


@pytest.mark.parametrize('mode', ['cap', 'usage'])
def test_loop_denial_runs_zero_tools_and_does_not_retry(tmp_path, mode):
    from exo.core.loop import Loop
    from exo.core.sessions import SessionStore
    from exo.core.types import ToolSpec
    cfg = policy(tmp_path); calls, tools = [], []
    if mode == 'cap': cfg['daily_usd'] = '0'
    def handle(req):
        calls.append(req)
        return response(output=101, tool=True)
    r = router(cfg, handle)
    loop = Loop(SessionStore(tmp_path / 'sessions.sqlite'), r.provider(),
                tool_specs=[ToolSpec('run', 'run tool')], run_tool=lambda *args: tools.append(args), retry_delay=0)
    async def run():
        session = await loop.create_session()
        return await loop.send(session.id, 'do it')
    result = asyncio.run(run())
    assert result.error and not tools and len(calls) == (0 if mode == 'cap' else 1)


def test_background_summary_and_child_role_share_principal_cap(tmp_path):
    from exo.core.context import DefaultCompressor
    cfg = policy(tmp_path); cfg['daily_usd'] = '.0021'; seen = []
    r = router(cfg, lambda req: seen.append(req) or response())
    r.roles['delegate'] = ['test:m']
    assert asyncio.run(DefaultCompressor(r.provider('aux')).summarize('long history', session='background')) == 'hi'
    ev = collect(r.provider('delegate').stream([Message.user('child task')], [], session_id='child'))
    assert ev[-1].kind == 'error' and len(seen) == 1
    with sqlite3.connect(cfg['journal']) as db:
        assert db.execute('SELECT session FROM calls').fetchall() == [('background',)]


def test_operator_review_never_discards_usage_or_raises_caps(tmp_path):
    cfg = policy(tmp_path); r = router(cfg, lambda req: httpx.Response(500))
    call(r)
    with pytest.raises(SpendDenied): SpendJournal.operator_review(cfg, resume=True)
    with sqlite3.connect(cfg['journal']) as db:
        token = db.execute('SELECT id FROM calls').fetchone()[0]
    # Explicit confirmation can account more than the original bound, but cannot permit more spend.
    SpendJournal.operator_review(cfg, settlements={token: '.01'}, resume=True)
    j = SpendJournal(cfg); p = j.prices['test:m']
    with pytest.raises(SpendDenied): j.reserve('test:m', '', p.maximum(100), p)
    assert rows(cfg) == [(2_000_000, 10_000_000)]


def test_monthly_accumulates_across_days_and_settlement_covers_both_periods(tmp_path):
    cfg = policy(tmp_path); cfg['monthly_usd'] = '.003'
    now = [0]
    j = SpendJournal(cfg, clock=lambda: now[0]); p = j.prices['test:m']
    token = j.reserve('test:m', '', p.maximum(100), p)
    now[0] = 86400
    j.settle(token, 2_000_000)
    with pytest.raises(SpendDenied): j.reserve('test:m', '', p.maximum(100), p)
    assert len(rows(cfg)) == 1  # no duplicate counting within one month


def test_anthropic_cache_writes_and_prepared_max_tokens(tmp_path):
    from .conftest import fixture_bytes
    cfg = policy(tmp_path)
    cfg['daily_usd'] = cfg['monthly_usd'] = 1
    cfg['routes'] = {'test:claude-opus-5-5': dict(contract='text-tokens-v1',
        base_url='https://api.anthropic.com', model='claude-opus-5-5', transport='anthropic',
        input_bound=8000, max_output=1000, input=1, output=10, cache_read='.1', cache_write='1.25')}
    seen = []
    def handle(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, content=fixture_bytes('anthropic_tool_stream.sse'))
    r = Router.from_config({'spend': cfg, 'roles': {'main': ['test:claude-opus-5-5']},
        'providers': {'test': {'transport': 'anthropic', 'base_url': 'https://api.anthropic.com'}}},
        catalog={}, env={}, client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    event = call(r)[-1]
    assert event.kind == 'done' and seen[0]['max_tokens'] == 1000
    assert rows(cfg)[0][1] is not None


def test_required_direct_router_cannot_bypass_missing_policy(monkeypatch):
    from exo.core.models.fake import FakeProvider
    fake = FakeProvider(['should not run'])
    r = Router(roles={'main': ['fake:m']}, catalog={}, env={}, instances={'fake': fake})
    monkeypatch.setenv('EXO_MODEL_SPEND_REQUIRED', '1')
    assert call(r)[-1].kind == 'error' and not fake.calls


def test_backward_clock_cannot_reopen_old_period(tmp_path):
    cfg = policy(tmp_path)
    now = [86400 * 35]
    j = SpendJournal(cfg, clock=lambda: now[0]); p = j.prices['test:m']
    token = j.reserve('test:m', '', p.maximum(100), p)
    j.settle(token, 200_000)
    now[0] = 1
    restarted = SpendJournal(cfg, clock=lambda: now[0])
    with pytest.raises(SpendDenied, match='clock moved backwards'):
        restarted.reserve('test:m', '', 1, p)


def test_month_boundary_charged_once_in_each_month(tmp_path):
    cfg = policy(tmp_path); cfg['monthly_usd'] = '.003'
    now = [86400 * 30]  # Jan 31, 1970
    j = SpendJournal(cfg, clock=lambda: now[0]); p = j.prices['test:m']
    token = j.reserve('test:m', '', p.maximum(100), p)
    now[0] += 86400  # February
    j.settle(token, 2_000_000)
    with pytest.raises(SpendDenied, match='month'):
        j.reserve('test:m', '', p.maximum(100), p)
    assert len(rows(cfg)) == 1


@pytest.mark.parametrize('case', ['input', 'output', 'unknown', 'headers', 'session_header'])
def test_native_context_cannot_be_underreserved_or_expanded(tmp_path, case):
    cfg = policy(tmp_path); seen = []
    p = cfg['routes'].pop('test:m')
    p.update(model='gpt-4.1-2025-04-14', input_bound=1_047_576, max_output=100)
    cfg['routes']['test:gpt-4.1-2025-04-14'] = p
    if case == 'input': p['input_bound'] = 1000
    if case == 'output': p['max_output'] = 32_769
    if case == 'unknown': p['model'] = 'unreviewed-alias'
    r = router(cfg, lambda req: seen.append(req) or response())
    r.roles['main'] = ['test:gpt-4.1-2025-04-14']
    if case == 'headers': r.providers['test']['headers'] = {'anthropic-beta': 'new-context'}
    if case == 'session_header': r.providers['test']['session_header'] = 'custom-header'
    event = call(r)[-1]
    assert event.kind == 'error' and not seen and not rows(cfg)
    if case == 'input': assert 'full native context' in event.error


def test_required_decision_missing_policy_is_a_hard_gate_refusal(monkeypatch):
    from exo.core.models.fake import FakeProvider
    fake = FakeProvider(['should not run'])
    r = Router(roles={'decision': ['fake:m']}, catalog={}, env={}, instances={'fake': fake})
    monkeypatch.setenv('EXO_MODEL_SPEND_REQUIRED', '1')
    with pytest.raises(SpendDenied): asyncio.run(ask_choice(r, 's', 'q', ['a', 'b']))
    assert not fake.calls
