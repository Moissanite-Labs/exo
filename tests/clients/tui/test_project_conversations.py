import asyncio

import pytest

from exo_tui import parse
from exo_tui.app import ExoApp
from exo_tui.restart import new_command
from test_tui import FakeServerClient, make_engine


def test_cwd_option_validates_local_directory(tmp_path):
    assert parse(['--cwd', str(tmp_path)]).cwd == str(tmp_path.resolve())
    with pytest.raises(SystemExit):
        parse(['--cwd', str(tmp_path / 'missing')])


def test_new_tab_command_drops_old_session_and_permission_flag(tmp_path):
    command = new_command(str(tmp_path), {}, ['exo', 'tui', '--seat', 'canary', '--session', 'old',
                                               '--cwd', '/old', '--skip-permissions'])
    assert command[-2:] == ['--cwd', str(tmp_path)]
    assert '--session' not in command and '--resume' not in command
    assert '--skip-permissions' not in command and '/old' not in command
    assert '--seat' in command


def test_new_and_resumed_sessions_use_their_project_for_files(tmp_path, monkeypatch):
    a, b = tmp_path / 'alpha', tmp_path / 'beta'
    a.mkdir(); b.mkdir()
    (a / 'note.txt').write_text('alpha project')
    (b / 'note.txt').write_text('beta project')
    eng = make_engine(tmp_path)

    async def exercise():
        app = ExoApp(FakeServerClient(eng), seat='tester', cwd=str(a))
        async with app.run_test(size=(120, 40)):
            await asyncio.wait_for(app.ready.wait(), 5)
            first = app.session
            assert eng.store.get_session(first).cwd == str(a)
            assert app.files.cwd == a
            assert 'alpha project' in app.file_parts('@note.txt')[0].text
            app.new_cwd = str(b)
            await app.new_session()
            second = app.session
            assert first != second and eng.store.get_session(second).cwd == str(b)
            assert app.files.cwd == b
            assert 'beta project' in app.file_parts('@note.txt')[0].text
            await app.load_session(first)
            assert app.files.cwd == a
            assert 'alpha project' in app.file_parts('@note.txt')[0].text
            opened = []
            monkeypatch.setattr('exo_tui.restart.open_tab', lambda cmd: opened.append(cmd) or 'opened')
            await app.slash('tab', str(b))
            assert opened[0][-2:] == ['--cwd', str(b)]
            assert app.session == first
            await app.slash('tab', str(tmp_path / 'missing'))
            assert len(opened) == 1

    asyncio.run(exercise())


def test_two_windows_keep_distinct_project_sessions(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir(); b.mkdir()
    eng = make_engine(tmp_path)

    async def exercise():
        first = ExoApp(FakeServerClient(eng), seat='tester', cwd=str(a))
        second = ExoApp(FakeServerClient(eng), seat='tester', cwd=str(b))
        async with first.run_test(size=(120, 40)):
            await asyncio.wait_for(first.ready.wait(), 5)
            async with second.run_test(size=(120, 40)):
                await asyncio.wait_for(second.ready.wait(), 5)
                assert first.session != second.session
                assert eng.store.get_session(first.session).cwd == str(a)
                assert eng.store.get_session(second.session).cwd == str(b)
                assert first.files.cwd == a and second.files.cwd == b

    asyncio.run(exercise())
