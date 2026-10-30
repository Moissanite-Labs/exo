"""Parent cues project active task ancestry without projecting approval authority (#996)."""
import asyncio

from exo.protocol import schema as s
from test_subagents import add_task
from test_tui import pending_approval, run, send, until


def cue(app):
    return str(app.query_one('#delegated-approvals').content) if app.query('#delegated-approvals') else ''


def test_parent_cue_opens_child_without_answering_or_stealing_focus(tmp_path):
    async def scenario(app, pilot, client, eng):
        parent = app.session
        child = add_task(eng, parent, 'running', '[bold]Scout[/bold]')
        app.busy = True
        app.composer.text = 'parent draft'
        focused = app.focused
        eng.hub.publish(s.TaskUpdated(task_id='k_abc123', child_session=child, status='running', title='[bold]Scout[/bold]'), parent)
        pending_approval(eng, child, s.ApprovalRequested(request_id='child-approval', summary='fixture'))
        await until(lambda: '/open k_abc123' in cue(app), pilot)
        assert app.card is None and not app.approvals and app.busy
        assert app.composer.text == 'parent draft' and app.focused is focused
        assert '[bold]Scout[/bold]' in cue(app)  # rendered as Text, not Rich markup
        assert not any(isinstance(body, s.ApprovalRespond) for body in app.sent)
        await send(app, pilot, '/open k_abc123')
        await until(lambda: app.session == child and app.card is not None, pilot)
        assert app.card.req.request_id == 'child-approval'
        assert not any(isinstance(body, s.ApprovalRespond) for body in app.sent)
    run(tmp_path, scenario)


def test_nested_edges_partial_updates_terminal_retarget_and_forks(tmp_path):
    async def scenario(app, pilot, client, eng):
        parent = app.session
        child = add_task(eng, parent, 'running')
        grandchild = eng.store.create_session(parent=child, kind='subagent').id
        fork = eng.store.create_session(parent=parent, kind='subagent').id
        await app.refresh_delegated_approvals()
        eng.hub.publish(s.TaskUpdated(task_id='nested', child_session=grandchild, status='running', title='Nested'), child)
        pending_approval(eng, grandchild, s.ApprovalRequested(request_id='nested-request', summary='fixture'))
        pending_approval(eng, fork, s.ApprovalRequested(request_id='fork-request', summary='fixture'))
        await until(lambda: '/open nested' in cue(app), pilot)
        assert fork not in cue(app)
        eng.hub.publish(s.TaskUpdated(task_id='nested', status='running'), child)
        await pilot.pause()
        assert '/open nested' in cue(app)
        eng.hub.publish(s.TaskUpdated(task_id='nested', child_session=fork, status='running'), child)
        await until(lambda: fork in cue(app) and grandchild not in cue(app), pilot)
        eng.hub.publish(s.TaskUpdated(task_id='nested', status='done'), child)
        await until(lambda: not cue(app), pilot)
        eng.hub.publish(s.TaskUpdated(task_id='cycle', child_session=grandchild, status='running'), grandchild)
        await pilot.pause()
        assert not cue(app) and app.card is None
    run(tmp_path, scenario)


def test_snapshots_restore_cue_and_older_reply_cannot_undo_newer_terminal(tmp_path):
    async def scenario(app, pilot, client, eng):
        parent = app.session
        child = add_task(eng, parent, 'running')
        pending_approval(eng, child, s.ApprovalRequested(request_id='snapshot-request', summary='fixture'))
        await app.refresh_delegated_approvals()
        assert child in cue(app)
        original = client.send
        entered, release = asyncio.Event(), asyncio.Event()
        held = False
        async def send_held(body):
            nonlocal held
            if isinstance(body, s.TasksList) and not held:
                held = True
                result = await original(body)
                entered.set()
                await release.wait()
                return result
            return await original(body)
        client.send = send_held
        old = asyncio.create_task(app.refresh_delegated_approvals())
        await entered.wait()
        eng.store._q("UPDATE tasks SET status='done' WHERE id='k_abc123'")
        eng.hub.publish(s.TaskUpdated(task_id='k_abc123', status='done'), parent)
        await until(lambda: not cue(app), pilot)
        await app.refresh_delegated_approvals()
        release.set()
        await old
        assert not cue(app)
        client.send = original
    run(tmp_path, scenario)



def test_resolved_during_snapshot_is_not_readded_and_failed_refresh_is_qualified(tmp_path):
    async def scenario(app, pilot, client, eng):
        child = add_task(eng, app.session, 'running')
        request = s.ApprovalRequested(request_id='held-request', summary='fixture')
        pending_approval(eng, child, request)
        await app.refresh_delegated_approvals()
        original = client.send
        entered, release = asyncio.Event(), asyncio.Event()
        async def held_approvals(body):
            result = await original(body)
            if isinstance(body, s.ApprovalsList):
                entered.set()
                await release.wait()
            return result
        client.send = held_approvals
        old = asyncio.create_task(app.refresh_delegated_approvals())
        await entered.wait()
        eng.approvals.open.pop(request.request_id)
        eng.hub.publish(s.ApprovalResolved(request_id=request.request_id, decision='deny'), child)
        await until(lambda: not cue(app), pilot)
        release.set()
        await old
        assert not cue(app)
        client.send = original
        pending_approval(eng, child, s.ApprovalRequested(request_id='next-request', summary='fixture'))
        await until(lambda: child in cue(app), pilot)
        async def fail_tasks(body):
            if isinstance(body, s.TasksList):
                raise RuntimeError('metadata unavailable')
            return await original(body)
        client.send = fail_tasks
        await app.refresh_delegated_approvals()
        assert '(last known)' in cue(app) and app.card is None
        client.send = original
        await app.refresh_delegated_approvals()
        assert '(last known)' not in cue(app)
    run(tmp_path, scenario)



def test_navigation_clears_parent_cue_before_held_child_metadata_reply(tmp_path):
    async def scenario(app, pilot, client, eng):
        child = add_task(eng, app.session, 'running')
        pending_approval(eng, child, s.ApprovalRequested(request_id='nav-request', summary='fixture'))
        await app.refresh_delegated_approvals()
        assert child in cue(app)
        other = eng.store.create_session(title='unrelated').id
        original = client.send
        entered, release = asyncio.Event(), asyncio.Event()
        async def held_tasks(body):
            if isinstance(body, s.TasksList):
                entered.set()
                await release.wait()
            return await original(body)
        client.send = held_tasks
        navigation = asyncio.create_task(app.load_session(other, resume=False))
        await entered.wait()
        assert app.session == other and not cue(app)
        release.set()
        await navigation
        assert not cue(app) and app.card is None
        client.send = original
    run(tmp_path, scenario)



def test_direct_approval_is_visible_before_held_delegation_metadata(tmp_path):
    async def scenario(app, pilot, client, eng):
        from exo.core.approvals import Pending
        request = s.ApprovalRequested(request_id='own-snapshot', summary='own fixture')
        eng.approvals.open[request.request_id] = Pending(asyncio.get_running_loop().create_future(), app.session, request)
        original = client.send
        entered, release = asyncio.Event(), asyncio.Event()
        async def held_tasks(body):
            if isinstance(body, s.TasksList):
                entered.set()
                await release.wait()
            return await original(body)
        client.send = held_tasks
        restoration = asyncio.create_task(app.restore_approvals())
        await entered.wait()
        try:
            assert app.card is not None and app.card.req.request_id == request.request_id
            assert not any(isinstance(body, s.ApprovalRespond) for body in app.sent)
        finally:
            release.set()
            await restoration
            client.send = original
    run(tmp_path, scenario)
