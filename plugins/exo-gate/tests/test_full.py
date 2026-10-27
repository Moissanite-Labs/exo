"""Full permission mode uses disposable signed ledgers and never executes a tool."""
import asyncio
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from exo import crypto, principal
from exo.core.models.keys import remember_secret
from exo.server.engine import CommandFailed
from exo_gate import hook
from exo_gate import permissions as perm
from test_bypass import FLOOR, RISKY, rows
from test_hook import make_gate


@pytest.fixture
def keyed_store(example_store, tmp_path):
    key = tmp_path / "keys/exo-gate.key"
    crypto.save_key(crypto.keygen(), key)
    return example_store, key


@pytest.fixture
def as_caller(request):
    def stamp(who):
        token = principal.CALLER.set(principal.Caller(who))
        request.addfinalizer(lambda: principal.CALLER.reset(token))
    return stamp


def invoke(g, tool='terminal', args=None, session='s1', author='principal:tui', asynchronous=True):
    kw = dict(tool_name=tool, args=args or {'command': 'rm -rf fixture'}, session_id=session,
              tool_call_id='c1', turn_id='t1', author=author)
    return asyncio.run(g.apre_tool_call(**kw)) if asynchronous else g.pre_tool_call(**kw)


def full_gate(keyed_store, as_caller, **settings):
    store, key = keyed_store
    g, _ = make_gate(store, key, **settings)
    as_caller('principal:tui')
    hook.permissions_command(g, 'full s1')
    return g, store


@pytest.mark.parametrize('tool,args', FLOOR + RISKY + [
    ('terminal', {'command': 'git push --force origin main'}),
    ('terminal', {'command': 'gh pr comment 5 --repo fixture/demo --body "unreviewed fixture"'}),
])
def test_full_skips_all_gate_categories_only_after_exact_signed_audit(keyed_store, as_caller, tool, args):
    g, store = full_gate(keyed_store, as_caller)
    assert invoke(g, tool, args) is None
    [row] = rows(store)
    assert row['decided_by'] == row['mode'] == 'full' and row['outcome'] == 'allow'
    assert json.loads(row['call']['args']) == args
    assert row['args_sha256'] == hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    assert row['full_session'] == 's1' and row['turn_author'] == 'principal:tui'
    assert row['tool_call_id'] == 'c1' and row['turn_id'] == 't1'
    assert g.permissions.full_auto_approved == 1 and g.permissions.auto_approved == 0


@pytest.mark.parametrize('asynchronous', [True, False])
def test_full_bypasses_classification_risk_and_decision_model_errors(keyed_store, as_caller, monkeypatch, asynchronous):
    g, _ = full_gate(keyed_store, as_caller, decision_model=True)
    def unexpected(*a, **kw):
        pytest.fail('full reached ordinary policy/risk/model path')
    for name in ('_rules', '_risk', '_verdict'):
        monkeypatch.setattr(g, name, unexpected)
    assert invoke(g, asynchronous=asynchronous) is None


@pytest.mark.parametrize('author', ['principal:tui', 'local', 'peer:fixture', 'bus:fixture', 'job', ''])
def test_full_selected_session_includes_background_and_steered_turns(keyed_store, as_caller, author):
    g, store = full_gate(keyed_store, as_caller, decision_model=False)
    assert invoke(g, author=author) is None
    assert rows(store)[0]['turn_author'] == author


@pytest.mark.parametrize('session,author', [('s2', 'principal:tui'), ('s2', 'peer:fixture'), ('', 'principal:tui')])
def test_full_never_lowers_other_sessions_or_calls_without_session(keyed_store, as_caller, session, author):
    g, store = full_gate(keyed_store, as_caller, decision_model=False)
    assert invoke(g, session=session, author=author)['action'] == 'approve'
    assert rows(store)[0]['outcome'] == 'ask' and g.permissions.full_auto_approved == 0


@pytest.mark.parametrize('who', ['unknown', 'local', 'peer:fixture', 'bus:fixture', 'agent'])
def test_only_live_principal_can_enable_full(keyed_store, as_caller, who):
    g, _ = make_gate(*keyed_store, decision_model=False)
    as_caller(who)
    with pytest.raises(CommandFailed):
        hook.permissions_command(g, 'full s1')
    assert g.permissions.mode == 'ask'


