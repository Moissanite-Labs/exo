"""An unsupported core cannot turn a human authority command into model input."""
from exo.protocol import schema as s
from test_tui import run


def test_old_core_tui_refuses_before_grant_without_turn_send(tmp_path):
    async def scenario(app, pilot, client, eng):
        original, calls = client.send, []
        async def unsupported(body):
            calls.append(body)
            if isinstance(body, s.CommandRun) and body.name == 'approval-authority':
                return s.Response(id='fake', result={'output': {}})
            return await original(body)
        client.send = unsupported
        await app.slash('approval-authority', 'grant')
        assert 'does not support delegated approval authority' in app.transcript_text()
        assert [body.args for body in calls if isinstance(body, s.CommandRun) and body.name == 'approval-authority'] == ['status']
        assert not any(isinstance(body, s.TurnSend) for body in calls)
        client.send = original
    run(tmp_path, scenario)


def test_human_tui_grant_and_revoke_use_selected_parent_without_model_turn(tmp_path):
    async def scenario(app, pilot, client, eng):
        eng.plugins.hooks.register('delegated_approval_audit', lambda **kw: 'signed-fixture', 'exo-gate')
        await app.slash('approval-authority', 'grant')
        assert app.session in eng.approvals.delegation_grants
        await app.slash('approval-authority', 'revoke')
        assert app.session not in eng.approvals.delegation_grants
        commands = [body for body in app.sent if isinstance(body, s.CommandRun) and body.name == 'approval-authority']
        assert [body.args for body in commands] == ['status', 'grant', 'status', 'revoke']
        assert all(body.session == app.session for body in commands)
        assert not any(isinstance(body, s.TurnSend) for body in app.sent)
    run(tmp_path, scenario)
