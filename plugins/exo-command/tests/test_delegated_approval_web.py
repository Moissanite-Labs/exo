"""Parent visibility uses actual task edges without changing approval authority (#889)."""
from browser import Browser, Served
from test_layout_web import ThreadSrc, needs_chrome


class DelegatedSource(ThreadSrc):
    def __init__(self):
        super().__init__(0)
        self.tasks = []
        self.calls = []
        self.pending = []

    async def call(self, body):
        self.calls.append(type(body).TYPE)
        if type(body).TYPE == "session.list":
            return {"sessions": [dict(id=sid, title=sid, agent="atlas", model="m", status="live" if sid == "s1" else "idle", kind="thread" if sid == "s1" else "subagent", parent=None if sid == "s1" else "s1") for sid in ("s1", "s2", "s3", "fork")]}
        if type(body).TYPE == "session.get":
            return {"session": {"id": body.session, "model": "m", "agent": "atlas", "status": "live" if body.session == "s1" else "idle"}, "messages": [], "seq": 0}
        if type(body).TYPE == "approvals.list":
            return {"approvals": self.pending}
        if type(body).TYPE == "tasks.list":
            return {"tasks": self.tasks}
        return await super().call(body)


def event(srv, seq, kind, session, body):
    ev = {"seq": seq, "type": kind, "session": session, "body": body, "ts": "2026-10-29T10:00:00Z"}
    srv.call(srv.centre.state.apply, ev)
    srv.call(srv.centre.record, ev)


def request(srv, seq, sid):
    srv.source.pending.append({"request_id": "r-" + sid, "session": sid, "summary": "Read fixture", "risk": "low", "options": ["allow_once", "deny"]})
    event(srv, seq, "approval.requested", sid, {"request_id": "r-" + sid, "tool": "read", "summary": "Read fixture", "risk": "low", "options": ["allow_once", "deny"]})


def open_parent(srv, b):
    b.call("Page.addScriptToEvaluateOnNewDocument", source="""
      window.pendingFetches=0;const original=window.fetch;
      window.fetch=async(...args)=>{window.pendingFetches++;try {
        const response=await original(...args),json=response.json.bind(response);
        response.json=async()=>{try{return await json();}finally{window.pendingFetches--;}};
        return response;
      }catch(e){window.pendingFetches--;throw e;}};
    """)
    b.goto(srv.url())
    b.wait("document.querySelector('.xc-state')?.textContent==='working' && window.pendingFetches===0")
    b.eval("new Promise(resolve=>requestAnimationFrame(resolve))")


@needs_chrome
def test_parent_sees_actual_nested_delegation_without_answering_or_losing_draft():
    src = DelegatedSource()
    src.tasks = [dict(task_id="t2", parent="s1", child_session="s2", status="running", title="Scout"), dict(task_id="t3", parent="s2", child_session="s3", status="running", title="Nested scout")]
    srv = Served(src)
    try:
        with Browser() as b:
            open_parent(srv, b)
            b.eval("(()=>{let i=document.querySelector('.xc-input');i.value='parent draft';i.dispatchEvent(new Event('input'));})()")
            request(srv, 1, "fork")
            request(srv, 2, "unrelated")
            request(srv, 3, "s3")
            b.wait("document.querySelector('.xc-delegated-approvals').textContent.includes('Nested scout')")
            assert b.eval("document.querySelector('.xc-state').textContent") == "working"
            assert b.eval("document.querySelectorAll('.xc-delegated-approvals button').length") == 1
            assert b.eval("document.querySelector('.xc-input').value") == "parent draft"
            assert "approval.respond" not in src.calls and "turn.send" not in src.calls
            b.eval("document.querySelector('.xc-delegated-approvals button').click()")
            b.wait("document.querySelector('.xc-conversation-current').textContent.includes('s3')")
            assert "approval.respond" not in src.calls
            b.eval("[...document.querySelectorAll('.xc-sess')].find(e=>e.querySelector('.xc-sess-title').textContent==='s1').click()")
            b.wait("document.querySelector('.xc-conversation-current').textContent==='s1'")
            assert b.eval("document.querySelector('.xc-input').value") == "parent draft"
    finally:
        srv.close()


