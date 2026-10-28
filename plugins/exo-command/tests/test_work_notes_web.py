"""Read-only inspection of existing working notes, with no model or notebook write path."""
import importlib.util
import json

import pytest

from browser import Browser, Served, find_chrome
from test_chat import FakeSource, call, centre
from test_layout_web import ThreadSrc


@pytest.mark.parametrize("args", ["", "show", "history"])
def test_work_command_uses_existing_scoped_command(args):
    src = FakeSource({"command.run": {"output": {"revision": 0}}})
    assert call(centre(src), "POST", "/api/chat/sessions/private-one/command", {"name": "work", "args": args, "session": "other"}) == (200, {"output": {"revision": 0}})
    assert [(type(cmd).TYPE, cmd.name, cmd.args, cmd.session) for cmd in src.calls] == [("command.run", "work", args, "private-one")]


@pytest.mark.parametrize("args", ["update", "read", "history 2", "show\nupdate", "show;update", {}, [], 1, "x" * 41])
def test_work_command_rejects_non_read_arguments_before_runtime(args):
    src = FakeSource()
    assert call(centre(src), "POST", "/api/chat/sessions/s1/command", {"name": "work", "args": args})[0] == 400
    assert not src.calls


@pytest.mark.parametrize("code,status", [("forbidden", 403), ("unsupported", 501), ("failed", 502)])
def test_work_command_preserves_core_refusal(code, status):
    src = FakeSource(fail={"command.run": code})
    assert call(centre(src), "POST", "/api/chat/sessions/s1/command", {"name": "work", "args": "show"}) == (status, {"error": "nope", "code": code})
    assert len(src.calls) == 1


ROW = {"session": "s1", "revision": 3, "created": 1793100000, "author": "agent", "basis_message": "m1", "stale": True,
       "document": {"goal": "Inspect <img src=x onerror=alert(1)>", "constraints": ["Keep source authority"],
                    "steps": [{"id": "verify", "text": "Run tests", "status": "in_progress"}], "blockers": ["Waiting on data"],
                    "next_action": "Read the evidence", "evidence": [{"kind": "tool", "id": "call1", "note": "Recorded output"}]}}


class WorkSrc(ThreadSrc):
    def __init__(self):
        super().__init__(pairs=0)
        self.sent = []
        self.reads = []

    async def call(self, body):
        kind = type(body).TYPE
        if kind == "session.list":
            return {"sessions": [{"id": s, "title": s, "model": "m", "status": "idle"} for s in ("s1", "s2")]}
        if kind == "command.run" and body.name == "work":
            self.reads.append((body.session, body.args))
            row = dict(ROW, session=body.session)
            return {"output": {"history": [row]} if body.args == "history" else row}
        if kind == "turn.send":
            self.sent.append(body)
        return await super().call(body)


needs_browser = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"), reason="Chrome or websockets missing")


def put(b, text, start=None, end=None):
    start = len(text) if start is None else start
    end = start if end is None else end
    b.eval(f"(()=>{{const i=document.querySelector('.xc-input');i.focus();i.value={json.dumps(text)};i.setSelectionRange({start},{end});i.dispatchEvent(new Event('input'));}})()")


def press(b, label):
    b.eval(f"[...document.querySelectorAll('.xc-work-notes button')].find(x=>x.textContent==={json.dumps(label)}).click()")


