"""Offline adapter boundaries: temporary stores only; no composition or live config."""
import asyncio
import json
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from exo.config import ConfigError
from exo.core.sessions import SessionStore
from exo.core.tools.registry import Registry, ToolContext, ToolError
from exo.core.types import Message, ToolCall
from exo.principal import CALLER, Caller
from exo.protocol.schema import CommandRun
from exo.server.engine import CommandFailed
from exo.shared_pad import PadValidationError
from exo.shared_pad_adapter import (
    MAX_ARGS_BYTES, SharedPadConfig, _parse, attach, command,
)


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def engine(tmp_path):
    store = SessionStore(tmp_path / 'sessions.sqlite')
    eng = SimpleNamespace(home=tmp_path / 'home', agent='agent', store=store,
                          registry=Registry())
    yield eng
    store.close()


def cmd(engine, args=None, *, session=None, who='principal:web', live=True):
    token = CALLER.set(Caller(who, live=live))
    try:
        return run(command(engine, CommandRun('pad', '' if args is None else json.dumps(args), session)))
    finally:
        CALLER.reset(token)


def recorded(engine, args, session=None):
    session = session or engine.store.create_session(agent=engine.agent).id
    call = ToolCall('call-1', 'shared_pad', args)
    engine.store.append_message(session, Message.assistant(tool_calls=[call]), turn_id='turn-1')
    return ToolContext(session=session, call_id=call.id, meta={'turn_id': 'turn-1'})


def test_opt_in_and_collision(engine):
    assert not SharedPadConfig().enabled
    assert attach(engine) is None
    assert engine.registry.get('shared_pad') is None
    assert not engine.home.exists()
    with pytest.raises(CommandFailed) as exc:
        cmd(engine)
    assert exc.value.code == 'unsupported'
    adapter = attach(engine, principal='alice', enabled=True)
    assert engine.registry.get('shared_pad')
    assert engine.shared_pad is adapter
    with pytest.raises(ConfigError):
        attach(engine, enabled=True)
    del engine.shared_pad
    with pytest.raises(ConfigError):
        attach(engine, enabled=True)


@pytest.mark.parametrize('config', [True, [], {'enabled': 1}, {'enabled': None}, {'owner': 'alice'}])
def test_config_strict(config):
    with pytest.raises(ConfigError):
        SharedPadConfig(config)


def test_command_tool_cas_history_restart_and_owner(engine):
    adapter = attach(engine, principal='alice', enabled=True)
    first_session = engine.store.create_session(agent=engine.agent).id
    other_session = engine.store.create_session(agent=engine.agent).id
    assert cmd(engine)['snapshot']['document'] is None
    update = {'action': 'update', 'expected_revision': 0, 'document': 'user 界\n<script>DATA</script>'}
    first = cmd(engine, update, session=first_session)['snapshot']
    assert set(first) == {'pad', 'revision', 'created', 'document'}
    read = {'action': 'read'}
    ctx = recorded(engine, read, other_session)
    assert json.loads(run(adapter.tool(read, ctx)))['snapshot'] == first
    stale = {**update, 'document': 'stale agent draft'}
    ctx = recorded(engine, stale, other_session)
    conflict = json.loads(run(adapter.tool(stale, ctx)))
    assert conflict == {'ok': False, 'code': 'revision_conflict', 'current': first}
    clear = {'action': 'update', 'expected_revision': 1, 'document': ''}
    ctx = recorded(engine, clear, other_session)
    second = json.loads(run(adapter.tool(clear, ctx)))['snapshot']
    assert second['revision'] == 2 and second['document'] == ''
    assert cmd(engine, {'action': 'read', 'revision': 1})['snapshot'] == first
    assert cmd(engine, {'action': 'history'})['history'] == [second, first]
    assert cmd(engine, update) == {'ok': False, 'code': 'revision_conflict', 'current': second}
    rebuilt = SimpleNamespace(**{**vars(engine), 'registry': Registry(), 'shared_pad': None})
    assert attach(rebuilt, principal='alice', enabled=True).owner == adapter.owner
    assert cmd(rebuilt)['snapshot'] == second
    foreign = SimpleNamespace(**{**vars(engine), 'registry': Registry(), 'shared_pad': None})
    assert attach(foreign, principal='bob', enabled=True).owner != adapter.owner
    assert cmd(foreign)['snapshot']['revision'] == 0
    assert cmd(foreign, {**update, 'expected_revision': 3})['current']['document'] is None
    assert cmd(foreign, {'action': 'history'})['history'] == []


@pytest.mark.parametrize('who,live', [('unknown', True), ('local', True), ('channel', True),
                                      ('principal', False), ('peer:alice', True)])