@pytest.mark.parametrize('failure', [None, '', RuntimeError('private error')])
def test_full_audit_failure_blocks_without_fallback_even_for_safe_tools(keyed_store, as_caller, monkeypatch, failure):
    g, _ = full_gate(keyed_store, as_caller)
    def append(*a, **kw):
        if isinstance(failure, Exception):
            raise failure
        return failure
    monkeypatch.setattr(g.sink, 'append', append)
    result = invoke(g, 'read_file', {'path': 'fixture.txt'})
    assert result == {'action': 'block', 'message': 'Full permission audit could not be saved; call blocked'}
    assert g.permissions.full_auto_approved == 0


@pytest.mark.parametrize('failure', [None, RuntimeError('private enable audit error')])
def test_full_enable_requires_committed_audit_and_ask_revokes_even_when_audit_fails(keyed_store, as_caller, monkeypatch, failure):
    g, _ = make_gate(*keyed_store, decision_model=False)
    as_caller('principal:tui')
    original = g.sink.append
    def unavailable(*a, **kw):
        if isinstance(failure, Exception):
            raise failure
        return failure
    monkeypatch.setattr(g.sink, 'append', unavailable)
    with pytest.raises(hook.GateAuditError, match='^Full permission mode audit could not be saved$'):
        hook.permissions_command(g, 'full s1')
    assert g.permissions.mode == 'ask'
    monkeypatch.setattr(g.sink, 'append', original)
    hook.permissions_command(g, 'full s1')
    def fail(*a, **kw):
        raise RuntimeError('private error')
    monkeypatch.setattr(g.sink, 'append', fail)
    as_caller('local')
    assert hook.permissions_command(g, 'ask').startswith('permissions: ask')
    assert g.permissions.mode == 'ask' and g.permissions.full_session is None


def test_full_records_untruncated_call_with_known_and_obvious_secrets_redacted(keyed_store, as_caller):
    g, store = full_gate(keyed_store, as_caller)
    secret = 'synthetic-full-token-never-real-12345'
    remember_secret(secret)
    args = {'command': 'printf fixture', 'password': 'short', 'token': secret, 'text': 'x' * 9000}
    assert invoke(g, args=args) is None
    [row] = rows(store)
    text = row['call']['args']
    assert secret not in text and args['password'] not in text
    assert json.loads(text)['text'] == args['text'] and 'truncated' not in row['call']


def test_full_redaction_or_serialization_failure_blocks(keyed_store, as_caller, monkeypatch):
    g, _ = full_gate(keyed_store, as_caller)
    assert invoke(g, args={'value': object()})['action'] == 'block'
    def fail(*a):
        raise RuntimeError('private redaction error')
    monkeypatch.setattr(perm, 'full_recorded_call', fail)
    assert invoke(g)['action'] == 'block'


def test_full_snapshot_and_switches_are_ephemeral_and_do_not_answer_pending_requests(keyed_store, as_caller):
    g, _ = make_gate(*keyed_store, decision_model=False)
    pending = invoke(g)
    existing = dict(g._pending)
    as_caller('principal:tui')
    hook.permissions_command(g, 'bypass')
    hook.permissions_command(g, 'full s1')
    snap = hook.permissions_command(g, 'json')
    assert snap['supports_full'] and snap['full_available'] and snap['mode'] == 'full'
    assert snap['full_session'] == 's1' and snap['scope'] == 'session-all-turns' and snap['fallback_mode'] == 'ask'
    assert snap['floor'] == [] and g._pending == existing and pending['action'] == 'approve'
    assert invoke(g, session='s2')['action'] == 'approve'
    hook.permissions_command(g, 'bypass')
    assert g.permissions.full_session is None and g.bypass_on
    fresh, _ = make_gate(*keyed_store, decision_model=False)
    assert fresh.permissions.mode == 'ask' and fresh.permissions.full_session is None


