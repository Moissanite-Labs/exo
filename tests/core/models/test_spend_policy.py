"""Protected selection and zero-paid offline operation; never connects to a provider.

Root metadata is simulated on temporary descriptors. Symlink traversal, JSON reads,
journal transactions and routed generation are real. OS acceptance tests own actual
root provisioning and the service/sandbox boundary.
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import httpx
import pytest

from exo.model_spend import policy as spend_policy
from exo.core.models.fake import FakeProvider, RuleFake
from exo.core.models.router import Router
from exo.model_spend import SpendDenied, SpendJournal
from exo.core.types import Message, ToolResult, ToolSpec
from .conftest import collect


@pytest.fixture
def root_metadata(monkeypatch):
    actual = os.fstat
    def fake(fd):
        values = list(actual(fd))
        values[0] &= ~0o022
        values[4] = 0
        return os.stat_result(values)
    monkeypatch.setattr(spend_policy.os, 'fstat', fake)
    return actual


@pytest.fixture
def protected(tmp_path, monkeypatch, root_metadata):
    journal = tmp_path / 'spend.sqlite'
    cfg = dict(principal='alice', journal=str(journal), journal_id=SpendJournal.provision(journal, 'alice'),
               daily_usd='0', monthly_usd='0', routes={})
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(cfg))
    monkeypatch.setenv('EXO_MODEL_SPEND_POLICY', str(path))
    monkeypatch.delenv('EXO_PRINCIPAL', raising=False)
    return cfg, path


def run(router):
    return collect(router.stream('main', [Message.user('go')], []))


def fake_router(fake=None, **kw):
    return Router(roles={'main': ['fake:m']}, catalog={}, env={}, instances={'fake': fake or RuleFake({'reply': 'offline'})}, **kw)


def test_public_policy_decode_and_validation(protected):
    from exo.model_spend import decode_policy, validate_policy
    cfg, _ = protected
    assert validate_policy(cfg) is cfg
    assert decode_policy(json.dumps(cfg).encode()) == cfg
    assert decode_policy(json.dumps(cfg)) == cfg
    for raw in [json.dumps(cfg)[:-1] + ', "principal":"alice"}',
                json.dumps({**cfg, 'unknown': True}), json.dumps({**cfg, 'daily_usd': 0}),
                ' ' * 65537, b'\xff', json.dumps(cfg).encode('utf-16')]:
        with pytest.raises(ValueError): decode_policy(raw)


def test_public_accounting_import_has_no_core_dependency():
    child = subprocess.run([sys.executable, '-c', '''
import sys
from exo.model_spend import SpendJournal, Price, decode_policy, validate_policy
assert not any(name == "exo.core" or name.startswith("exo.core.") for name in sys.modules)
'''], capture_output=True, text=True, timeout=10)
    assert child.returncode == 0, child.stderr


def test_root_floor_ignores_mutable_config_and_remains_pinned(protected, monkeypatch):
    cfg, path = protected
    user = {**cfg, 'daily_usd': '99', 'principal': 'mallory', 'journal_id': str(uuid.uuid4())}
    router = Router.from_config({'spend': user, 'roles': {'main': ['fake:m']}}, catalog={}, env={},
                                instances={'fake': RuleFake({'reply': 'offline'})})
    assert router.spend.daily == 0 and router.spend.identity == cfg['journal_id']
    selection = spend_policy.resolve_spend_policy(user)
    with pytest.raises(TypeError): selection.config['daily_usd'] = '99'
    with pytest.raises(TypeError): selection.config['routes']['fake:m'] = {}
    path.write_text(json.dumps(user))
    monkeypatch.delenv('EXO_MODEL_SPEND_POLICY')
    assert run(router)[-1].kind == 'done' and router.spend.daily == 0
    assert router.spend_status()['policy_source'] == str(path)


def test_direct_router_loads_floor_and_rejects_injected_journal(protected):
    cfg, _ = protected
    assert fake_router().spend.principal == 'alice'
    with pytest.raises(SpendDenied, match='injected'):
        fake_router(spend=SpendJournal(cfg))
    with pytest.raises(SpendDenied, match='injected'):
        Router.from_config({}, spend=SpendJournal(cfg), catalog={}, env={})


def test_loader_reads_the_same_inode_it_checked(protected, monkeypatch):
    cfg, path = protected
    inode, current = path.stat().st_ino, os.fstat
    swapped = []
    def replace_after_check(fd):
        info = current(fd)
        if info.st_ino == inode and not swapped:
            replacement = path.with_name('replacement.json')
            replacement.write_text(json.dumps({**cfg, 'daily_usd': '99'}))
            replacement.replace(path)
            swapped.append(True)
        return info
    monkeypatch.setattr(spend_policy.os, 'fstat', replace_after_check)
    selected = spend_policy.resolve_spend_policy()
    assert swapped and selected.config['daily_usd'] == '0'
    assert json.loads(path.read_text())['daily_usd'] == '99'


@pytest.mark.parametrize('value', ['', 'relative.json', '/missing/exo/policy.json'])
def test_protected_selector_never_falls_back(tmp_path, monkeypatch, value):
    monkeypatch.setenv('EXO_MODEL_SPEND_POLICY', value)
    with pytest.raises(SpendDenied, match='protected'): fake_router()


def test_policy_cannot_be_enabled_on_an_old_unguarded_router(protected, monkeypatch):
    _, path = protected
    monkeypatch.delenv('EXO_MODEL_SPEND_POLICY')
    router = fake_router()
    monkeypatch.setenv('EXO_MODEL_SPEND_POLICY', str(path))
    assert run(router)[-1].kind == 'error'


@pytest.mark.parametrize('case', ['duplicate', 'unknown', 'numeric_cap', 'negative', 'exponent', 'huge',
                                 'bad_uuid', 'relative_journal', 'routes_list', 'bad_price', 'nested'])
def test_bounded_strict_policy_json(protected, case):
    cfg, path = protected
    if case == 'unknown': cfg['enabled'] = False
    if case == 'numeric_cap': cfg['daily_usd'] = 0
    if case == 'negative': cfg['daily_usd'] = '-1'
    if case == 'exponent': cfg['daily_usd'] = '1e999999999'
    if case == 'bad_uuid': cfg['journal_id'] = 'bad'
    if case == 'relative_journal': cfg['journal'] = 'local.sqlite'
    if case == 'routes_list': cfg['routes'] = []
    if case == 'bad_price': cfg['routes'] = {'fake:m': {'input': '0'}}
    raw = json.dumps(cfg)
    if case == 'duplicate': raw = raw[:-1] + ', "routes": {}}'
    if case == 'huge': raw = ' ' * (spend_policy.MAX_BYTES + 1)
    if case == 'nested': raw = '[' * 2000 + ']' * 2000
    path.write_text(raw)
    with pytest.raises(SpendDenied, match='protected'): fake_router()


@pytest.mark.parametrize('case', ['file_owner', 'file_write', 'ancestor_owner', 'ancestor_write', 'leaf_symlink', 'ancestor_symlink', 'directory', 'fifo'])
def test_checked_descriptors_reject_untrusted_paths(protected, monkeypatch, root_metadata, case):
    _, path = protected
    if case in ('leaf_symlink', 'directory', 'fifo'):
        path.unlink()
        if case == 'directory': path.mkdir()
        elif case == 'fifo': os.mkfifo(path)
        else:
            target = path.with_name('real.json')
            target.write_text('{}')
            path.symlink_to(target)
    elif case == 'ancestor_symlink':
        link = path.parent / 'link'
        link.symlink_to(path.parent, target_is_directory=True)
        monkeypatch.setenv('EXO_MODEL_SPEND_POLICY', str(link / path.name))
    else:
        target = path if case.startswith('file') else path.parent
        inode = target.stat().st_ino
        current = os.fstat
        def bad(fd):
            values = list(current(fd))
            if root_metadata(fd).st_ino == inode:
                if case.endswith('owner'): values[4] = 12345
                else: values[0] |= 0o020
            return os.stat_result(values)
        monkeypatch.setattr(spend_policy.os, 'fstat', bad)
    with pytest.raises(SpendDenied, match='protected'): fake_router()


def test_exact_rulefake_uses_class_implementation_and_no_reservation(protected):
    cfg, _ = protected
    fake = RuleFake({'reply': 'offline'})
    def bad(*args, **kw): raise AssertionError('instance override dispatched')
    fake.stream = bad
    fake._turn = bad
    events = run(fake_router(fake))
    assert events[-1].kind == 'done' and events[-1].message.text() == 'offline'
    assert events[-1].usage.cost_usd == 0
    with sqlite3.connect(cfg['journal']) as db:
        assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 0


@pytest.mark.parametrize('case', ['subclass', 'name', 'local_http', 'native', 'cli'])
def test_zero_policy_rejects_every_nonbuiltin_transport_without_dispatch(protected, case, monkeypatch):
    seen = []
    if case in ('subclass', 'name'):
        class Pretend(RuleFake):
            def stream(self, *a, **k): seen.append(1); raise AssertionError('subclass dispatched')
        provider = Pretend() if case == 'subclass' else FakeProvider(['never'], name='fake')
        router = fake_router(provider)
    else:
        spec = {'transport': 'openai', 'base_url': 'https://api.openai.com/v1'}
        if case == 'local_http': spec.update(base_url='http://127.0.0.1:1234', local=True)
        if case == 'cli':
            spec = {'transport': 'muse-cli', 'command': sys.executable}
            monkeypatch.setattr('exo.core.models.muse_cli.MuseCliProvider.stream',
                                lambda *a, **kw: pytest.fail('CLI generation dispatched'))
        router = Router(roles={'main': ['fake:m']}, providers={'fake': spec}, catalog={}, env={},
                        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req: seen.append(req))))
    assert run(router)[-1].kind == 'error' and not seen
    assert router.spend_status()['pending_count'] == 0
    assert router.spend_status()['state'] == 'offline'
    assert run(fake_router())[-1].kind == 'done'


def test_required_policy_is_still_required_for_exact_offline_provider(monkeypatch):
    monkeypatch.delenv('EXO_MODEL_SPEND_POLICY', raising=False)
    monkeypatch.setenv('EXO_MODEL_SPEND_REQUIRED', '1')
    with pytest.raises(SpendDenied, match='missing'): fake_router()


def test_inspection_opens_only_readonly_sql_and_preserves_stop(protected, monkeypatch):
    cfg, _ = protected
    SpendJournal(cfg).freeze('principal review required')
    actual, statements, connections = sqlite3.connect, [], []
    def traced(database, *args, **kw):
        connections.append(database)
        db = actual(database, *args, **kw)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(sqlite3, 'connect', traced)
    status = SpendJournal.inspect(cfg)
    assert status['state'] == 'stopped'
    assert connections and all(path.endswith('?mode=ro') for path in connections)
    assert all(sql.startswith(('BEGIN', 'SELECT', 'COMMIT')) and sql != 'BEGIN IMMEDIATE' for sql in statements)
    assert status['stop_reason'] == 'principal review required'


def test_protected_priced_contract_uses_explicit_caps_and_exact_status(protected):
    cfg, path = protected
    ref = 'native:gpt-4.1-2025-04-14'
    cfg.update(daily_usd='2', monthly_usd='3', routes={ref: {
        'contract': 'text-tokens-v1', 'transport': 'openai', 'base_url': 'https://api.openai.com/v1',
        'model': 'gpt-4.1-2025-04-14', 'input_bound': 1_047_576, 'max_output': 100,
        'input': '1', 'output': '10', 'cache_read': '1', 'cache_write': '1'}})
    path.write_text(json.dumps(cfg))
    seen = []
    def reply(request):
        seen.append(request)
        usage = {'prompt_tokens': 100, 'completion_tokens': 10}
        body = {'choices': [{'delta': {'content': 'ok'}, 'finish_reason': 'stop'}], 'usage': usage}
        return httpx.Response(200, text='data: ' + json.dumps(body) + '\n\ndata: [DONE]\n\n')
    router = Router(roles={'main': [ref]}, providers={'native': {
        'transport': 'openai', 'base_url': 'https://api.openai.com/v1'}}, catalog={}, env={},
        client=httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    assert run(router)[-1].kind == 'done' and len(seen) == 1
    status = router.spend_status()
    assert status['day_charged_usd'] == status['month_charged_usd'] == '0.000200000'
    assert status['daily_cap_usd'] == '2.000000000' and status['pending_reserved_usd'] == '0.000000000'


def test_root_floor_ignores_seat_and_user_spend_policies(protected, tmp_path):
    from exo.core.engine import CoreEngine
    from exo.core.seats import Seat
    engine = CoreEngine(tmp_path / 'engine', {'spend': {'principal': 'wrong'},
        'roles': {'main': ['fake:m']}}, seat=Seat(name='seat', principal='alice',
        models={'spend': {'daily_usd': '999'}}), schedule=False)
    try:
        assert engine.router.spend.daily == 0
        assert run(engine.router)[-1].kind == 'done'
    finally:
        asyncio.run(engine.close())


def test_typed_decision_and_setup_probes_use_resolved_floor(protected):
    from exo.core.models.decision import ask_choice_result
    from exo.providers_setup import test_provider as probe
    seen = []
    router = Router(roles={'decision': ['typed:m']}, providers={'typed': {'transport': 'decision', 'base_url': 'https://typed'}},
                    catalog={}, env={}, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req: seen.append(req))))
    with pytest.raises(SpendDenied): asyncio.run(ask_choice_result(router, 's', 'q', ['a', 'b']))
    assert probe('typed', router=router, save=False)['error_class'] == 'spend_boundary'
    assert not seen


def test_offline_tool_flow_still_checks_global_stop(protected, tmp_path):
    from exo.core.loop import Loop
    from exo.core.sessions import SessionStore
    calls = []
    fake = RuleFake({'rules': [{'match': 'go', 'steps': [{'tool': 'run', 'args': {}}, 'done']}]})
    router = fake_router(fake)
    async def tool(call, ctx):
        calls.append(call.name)
        return ToolResult.of_text(call.id, 'ok')
    async def scenario():
        loop = Loop(SessionStore(tmp_path / 'sessions.sqlite'), router.provider(),
                    tool_specs=[ToolSpec('run', 'test')], run_tool=tool)
        session = await loop.create_session()
        result = await loop.send(session.id, 'go')
        assert result.message.text() == 'done' and calls == ['run']
        router.spend.freeze('principal review required')
        session = await loop.create_session()
        result = await loop.send(session.id, 'go')
        assert result.error and calls == ['run']
    asyncio.run(scenario())
    assert router.spend_status()['pending_count'] == 0


def test_stopped_engine_factory_keeps_status_and_transcripts_available(protected, tmp_path, monkeypatch):
    from exo.core.engine import create
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher
    cfg, _ = protected
    SpendJournal(cfg).freeze('principal review required')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'seats').mkdir()
    (home / 'seats' / 'seat.json').write_text(json.dumps({'principal': 'alice'}))
    (home / 'config.json').write_text(json.dumps({'principal': 'different-default', 'core_plugins': [],
        'learn': {'enabled': False}, 'schedule': False, 'models': {'fake': {'reply': 'offline'}}}))
    monkeypatch.setenv('EXO_HOME', str(home))
    monkeypatch.setattr('exo.models_cmd.available_models', lambda *a: [])
    eng = create('seat')
    eng.peers.autostart = False
    async def scenario():
        try:
            dispatcher = Dispatcher(eng)
            status = await dispatcher.run(s.Command.of(s.ModelsList()))
            assert status.ok and status.result['spend']['state'] == 'stopped'
            assert status.result['spend']['daily_cap_usd'] == '0.000000000'
            sessions = await dispatcher.run(s.Command.of(s.SessionList()))
            assert sessions.ok
        finally:
            await eng.close()
    # Generation has its own event loop; the server commands share theirs below.
    assert run(eng.router)[-1].kind == 'error'
    asyncio.run(scenario())


def test_environment_seat_principal_disagreement_precedes_plugins(protected, tmp_path, monkeypatch):
    from exo.core.engine import create
    home = tmp_path / 'home'
    (home / 'seats').mkdir(parents=True)
    (home / 'seats' / 'seat.json').write_text(json.dumps({'principal': 'alice'}))
    (home / 'config.json').write_text('{}')
    monkeypatch.setenv('EXO_HOME', str(home))
    monkeypatch.setenv('EXO_PRINCIPAL', 'bob')
    monkeypatch.setattr('exo.core.plugins.PluginManager.load_all', lambda *a, **k: pytest.fail('plugins loaded'))
    with pytest.raises(ValueError, match='conflicts'): create('seat')


def test_stopped_protected_daemon_serves_status_and_saved_transcript(protected, tmp_path):
    from exo.core.sessions import SessionStore
    from exo.protocol import schema as s
    cfg, policy_path = protected
    SpendJournal(cfg).freeze('principal review required')
    home = tmp_path / 'daemon'
    home.mkdir()
    (home / 'config.json').write_text(json.dumps({'principal': 'alice', 'core_plugins': [],
        'learn': {'enabled': False}, 'schedule': False, 'models': {'fake': {'reply': 'offline'}}}))
    store = SessionStore(home / 'sessions.sqlite')
    session = store.create_session(title='saved transcript', agent='default')
    store.append_message(session.id, Message.user('saved before stop'))
    commands = [s.Command.of(s.ModelsList()), s.Command.of(s.SessionGet(session=session.id))]
    # Only root metadata is simulated. This launches the real CLI, factory and stdio
    # daemon in a fresh process; no stopped-journal constructor/status patch is used.
    source = Path(__file__).resolve().parents[3] / 'src'
    script = '''
import os, socket, exo
from pathlib import Path
assert Path(exo.__file__).resolve() == Path(os.environ["EXO_TEST_SOURCE"]) / "exo" / "__init__.py"
actual = os.fstat
def metadata(fd):
    values = list(actual(fd))
    values[0] &= ~0o022
    values[4] = 0
    return os.stat_result(values)
os.fstat = metadata
def no_network(*a, **kw):
    raise AssertionError("network in offline daemon test")
socket.socket.connect = no_network
from exo.cli import main
raise SystemExit(main(["serve", "--stdio"]))
'''
    env = {'PATH': os.defpath, 'PYTHONPATH': str(source), 'EXO_TEST_SOURCE': str(source),
           'EXO_HOME': str(home), 'EXO_MODEL_SPEND_POLICY': str(policy_path),
           'EXO_MODEL_SPEND_REQUIRED': '1', 'EXO_NO_KEYCHAIN': '1', 'EXO_NO_SECRET_SERVICE': '1',
           'EXO_KEYS_FILE': str(tmp_path / 'no-keys'), 'EXO_PEERS_DIR': str(tmp_path / 'no-peers')}
    with tempfile.TemporaryDirectory(prefix='exo-spend-', dir='/tmp') as peers:
        env['EXO_PEERS_DIR'] = peers  # Unix socket path must fit macOS's 104-byte limit.
        result = subprocess.run([sys.executable, '-c', script], env=env,
            input=''.join(c.to_json() + '\n' for c in commands), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    responses = {r['id']: r for line in result.stdout.splitlines() if '"ok"' in line for r in [json.loads(line)]}
    status, transcript = (responses[c.id] for c in commands)
    assert status['ok'] and status['result']['spend']['state'] == 'stopped', result.stdout + result.stderr
    assert status['result']['spend']['daily_cap_usd'] == '0.000000000'
    assert transcript['ok'] and 'saved before stop' in json.dumps(transcript['result']['messages'])
    assert SpendJournal(cfg).status()['stop_reason'] == 'principal review required'


def _tiered_route(**overrides):
    route = {'contract': 'text-tokens-v1', 'transport': 'anthropic', 'base_url': 'https://api.anthropic.com',
             'model': 'claude-haiku-5-5', 'input_bound': 1_000_000, 'max_output': 128_000,
             'input': '0.50', 'output': '2.50', 'cache_read': '0.05', 'cache_write': '0.625'}
    route.update(overrides)
    return route


def test_policy_refuses_pin_below_audited_tier(protected):
    from exo.model_spend import validate_policy
    cfg, _ = protected
    for field, low in (('input', '0.10'), ('output', '0.50')):
        bad = dict(cfg, routes={'anthropic:claude-haiku-5-5': _tiered_route(**{field: low})})
        with pytest.raises(ValueError, match='tier'):
            validate_policy(bad)


def test_policy_refuses_cache_pin_below_audited_tier(protected):
    from exo.model_spend import validate_policy
    cfg, _ = protected
    for field, low in (('cache_read', '0.01'), ('cache_write', '0.60'), ('cache_write', '0.125')):
        bad = dict(cfg, routes={'anthropic:claude-haiku-5-5': _tiered_route(**{field: low})})
        with pytest.raises(ValueError, match=f'{field} rate below audited tier'):
            validate_policy(bad)


def test_policy_accepts_pin_at_audited_tier(protected):
    from exo.model_spend import validate_policy
    cfg, _ = protected
    good = dict(cfg, routes={'anthropic:claude-haiku-5-5': _tiered_route()})
    assert validate_policy(good) is good


def test_policy_ignores_single_tier_routes(protected):
    from exo.model_spend import validate_policy
    cfg, _ = protected
    single = dict(cfg, routes={'openai:gpt-4.1-2025-04-14': {
        'contract': 'text-tokens-v1', 'transport': 'openai', 'base_url': 'https://api.openai.com/v1',
        'model': 'gpt-4.1-2025-04-14', 'input_bound': 1_047_576, 'max_output': 100,
        'input': '0.01', 'output': '0.02', 'cache_read': '0.01', 'cache_write': '0.01'}})
    assert validate_policy(single) is single
