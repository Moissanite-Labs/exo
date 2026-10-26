from exo.core.agentlog import AgentLogView
from exo.core.sessions import SessionStore
from exo.core.types import Message
from exo_memory.episodic import EpisodicLayer
from test_agent_log import make


def test_core_recall_skips_recent_requesting_thread_not_other_threads(tmp_path, store):
    db = SessionStore()
    a = db.create_session(agent='razi')
    b = db.create_session(agent='razi')
    private = db.create_session(agent='razi', kind='private')
    db.append_message(a.id, Message.user('orchard old architecture'))
    db.append_message(a.id, Message.user('orchard current local'))
    db.append_message(b.id, Message.user('orchard other project'))
    db.append_message(private.id, Message.user('orchard private project'))
    view = AgentLogView(db, 'razi')
    p = make(tmp_path, store, view)
    try:
        hits = view.search('orchard', n=10, skip_recent=1, session_id=a.id)
        assert {h['session'] for h in hits} == {a.id, b.id}
        assert all('current' not in h['text'] and 'private' not in h['text'] for h in hits)
        result = p.prefetch('orchard project', session_id=a.id).replace('[', '').replace(']', '')
        assert 'other project' in result and 'private project' not in result
        assert len(result) <= p._state().cfg['prefetch_budget'] * 4
    finally:
        p.shutdown()
        db.close()


def test_standalone_recall_keeps_other_thread_after_index_reload(tmp_path):
    layer = EpisodicLayer(tmp_path, compact=False)
    layer.append('user', 'orchard previous local', session='a')
    layer.append('user', 'orchard current local', session='a')
    layer.append('user', 'orchard other project', session='b')
    try:
        hits = layer.search('orchard', n=10, skip_recent=1, session_id='a')
        assert {h['text'] for h in hits} == {'orchard previous local', 'orchard other project'}
        assert all('other project' not in h['text'] for h in layer.search('orchard', n=10, skip_recent=1))
    finally:
        layer.close()
    layer = EpisodicLayer(tmp_path, compact=False)
    try:
        hits = layer.search('orchard', n=10, skip_recent=1, session_id='a')
        assert {h['text'] for h in hits} == {'orchard previous local', 'orchard other project'}
    finally:
        layer.close()


def test_older_host_log_without_session_argument_still_works(tmp_path, store):
    class OldLog:
        def __len__(self):
            return 1

        def search(self, query, n=5, skip_recent=0):
            return [{'id': 1, 'kind': 'user', 'date': '2026-01-01', 'text': 'orchard history'}]

    p = make(tmp_path, store, OldLog())
    try:
        assert 'orchard history' in p.prefetch('orchard history', session_id='thread-a')
    finally:
        p.shutdown()


def test_private_descendants_never_enter_shared_recall_or_zoom(tmp_path, store):
    db = SessionStore()
    private = db.create_session(agent='razi', kind='private')
    child = db.create_session(agent='razi', kind='thread', parent=private.id)
    grandchild = db.create_session(agent='razi', kind='thread', parent=child.id)
    public = db.create_session(agent='razi')
    for session in (private, child, grandchild):
        db.append_message(session.id, Message.user('orchard confidential'))
    db.append_message(public.id, Message.user('orchard public result'))
    view = AgentLogView(db, 'razi')
    p = make(tmp_path, store, view)
    try:
        assert db.private(child.id) and db.private(grandchild.id)
        assert [h['session'] for h in db.search('orchard', limit=1, agent='razi')] == [public.id]
        for sid in ('', public.id):
            assert {h['session'] for h in view.search('orchard', n=5, session_id=sid)} == {public.id}
            assert 'confidential' not in p.prefetch('orchard confidential', session_id=sid)
        assert 'confidential' not in view.zoom(1, 4)
        assert db.search('confidential', session=grandchild.id)  # explicit local history remains available
        db._q('UPDATE sessions SET parent=? WHERE id=?', (grandchild.id, private.id))
        assert [h['session'] for h in db.search('orchard', limit=1, agent='razi')] == [public.id]
    finally:
        p.shutdown()
        db.close()


def test_shared_search_filters_private_descendants_before_result_limit():
    db = SessionStore()
    private = db.create_session(agent='razi', kind='private')
    parent = private
    for _ in range(24):
        parent = db.create_session(agent='razi', parent=parent.id)
        db.append_message(parent.id, Message.user('orchard confidential'))
    public = db.create_session(agent='razi')
    child = db.create_session(agent='razi', parent=public.id)
    db.append_message(child.id, Message.user('orchard public child'))
    db._q('UPDATE sessions SET parent=? WHERE id=?', (child.id, public.id))  # public cycle remains public
    try:
        assert not db.private(child.id)
        assert [h['session'] for h in db.search('orchard', limit=1, agent='razi')] == [child.id]
        assert [h['session'] for h in AgentLogView(db, 'razi').search('orchard', n=1)] == [child.id]
    finally:
        db.close()