@needs_chrome
def test_terminal_retarget_and_cycles_do_not_project_stale_or_fork_approvals():
    src = DelegatedSource()
    src.tasks = [dict(task_id="t2", parent="s1", child_session="s2", status="running", title="Scout")]
    srv = Served(src)
    try:
        with Browser() as b:
            open_parent(srv, b)
            request(srv, 1, "s2")
            b.wait("!document.querySelector('.xc-delegated-approvals').hidden")
            event(srv, 2, "task.updated", "s1", dict(task_id="t2", status="running"))
            b.wait("import('/static/bus.js').then(m=>m.lastSeq() >= 2)")
            assert not b.eval("document.querySelector('.xc-delegated-approvals').hidden")
            event(srv, 3, "task.updated", "s1", dict(task_id="t2", child_session="s3", status="running", title="Replacement"))
            b.wait("document.querySelector('.xc-delegated-approvals').hidden")
            request(srv, 4, "fork")
            request(srv, 5, "s3")
            b.wait("document.querySelector('.xc-delegated-approvals').textContent.includes('Replacement')")
            event(srv, 6, "task.updated", "s1", dict(task_id="t2", child_session="s3", status="done"))
            b.wait("document.querySelector('.xc-delegated-approvals').hidden")
            event(srv, 7, "task.updated", "s3", dict(task_id="cycle", child_session="s3", status="running"))
            b.wait("import('/static/bus.js').then(m=>m.lastSeq() >= 7)")
            assert b.eval("document.querySelector('.xc-delegated-approvals').hidden")
    finally:
        srv.close()


@needs_chrome
def test_overlapping_task_snapshots_cannot_revive_terminal_edge():
    src = DelegatedSource()
    src.tasks = [dict(task_id="t2", parent="s1", child_session="s2", status="running", title="Scout")]
    srv = Served(src)
    try:
        with Browser() as b:
            open_parent(srv, b)
            request(srv, 1, "s2")
            b.wait("!document.querySelector('.xc-delegated-approvals').hidden")
            b.eval("""(()=>{const original=window.fetch;window.taskRequests=[];
              window.fetch=(url,opt)=>url==='/api/tasks'?new Promise(resolve=>window.taskRequests.push(resolve)):original(url,opt);
              window.openParent=()=>[...document.querySelectorAll('.xc-sess')].find(e=>e.querySelector('.xc-sess-title').textContent==='s1').click();
              window.releaseTask=(i,status)=>window.taskRequests[i](new Response(JSON.stringify({tasks:[{task_id:'t2',parent:'s1',child_session:'s2',status,title:'Scout'}]}),{status:200}));
              window.openParent();})()""")
            b.wait("window.taskRequests.length===1")
            event(srv, 2, "task.updated", "s1", dict(task_id="t2", status="done"))
            b.wait("document.querySelector('.xc-delegated-approvals').hidden")
            b.eval("window.openParent()")
            b.wait("window.taskRequests.length===2")
            b.eval("window.releaseTask(1,'done')")
            b.wait("document.querySelector('.xc-delegated-approvals').hidden")
            b.eval("window.releaseTask(0,'running')")
            b.eval("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
            assert b.eval("document.querySelector('.xc-delegated-approvals').hidden")
    finally:
        srv.close()


@needs_chrome
def test_reconnect_refreshes_graph_and_unmount_ignores_pending_snapshot():
    src = DelegatedSource()
    srv = Served(src)
    try:
        with Browser() as b:
            open_parent(srv, b)
            request(srv, 1, "s2")
            b.wait("import('/static/bus.js').then(m=>m.lastSeq()>=1)")
            assert b.eval("document.querySelector('.xc-delegated-approvals').hidden")
            src.tasks = [dict(task_id="t2", parent="s1", child_session="s2", status="running", title="Restored scout")]
            b.eval("import('/static/bus.js').then(m=>m.restart())")
            b.wait("!document.querySelector('.xc-delegated-approvals').hidden")
            b.eval("""(()=>{const old=window.fetch;window.fetch=(url,opt)=>url==='/api/tasks'?new Promise(resolve=>window.releaseDelegates=()=>resolve(new Response(JSON.stringify({tasks:[]})))):old(url,opt);
              document.querySelector('.xc-sess.on').click();})()""")
            b.wait("typeof window.releaseDelegates==='function'")
            b.eval("location.hash='#tasks'")
            b.wait("!document.querySelector('.xc-chat-main')")
            b.eval("window.releaseDelegates();new Promise(resolve=>requestAnimationFrame(resolve))")
            assert b.eval("!document.querySelector('.xc-delegated-approvals')")
            assert "approval.respond" not in src.calls and "turn.send" not in src.calls
    finally:
        srv.close()
