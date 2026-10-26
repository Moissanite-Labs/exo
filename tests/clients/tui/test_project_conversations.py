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


@pytest.mark.parametrize('base', [
    'exo resume --session old --resume --cwd /old --title old --sk',
    'exo-canary resume /old --session=old --res --cw=/old --ti old --skip',
    'exo-projects --session=old --title=old --dangerously-skip-permissions',
])
def test_custom_new_command_is_fresh_and_drops_permission_abbreviations(base, tmp_path):
    command = new_command(str(tmp_path), {'EXO_RESTART_CMD': base})
    assert command[-2:] == ['--cwd', str(tmp_path)]
    assert all(arg not in command for arg in ('resume', 'old', '--resume', '--res', '--sk', '--skip',
                                              '--dangerously-skip-permissions'))
    assert not any(arg.startswith(('--session', '--title', '--ti', '--cw=')) for arg in command)


def test_fallback_new_command_filters_abbreviated_flags(tmp_path):
    command = new_command(str(tmp_path), {}, ['exo', 'resume', '--sess', 'old', '--res', '--ti=old', '--sk'])
    assert command[-2:] == ['--cwd', str(tmp_path)]
    assert 'resume' not in command and 'old' not in command and '--sk' not in command


def test_remote_cwd_is_not_validated_on_the_client():
    assert parse(['--url', 'http://remote', '--cwd', '/remote-only/project']).cwd == '/remote-only/project'


def test_local_default_is_window_cwd_and_remote_attachments_stay_local(tmp_path, monkeypatch):
    local = tmp_path / 'local'
    local.mkdir()
    (local / 'note.txt').write_text('client local file')
    monkeypatch.chdir(local)
    app = ExoApp(object())
    assert app.new_cwd == str(local.resolve())
    remote = ExoApp(object(), remote=True, cwd='/remote-only/new-project')
    remote.set_session_cwd('/remote-only/saved-project')
    assert remote.new_cwd == '/remote-only/new-project'
    assert remote.files.cwd == local
    assert remote.file_parts('@note.txt')[0].text.endswith('client local file')


def test_new_local_window_without_cwd_does_not_inherit_engine_directory(tmp_path, monkeypatch):
    core_dir, project = tmp_path / 'core', tmp_path / 'project'
    core_dir.mkdir(); project.mkdir()
    eng = make_engine(core_dir)
    monkeypatch.chdir(project)

    async def exercise():
        app = ExoApp(FakeServerClient(eng), seat='tester')
        async with app.run_test(size=(120, 40)):
            await asyncio.wait_for(app.ready.wait(), 5)
            assert eng.store.get_session(app.session).cwd == str(project.resolve())
    asyncio.run(exercise())


def test_slow_tab_opener_does_not_block_event_loop(tmp_path, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()

    def slow_open(cmd):
        started.set()
        assert release.wait(2)
        return 'opened'

    async def exercise():
        eng = make_engine(tmp_path)
        app = ExoApp(FakeServerClient(eng), seat='tester', cwd=str(tmp_path))
        async with app.run_test(size=(120, 40)):
            await asyncio.wait_for(app.ready.wait(), 5)
            monkeypatch.setattr('exo_tui.restart.open_tab', slow_open)
            task = asyncio.create_task(app.slash('tab', str(tmp_path)))
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(.001)
                assert started.is_set() and not task.done()
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
            assert task.result() is None
    asyncio.run(exercise())


def test_old_session_reports_effective_core_cwd(tmp_path):
    from exo.protocol import schema as s
    eng = make_engine(tmp_path)

    async def exercise():
        old = eng.store.create_session(agent=eng.agent)
        result = await eng._session_get(s.SessionGet(session=old.id))
        assert result['session']['cwd'] == str(tmp_path)
        await eng.close()
    asyncio.run(exercise())


def test_unknown_legacy_local_cwd_disables_relative_attachments(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'note.txt').write_text('do not assume this is the server folder')
    app = ExoApp(object())
    app.set_session_cwd(None)
    assert app.file_parts('@note.txt') == []


def test_unchanged_directory_keeps_file_index(tmp_path):
    app = ExoApp(object())
    app.set_session_cwd(str(tmp_path))
    index = app.files
    app.set_session_cwd(str(tmp_path))
    assert app.files is index


def test_remote_tab_accepts_a_server_only_directory(tmp_path, monkeypatch):
    async def exercise():
        app = ExoApp(FakeServerClient(make_engine(tmp_path)), seat='tester', remote=True)
        async with app.run_test(size=(120, 40)):
            await asyncio.wait_for(app.ready.wait(), 5)
            local_files = app.files
            opened = []
            monkeypatch.setattr('exo_tui.restart.open_tab', lambda cmd: opened.append(cmd) or 'opened')
            await app.slash('tab', '/server-only/project')
            assert opened[0][-2:] == ['--cwd', '/server-only/project']
            assert app.files is local_files
    asyncio.run(exercise())