@needs_browser
def test_structured_inspector_history_hostile_text_and_draft_selection():
    src = WorkSrc(); srv = Served(src)
    try:
        with Browser(width=1000, height=850) as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            assert not src.reads
            put(b, "keep this draft", 2, 7)
            b.eval("document.querySelector('.xc-work-open').click()")
            b.wait("document.querySelector('.xc-work-notes').textContent.includes('Revision 3')")
            content = b.eval("document.querySelector('.xc-work-notes').textContent")
            for expected in ("Agent-authored working claims", "not independent verification", "Goal", "Constraints", "Steps", "In progress", "Blockers", "Next action", "Sources", "call1", "Stale after transcript undo", "correct the notes in a message", "<img src=x onerror=alert(1)>"):
                assert expected in content
            assert b.eval("document.querySelector('.xc-work-notes img') === null")
            assert b.eval("getComputedStyle(document.querySelector('.xc-side')).display !== 'none'")
            press(b, "History"); b.wait("!!document.querySelector('.xc-work-history')")
            assert b.eval("[document.querySelector('.xc-input').value,document.querySelector('.xc-input').selectionStart,document.querySelector('.xc-input').selectionEnd]") == ["keep this draft", 2, 7]
            assert "Inspect <img" not in b.eval("document.querySelector('.xc-work-notes').textContent")
            press(b, "Current notes"); b.wait("document.querySelector('.xc-work-notes').textContent.includes('Goal')")
            press(b, "Close")
            assert b.eval("document.querySelector('.xc-work-notes').hidden")
            assert not src.sent
            put(b, "/work history"); b.press('Enter', 'Enter', 13)
            b.wait("!!document.querySelector('.xc-work-history')")
            assert not src.sent
            put(b, "/work update"); b.press('Enter', 'Enter', 13)
            b.wait("document.querySelector('.xc-strip').textContent.includes('usage: /work')")
            assert b.eval("document.querySelector('.xc-input').value") == "/work update"
            assert not src.sent
    finally:
        srv.close()


@needs_browser
def test_inspector_response_generations_thread_refresh_close_and_unmount():
    src = WorkSrc(); srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            b.eval("""(()=>{const fetch0=window.fetch;window.notesRequests=[];
              window.fetch=(url,options)=>String(url).endsWith('/command') && JSON.parse(options.body).name==='work'
                ? new Promise(resolve=>window.notesRequests.push({url,body:JSON.parse(options.body),resolve}))
                : fetch0(url,options);})()""")
            b.eval("document.querySelector('.xc-work-open').click()")
            b.wait("window.notesRequests.length === 1")
            b.eval("[...document.querySelectorAll('.xc-sess')].find(x=>x.textContent.includes('s2')).click()")
            b.wait("window.notesRequests.length === 2")
            reply(b, 0, dict(ROW, document={"goal": "OLD THREAD"}))
            assert "OLD THREAD" not in b.eval("document.querySelector('.xc-work-notes').textContent")
            reply(b, 1, dict(ROW, session="s2", document={"goal": "SECOND THREAD"}))
            b.wait("document.querySelector('.xc-work-notes').textContent.includes('SECOND THREAD')")
            press(b, "History"); b.wait("window.notesRequests.length === 3")
            press(b, "Refresh"); b.wait("window.notesRequests.length === 4")
            reply(b, 3, {"history": [dict(ROW, session="s2", revision=8)]})
            b.wait("document.querySelector('.xc-work-notes').textContent.includes('Revision 8')")
            reply(b, 2, {"history": [dict(ROW, session="s2", revision=4)]})
            assert "Revision 4" not in b.eval("document.querySelector('.xc-work-notes').textContent")
            press(b, "Current notes"); b.wait("window.notesRequests.length === 5")
            srv.call(srv.centre.record, {"seq": 1, "type": "turn.completed", "session": "s2", "body": {"turn_id": "t1", "stop_reason": "end"}})
            b.wait("window.notesRequests.length === 6")
            reply(b, 5, dict(ROW, session="s2", revision=9))
            b.wait("document.querySelector('.xc-work-notes').textContent.includes('Revision 9')")
            reply(b, 4, dict(ROW, session="s2", revision=5))
            assert "Revision 5" not in b.eval("document.querySelector('.xc-work-notes').textContent")
            press(b, "Refresh"); b.wait("window.notesRequests.length === 7")
            b.eval("location.hash='#tasks'"); b.wait("!document.querySelector('.xc-composer')")
            reply(b, 6, dict(ROW, session="s2", revision=99))
            b.eval("location.hash='#chat'"); b.wait("!!document.querySelector('.xc-work-open')")
            assert b.eval("document.querySelector('.xc-work-notes').hidden")
            b.eval("document.querySelector('.xc-work-open').click()")
            b.wait("window.notesRequests.length === 8")
            reply(b, 7, dict(ROW, revision=10))
            b.wait("document.querySelector('.xc-work-notes').textContent.includes('Revision 10')")
            assert "Revision 99" not in b.eval("document.querySelector('.xc-work-notes').textContent")
            press(b, "History"); b.wait("window.notesRequests.length === 9"); press(b, "Close")
            reply(b, 8, {"history": [dict(ROW, revision=11)]})
            assert b.eval("document.querySelector('.xc-work-notes').hidden")
            assert not src.sent
    finally:
        srv.close()


