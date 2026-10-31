"""Explicit human grants let the same parent turn review eligible children; no model/network keys."""
import asyncio
import json

import pytest

from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider, tool_turn
from exo.principal import CALLER, Caller
from exo.protocol import schema as s


class Parent:
    name = 'fake'

    async def stream(self, messages, tools, **opts):
        results = [json.loads(result.text()) for message in messages for result in message.tool_results
                   if result.text().startswith('{')]
        if not results and any(message.tool_results for message in messages):
            step = "parent finished"
        elif not results:
            step = tool_turn('delegate', {'goal': 'CHILD read fixture', 'role': 'builder'})
        elif results[-1].get('status') == 'approval_pending':
            row = results[-1]
            step = tool_turn('delegation_review', {'action': 'allow', 'invocation': row['invocation'],
                'generation': row['generation'], 'request_id': row['pending'][0]['request_id'], 'binding': row['pending'][0]['binding']})
        elif results[-1].get('decision'):
            row = next(row for row in reversed(results) if row.get('invocation'))
            step = tool_turn('delegation_review', {'action': 'wait', 'invocation': row['invocation'], 'generation': row['generation']})
        else:
            step = 'parent finished'
        async for event in FakeProvider([step]).stream(messages, tools):
            yield event


def engine(tmp_path):
    eng = CoreEngine(tmp_path / 'home', {'roles': {'main': ['fake:x'], 'aux': ['fake:x']}}, cwd=str(tmp_path), env={})
    audit = []
    eng.delegation_validations = []
    eng.plugins.hooks.register('delegated_approval_validate', lambda context: eng.delegation_validations.append(context) or True, 'exo-gate')
    eng.plugins.hooks.register('delegated_approval_audit', lambda body, session, context=None: audit.append(body) or 'signed-fixture', 'exo-gate')
    eng.plugins.hooks.register('pre_tool_call', lambda tool_name='', **kw: {
        'action': 'approve', 'message': 'read fixture', 'delegation_eligible': True, 'approvers': ['principal']
    } if tool_name == 'read_file' else None, 'exo-gate')
    eng.loop.provider = Parent()
    child = FakeProvider([tool_turn('read_file', {'path': 'fixture.txt'}), 'child finished'])
    eng.agents.route = lambda tier: (child, 'fake:x')
    (tmp_path / 'fixture.txt').write_text('owned fixture')
    return eng, audit


def test_parent_regains_control_approves_child_once_waits_and_finishes(tmp_path):
    async def go():
        eng, audit = engine(tmp_path)
        info = await eng.loop.create_session(title='parent', agent=eng.agent)
        token = CALLER.set(Caller('principal:test'))
        try:
            grant = eng.approvals.delegation_command(info.id, 'grant')
        finally:
            CALLER.reset(token)
        result = await asyncio.wait_for(eng.run_turn(s.TurnSend(session=info.id, text='parent work')), 5)
        assert result.stop_reason == 'end' and result.message.text() == 'parent finished'
        rows = eng.agents.list(info.id)
        assert len(rows) == 1 and rows[0]['status'] == 'done'
        events = eng.store.events_since(session=rows[0]['child_session'])
        assert len([event for event in events if event.type == 'approval.resolved']) == 1
        assert len([body for body in audit if body['phase'] == 'delegation_decision']) == 1
        decision = audit[-1]
        assert decision['generation'] == grant['generation'] and decision['actor']['author'] != 'unknown'
        assert not eng.agents.review_invocations and eng.delegation_validations
        await eng.close()
    asyncio.run(go())


def test_parent_cannot_create_grant_through_its_review_tool(tmp_path):
    async def go():
        eng, audit = engine(tmp_path)
        from exo.core.tools.registry import ToolContext, ToolError
        with pytest.raises(ToolError, match='matching owned'):
            await eng.agents.review_tool({'action': 'grant', 'invocation': 'fake', 'generation': 'fake'}, ToolContext(session='parent'))
        assert not eng.approvals.delegation_grants and not audit
        await eng.close()
    asyncio.run(go())