def test_full_required_executor_refuses_enable_and_later_full_calls(keyed_store, as_caller):
    g, _ = full_gate(keyed_store, as_caller)
    g.required_getter = lambda: True
    assert invoke(g)['action'] == 'block'
    assert g.permissions_snapshot()['mode'] == 'ask' and not g.permissions_snapshot()['full_available']
    with pytest.raises(CommandFailed):
        hook.permissions_command(g, 'full s2')


def test_full_audit_and_revocation_have_one_ordered_boundary(keyed_store, as_caller, monkeypatch):
    g, _ = full_gate(keyed_store, as_caller)
    entered, release, revoked, attempted = (threading.Event() for _ in range(4))
    original = g.sink.append
    def held(body, **kw):
        if body.get('decided_by') == 'full':
            entered.set()
            assert release.wait(5)
        return original(body, **kw)
    monkeypatch.setattr(g.sink, 'append', held)
    def revoke():
        attempted.set()
        g.set_permissions('ask')
        revoked.set()
    with ThreadPoolExecutor(2) as pool:
        allowed = pool.submit(invoke, g)
        assert entered.wait(2)
        off = pool.submit(revoke)
        assert attempted.wait(2) and not revoked.is_set()
        release.set()
        assert allowed.result(5) is None
        off.result(5)
    assert revoked.is_set() and g.permissions.mode == 'ask'
    assert invoke(g)['action'] == 'approve'



def test_expired_principal_authority_cannot_enable_full(keyed_store, as_caller):
    g, _ = make_gate(*keyed_store, decision_model=False)
    as_caller('principal:tui')
    principal.CALLER.get().live = False
    with pytest.raises(CommandFailed):
        hook.permissions_command(g, 'full s1')
    assert g.permissions.mode == 'ask'


def test_seat_file_never_persists_full(monkeypatch, tmp_path):
    home = tmp_path / 'fake-home'
    seats = home / 'seats'
    seats.mkdir(parents=True)
    (seats / 'fixture.toml').write_text('[permissions]\nmode = "full"\n')
    assert perm.seat_default({'EXO_HOME': str(home), 'EXO_SEAT': 'fixture'}) == 'ask'


@pytest.mark.parametrize('target', ['all', '*'])
def test_explicit_full_all_covers_sessions_and_children_but_not_unscoped_calls(keyed_store, as_caller, target):
    g, store = full_gate(keyed_store, as_caller, decision_model=False)
    hook.permissions_command(g, 'full ' + target)
    snap = g.permissions_snapshot()
    assert snap['supports_full_all'] and snap['full_session'] == '*' and snap['scope'] == 'core-all-sessions'
    for session in ('s1', 's_other', 's_delegated_child'):
        assert invoke(g, session=session, author='job') is None
    assert invoke(g, session='')['action'] == 'approve'
    approved = [row for row in rows(store) if row.get('decided_by') == 'full']
    assert [row['session_id'] for row in approved] == ['s1', 's_other', 's_delegated_child']
    assert all(row['scope'] == 'core-all-sessions' and row['full_session'] == '*' for row in approved)
    as_caller('local')
    hook.permissions_command(g, 'ask')
    assert invoke(g, session='s_other')['action'] == 'approve'
    assert g.permissions.full_session is None


def test_full_all_requires_principal_and_audit_and_rechecks_each_call(keyed_store, as_caller, monkeypatch):
    g, _ = make_gate(*keyed_store, decision_model=False)
    as_caller('local')
    with pytest.raises(CommandFailed):
        hook.permissions_command(g, 'full all')
    as_caller('principal:tui')
    hook.permissions_command(g, 'full all')
    monkeypatch.setattr(g.sink, 'append', lambda *a, **kw: None)
    assert invoke(g, session='s_child')['action'] == 'block'
    as_caller('local')
    hook.permissions_command(g, 'ask')
    assert g.permissions.mode == 'ask'


