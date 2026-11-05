"""Find loaded conversations and return to live output without losing the working draft."""
import json

import pytest

from browser import Browser, Served, find_chrome
from test_layout_web import ThreadSrc

pytestmark = pytest.mark.skipif(not find_chrome(), reason="Chrome is unavailable")


class WorkspaceSource(ThreadSrc):
    def __init__(self, pairs=40):
        super().__init__(pairs)
        self.rows = [
            {"id": "s1", "title": "Ship <img src=x onerror=alert(1)>", "agent": "atlas", "model": "m", "status": "idle"},
            {"id": "s2", "title": "Research the design", "agent": "nova", "model": "m", "status": "live"},
            {"id": "s3", "title": "Review the result", "agent": "atlas", "model": "m", "status": "idle"},
        ]
        self.calls = []
        self.pending = []

    async def call(self, body):
        self.calls.append(type(body).TYPE)
        if type(body).TYPE == "session.list":
            return {"sessions": self.rows}
        if type(body).TYPE == "approvals.list":
            return {"approvals": self.pending}
        result = await super().call(body)
        if type(body).TYPE == "session.get":
            result["session"] = next(s for s in self.rows if s["id"] == body.session)
        return result


def put(b, selector, text, start=None, end=None):
    b.eval(f"(()=>{{const e=document.querySelector({json.dumps(selector)});e.focus();e.value={json.dumps(text)};e.dispatchEvent(new Event('input'));"
           + (f"e.setSelectionRange({start},{end});e.dispatchEvent(new Event('select'));" if start is not None else "") + "})()")


def choose(b, value):
    b.eval(f"(()=>{{const e=document.querySelector('.xc-thread-filter');e.value={json.dumps(value)};e.dispatchEvent(new Event('change'));}})()")


def rows(b):
    return b.eval("[...document.querySelectorAll('.xc-sess')].map(e=>e.textContent)")


def test_loaded_search_filters_preserve_active_conversation_and_draft():
    src = WorkspaceSource(pairs=0); srv = Served(src)
    try:
        with Browser(width=1200, height=900) as b:
            b.goto(srv.url()); b.wait("document.querySelectorAll('.xc-sess').length===3 && !!document.querySelector('[aria-current=true]')")
            b.settle()   # the stream's first catch-up reads the snapshot again: that read is not the filter's
            assert b.eval("document.querySelector('.xc-sess[aria-current=true]').textContent.includes('Ship <img')")
            assert not b.eval("!!document.querySelector('.xc-sessions img')")
            put(b, '.xc-composer .xc-input', 'keep this draft', 2, 7)
            initial_reads = src.calls.count('session.get')
            put(b, '.xc-thread-search', 'NOVA')
            assert len(rows(b)) == 1 and 'Research' in rows(b)[0]
            assert src.calls.count('session.get') == initial_reads  # filtering never switches the active owner
            assert b.eval("document.querySelector('.xc-thread-scope').textContent") == '1 of 3 loaded conversations'
            put(b, '.xc-thread-search', 's3')
            assert 'Review' in rows(b)[0]
            put(b, '.xc-thread-search', 'nothing matches')
            assert rows(b) == [] and b.eval("document.querySelector('.xc-thread-empty').textContent.includes('No loaded conversations match')")
            b.eval("document.querySelector('.xc-thread-empty button').click()")
            assert len(rows(b)) == 3
            choose(b, 'working')
            assert len(rows(b)) == 1 and 'Research' in rows(b)[0]
            choose(b, 'attention')
            assert rows(b) == []
            src.pending = [{'request_id': 'approval-review', 'session': 's3', 'summary': 'Review this request', 'risk': 'medium', 'options': ['allow', 'deny']}]
            srv.call(srv.centre.record, {'seq': 1, 'type': 'approval.requested', 'session': 's3',
                'body': {'request_id': 'approval-review', 'summary': 'Review this request', 'risk': 'medium', 'options': ['allow', 'deny']}})
            b.wait("document.querySelectorAll('.xc-sess').length===1")
            assert 'Review' in rows(b)[0]
            choose(b, 'all')
            put(b, '.xc-thread-search', 'atlas', 1, 3)
            src.rows.append({'id': 's4', 'title': 'Another conversation', 'agent': 'atlas', 'model': 'm', 'status': 'idle'})
            srv.call(srv.centre.record, {'seq': 2, 'type': 'session.updated', 'session': 's4', 'body': {'session': src.rows[-1]}})
            b.wait("document.querySelector('.xc-thread-scope').textContent==='3 of 4 loaded conversations'")
            assert b.eval("[document.activeElement.className,document.querySelector('.xc-thread-search').selectionStart,document.querySelector('.xc-thread-search').selectionEnd]") == ['xc-thread-search', 1, 3]
            assert b.eval("[document.querySelector('.xc-composer .xc-input').value,document.querySelector('.xc-composer .xc-input').selectionStart,document.querySelector('.xc-composer .xc-input').selectionEnd]") == ['keep this draft', 2, 7]
            b.eval("document.querySelector('.xc-thread-clear').click()")
            assert len(rows(b)) == 4 and b.eval("document.activeElement.classList.contains('xc-thread-search')")
            assert 'turn.send' not in src.calls
    finally:
        srv.close()


