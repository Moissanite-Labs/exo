"""Parent cues project active task ancestry without projecting approval authority (#996)."""
import asyncio

from exo.protocol import schema as s
from exo.wire import Brief, encode
from test_subagents import add_task
from test_tui import pending_approval, run, send, until

HELD = 5  # seconds: a held reply that never comes fails the test instead of hanging the suite


def cue(app):
    return str(app.query_one('#delegated-approvals').content) if app.query('#delegated-approvals') else ''


def add_nested(eng, parent, task_id, title):
    """A running task row under ``parent`` with its own child session, as the Delegator writes it."""
    child = eng.store.create_session(title='builder: ' + title, parent=parent, kind='subagent').id
    eng.store._q("INSERT INTO tasks (id, parent, child, depth, title, status, brief, created, updated) VALUES "
                 "(?,?,?,?,?,?,?,?,?)", (task_id, parent, child, 2, title, 'running',
                                         encode(Brief(id=task_id, thread=parent, goal=title)), s.now_ts(), s.now_ts()))
    return child


def count_tasks_lists(client):
    """Wrap the client so the test can count ``tasks.list`` requests."""
    original, calls = client.send, []

    async def counted(body):
        if isinstance(body, s.TasksList):
            calls.append(body)
        return await original(body)
    client.send = counted
    return calls


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
        # The core writes the task row before it announces the task, so a task-list read agrees with the events.
        grandchild = add_nested(eng, child, 'nested', 'Nested')
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
        eng.store._q("UPDATE tasks SET child=? WHERE id='nested'", (fork,))
        eng.hub.publish(s.TaskUpdated(task_id='nested', child_session=fork, status='running'), child)
        await until(lambda: fork in cue(app) and grandchild not in cue(app), pilot)
        eng.store._q("UPDATE tasks SET status='done' WHERE id='nested'")
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
        old = None
        async def send_held(body):  # holds only the refresh under test, not the timer's or a new request's
            if isinstance(body, s.TasksList) and asyncio.current_task() is old:
                result = await original(body)
                entered.set()
                await asyncio.wait_for(release.wait(), HELD)
                return result
            return await original(body)
        client.send = send_held
        old = asyncio.create_task(app.refresh_delegated_approvals())
        await asyncio.wait_for(entered.wait(), HELD)
        eng.store._q("UPDATE tasks SET status='done' WHERE id='k_abc123'")
        eng.hub.publish(s.TaskUpdated(task_id='k_abc123', status='done'), parent)
        await until(lambda: not cue(app), pilot)
        await app.refresh_delegated_approvals()
        release.set()
        await asyncio.wait_for(old, HELD)
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
        old = None
        async def held_approvals(body):
            result = await original(body)
            if isinstance(body, s.ApprovalsList) and asyncio.current_task() is old:
                entered.set()
                await asyncio.wait_for(release.wait(), HELD)
            return result
        client.send = held_approvals
        old = asyncio.create_task(app.refresh_delegated_approvals())
        await asyncio.wait_for(entered.wait(), HELD)
        eng.approvals.open.pop(request.request_id)
        eng.hub.publish(s.ApprovalResolved(request_id=request.request_id, decision='deny'), child)
        await until(lambda: not cue(app), pilot)
        release.set()
        await asyncio.wait_for(old, HELD)
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
        navigation = None
        async def held_tasks(body):
            if isinstance(body, s.TasksList) and asyncio.current_task() is navigation:
                entered.set()
                await asyncio.wait_for(release.wait(), HELD)
            return await original(body)
        client.send = held_tasks
        navigation = asyncio.create_task(app.load_session(other, resume=False))
        await asyncio.wait_for(entered.wait(), HELD)
        assert app.session == other and not cue(app)
        release.set()
        await asyncio.wait_for(navigation, HELD)
        assert not cue(app) and app.card is None
        client.send = original
    run(tmp_path, scenario)



