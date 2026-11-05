"""Bare /permissions full targets the selected real session, only on a capable core."""
import asyncio
from types import SimpleNamespace

import pytest

from exo.protocol import schema as s
from exo_tui.app import ExoApp


class Client:
    def __init__(self, supported=True, available=True, missing=False):
        self.calls = []
        self.supported, self.available, self.missing = supported, available, missing
    async def call(self, body):
        self.calls.append(body)
        if isinstance(body, s.CommandRun):
            assert body.args == 'json'  # this probe must never mutate permissions
            return {'output': {'supports_full': self.supported, 'full_available': self.available}}
        return {'session': {'id': 'other' if self.missing else body.session}}


def test_bare_full_expands_selected_session_after_capability_and_snapshot():
    client = Client()
    assert asyncio.run(ExoApp.full_permissions_args(SimpleNamespace(session='s1', client=client))) == 'full s1'
    assert [type(body) for body in client.calls] == [s.CommandRun, s.SessionGet]


@pytest.mark.parametrize('session,supported,available,missing', [(None, True, True, False), ('s1', False, True, False), ('s1', True, False, False), ('s1', True, True, True)])
def test_unavailable_or_unselected_session_cannot_prepare_full(session, supported, available, missing):
    client = Client(supported, available, missing)
    with pytest.raises(ValueError):
        asyncio.run(ExoApp.full_permissions_args(SimpleNamespace(session=session, client=client)))
    assert all(not isinstance(body, s.CommandRun) or body.args == 'json' for body in client.calls)


def test_tui_bare_full_command_and_visible_scope_use_selected_thread(tmp_path):
    from test_permissions_banner import run, until, strip_text
    from exo_tui.widgets import Composer
    async def scenario(app, pilot, engine):
        state = {'mode': 'ask', 'supports_full': True, 'full_available': True}
        calls = []
        def command(args):
            if args == 'json':
                return dict(state)
            calls.append(args)
            if args.startswith('full '):
                state.update(mode='full', full_session=args.split()[1])
            elif args == 'ask':
                state.update(mode='ask', full_session=None)
            return 'permissions: ' + args
        engine.plugins.commands['permissions'].handler = command
        selected = app.session
        app.query_one(Composer).text = '/permissions full'
        await pilot.press('enter')
        await until(lambda: app.perm.get('mode') == 'full', pilot)
        assert calls == ['full ' + selected]
        assert strip_text(app).startswith('permissions: full only ' + selected)
        assert 'other threads ask' in app.status_text()
        app.query_one(Composer).text = '/permissions ask'
        await pilot.press('enter')
        await until(lambda: app.perm.get('mode') == 'ask', pilot)
        assert 'permissions:' not in strip_text(app)
    run(tmp_path, scenario)


def test_full_access_flag_is_explicit_and_separate_from_bypass():
    from exo_tui import parse
    assert parse(['--full-access', '--resume']).full_access
    assert not parse([]).full_access
    assert parse(['--skip-permissions']).skip_permissions
    with pytest.raises(SystemExit):
        parse(['--full-access', '--skip-permissions'])


@pytest.mark.parametrize('supported', [True, False])
def test_full_access_boot_uses_one_selected_session_and_never_inherits_to_new_thread(tmp_path, supported):
    from test_permissions_banner import engine, strip_text
    core = engine(tmp_path)
    calls = []
    state = {'mode': 'ask', 'supports_full': supported, 'full_available': True}
    def command(args):
        if args == 'json':
            return dict(state)
        calls.append(args)
        state.update(mode='full', full_session=args.split()[1])
        return 'permissions: ' + args
    core.plugins.commands['permissions'].handler = command
    from exo_tui.conn import LocalClient
    async def scenario():
        app = ExoApp(LocalClient(core), full_access=True)
        async with app.run_test(size=(140, 40)):
            await asyncio.wait_for(app.ready.wait(), 5)
            selected = app.session
            if supported:
                assert calls == ['full ' + selected]
                assert strip_text(app).startswith('permissions: full only ' + selected)
                await app.new_session()
                assert app.session != selected and calls == ['full ' + selected]
                assert app.perm['full_session'] == selected
            else:
                assert not calls and 'does not support session-scoped full' in app.transcript_text()
    asyncio.run(scenario())


def test_explicit_full_all_requires_its_own_capability_and_no_selected_session():
    client = Client()
    with pytest.raises(ValueError, match='all sessions'):
        asyncio.run(ExoApp.full_permissions_args(SimpleNamespace(session='s1', client=client), all_sessions=True))
    async def capable(body):
        client.calls.append(body)
        assert isinstance(body, s.CommandRun) and body.args == 'json'
        return {'output': {'supports_full': True, 'supports_full_all': True, 'full_available': True}}
    client.call = capable
    assert asyncio.run(ExoApp.full_permissions_args(SimpleNamespace(session=None, client=client), all_sessions=True)) == 'full all'