@pytest.mark.parametrize('layout', ['inverse', 'classic'])
@pytest.mark.parametrize('width', [1200, 390])
def test_show_latest_keeps_reading_position_and_selection_during_stream(layout, width):
    src = WorkspaceSource(); srv = Served(src)
    try:
        with Browser(width=width, height=900) as b:
            b.goto(srv.url()); b.eval(f"localStorage.setItem('xc-layout',{json.dumps(layout)})"); b.reload()
            b.wait("document.querySelectorAll('.xc-msg').length>30")
            b.settle()   # the start-up redraw is done, so the element read below is the one on screen
            put(b, '.xc-composer .xc-input', 'keep selection', 2, 6)
            b.eval(f"(()=>{{const l=document.querySelector('.xc-log');l.scrollTop={('400' if layout == 'inverse' else 'Math.max(0,l.scrollHeight-l.clientHeight-400)')};l.dispatchEvent(new Event('scroll'));"
                   "window.readingElement=[...l.children].find(e=>e.getBoundingClientRect().bottom>l.getBoundingClientRect().top+10);window.readingTop=readingElement.getBoundingClientRect().top;})()")
            b.wait("!document.querySelector('.xc-latest').hidden")
            srv.call(srv.centre.record, {'seq': 1, 'type': 'message.delta', 'session': 's1',
                'body': {'message_id': 'stream-new', 'text': 'Live output ' + 'new words ' * 200}})
            b.wait("document.querySelector('.xc-streaming')?.textContent.includes('Live output')")
            assert abs(b.eval('readingElement.getBoundingClientRect().top-readingTop')) < 3
            assert b.eval("[document.querySelector('.xc-composer .xc-input').value,document.querySelector('.xc-composer .xc-input').selectionStart,document.querySelector('.xc-composer .xc-input').selectionEnd]") == ['keep selection', 2, 6]
            assert b.eval("document.activeElement===document.querySelector('.xc-composer .xc-input')")
            b.eval("document.querySelector('.xc-latest').click()")
            b.wait("document.querySelector('.xc-latest').hidden")
            assert b.eval("document.activeElement.id") == 'xc-transcript'
            position = b.eval("(()=>{const l=document.querySelector('.xc-log');return [l.scrollTop,l.scrollHeight-l.clientHeight]})()")
            assert abs(position[0] - (0 if layout == 'inverse' else position[1])) < 2
            assert b.eval('document.documentElement.scrollWidth<=innerWidth')
    finally:
        srv.close()


