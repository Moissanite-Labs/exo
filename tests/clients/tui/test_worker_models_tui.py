"""Real Textual controls use the core policy API without sending a model turn."""
from textual.widgets import Button, Input, Static

from test_tui import run, send, until
from exo.config import Config
from exo_tui.worker_models import WorkerModelsScreen


def test_worker_form_saves_live_and_persistent_routes(tmp_path):
    async def scenario(app, pilot, client, eng):
        await send(app, pilot, '/workers')
        await until(lambda: isinstance(app.screen, WorkerModelsScreen) and app.screen.state is not None, pilot)
        screen = app.screen
        screen.query_one('#worker-fast', Input).value = 'fake:quick, fake:backup'
        screen.query_one('#worker-standard', Input).value = 'fake:normal'
        screen.query_one('#worker-deep', Input).value = 'fake:think'
        screen.query_one('#worker-save', Button).press()
        await until(lambda: 'Saved and applied' in str(screen.query_one('#worker-model-status', Static).render()), pilot)
        assert eng.router.roles['fast'] == ['fake:quick', 'fake:backup']
        assert Config.load(eng.home).get('models.roles.deep') == ['fake:think']
        assert eng.loop.model == 'fake:x' and not app.busy
        await pilot.press('escape')
        assert not isinstance(app.screen, WorkerModelsScreen)
    run(tmp_path, scenario)


def test_stale_worker_draft_stays_visible_with_reload_hint(tmp_path):
    async def scenario(app, pilot, client, eng):
        await send(app, pilot, '/workers')
        await until(lambda: isinstance(app.screen, WorkerModelsScreen) and app.screen.state is not None, pilot)
        screen = app.screen
        screen.query_one('#worker-fast', Input).value = 'fake:draft'
        eng.router.set_role('fast', ['fake:other-window'])
        screen.query_one('#worker-save', Button).press()
        await until(lambda: 'Settings changed' in str(screen.query_one('#worker-model-status', Static).render()), pilot)
        assert screen.query_one('#worker-fast', Input).value == 'fake:draft'
        assert eng.router.roles['fast'] == ['fake:other-window']
        screen.query_one('#worker-reload', Button).press()
        await until(lambda: screen.query_one('#worker-fast', Input).value == 'fake:other-window', pilot)
    run(tmp_path, scenario)


def test_worker_model_search_filters_and_keeps_fallback_order(tmp_path):
    from exo.protocol import schema as s
    from exo_tui.widgets import Palette
    async def scenario(app, pilot, client, eng):
        original = client.call
        async def call(body):
            if isinstance(body, s.ModelsList):
                return {'models': [], 'available': [{'route': 'fake:quick'}, {'route': 'fake:careful'}]}
            return await original(body)
        client.call = call
        await send(app, pilot, '/workers')
        await until(lambda: isinstance(app.screen, WorkerModelsScreen) and app.screen.state is not None, pilot)
        screen = app.screen
        screen.query_one('#worker-fast', Input).value = 'fake:old, fake:backup'
        screen.query_one('#worker-pick-fast', Button).press()
        await until(lambda: isinstance(app.screen, Palette), pilot)
        app.screen.query_one(Input).value = 'quick'
        await until(lambda: len(app.screen.shown) == 1, pilot)
        await pilot.press('enter')
        await until(lambda: app.screen is screen, pilot)
        assert screen.query_one('#worker-fast', Input).value == 'fake:quick, fake:backup'
        assert eng.router.roles.get('fast') != ['fake:quick', 'fake:backup']  # choice is only a draft
    run(tmp_path, scenario)