def reply(b, index, output, status=200):
    payload = {"output": output} if status == 200 else output
    b.eval(f"window.notesRequests[{index}].resolve(new Response({json.dumps(json.dumps(payload))},{{status:{status}}}))")
    # Flush promise callbacks through a subsequent browser task.
    b.eval("new Promise(resolve=>setTimeout(resolve,0))")


@needs_browser
@pytest.mark.parametrize("output,status,expected", [
    ({"session": "s1", "revision": 0, "document": None, "stale": False}, 200, "No working notes saved"),
    ({"error": "working notes are disabled", "code": "unsupported"}, 501, "Working notes unavailable: working notes are disabled"),
    ({"error": "principal required", "code": "forbidden"}, 403, "Access refused: principal required"),
    ({"error": "not supported by executor", "code": "failed"}, 502, "Working notes unavailable: not supported by executor"),
    (dict(ROW, session="another-private-thread"), 200, "did not match this conversation"),
])
def test_empty_disabled_refused_and_mismatched_notes(output, status, expected):
    src = WorkSrc(); srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            b.eval("""(()=>{const fetch0=window.fetch;window.notesRequests=[];
              window.fetch=(url,options)=>String(url).endsWith('/command') && JSON.parse(options.body).name==='work'
                ? new Promise(resolve=>window.notesRequests.push({resolve})) : fetch0(url,options);})()""")
            b.eval("document.querySelector('.xc-work-open').click()")
            b.wait("window.notesRequests.length === 1"); reply(b, 0, output, status)
            b.wait("document.querySelector('.xc-work-notes').textContent.includes(" + json.dumps(expected) + ")")
            assert not src.sent
    finally:
        srv.close()


@needs_browser
def test_keyboard_focus_survives_controls_and_background_refresh(tmp_path):
    src = WorkSrc(); srv = Served(src)
    try:
        with Browser(width=390, height=850) as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-sess.on')?.textContent.includes('s1')")
            put(b, "preserve my selection", 2, 8)
            b.eval("document.querySelector('.xc-work-open').focus()")
            activate_button(b)
            b.wait("document.querySelector('.xc-work-notes').textContent.includes('Revision 3')")
            assert b.eval("document.activeElement.textContent") == "Current notes"
            b.press('Tab', 'Tab', 9)
            assert b.eval("document.activeElement.textContent") == "History"
            activate_button(b)
            b.wait("!!document.querySelector('.xc-work-history')")
            assert b.eval("document.activeElement.textContent") == "History"
            b.press('Tab', 'Tab', 9)
            assert b.eval("document.activeElement.textContent") == "Refresh"
            activate_button(b)
            b.wait("!!document.querySelector('.xc-work-history')")
            assert b.eval("document.activeElement.textContent") == "Refresh"
            put(b, "preserve my selection", 2, 8)
            b.eval("""(()=>{const fetch0=window.fetch;window.backgroundReads=0;
              window.finishedFindings=0;
              window.fetch=(url,options)=>{if(String(url).endsWith('/command')){const name=JSON.parse(options.body).name;if(name==='work')window.backgroundReads++;if(name==='findings')return fetch0(url,options).then(r=>{window.finishedFindings++;return r;});}return fetch0(url,options);};})()""")
            srv.call(srv.centre.record, {"seq": 1, "type": "turn.completed", "session": "s1", "body": {"turn_id": "t1", "stop_reason": "end"}})
            b.wait("window.backgroundReads === 1")
            b.wait("!!document.querySelector('.xc-work-history')")
            assert b.eval("document.activeElement === document.querySelector('.xc-input')")
            assert b.eval("[document.querySelector('.xc-input').selectionStart,document.querySelector('.xc-input').selectionEnd]") == [2, 8]
            b.wait("window.finishedFindings >= 1")
            b.eval("[...document.querySelectorAll('.xc-work-notes button')].find(x=>x.textContent==='History').focus()")
            srv.call(srv.centre.record, {"seq": 2, "type": "turn.completed", "session": "s1", "body": {"turn_id": "t2", "stop_reason": "end"}})
            b.wait("window.backgroundReads === 2 && window.finishedFindings >= 2")
            b.wait("!!document.querySelector('.xc-work-history')")
            assert b.eval("document.activeElement.textContent") == "History"
            assert b.eval("document.documentElement.scrollWidth <= innerWidth")
            assert b.eval("getComputedStyle(document.querySelector('.xc-side')).display !== 'none'")
            press(b, "Current notes"); b.wait("!!document.querySelector('.xc-work-stale')")
            b.eval("document.querySelector('.xc-work-notes').scrollIntoView({block:'start'})")
            b.shot(tmp_path / "working-notes-mobile.png")
            b.eval("document.querySelector('[aria-label=\\\"Close working notes\\\"]').focus()")
            activate_button(b)
            assert b.eval("document.activeElement === document.querySelector('.xc-work-open')")
            assert b.eval("document.querySelector('.xc-work-notes').hidden")
    finally:
        srv.close()


