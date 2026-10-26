import asyncio

import pytest

from exo_tui import parse
from exo_tui.app import ExoApp
from test_tui import FakeServerClient, make_engine, send, until


def test_parse_rounds():
    assert parse(['--max-rounds', '0']).max_rounds == 0
    assert parse(['--max-rounds', '60']).max_rounds == 60
    with pytest.raises(SystemExit):
        parse(['--max-rounds', '-1'])


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
            await app.slash('rounds', 'default')
            assert app.max_rounds is None
    asyncio.run(go())