@pytest.mark.parametrize('layout', ['inverse', 'classic'])
def test_a_stream_catch_up_keeps_the_reader_where_they_were(layout):
    """Each time the event stream catches up (on load, and after any reconnect) the view reads its snapshot again and
    redraws the transcript. A reader who scrolled away from the newest messages stays where they were, as they do
    when new output arrives; a reader at the newest messages stays there."""
    src = WorkspaceSource(); srv = Served(src)
    try:
        with Browser(width=1200, height=900) as b:
            b.goto(srv.url()); b.eval(f"localStorage.setItem('xc-layout',{json.dumps(layout)})"); b.reload()
            b.wait("document.querySelectorAll('.xc-msg').length>30")
            b.settle()
            top = b.eval("(()=>{const l=document.querySelector('.xc-log');l.scrollTop="
                         + ('400' if layout == 'inverse' else 'l.scrollHeight-l.clientHeight-400') + ";return l.scrollTop;})()")
            b.wait("!document.querySelector('.xc-latest').hidden")
            reads = src.calls.count('session.get')
            b.eval("import('/static/bus.js').then(bus=>{bus.restart();return true;})")
            b.settle(syncs=2)
            assert src.calls.count('session.get') > reads   # the catch-up did read and redraw the snapshot
            assert abs(b.eval("document.querySelector('.xc-log').scrollTop") - top) < 2
            assert not b.eval("document.querySelector('.xc-latest').hidden")
            b.eval("document.querySelector('.xc-latest').click()")
            b.wait("document.querySelector('.xc-latest').hidden")
            b.eval("import('/static/bus.js').then(bus=>{bus.restart();return true;})")
            b.settle(syncs=3)
            position = b.eval("(()=>{const l=document.querySelector('.xc-log');return [l.scrollTop,l.scrollHeight-l.clientHeight]})()")
            assert abs(position[0] - (0 if layout == 'inverse' else position[1])) < 2
            assert b.eval("document.querySelector('.xc-latest').hidden")
    finally:
        srv.close()


# The first message at least partly in view, and how far its top sits below the top of the transcript's box.
READING = ("(()=>{const top=document.querySelector('.xc-log').getBoundingClientRect().top;"
           "const m=[...document.querySelectorAll('.xc-log .xc-msg')].find(e=>e.getBoundingClientRect().bottom>top);"
           "return [m.textContent,m.getBoundingClientRect().top-top];})()")


@pytest.mark.parametrize('layout', ['inverse', 'classic'])
def test_a_catch_up_with_new_messages_keeps_the_message_being_read_in_place(layout):
    """A catch-up can bring messages that came while the stream was down. The redraw adds them on the newest side
    (above the reader in the inverse layout, below in classic), and the message the reader was looking at stays
    where it was on screen."""
    src = WorkspaceSource(); srv = Served(src)
    try:
        with Browser(width=1200, height=900) as b:
            b.goto(srv.url()); b.eval(f"localStorage.setItem('xc-layout',{json.dumps(layout)})"); b.reload()
            b.wait("document.querySelectorAll('.xc-msg').length>30")
            b.settle()
            b.eval("(()=>{const l=document.querySelector('.xc-log');l.scrollTop="
                   + ('400' if layout == 'inverse' else 'l.scrollHeight-l.clientHeight-400') + ";})()")
            b.wait("!document.querySelector('.xc-latest').hidden")
            count = b.eval("document.querySelectorAll('.xc-log .xc-msg').length")
            text, offset = b.eval(READING)
            src.pairs += 3   # three exchanges arrive while the stream is down
            b.eval("import('/static/bus.js').then(bus=>{bus.restart();return true;})")
            b.settle(syncs=2)
            assert b.eval("document.querySelectorAll('.xc-log .xc-msg').length") == count + 6
            moved = b.eval(f"(()=>{{const top=document.querySelector('.xc-log').getBoundingClientRect().top;"
                           f"const m=[...document.querySelectorAll('.xc-log .xc-msg')].find(e=>e.textContent==={json.dumps(text)});"
                           f"return m.getBoundingClientRect().top-top-({offset});}})()")
            assert abs(moved) < 2
            assert not b.eval("document.querySelector('.xc-latest').hidden")
    finally:
        srv.close()