def activate_button(b):
    # Native Enter activation needs the carriage-return text event, unlike our textarea key handler.
    b.call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")
    b.call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)


def test_composed_notebook_show_and_history_through_web_api(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    from exo import compose
    from exo.config import Config
    from exo.core.models.fake import FakeProvider, tool_turn
    from exo.server.app import Dispatcher
    from exo_command import api
    from exo_command.source import DispatcherSource

    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_PLUGIN_DIRS", str(Path(__file__).resolve().parents[2]))
    principal = tmp_path / "principal"
    principal.mkdir()
    Config({"harness": {"profile": "lean"}, "models": {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
            "cwd": str(tmp_path), "work_notes": {"enabled": True},
            "plugin_config": {"exo-gate": {"principal_store": str(principal),
                "key_path": str(tmp_path / "gate.key"), "decision_model": False}}}, tmp_path / "home").save()
    engine = compose.create()
    provider = FakeProvider([tool_turn("work_notes", {"action": "update", "expected_revision": 0,
        "document": {"goal": "Inspect the real notebook contract", "next_action": "Read through the API"}}), "saved"])
    engine.loop.provider = provider

    async def scenario():
        try:
            sid = (await engine.loop.create_session(agent=engine.agent, cwd=str(tmp_path))).id
            source = DispatcherSource(Dispatcher(engine))
            command_centre = centre(source)
            path = "/api/chat/sessions/" + sid + "/command"
            empty = await api.dispatch(command_centre, "POST", path, {}, {"name": "work", "args": "show"})
            assert empty[0] == 200 and empty[1]["output"]["revision"] == 0
            # Seed through an actual recorded provider tool call and the composed gate, not notebook storage.
            await engine.loop.send(sid, "Save working notes")
            calls, messages = len(provider.calls), engine.store.messages(sid)
            shown = await api.dispatch(command_centre, "POST", path, {}, {"name": "work", "args": "show"})
            history = await api.dispatch(command_centre, "POST", path, {}, {"name": "work", "args": "history"})
            assert shown[0] == history[0] == 200
            assert shown[1]["output"]["document"]["goal"] == "Inspect the real notebook contract"
            assert shown[1]["output"]["revision"] == 1
            rows = history[1]["output"]["history"]
            assert len(rows) == 1 and rows[0]["revision"] == 1 and rows[0]["session"] == sid
            assert "document" not in rows[0]
            assert len(provider.calls) == calls and engine.store.messages(sid) == messages
            again = await api.dispatch(command_centre, "POST", path, {}, {"name": "work", "args": "history"})
            assert again == history  # Inspector reads neither call a model nor append a notebook revision.
        finally:
            await engine.close()

    asyncio.run(asyncio.wait_for(scenario(), 10))
