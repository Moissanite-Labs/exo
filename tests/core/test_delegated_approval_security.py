"""The bounded delegation capability does not become principal or a standing approval."""
import asyncio

import pytest

from exo.core.approvals import ApprovalDesk, Pending
from exo.core.types import ToolCall
from exo.principal import CALLER, Caller
from exo.protocol import schema as s
from exo.server.engine import CommandFailed


def grant(desk, action='grant'):
    token = CALLER.set(Caller('principal:test'))
    try:
        return desk.delegation_command('parent', action)
    finally:
        CALLER.reset(token)


def desk_fixture():
    audit, edges = [], [{'task_id': 'task', 'parent': 'parent', 'child_session': 'child', 'status': 'running'}]
    desk = ApprovalDesk(lambda *args: None, lambda *args: None)
    desk.delegation_audit = lambda body, parent, context=None: audit.append(body) or 'signed-fixture'
    desk.delegation_edges = lambda: edges
    desk.delegation_validate = lambda context: True
    desk.delegation_parent_allowed = lambda parent: True
    request = s.ApprovalRequested(request_id='request', summary='fixture', call=ToolCall('call', 'read_file', {'path': 'fixture'}), approvers=['principal'])
    pending = Pending(asyncio.get_running_loop().create_future(), 'child', request, delegation_eligible=True)
    pending.delegation_binding = desk._delegation_digest(pending)
    desk.open['request'] = pending
    return desk, pending, audit, edges


@pytest.mark.parametrize('caller', ['local', 'channel', 'unknown', 'peer:fixture'])
def test_only_principal_can_grant_or_revoke(caller):
    desk = ApprovalDesk(lambda *args: None, lambda *args: None)
    token = CALLER.set(Caller(caller))
    try:
        for action in ('grant', 'revoke', 'status'):
            with pytest.raises(CommandFailed):
                desk.delegation_command('parent', action)
        assert not desk.delegation_grants
    finally:
        CALLER.reset(token)


@pytest.mark.parametrize('mutation', ['deny-standing', 'binding', 'changed-call', 'other-parent', 'terminal', 'retarget', 'fork', 'cycle', 'human-only', 'revoke', 'replace-grant', 'audit-failure', 'human-race', 'audit-revoke'])
def test_scope_binding_and_lifecycle_fail_closed(mutation):
    async def go():
        desk, pending, audit, edges = desk_fixture()
        generation = grant(desk)['generation']
        parent, decision, binding = 'parent', 'allow', pending.delegation_binding
        if mutation == 'deny-standing': decision = 'allow_always'
        elif mutation == 'binding': binding = 'wrong'
        elif mutation == 'changed-call': pending.request.call.arguments['path'] = 'different'
        elif mutation == 'other-parent': parent = 'unrelated'
        elif mutation == 'terminal': edges[0]['status'] = 'done'
        elif mutation == 'retarget': edges[0]['child_session'] = 'different'
        elif mutation == 'fork': edges.clear()
        elif mutation == 'cycle': edges[0]['parent'] = 'child'
        elif mutation == 'human-only': pending.delegation_eligible = False
        elif mutation == 'revoke': grant(desk, 'revoke')
        elif mutation == 'replace-grant': grant(desk)
        elif mutation == 'audit-failure': desk.delegation_audit = lambda *args: None
        elif mutation == 'human-race':
            token = CALLER.set(Caller('principal:test'))
            try: desk.respond(s.ApprovalRespond(request_id='request', decision='deny'))
            finally: CALLER.reset(token)
        elif mutation == 'audit-revoke': desk.delegation_audit = lambda *args: desk.delegation_grants.clear() or 'signed-fixture'
        with pytest.raises(CommandFailed):
            desk.respond_delegated(parent, generation, 'request', binding, decision, {'turn_id': 'turn', 'call_id': 'review', 'author': 'local'})
        assert not pending.future.done() or pending.future.result() == 'deny'
    asyncio.run(go())


@pytest.mark.parametrize('decision', ['allow', 'deny'])
def test_exact_once_and_actor_audit_without_principal_impersonation(decision):
    async def go():
        desk, pending, audit, edges = desk_fixture()
        generation = grant(desk)['generation']
        actor = {'turn_id': 'turn', 'call_id': 'review', 'author': 'delegate'}
        desk.respond_delegated('parent', generation, 'request', pending.delegation_binding, decision, actor)
        assert pending.future.result() == decision and pending.decider == 'delegate:parent'
        assert audit[-1]['actor'] == actor and audit[-1]['tasks'] == ['task']
        with pytest.raises(CommandFailed):
            desk.respond_delegated('parent', generation, 'request', pending.delegation_binding, decision, actor)
        assert not desk.always
    asyncio.run(go())


def test_audit_failure_never_grants_and_revoke_still_removes_grant():
    desk = ApprovalDesk(lambda *args: None, lambda *args: None)
    with pytest.raises(CommandFailed): grant(desk)
    assert not desk.delegation_grants
    desk.delegation_grants['parent'] = 'existing'
    with pytest.raises(CommandFailed): grant(desk, 'revoke')
    assert not desk.delegation_grants


def test_inspection_scrubs_raw_string_leaves_and_keys_before_json_escaping(monkeypatch):
    import json
    from exo.core.models import keys
    async def go():
        desk, pending, audit, edges = desk_fixture()
        secret = 'synthetic-quoted-' + '"-slash-\\-not-real-value\n\t'
        monkeypatch.setattr(keys, 'known_secret_values', lambda: [secret])
        pending.request.call.arguments = {'text': secret, secret: [secret], '[redacted]': 'keep', '[redacted] [field 2]': 'also keep'}
        pending.request.summary = json.dumps({'escaped': secret})
        pending.delegation_binding = desk._delegation_digest(pending)
        generation = grant(desk)['generation']
        assert desk.delegated_listing('parent', generation)[0]['summary'] == 'Review delegated tool call'
        serialized = desk.delegated_listing('parent', generation)[0]['call']
        parsed = json.loads(serialized)['arguments']
        assert secret not in serialized and secret not in str(parsed)
        assert len(parsed) == 4 and parsed['text'] != secret
        assert 'keep' in parsed.values() and 'also keep' in parsed.values()
    asyncio.run(go())


@pytest.mark.parametrize('failure', ['lookup', 'redactor'])
def test_inspection_failure_withholds_all_details_and_exception_text(monkeypatch, failure):
    from exo.core.models import keys
    async def go():
        desk, pending, audit, edges = desk_fixture()
        generation = grant(desk)['generation']
        def fail(*args, **kwargs): raise RuntimeError('private-synthetic-detail')
        monkeypatch.setattr(keys, 'known_secret_values', fail if failure == 'lookup' else lambda: [])
        if failure == 'redactor': monkeypatch.setattr(keys, 'redact_secrets', fail)
        with pytest.raises(CommandFailed) as error:
            desk.delegated_listing('parent', generation)
        assert 'private-synthetic-detail' not in str(error.value)
        assert not pending.future.done()
    asyncio.run(go())