@pytest.mark.parametrize('action', ['read', 'history', 'update'])
def test_command_auth_before_storage(engine, monkeypatch, who, live, action):
    adapter = attach(engine, enabled=True)
    monkeypatch.setattr(adapter, '_execute', lambda args: pytest.fail('unauthorized storage'))
    with pytest.raises(CommandFailed) as exc:
        cmd(engine, {'action': action}, who=who, live=live)
    assert exc.value.code == 'forbidden'


@pytest.mark.parametrize('action', ['read', 'history', 'update'])
@pytest.mark.parametrize('kind', ['private', 'private-child', 'closed', 'foreign', 'missing'])
def test_session_denials_on_both_entries(engine, action, kind):
    adapter = attach(engine, enabled=True)
    store = engine.store
    parent = store.create_session(agent=engine.agent, kind='private')
    session = store.create_session(agent='other' if kind == 'foreign' else engine.agent,
                                   kind='private' if kind == 'private' else 'thread',
                                   parent=parent.id if kind == 'private-child' else None).id
    if kind == 'closed':
        store.lock(session)
    args = {'action': action}
    if action == 'update':
        args.update(expected_revision=0, document='secret')
    ctx = recorded(engine, args, session)
    if kind == 'missing':
        session = ctx.session = 'missing'
    with pytest.raises(ToolError):
        run(adapter.tool(args, ctx))
    with pytest.raises(CommandFailed) as exc:
        cmd(engine, args, session=session)
    assert exc.value.code == 'forbidden'
    assert cmd(engine)['snapshot']['revision'] == 0


def test_fabricated_and_substituted_tool_calls(engine):
    adapter = attach(engine, enabled=True)
    read = {'action': 'read'}
    ctx = recorded(engine, read)
    write = {'action': 'update', 'expected_revision': 0, 'document': 'substitution'}
    for args, bad in [(write, ctx), (read, ToolContext()),
                      (read, ToolContext(session=ctx.session, call_id='forged', meta=ctx.meta)),
                      (read, ToolContext(session=ctx.session, call_id=ctx.call_id, meta={'turn_id': 'forged'}))]:
        with pytest.raises(ToolError):
            run(adapter.tool(args, bad))
    assert cmd(engine)['snapshot']['revision'] == 0


@pytest.mark.parametrize('raw', ['null', '[]', 'true', '{', '{"action":"read","action":"history"}',
    '{"action":"read","revision":true}', '{"action":"read","revision":0}',
    '{"action":"read","revision":1.0}', '{"action":"history","limit":101}',
    '{"action":"history","limit":false}', '{"action":"update","expected_revision":0}',
    '{"action":"update","expected_revision":true,"document":"x"}',
    '{"action":"read","owner":"alice"}', '{"action":"read","path":"/tmp/x"}',
    '{"action":"read","principal":"alice"}', '{"action":"read","pad":"other"}',
    '{"action":"read","revision":NaN}', '{"action":"read","revision":9223372036854775808}',
    '{"action":"unknown"}', 'x' * (MAX_ARGS_BYTES + 1)])
def test_parser_strict(raw):
    with pytest.raises(PadValidationError):
        _parse(raw)


def test_document_byte_budget_and_error_mapping(engine):
    adapter = attach(engine, enabled=True)
    args = {'action': 'update', 'expected_revision': 0, 'document': '界' * 10922 + 'aa'}
    assert cmd(engine, args)['snapshot']['revision'] == 1
    for text in ['界' * 10923, '\ud800', '\x00', '\r', None]:
        bad = {**args, 'document': text}
        with pytest.raises(CommandFailed) as exc:
            cmd(engine, bad)
        assert exc.value.code == 'bad_request'
        with pytest.raises(ToolError):
            run(adapter.tool(bad, recorded(engine, {"action": "read"})))
    with pytest.raises(CommandFailed) as exc:
        cmd(engine, {'action': 'read', 'revision': 10})
    assert exc.value.code == 'not_found'
    assert len(cmd(engine, {'action': 'history'})['history']) == 1


def test_private_modes_symlinks_and_storage_errors(engine, monkeypatch):
    adapter = attach(engine, enabled=True)
    path = engine.home / 'shared-pad' / 'pads.sqlite'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    side = path.with_name(path.name + '-wal')
    side.symlink_to(path)
    with pytest.raises(CommandFailed) as exc:
        cmd(engine)
    assert exc.value.code == 'failed'
    side.unlink()
    path.chmod(0o666)
    path.parent.chmod(0o755)
    cmd(engine)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    def fail(*args):
        raise sqlite3.OperationalError('secret filesystem path')
    monkeypatch.setattr(adapter._storage, 'read', fail)
    with pytest.raises(CommandFailed) as exc:
        cmd(engine)
    assert exc.value.message == 'shared pad private storage unavailable'
    with pytest.raises(ToolError, match='^shared pad private storage unavailable$'):
        run(adapter.tool({'action': 'read'}, recorded(engine, {'action': 'read'})))


