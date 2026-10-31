import asyncio
from exo.protocol import schema as s
from exo_tui.app import ExoApp
from test_tui import FakeServerClient, make_engine


def test_limits_is_core_command_not_chat_and_preserves_window_choice(tmp_path):
    async def go():
        eng = make_engine(tmp_path)
        app = ExoApp(FakeServerClient(eng),seat='tester',max_rounds=3)
        async with app.run_test(size=(120,40)):
            await asyncio.wait_for(app.ready.wait(),5)
            await app.slash('limits','rounds off')
            assert eng.run_limits.snapshot().rounds == 0 and app.max_rounds == 3
            await app.slash('limits','worker-timeout 15m')
            assert eng.run_limits.snapshot().worker_timeout_s == 900
            text=app.transcript_text()
            assert 'Core scope: all conversations and delegated workers' in text
            assert '900 seconds (15m)' in text and 'Resets on core restart' in text
            assert not any(isinstance(x,s.TurnSend) for x in app.sent)
            await app.slash('limits','default')
            assert eng.run_limits.snapshot().rounds is None
    asyncio.run(go())


def test_old_core_probe_cannot_mutate_or_send_as_chat(tmp_path):
    class Old(FakeServerClient):
        async def send(self,body):
            if isinstance(body,s.CommandRun) and body.name=='limits':
                assert body.args=='json'
                return s.Response(id='old',result={'output':'unknown old command'})
            return await super().send(body)
    async def go():
        app=ExoApp(Old(make_engine(tmp_path)),seat='tester')
        async with app.run_test(size=(120,40)):
            await asyncio.wait_for(app.ready.wait(),5)
            await app.slash('limits','rounds off')
            assert 'does not support /limits' in app.transcript_text()
            assert not any(isinstance(x,s.TurnSend) for x in app.sent)
    asyncio.run(go())


def test_window_unlimited_label_does_not_claim_to_override_core_limit(tmp_path):
    from exo_tui.widgets import StatusStrip

    async def go():
        eng = make_engine(tmp_path)
        app = ExoApp(FakeServerClient(eng), seat='tester')
        async with app.run_test(size=(120, 40)):
            await asyncio.wait_for(app.ready.wait(), 5)
            await app.slash('rounds', 'off')
            await app.slash('limits', 'rounds 100')
            assert app.max_rounds == 0
            assert eng.run_limits.snapshot().rounds_for(24, app.max_rounds) == 100
            assert 'window rounds: unlimited' in app.status_text()
            assert 'window rounds: unlimited' in app.query_one(StatusStrip).render().plain
            assert 'core /limits policy may override this window choice' in app.transcript_text()
            await app.slash('limits', '')
            assert '100' in app.transcript_text()
            assert not any(isinstance(x, s.TurnSend) for x in app.sent)
    asyncio.run(go())