def test_full_redacts_escaped_known_secrets_before_serialization(keyed_store, as_caller):
    g, store = full_gate(keyed_store, as_caller)
    secret = 'synthetic-quoted-"-slash-\\-not-real-12345'
    remember_secret(secret)
    args = {'text': secret, 'nested': [{secret: secret}], 'ordinary': 'kept'}
    assert invoke(g, args=args) is None
    [row] = rows(store)
    recorded = json.loads(row['call']['args'])
    assert recorded['text'] != secret
    assert secret not in recorded['nested'][0]
    assert secret not in recorded['nested'][0].values()
    assert recorded['ordinary'] == 'kept'
    assert row['args_sha256'] == hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()


def test_full_string_redaction_failure_blocks_without_audit(keyed_store, as_caller, monkeypatch):
    from exo.core.models import keys
    g, store = full_gate(keyed_store, as_caller)
    def unavailable(*a, **kw):
        raise RuntimeError('synthetic redaction failure')
    monkeypatch.setattr(keys, 'redact_secrets', unavailable)
    assert invoke(g, args={'text': 'fixture'})['action'] == 'block'
    assert rows(store) == [] and g.permissions.full_auto_approved == 0


@pytest.mark.parametrize('template', ['export API_KEY="{}"', 'echo {{"password": "{}"}}'])
def test_full_patterns_scrub_raw_strings_before_json_without_known_values(keyed_store, as_caller, monkeypatch, template):
    from exo.core.models import keys
    monkeypatch.setattr(keys, 'known_secret_values', lambda: ())
    secret = 'synthetic' + '0fixture0value'
    g, store = full_gate(keyed_store, as_caller)
    assert invoke(g, args={'command': template.format(secret)}) is None
    [row] = rows(store)
    assert secret not in row['call']['args']
    assert secret not in json.loads(row['call']['args'])['command']


def test_full_redacted_keys_preserve_every_entry_and_literal_masked_names(monkeypatch):
    from exo.core.models import keys
    first, second = 'synthetic-key-one', 'synthetic-key-two'
    monkeypatch.setattr(keys, 'known_secret_values', lambda: (first, second))
    args = {'env': {first: 'first', second: 'second', '[redacted]': 'literal', '[redacted] [2]': 'numbered'}}
    call, _ = perm.full_recorded_call('terminal', args)
    recorded = json.loads(call['args'])['env']
    assert len(recorded) == 4 and set(recorded.values()) == {'first', 'second', 'literal', 'numbered'}
    assert recorded['[redacted]'] == 'literal' and recorded['[redacted] [2]'] == 'numbered'
    assert first not in call['args'] and second not in call['args']


def test_full_pattern_redactor_failure_blocks_without_decision_audit(keyed_store, as_caller, monkeypatch):
    from exo.ingest import common
    g, store = full_gate(keyed_store, as_caller)
    def unavailable(*a, **kw):
        raise RuntimeError('synthetic pattern failure')
    monkeypatch.setattr(common, 'redact', unavailable)
    assert invoke(g, args={'command': 'printf fixture'})['action'] == 'block'
    assert rows(store) == []


def test_full_transition_audit_names_previous_session(keyed_store, as_caller):
    g, store = full_gate(keyed_store, as_caller)
    hook.permissions_command(g, 'full s2')
    hook.permissions_command(g, 'full all')
    hook.permissions_command(g, 'ask')
    transitions = rows(store, phase='permissions')
    assert [row.get('previous_session') for row in transitions] == [None, 's1', 's2', '*']



def test_full_patterns_scrub_raw_dictionary_keys_without_known_values(monkeypatch):
    from exo.core.models import keys
    monkeypatch.setattr(keys, 'known_secret_values', lambda: ())
    secret = 'synthetic' + '0fixture0value'
    call, _ = perm.full_recorded_call('terminal', {'API_KEY="' + secret + '"': 'fixture'})
    assert secret not in call['args'] and len(json.loads(call['args'])) == 1


def test_full_absent_signed_ledger_blocks_without_decision_row(keyed_store, as_caller):
    g, store = full_gate(keyed_store, as_caller)
    g.sink._ledger = None
    g.sink._tried = True
    assert invoke(g)['action'] == 'block'
    assert rows(store) == [] and g.permissions.full_auto_approved == 0