def test_default_off_keeps_synchronous_delegation_and_human_card(tmp_path):
    async def go():
        eng, audit = engine(tmp_path)
        parent = (await eng.loop.create_session(title='parent', agent=eng.agent)).id
        running = asyncio.create_task(eng.run_turn(s.TurnSend(session=parent, text='parent work')))
        for _ in range(100):
            if eng.approvals.open:
                break
            await asyncio.sleep(.01)
        assert eng.approvals.open and all(row['generation'] is None for row in eng.agents.review_invocations.values()) and not audit
        rid = next(iter(eng.approvals.open))
        token = CALLER.set(Caller('principal:test'))
        try:
            eng.approvals.respond(s.ApprovalRespond(request_id=rid, decision='deny'))
        finally:
            CALLER.reset(token)
        await asyncio.wait_for(running, 5)
        await eng.close()
    asyncio.run(go())



@pytest.mark.parametrize('mode', ['end', 'close', 'cancel'])
def test_yielded_children_are_joined_on_parent_end_or_core_close(tmp_path, mode):
    async def go():
        eng, audit = engine(tmp_path)
        parent = (await eng.loop.create_session(title='parent', agent=eng.agent)).id
        token = CALLER.set(Caller('principal:test'))
        try: eng.approvals.delegation_command(parent, 'grant')
        finally: CALLER.reset(token)
        entered, stopped = asyncio.Event(), asyncio.Event()
        class StopParent(Parent):
            async def stream(self, messages, tools, **opts):
                if any(message.tool_results for message in messages):
                    entered.set()
                    if mode in ('close', 'cancel'):
                        try: await asyncio.Event().wait()
                        finally: stopped.set()
                    else:
                        async for event in FakeProvider(['ending without join']).stream(messages, tools): yield event
                else:
                    async for event in super().stream(messages, tools, **opts): yield event
        eng.loop.provider = StopParent()
        eng.start_turn(parent, 'parent work', {'author': 'principal'})
        primary = next(iter(eng._tasks))
        await asyncio.wait_for(entered.wait(), 5)
        if mode == 'close':
            assert eng.agents.review_invocations and eng.approvals.open
            await asyncio.wait_for(eng.close(), 5)
            assert stopped.is_set()
        elif mode == 'cancel':
            primary.cancel()
            with pytest.raises(asyncio.CancelledError): await primary
            assert stopped.is_set() and not eng.agents.review_invocations and not eng.approvals.open
            await eng.close()
        else:
            for _ in range(100):
                if not eng._tasks: break
                await asyncio.sleep(.01)
            assert not eng._tasks
            rows = eng.agents.list(parent)
            assert rows[0]['status'] in ('cancelled', 'failed')
            await eng.close()
        assert not eng.approvals.open and not eng.agents.loops
        assert not any(not task.done() for task in eng._tasks)
    asyncio.run(go())



def test_grant_while_synchronous_delegate_waits_wakes_same_parent_turn(tmp_path):
    async def go():
        eng, audit = engine(tmp_path)
        parent = (await eng.loop.create_session(title='parent', agent=eng.agent)).id
        running = asyncio.create_task(eng.run_turn(s.TurnSend(session=parent, text='parent work')))
        for _ in range(100):
            if eng.approvals.open: break
            await asyncio.sleep(.01)
        assert eng.approvals.open and all(row['generation'] is None for row in eng.agents.review_invocations.values())
        token = CALLER.set(Caller('principal:test'))
        try: eng.approvals.delegation_command(parent, 'grant')
        finally: CALLER.reset(token)
        result = await asyncio.wait_for(running, 5)
        assert result.stop_reason == 'end' and result.message.text() == 'parent finished'
        assert len([body for body in audit if body['phase'] == 'delegation_decision']) == 1
        await eng.close()
    asyncio.run(go())