def test_empty_conversation_asks_for_outcome_and_keeps_route_visible():
    src = WorkspaceSource(pairs=0); srv = Served(src)
    try:
        with Browser(width=390, height=900) as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-hero-title')?.textContent==='What are we working on?'")
            assert b.eval("document.querySelector('.xc-hero-sub').textContent") == 'Describe the outcome, ask a question, or bring a task.'
            assert b.eval("document.querySelector('.xc-new-thread').textContent") == 'New thread'
            assert not b.eval("document.querySelector('.xc-new-thread').classList.contains('xc-primary')")
            b.wait("document.querySelector('.xc-model .xc-v')?.textContent==='m'")
            assert not b.eval("document.querySelector('.xc-hero-text').textContent.includes('memory')")
            assert not b.eval("document.querySelector('.xc-hero-text').textContent.includes('approvals')")
            assert b.eval("document.querySelector('.xc-hero-text').textContent.includes('Shift+Enter')")
            assert not b.eval("!!document.querySelector('.xc-hero-canvas')")
            assert 'turn.send' not in src.calls
    finally:
        srv.close()


def test_mobile_conversation_chooser_keyboard_selection_and_draft_restore():
    src = WorkspaceSource(pairs=0); srv = Served(src)
    try:
        with Browser(width=390, height=900) as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-sess[aria-current=true]')")
            assert not b.eval("document.querySelector('.xc-conversations').open")
            put(b, '.xc-composer .xc-input', 'first draft', 2, 5)
            b.eval("document.querySelector('.xc-conversation-summary').focus()")
            b.press(' ', 'Space', 32)
            b.wait("document.querySelector('.xc-conversations').open")
            assert b.eval("document.querySelector('.xc-conversation-current').textContent.includes('Ship')")
            put(b, '.xc-thread-search', 'nothing')
            b.eval("document.querySelector('.xc-thread-empty button').click()")
            assert b.eval("document.querySelector('.xc-conversations').open")
            b.eval("[...document.querySelectorAll('.xc-sess')].find(e=>e.textContent.includes('Research')).click()")
            b.wait("document.querySelector('.xc-sess[aria-current=true]')?.textContent.includes('Research') && !document.querySelector('.xc-conversations').open")
            assert b.eval("document.activeElement===document.querySelector('.xc-composer .xc-input')")
            put(b, '.xc-composer .xc-input', 'second draft', 1, 4)
            b.eval("document.querySelector('.xc-conversation-summary').click()")
            b.eval("[...document.querySelectorAll('.xc-sess')].find(e=>e.textContent.includes('Ship')).click()")
            b.wait("!document.querySelector('.xc-conversations').open && document.querySelector('.xc-composer .xc-input').value==='first draft'")
            assert b.eval("[document.querySelector('.xc-composer .xc-input').selectionStart,document.querySelector('.xc-composer .xc-input').selectionEnd]") == [2, 5]
            assert 'turn.send' not in src.calls
    finally:
        srv.close()


def test_resizing_never_collapses_focused_conversation_controls():
    src = WorkspaceSource(pairs=0); srv = Served(src)
    try:
        with Browser(width=1200, height=900) as b:
            b.goto(srv.url()); b.wait("document.querySelectorAll('.xc-sess').length===3")
            assert b.eval("document.querySelector('.xc-conversations').open")
            put(b, '.xc-thread-search', 'atlas', 1, 3)
            b.call('Emulation.setDeviceMetricsOverride', width=390, height=900, deviceScaleFactor=1, mobile=False)
            b.wait('innerWidth===390')
            assert b.eval("document.querySelector('.xc-conversations').open && document.activeElement===document.querySelector('.xc-thread-search')")
            assert b.eval("[document.querySelector('.xc-thread-search').selectionStart,document.querySelector('.xc-thread-search').selectionEnd]") == [1, 3]
            b.call('Emulation.setDeviceMetricsOverride', width=1200, height=900, deviceScaleFactor=1, mobile=False)
            b.wait('innerWidth===1200')
            b.eval("document.querySelector('.xc-composer .xc-input').focus()")
            b.call('Emulation.setDeviceMetricsOverride', width=390, height=900, deviceScaleFactor=1, mobile=False)
            b.wait("innerWidth===390 && !document.querySelector('.xc-conversations').open")
    finally:
        srv.close()


