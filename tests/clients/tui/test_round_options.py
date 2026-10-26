import asyncio

import pytest

from exo_tui import parse
from exo_tui.app import ExoApp, ROUND_LIMIT_NOTE
from exo.protocol import schema as s
from test_tui import FakeServerClient, make_engine, send, until


def test_parse_rounds():
    assert parse(['--max-rounds', '0']).max_rounds == 0
    assert parse(['--max-rounds', '60']).max_rounds == 60
    assert parse(['--max-rounds', '1000000000']).max_rounds == 1000000000
    with pytest.raises(SystemExit):
        parse(['--max-rounds', '-1'])


def test_default_wire_request_remains_compatible_with_older_cores():
    assert 'max_rounds' not in s.Command.of(s.TurnSend(session='s')).to_dict()['body']
    assert s.Command.of(s.TurnSend(session='s', max_rounds=0)).to_dict()['body']['max_rounds'] == 0


def test_window_can_change_limit_for_future_turns(tmp_path):
    async def go():
        eng = make_engine(tmp_path)
        client = FakeServerClient(eng)
        app = ExoApp(client, seat='tester')
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await app.slash('rounds', 'off')
            assert app.max_rounds == 0
            await send(app, pilot, 'hello')
            await until(lambda: not app.busy and any(getattr(x, 'max_rounds', None) == 0 for x in app.sent), pilot)
            assert any(getattr(x, 'max_rounds', None) == 0 for x in app.sent)
            await app.slash('rounds', '32')
            assert app.max_rounds == 32
            assert ROUND_LIMIT_NOTE in app.transcript_text()
            await app.slash('rounds', 'default')
            assert app.max_rounds is None
    asyncio.run(go())


@pytest.mark.parametrize('limit', [0, 1000000000])
def test_explicit_boot_limit_explains_that_rounds_do_not_bound_time(tmp_path, limit):
    async def go():
        app = ExoApp(FakeServerClient(make_engine(tmp_path)), seat='tester', max_rounds=limit)
        async with app.run_test(size=(120, 40)):
            await asyncio.wait_for(app.ready.wait(), 5)
            assert ROUND_LIMIT_NOTE in app.transcript_text()
            assert ('rounds: unlimited' if limit == 0 else f'rounds: {limit}') in app.status_text()
    asyncio.run(go())