def test_direct_approval_is_visible_before_held_delegation_metadata(tmp_path):
    async def scenario(app, pilot, client, eng):
        from exo.core.approvals import Pending
        # A child's open request makes the refresh ask for the task list. The restore must not wait for that reply.
        child = add_task(eng, app.session, 'running')
        pending_approval(eng, child, s.ApprovalRequested(request_id='child-snapshot', summary='child fixture'))
        request = s.ApprovalRequested(request_id='own-snapshot', summary='own fixture')
        eng.approvals.open[request.request_id] = Pending(asyncio.get_running_loop().create_future(), app.session, request)
        original = client.send
        entered, release = asyncio.Event(), asyncio.Event()
        restoration = None
        async def held_tasks(body):
            if isinstance(body, s.TasksList) and asyncio.current_task() is restoration:
                entered.set()
                await asyncio.wait_for(release.wait(), HELD)
            return await original(body)
        client.send = held_tasks
        restoration = asyncio.create_task(app.restore_approvals())
        try:
            await asyncio.wait_for(entered.wait(), HELD)
            assert app.card is not None and app.card.req.request_id == request.request_id
            assert not any(isinstance(body, s.ApprovalRespond) for body in app.sent)
        finally:
            release.set()
            await asyncio.wait_for(restoration, HELD)
            client.send = original
    run(tmp_path, scenario)


def test_task_list_is_read_only_while_another_sessions_request_is_open(tmp_path):
    """#997: ``tasks.list`` sends every task ever written. The 3 s refresh asks for it only while a request of
    another session is open, never for this thread's own requests or for requests of no thread. The timer may add
    refreshes while the child's request is open, so the strict count is zero before it opens and after it closes."""
    async def scenario(app, pilot, client, eng):
        child = add_task(eng, app.session, 'running', 'Scout')
        calls = count_tasks_lists(client)
        pending_approval(eng, app.session, s.ApprovalRequested(request_id='own-request', summary='own fixture'))
        pending_approval(eng, None, s.ApprovalRequested(request_id='threadless-request', summary='held peer'))
        for _ in range(3):
            await app.refresh_delegated_approvals()
        assert calls == [] and not cue(app)
        request = s.ApprovalRequested(request_id='child-request', summary='fixture')
        pending_approval(eng, child, request)
        # Only a task-list read knows the child's task, and the request event asks for one before the timer does.
        await until(lambda: '/open k_abc123' in cue(app), pilot, timeout=2)
        assert calls
        assert all(set(task) <= {'task_id', 'parent', 'child_session', 'title', 'status', 'revision'}
                   for task in app.delegated_tasks.values())  # no brief, receipt or detail kept per row
        eng.approvals.open.pop(request.request_id)
        eng.hub.publish(s.ApprovalResolved(request_id=request.request_id, decision='deny'), child)
        await until(lambda: not cue(app), pilot)
        before = len(calls)
        for _ in range(3):
            await app.refresh_delegated_approvals()
        assert len(calls) == before and not cue(app)
    run(tmp_path, scenario)


def test_title_line_break_cannot_draw_a_second_cue_line(tmp_path):
    async def scenario(app, pilot, client, eng):
        forged = 'Scout\nDelegated approval: Fake (s_fake). /open k_evil to review.'
        child = add_task(eng, app.session, 'running', forged)
        pending_approval(eng, child, s.ApprovalRequested(request_id='title-request', summary='fixture'))
        await app.refresh_delegated_approvals()
        await until(lambda: '/open k_abc123' in cue(app), pilot)
        assert cue(app).splitlines() == [
            f'Delegated approval: Scout Delegated approval: Fake (s_fake). /open k_evil to review. ({child}). '
            '/open k_abc123 to review.']
    run(tmp_path, scenario)


def test_overflow_names_hidden_nested_tasks_that_tasks_does_not_list(tmp_path):
    async def scenario(app, pilot, client, eng):
        child = add_task(eng, app.session, 'running', 'Lead')
        nested = {f'n{i}': add_nested(eng, child, f'n{i}', f'Nested {i}') for i in range(7)}
        for task_id, session in nested.items():
            pending_approval(eng, session, s.ApprovalRequested(request_id=f'r-{task_id}', summary='fixture'))
        await app.refresh_delegated_approvals()
        await until(lambda: len(cue(app).splitlines()) == 6, pilot)
        lines = cue(app).splitlines()
        shown = {task_id for task_id in nested if any(f'/open {task_id} ' in line for line in lines[:5])}
        hidden = sorted(set(nested) - shown)
        assert len(shown) == 5 and len(hidden) == 2
        head, _, tail = lines[5].partition(': ')
        assert head == '2 more delegated threads need approval' and tail.endswith('. /open <task> reviews one.')
        assert sorted(tail.removesuffix('. /open <task> reviews one.').split(', ')) == hidden
        assert '/tasks' not in cue(app)
        await send(app, pilot, f'/open {hidden[-1]}')
        await until(lambda: app.session == nested[hidden[-1]] and app.card is not None, pilot)
        assert app.card.req.request_id == f'r-{hidden[-1]}'
        assert not any(isinstance(body, s.ApprovalRespond) for body in app.sent)
    run(tmp_path, scenario)