def test_global_pending_request_applies_to_needs_you_filter():
    src = WorkspaceSource(pairs=0); srv = Served(src)
    try:
        with Browser(width=1200, height=900) as b:
            b.goto(srv.url()); b.wait("document.querySelectorAll('.xc-sess').length===3")
            choose(b, 'attention')
            assert rows(b) == []
            src.pending = [{'request_id': 'global-request', 'session': None, 'summary': 'A global request needs attention', 'risk': 'medium', 'options': ['allow', 'deny']}]
            srv.call(srv.centre.record, {'seq': 1, 'type': 'approval.requested', 'session': None,
                'body': {'request_id': 'global-request', 'summary': 'A global request needs attention', 'risk': 'medium', 'options': ['allow', 'deny']}})
            b.wait("document.querySelectorAll('.xc-sess').length===3")
            assert len(b.eval("[...document.querySelectorAll('.xc-sess .xc-needs')].map(e=>e.textContent)")) == 3
            assert b.eval("document.querySelector('.xc-approvals').textContent.includes('global request')")
            src.pending = []
            srv.call(srv.centre.record, {'seq': 2, 'type': 'approval.resolved', 'session': None,
                'body': {'request_id': 'global-request', 'decision': 'deny', 'by': 'principal'}})
            b.wait("document.querySelectorAll('.xc-sess').length===0")
    finally:
        srv.close()


@pytest.mark.parametrize('control', ['.xc-sess', '.xc-rename'])
def test_live_conversation_refresh_preserves_focused_row_control(control):
    src = WorkspaceSource(pairs=0); srv = Served(src)
    try:
        with Browser(width=1200, height=900) as b:
            b.goto(srv.url()); b.wait("document.querySelectorAll('.xc-sess').length===3")
            b.eval(f"document.querySelectorAll({json.dumps(control)})[1].focus()")
            src.rows[1]['title'] = 'Research renamed in another window'
            srv.call(srv.centre.record, {'seq': 1, 'type': 'session.updated', 'session': 's2', 'body': {'session': src.rows[1]}})
            b.wait("[...document.querySelectorAll('.xc-sess')].some(e=>e.textContent.includes('Research renamed'))")
            assert b.eval(f"document.activeElement===document.querySelectorAll({json.dumps(control)})[1]")
            assert 'turn.send' not in src.calls
    finally:
        srv.close()


@pytest.mark.parametrize('layout', ['inverse', 'classic'])
def test_appending_inside_a_tall_stream_does_not_move_the_prefix_being_read(layout):
    src = WorkspaceSource(pairs=0); srv = Served(src)
    try:
        with Browser(width=1200, height=900) as b:
            b.goto(srv.url()); b.eval(f"localStorage.setItem('xc-layout',{json.dumps(layout)})"); b.reload()
            b.wait("!!document.querySelector('.xc-sess[aria-current=true]')")
            b.settle()   # a start-up redraw after the reader scrolls would be a second, unrelated move
            srv.call(srv.centre.record, {'seq': 1, 'type': 'message.delta', 'session': 's1',
                'body': {'message_id': 'long-stream', 'text': 'An unchanged prefix. ' + 'A long response is still being written. ' * 1800}})
            b.wait("document.querySelector('.xc-streaming')?.textContent.length>50000")
            b.eval("""(()=>{const log=document.querySelector('.xc-log');log.scrollTop=400;
              const box=log.getBoundingClientRect(), caret=document.caretRangeFromPoint(box.left+60,box.top+90);
              if(!caret||caret.startContainer.nodeType!==Node.TEXT_NODE)throw Error('No visible text anchor');
              window.prefixOffset=caret.startOffset;window.beforeScroll=log.scrollTop;
              const range=document.createRange();range.setStart(caret.startContainer,prefixOffset);range.setEnd(caret.startContainer,prefixOffset+1);
              window.prefixY=range.getBoundingClientRect().top;})()""")
            srv.call(srv.centre.record, {'seq': 2, 'type': 'message.delta', 'session': 's1',
                'body': {'message_id': 'long-stream', 'text': ' More text below the reader. ' * 300}})
            b.wait("document.querySelector('.xc-streaming').textContent.includes('More text below')")
            delta = b.eval("""(()=>{const node=document.querySelector('.xc-streaming .xc-text').firstChild;
              const range=document.createRange();range.setStart(node,prefixOffset);range.setEnd(node,prefixOffset+1);
              return [document.querySelector('.xc-log').scrollTop-beforeScroll,range.getBoundingClientRect().top-prefixY];})()""")
            assert abs(delta[0]) < 2 and abs(delta[1]) < 2
    finally:
        srv.close()


