"""Real native bounds, mock HTTP only: ambiguous Anthropic billing never releases headroom."""
import json
import sqlite3

import httpx
import pytest

from exo.core.models.router import Router
from exo.core.types import Message
from exo.model_spend import SpendJournal

from .conftest import collect


def stream_response(start, deltas):
    events = [{'type': 'message_start', 'message': {'usage': start}},
              {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': 'hi'}},
              {'type': 'content_block_stop', 'index': 0},
              *({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': delta} for delta in deltas),
              {'type': 'message_stop'}]
    return httpx.Response(200, text=''.join('data: ' + json.dumps(event) + '\n\n' for event in events))


def native_router(tmp_path, start, deltas):
    journal = tmp_path / 'spend.sqlite'
    route = 'anthropic:claude-opus-5-5'
    policy = dict(journal=str(journal), journal_id=SpendJournal.provision(journal, 'alice'), principal='alice',
                  daily_usd='50', monthly_usd='100', routes={route: dict(contract='text-tokens-v1',
                  base_url='https://api.anthropic.com', model='claude-opus-5-5', transport='anthropic',
                  input_bound=1_000_000, max_output=100, input=1, output=10, cache_read='.1', cache_write='1.25')})
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return stream_response(start, deltas)
    router = Router.from_config({'spend': policy, 'roles': {'main': [route]},
        'providers': {'anthropic': {'transport': 'anthropic', 'base_url': 'https://api.anthropic.com', 'key_env': []}}},
        catalog={}, env={}, client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    return router, journal, requests


def call(router):
    return collect(router.stream('main', [Message.user('hi')], []))


def complete_start(**changes):
    return dict(input_tokens=500_000, output_tokens=1, cache_read_input_tokens=0,
                cache_creation_input_tokens=0) | changes


@pytest.mark.parametrize('case', ['missing_cache', 'lower_input'])
def test_review_probes_retain_full_native_reservation(tmp_path, case):
    start = {'input_tokens': 100_000, 'output_tokens': 1} if case == 'missing_cache' else complete_start()
    delta = {'output_tokens': 10} | ({'input_tokens': 1} if case == 'lower_input' else {})
    router, journal, requests = native_router(tmp_path, start, [delta])
    assert_frozen(router, journal, requests)


def assert_frozen(router, journal, requests):
    events = call(router)
    assert events[-1].kind == 'error' and not any(e.kind == 'done' for e in events)
    assert events[-1].attempts[0]['outcome'] == 'spend_stopped'
    assert not events[-1].retryable and router.spend.status()['state'] == 'stopped'
    with sqlite3.connect(journal) as db:
        assert db.execute('SELECT reserved, actual FROM calls').fetchall() == [(1_251_000_000, None)]
    assert 'cache_control' in json.dumps(requests[0])
    assert call(router)[-1].attempts[0]['outcome'] == 'spend_stopped'
    assert len(requests) == 1


@pytest.mark.parametrize('field', ['input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens'])
@pytest.mark.parametrize('value', ['missing', None, False, -1, '0'])
def test_missing_or_invalid_input_counter_cannot_be_completed_by_delta(tmp_path, field, value):
    start = complete_start()
    if value == 'missing':
        del start[field]
    else:
        start[field] = value
    router, journal, requests = native_router(tmp_path, start, [{'output_tokens': 10}, complete_start(output_tokens=11)])
    assert_frozen(router, journal, requests)


@pytest.mark.parametrize('delta', [{'input_tokens': 0}, {'input_tokens': 500_001},
    {'input_tokens': None}, {'cache_read_input_tokens': 5}, {'cache_creation_input_tokens': 5},
    {'cache_read_input_tokens': False}, {'output_tokens': 2}, {'output_tokens': None}])
def test_contradictory_cumulative_usage_stays_ambiguous_after_later_valid_delta(tmp_path, delta):
    router, journal, requests = native_router(tmp_path, complete_start(),
        [{'output_tokens': 10}, {'output_tokens': 11} | delta, {'output_tokens': 12}])
    assert_frozen(router, journal, requests)


@pytest.mark.parametrize('echo', [False, True])
@pytest.mark.parametrize('read,write,actual', [(0, 0, 500_100_000), (20, 30, 500_139_500)])
def test_complete_cache_usage_settles_exactly_with_monotonic_output(tmp_path, echo, read, write, actual):
    start = complete_start(cache_read_input_tokens=read, cache_creation_input_tokens=write)
    delta = {'output_tokens': 10} | ({k: v for k, v in start.items() if k != 'output_tokens'} if echo else {})
    router, journal, requests = native_router(tmp_path, start, [{'output_tokens': 5}, delta])
    event = call(router)[-1]
    assert event.kind == 'done' and event.usage.input_tokens == 500_000
    assert event.usage.cache_read_tokens == read and event.usage.cache_write_tokens == write
    with sqlite3.connect(journal) as db:
        assert db.execute('SELECT reserved, actual FROM calls').fetchall() == [(1_251_000_000, actual)]
    assert router.spend.status()['state'] == 'ready' and len(requests) == 1


def test_repeated_start_cannot_reuse_previous_complete_delta(tmp_path, monkeypatch):
    original = stream_response
    def repeat(start, deltas):
        response = original(start, deltas)
        extra = 'data: ' + json.dumps({'type': 'message_start', 'message': {'usage': complete_start(input_tokens=1)}}) + '\n\n'
        return httpx.Response(200, text=response.text.replace('data: {"type": "message_stop"}', extra + 'data: {"type": "message_stop"}'))
    monkeypatch.setattr(f'{__name__}.stream_response', repeat)
    router, journal, requests = native_router(tmp_path, complete_start(), [{'output_tokens': 10}])
    assert_frozen(router, journal, requests)


def test_repeated_start_followed_by_valid_delta_cannot_reset_usage(tmp_path, monkeypatch):
    """A final delta cannot make a second start's cheaper counters trustworthy."""
    original = stream_response
    def repeat_then_complete(start, deltas):
        response = original(start, deltas)
        repeated = [
            {'type': 'message_start', 'message': {'usage': complete_start(input_tokens=1)}},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 20}},
        ]
        extra = ''.join('data: ' + json.dumps(event) + '\n\n' for event in repeated)
        return httpx.Response(200, text=response.text.replace(
            'data: {"type": "message_stop"}', extra + 'data: {"type": "message_stop"}'))
    monkeypatch.setattr(f'{__name__}.stream_response', repeat_then_complete)
    router, journal, requests = native_router(tmp_path, complete_start(), [{'output_tokens': 10}])
    assert_frozen(router, journal, requests)