def test_symlink_targets_refused_and_local_binding(engine, tmp_path):
    adapter = attach(engine, enabled=True)
    same = SimpleNamespace(**{**vars(engine), 'registry': Registry(), 'shared_pad': None})
    assert attach(same, principal=' ', enabled=True).owner == adapter.owner
    other = SimpleNamespace(**{**vars(same), 'home': tmp_path / 'other', 'registry': Registry(), 'shared_pad': None})
    assert attach(other, enabled=True).owner != adapter.owner
    path = engine.home / 'shared-pad' / 'pads.sqlite'
    path.unlink()
    target = tmp_path / 'untouched'
    target.write_text('do not change')
    path.symlink_to(target)
    with pytest.raises(CommandFailed):
        cmd(engine)
    assert target.read_text() == 'do not change'
    with pytest.raises(AttributeError):
        adapter.owner = 'payload'


def test_registry_gate_path_and_hidden_call(engine):
    from exo.core.tools.registry import PRE_TOOL_CALL
    adapter = attach(engine, enabled=True)
    args = {'action': 'read'}
    ctx = recorded(engine, args)
    call = ToolCall(ctx.call_id, 'shared_pad', args)
    result = run(engine.registry.call(call, ctx))
    assert not result.is_error
    assert json.loads(result.text())['snapshot']['revision'] == 0
    engine.store._db.execute("UPDATE messages SET hidden=1 WHERE session=?", (ctx.session,))
    with pytest.raises(ToolError):
        run(adapter.tool(args, ctx))
    engine.registry.add_hook(PRE_TOOL_CALL, lambda *args: 'test gate refusal')
    result = run(engine.registry.call(call, ctx))
    assert result.is_error and 'test gate refusal' in result.text()


def test_wrong_command_and_invalid_principal(engine):
    with pytest.raises(ConfigError):
        attach(engine, principal={'untrusted': 'payload'}, enabled=True)
    token = CALLER.set(Caller('principal'))
    try:
        with pytest.raises(CommandFailed) as exc:
            run(command(engine, CommandRun('wrong')))
        assert exc.value.code == 'bad_request'
    finally:
        CALLER.reset(token)


@pytest.mark.parametrize('data', ['{', 'null', '[]', '{"tool_calls":null}',
                                '{"tool_calls":[null,3,"fake"]}'])
def test_malformed_record_cannot_authorize(engine, data):
    adapter = attach(engine, enabled=True)
    args = {'action': 'read'}
    ctx = recorded(engine, args)
    engine.store._db.execute("UPDATE messages SET data=? WHERE session=?", (data, ctx.session))
    with pytest.raises(ToolError, match='matching recorded assistant call'):
        run(adapter.tool(args, ctx))


def test_tool_identity_not_inherited_from_principal_caller(engine):
    adapter = attach(engine, enabled=True)
    args = {'action': 'read'}
    ctx = recorded(engine, args)
    token = CALLER.set(Caller('principal:web'))
    try:
        with pytest.raises(ToolError):
            run(adapter.tool(args, ToolContext(session=ctx.session)))
        engine.store._db.execute("UPDATE messages SET data=? WHERE session=?",
            (json.dumps({'tool_calls': [{'id': ctx.call_id, 'name': 'work_notes',
                                         'arguments': args}]}), ctx.session))
        with pytest.raises(ToolError):
            run(adapter.tool(args, ctx))
    finally:
        CALLER.reset(token)


def test_bound_owner_ignores_display_label_and_preserves_work_tool(engine):
    from exo.core.types import ToolSpec
    sentinel = engine.registry.register(ToolSpec(name='work_notes', description='existing',
                                                parameters={}), lambda *args: 'unchanged')
    adapter = attach(engine, principal='configured-owner', enabled=True)
    assert engine.registry.get('work_notes') is sentinel
    body = {'action': 'update', 'expected_revision': 0, 'document': 'plain data'}
    saved = cmd(engine, body, who='principal:terminal')
    assert cmd(engine, who='principal:web') == saved
    assert adapter.owner not in json.dumps(saved)


def test_session_storage_errors_are_sanitized(engine, monkeypatch):
    attach(engine, enabled=True)
    def fail(*args):
        raise sqlite3.OperationalError('private database details')
    monkeypatch.setattr(engine.store, 'get_session', fail)
    with pytest.raises(CommandFailed) as exc:
        cmd(engine, session='session')
    assert exc.value.code == 'failed'
    assert exc.value.message == 'shared pad private storage unavailable'