def test_slow_mobile_selection_never_steals_later_search_focus():
    src = WorkspaceSource(pairs=1); srv = Served(src)
    try:
        with Browser(width=390, height=900) as b:
            b.goto(srv.url()); b.wait("document.querySelectorAll('.xc-msg').length===2")
            b.eval("""(()=>{const fetch0=window.fetch;window.fetch=(url,options)=>
              String(url).endsWith('/api/chat/sessions/s2') ? new Promise(resolve=>{
                window.releaseSession=()=>fetch0(url,options).then(resolve);
              }) : fetch0(url,options);})()""")
            b.eval("document.querySelector('.xc-conversation-summary').click()")
            b.eval("[...document.querySelectorAll('.xc-sess')].find(e=>e.textContent.includes('Research')).click()")
            b.wait("!!window.releaseSession")
            assert not b.eval("document.querySelector('.xc-conversations').open")
            assert b.eval("document.activeElement===document.querySelector('.xc-composer .xc-input')")
            b.eval("document.querySelector('.xc-conversation-summary').click()")
            put(b, '.xc-thread-search', 'nova', 1, 3)
            b.eval('window.releaseSession()')
            b.wait("document.querySelectorAll('.xc-msg').length===2")
            assert b.eval("document.querySelector('.xc-conversations').open && document.activeElement===document.querySelector('.xc-thread-search')")
            assert b.eval("[document.querySelector('.xc-thread-search').selectionStart,document.querySelector('.xc-thread-search').selectionEnd]") == [1, 3]
    finally:
        srv.close()


@pytest.mark.parametrize('layout', ['inverse', 'classic'])
def test_pending_home_frame_cannot_override_a_newer_reader_position(layout):
    src = WorkspaceSource(pairs=0); srv = Served(src)
    try:
        with Browser(width=1200, height=900) as b:
            b.goto(srv.url()); b.eval(f"localStorage.setItem('xc-layout',{json.dumps(layout)})"); b.reload()
            b.wait("!!document.querySelector('.xc-sess[aria-current=true]')")
            b.settle()
            put(b, '.xc-composer .xc-input', 'keep the draft', 2, 6)
            b.eval('window.nativeFrame=requestAnimationFrame;window.heldFrames=[];window.requestAnimationFrame=callback=>heldFrames.push(callback)')
            srv.call(srv.centre.record, {'seq': 1, 'type': 'message.delta', 'session': 's1',
                'body': {'message_id': 'long-stream', 'text': 'An unchanged prefix. ' + 'A long response is still being written. ' * 1800}})
            b.wait("document.querySelector('.xc-streaming')?.textContent.length>50000")
            # A real reader scrolls after the first output is rendered but before its delayed home callback runs.
            before = b.eval("(()=>{const log=document.querySelector('.xc-log');log.scrollTop=400;return {top:log.scrollTop,frames:heldFrames.length}})()")
            assert before['frames'] >= 1 and before['top'] == 400
            b.eval('heldFrames.splice(0).forEach(callback=>callback(performance.now()));window.requestAnimationFrame=window.nativeFrame')
            assert b.eval("document.querySelector('.xc-log').scrollTop") == before['top']
            assert b.eval("[document.querySelector('.xc-composer .xc-input').value,document.querySelector('.xc-composer .xc-input').selectionStart,document.querySelector('.xc-composer .xc-input').selectionEnd]") == ['keep the draft', 2, 6]
    finally:
        srv.close()