def test_stubborn_child_stays_owned_and_prevents_store_close(tmp_path, monkeypatch):
    import exo.core.engine as engine_module
    import exo.core.agents.delegate as delegate_module
    monkeypatch.setattr(engine_module, 'TURN_CLOSE_GRACE', .01)
    monkeypatch.setattr(engine_module, 'TURN_CANCEL_GRACE', .01)
    monkeypatch.setattr(delegate_module, 'STOP_GRACE', .01)
    async def go():
        eng, audit = engine(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        class StubbornChild:
            name = 'fake'
            async def stream(self, messages, tools, **opts):
                if not any(message.tool_results for message in messages):
                    step = tool_turn('read_file', {'path': 'fixture.txt'})
                else:
                    entered.set()
                    while not release.is_set():
                        try: await release.wait()
                        except asyncio.CancelledError: pass
                    step = 'released'
                async for event in FakeProvider([step]).stream(messages, tools): yield event
        class AbandonParent(Parent):
            async def stream(self, messages, tools, **opts):
                if any('"decision"' in result.text() for message in messages for result in message.tool_results):
                    await entered.wait()
                    async for event in FakeProvider(['ending']).stream(messages, tools): yield event
                else:
                    async for event in super().stream(messages, tools, **opts): yield event
        eng.agents.route = lambda tier: (StubbornChild(), 'fake:x')
        eng.loop.provider = AbandonParent()
        parent = (await eng.loop.create_session(title='parent', agent=eng.agent)).id
        token = CALLER.set(Caller('principal:test'))
        try: eng.approvals.delegation_command(parent, 'grant')
        finally: CALLER.reset(token)
        eng.start_turn(parent, 'parent work', {'author': 'principal'})
        primary = next(iter(eng._tasks))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            done, _ = await asyncio.wait({primary}, timeout=1)
            assert primary in done
            with pytest.raises(RuntimeError, match='cleanup incomplete'): primary.result()
            assert any(not task.done() for task in eng._tasks)
            with pytest.raises(RuntimeError, match='did not stop'): await eng.close()
            assert eng.store.get_session(parent) is not None  # store remains open for the survivor
            row = eng.agents.list(parent)[0]
            assert row['status'] == 'failed' and 'cleanup incomplete' in row['detail']
        finally:
            release.set()
            await asyncio.gather(*tuple(eng._tasks), return_exceptions=True)
            await eng.close()
    asyncio.run(go())


def test_worker_grant_refused_but_fork_with_same_kind_and_status_revoke_work(tmp_path):
    async def go():
        from exo.server.engine import CommandFailed
        eng, audit = engine(tmp_path)
        parent = (await eng.loop.create_session(title='parent', agent=eng.agent)).id
        worker = eng.store.create_session(title='worker', parent=parent, kind='subagent', agent=eng.agent).id
        fork = eng.store.create_session(title='separate fork', parent=worker, kind='subagent', agent=eng.agent).id
        eng.store._q("INSERT INTO tasks (id,parent,child,depth,title,status,brief,created,updated) VALUES (?,?,?,?,?,?,?,?,?)",
                     ('task-worker', parent, worker, 1, 'worker', 'running', '', s.now_ts(), s.now_ts()))
        token = CALLER.set(Caller('principal:test'))
        try:
            before = list(audit)
            with pytest.raises(CommandFailed, match='not a delegated worker'):
                eng.approvals.delegation_command(worker, 'grant')
            assert worker not in eng.approvals.delegation_grants and audit == before
            assert eng.approvals.delegation_command(worker, 'status')['enabled'] is False
            assert eng.approvals.delegation_command(worker, 'revoke')['enabled'] is False
            assert eng.approvals.delegation_command(fork, 'grant')['enabled'] is True
            eng.approvals.delegation_command(fork, 'revoke')
        finally:
            CALLER.reset(token)
        await eng.close()
    asyncio.run(go())
