"""Only trusted ordinary gate asks opt into explicitly granted parent review."""
from exo import gate
from test_hook import make_gate


def test_opaque_absolute_command_is_eligible_but_floors_and_unseen_bodies_are_not(example_store):
    g, _ = make_gate(example_store)
    g.policies()['gates'].setdefault('categories', {})['shell'] = 'ask'
    for command in ('/owned/venv/bin/ruff check .', '/owned/venv/bin/check-fixture .'):
        ordinary = g.pre_tool_call('bash', {'command': command}, session_id='child')
        assert ordinary['action'] == 'approve' and ordinary['delegation_eligible'] is True
    for args in ({'command': 'gh auth token'}, {'command': 'cat ~/.exo/keys/fixture.key'},
                 {'command': 'gh pr create --body-file /missing/public-body.txt'}):
        result = g.pre_tool_call('bash', args, session_id='child')
        assert result is not None and result.get('delegation_eligible') is not True


def test_explicit_principal_only_default_remains_human():
    assert gate.normalize_approvals({}).get('delegation_human_only_default') is False
    assert gate.normalize_approvals({'approvers': {'default': ['principal']}})['delegation_human_only_default'] is True


def test_current_policy_and_original_binding_are_checked_before_inspect_and_decision(example_store):
    from exo_gate.hook import GateAuditError
    import pytest
    g, _ = make_gate(example_store)
    args = {'command': '/owned/venv/bin/check-fixture .'}
    result = g.pre_tool_call('bash', args, session_id='child', tool_call_id='source-call')
    assert result['delegation_eligible']
    context = {'approval_key': result['approval_key'], 'tool_call_id': 'source-call', 'tool': 'bash',
               'args': args, 'risk': 'low', 'cwd': '', 'binding': result.get('binding')}
    assert g.delegated_approval_validate(context)
    assert not g.delegated_approval_validate({**context, 'tool_call_id': 'substituted'})
    assert not g.delegated_approval_validate({**context, 'binding': 'wrong'})
    g.policies()['approvals']['categories']['opaque_exec'] = ['principal']
    assert not g.delegated_approval_validate(context)
    with pytest.raises(GateAuditError):
        g.delegated_approval_audit({'phase': 'delegation_decision'}, 'parent', context)


def test_delegation_audit_requires_actual_signed_ledger(example_store, tmp_path):
    from exo import crypto
    from exo.ledger import Ledger
    from exo_gate.hook import GateAuditError
    import pytest
    g, _ = make_gate(None)
    with pytest.raises(GateAuditError):
        g.delegated_approval_audit({'phase': 'delegation_grant', 'generation': 'fake'}, 'parent')
    key = tmp_path / 'keys' / 'owned-gate.key'
    crypto.save_key(crypto.keygen(), key)
    g, _ = make_gate(example_store, key=key)
    eid = g.delegated_approval_audit({'phase': 'delegation_grant', 'generation': 'fake'}, 'parent')
    assert isinstance(eid, str) and eid
    with Ledger(example_store / 'ledger.sqlite') as ledger:
        rows = list(ledger.iter(kind='action'))
        assert ledger.verify()['ok']
    assert any(row['id'] == eid for row in rows)



def test_explicit_human_only_role_default_and_repeated_normalization():
    policy = gate.normalize_approvals({'approvers': {'default': ['role:human']}, 'roles': {'human': ['principal']}})
    assert policy['delegation_human_only_default']
    assert gate.normalize_approvals(policy)['delegation_human_only_default']
