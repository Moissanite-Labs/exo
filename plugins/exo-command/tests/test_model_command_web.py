"""#146 in the real page: which slash lines the composer holds, and the /model picker's keys (headless Chrome)."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import time

import pytest

from browser import Browser, Served, find_chrome
from exo_command.source import SourceError

needs_chrome = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"),
                                  reason="headless Chrome or the websockets package is missing")
CORE = ["context", "compact", "trace", "findings", "grants", "gate", "peers", "peer-release", "peer-deny", "exo"]


class ModelSrc:
    centre = None

    def __init__(self, failed_lists=0, older=False, core=CORE):
        self.calls = []
        self.failed_lists = failed_lists   # how many commands.list calls fail first (a core restarting)
        self.older = older   # an older core that has no commands.list at all
        self.core = core

    async def call(self, body):
        t = type(body).TYPE
        self.calls.append(body)
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        if t == "session.list":
            return {"sessions": [{"id": "s1", "title": "demo", "agent": "atlas", "model": "fake:x", "status": "idle",
                                  "updated": "2026-10-12T10:00:00Z"}]}
        if t == "session.get":
            return {"session": {"id": "s1", "model": "fake:x", "agent": "atlas", "cwd": ""},
                    "messages": [{"id": "u0", "role": "user", "content": [{"type": "text", "text": "hello"}]}], "seq": 0}
        if t == "commands.list":
            if self.older:
                raise SourceError("unknown_command", "'commands.list'")
            if self.failed_lists:
                self.failed_lists -= 1
                raise ConnectionError("core restarting")
            return {"commands": [{"name": n, "description": "", "args": ""} for n in self.core]}
        if t == "models.list":
            return {"models": [{"route": "fake:x", "role": "main"}], "available": [{"route": "fake:y"}]}
        if t == "turn.send":
            return {"message_id": "m%d" % len(self.calls)}
        if t == "config.get":   # as the real core answers for an unset key
            raise SourceError("not_found", "%s is not set" % body.key)
        return {}

    def sent(self):
        return [c.text for c in self.calls if type(c).TYPE == "turn.send"]

    def of(self, kind):
        return [c for c in self.calls if type(c).TYPE == kind]

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


@pytest.fixture
def page():
    src = ModelSrc()
    srv = Served(src)
    with Browser() as b:
        b.goto(srv.url())
        b.wait("document.querySelectorAll('.xc-msg').length > 0")
        end = time.monotonic() + 10
        while not src.of("commands.list") and time.monotonic() < end:
            time.sleep(0.05)
        b.eval("new Promise((r) => setTimeout(r, 300))")   # the composer has its command names
        yield src, b
    srv.close()


def enter(b, line):
    b.eval("(()=>{const i=document.querySelector('.xc-input');i.focus();i.value=%s;i.dispatchEvent(new Event('input'));})()"
           % json.dumps(line))
    b.press("Escape", "Escape", 27)   # close the suggestion menu, so Enter sends rather than completes
    b.press("Enter", "Enter", 13)


@needs_chrome
def test_paths_and_unknown_words_are_messages_and_core_commands_are_held(page):
    src, b = page
    for line, note in (("/grants revoke g1", "/grants runs in exo tui"), ("/grant list", "did you mean /grants?")):
        enter(b, line)
        b.wait("document.querySelector('.xc-strip').textContent.includes(%s)" % json.dumps(note))
        assert b.eval("document.querySelector('.xc-input').value") == line   # the draft stays
    assert src.sent() == [] and not [c for c in src.of("command.run") if c.name == "grants"]
    lines = ["/tmp is full, what can I delete?", "/home is on another disk", "/me waves", "/frobnicate keep draft",
             "/data is corrupted", "/Users is big", "/orch is next"]   # as in the TUI: no near name, or not a
    # lower-case name. /orch is near /orchestra, exo-orchestra's command, but that plugin is not loaded here.
    for i, line in enumerate(lines):
        enter(b, line)
        end = time.monotonic() + 10
        while len(src.sent()) <= i and time.monotonic() < end:
            time.sleep(0.05)
        # Core receipt can precede the HTTP reply. A new submission waits for the pending guard to settle.
        b.wait("!document.querySelector('.xc-composer .xc-btnrow button').disabled")
    assert src.sent() == lines


@needs_chrome
def test_escape_closes_the_model_picker_and_choosing_refocuses_the_box(page):
    src, b = page
    enter(b, "/model")
    b.wait("!document.querySelector('.xc-model-picker').hidden && document.activeElement.tagName === 'SELECT'")
    b.press("Escape", "Escape", 27)
    b.wait("document.querySelector('.xc-model-picker').hidden")
    assert b.eval("document.activeElement === document.querySelector('.xc-input')")
    enter(b, "/model")
    b.wait("!document.querySelector('.xc-model-picker').hidden")
    b.eval("(()=>{const s=document.querySelector('.xc-model-picker select');s.value='fake:y';"
           "[...document.querySelectorAll('.xc-model-picker button')].find(x=>x.textContent==='Use model').click();})()")
    b.wait("document.querySelector('.xc-model-picker').hidden")
    assert b.eval("document.activeElement === document.querySelector('.xc-input')")
    assert [(c.model, c.session) for c in src.of("models.set")] == [("fake:y", "s1")] and src.sent() == []


def strip_has(b, note):
    b.wait("document.querySelector('.xc-strip').textContent.includes(%s)" % json.dumps(note))


def sent_by(src, n):
    end = time.monotonic() + 10
    while len(src.sent()) < n and time.monotonic() < end:
        time.sleep(0.05)
    return src.sent()


@needs_chrome
def test_terminal_client_commands_and_their_typos_are_held(page):
    """The page runs neither /help nor /quit, yet they are commands every client may meet: kept, not a model turn."""
    src, b = page
    for line, note in (("/help", "/help runs in the terminal client"), ("/quit", "/quit runs in the terminal client"),
                       ("/usage", "/usage runs in the terminal client"), ("/hepl me", "did you mean /help? It runs in the terminal client")):
        enter(b, line)
        strip_has(b, note)
        assert b.eval("document.querySelector('.xc-input').value") == line   # the draft stays
    assert src.sent() == []
    enter(b, "/tmp is full")
    assert sent_by(src, 1) == ["/tmp is full"]


# The command names answer only when the test says: pass (the real answer) or fail (a network error).
GATE = """(() => {
  const real = window.fetch.bind(window);
  window.namesAsked = 0; window.namesServed = 0; window.namesGate = [];
  window.fetch = (url, opt) => String(url).includes("/api/complete/commands")
    ? new Promise((go) => { window.namesAsked++; window.namesGate.push(go); })
      .then((pass) => pass ? real(url, opt).then((r) => { window.namesServed++; return r; }) : Promise.reject(new TypeError("Failed to fetch")))
    : real(url, opt);
})();"""


@needs_chrome
def test_slash_lines_wait_for_the_command_names_and_a_failed_load_is_asked_again():
    src = ModelSrc()
    srv = Served(src)
    try:
        with Browser() as b:
            b.call("Page.addScriptToEvaluateOnNewDocument", source=GATE)
            b.goto(srv.url())
            b.wait("document.querySelectorAll('.xc-msg').length > 0 && window.namesAsked === 1")
            for line in ("/grants revoke g1", "/compact", "/help", "/tmp is full"):   # unknown until the names load: kept
                enter(b, line)
                strip_has(b, "commands are still loading")
                assert b.eval("document.querySelector('.xc-input').value") == line
            enter(b, "plain text goes")
            assert sent_by(src, 1) == ["plain text goes"]
            # The send clears the strip once its answer is in; wait for that, so it cannot wipe the next note.
            b.wait("!document.querySelector('.xc-strip').textContent.includes('commands are still loading')")
            b.eval("window.namesGate.shift()(false)")   # the first load fails
            enter(b, "/compact")
            strip_has(b, "commands are still loading")
            b.wait("window.namesAsked === 2")   # asked again, not left empty for the page's life
            b.eval("window.namesGate.shift()(true)")
            b.wait("window.namesServed === 1")
            b.eval("new Promise((r) => setTimeout(r, 200))")
            for line, note in (("/compact", "/compact runs in exo tui or exo chat"), ("/help", "/help runs in the terminal client")):
                enter(b, line)
                strip_has(b, note)
                assert b.eval("document.querySelector('.xc-input').value") == line
            enter(b, "/tmp is full")
            assert sent_by(src, 2) == ["plain text goes", "/tmp is full"]
            assert b.eval("window.namesAsked") == 2
    finally:
        srv.close()


@needs_chrome
def test_core_names_missing_from_a_good_answer_are_asked_again_by_the_timer():
    """The route answers 200 without the core's names: the backoff timer asks again, with no send to prompt it."""
    src = ModelSrc(failed_lists=1)
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("document.querySelectorAll('.xc-msg').length > 0")
            end = time.monotonic() + 15
            while len(src.of("commands.list")) < 2 and time.monotonic() < end:
                time.sleep(0.05)
            assert len(src.of("commands.list")) == 2 and src.sent() == []   # nothing was typed: the timer asked
            b.eval("new Promise((r) => setTimeout(r, 300))")
            enter(b, "/gate status")
            strip_has(b, "/gate runs in exo tui")
            assert b.eval("document.querySelector('.xc-input').value") == "/gate status" and src.sent() == []
    finally:
        srv.close()


@needs_chrome
def test_a_core_away_for_seven_lists_and_many_sends_is_asked_until_its_names_come():
    """No retry budget runs out, and a send asks without moving the backoff on: after seven failed lists, most of them
    asked by sends, the timer soon asks again by itself and the page then uses the core's names (#146)."""
    src = ModelSrc(failed_lists=7, core=["context", "compact", "gate", "standup"])
    srv = Served(src)

    def lists():
        return len(src.of("commands.list"))
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("document.querySelectorAll('.xc-msg').length > 0")
            end = time.monotonic() + 10
            while not lists() and time.monotonic() < end:
                time.sleep(0.05)
            b.eval("new Promise((r) => setTimeout(r, 300))")
            enter(b, "/help")   # the page's own names and the terminal client's came with the first answer
            strip_has(b, "/help runs in the terminal client")
            end = time.monotonic() + 60
            while src.failed_lists and time.monotonic() < end:   # Enter again and again, as the note invites
                enter(b, "/standup")   # any other name could be one of the core's: held, and the send asks again
                strip_has(b, "commands are still loading")
                assert b.eval("document.querySelector('.xc-input').value") == "/standup"
                time.sleep(0.2)
            assert src.failed_lists == 0 and src.sent() == []
            # The core is back and nothing more is typed. No send moved the backoff on, so the timer asks within seconds
            # (in 30 s had each send spent a step, never had a budget run out).
            end = time.monotonic() + 12
            while lists() < 8 and time.monotonic() < end:
                time.sleep(0.05)
            assert lists() == 8 and src.sent() == []
            b.eval("new Promise((r) => setTimeout(r, 300))")
            for line, note in (("/standup", "/standup runs in exo tui or exo chat"), ("/gate status", "/gate runs in exo tui")):
                enter(b, line)
                strip_has(b, note)
                assert b.eval("document.querySelector('.xc-input').value") == line
            enter(b, "/tmp is full")
            assert sent_by(src, 1) == ["/tmp is full"] and lists() == 8
    finally:
        srv.close()


class GuidanceOffSrc(ModelSrc):
    """A core whose config turns the guidance line off, but which cannot say so while it is down."""

    async def call(self, body):
        t = type(body).TYPE
        if t == "config.get":
            self.calls.append(body)
            if self.failed_lists:
                raise ConnectionError("core restarting")
            return {"key": body.key, "value": False}
        if t == "command.run" and body.name == "findings":
            self.calls.append(body)
            return {"output": {"open": 2}}
        return await super().call(body)


@needs_chrome
def test_a_core_down_at_load_still_turns_the_guidance_line_off_once_it_is_back():
    """The first answers cannot read guidance.enabled and leave the line on; the core's false, once it answers, wins."""
    src = GuidanceOffSrc(failed_lists=2)
    srv = Served(src)
    tip = "document.querySelector('.xc-guide')"
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("%s && !%s.hidden && %s.textContent.includes('2 open finding(s)')" % (tip, tip, tip))
            end = time.monotonic() + 15
            while len(src.of("commands.list")) < 3 and time.monotonic() < end:
                time.sleep(0.05)
            assert len(src.of("commands.list")) == 3 and src.sent() == []
            b.wait("%s.hidden && !%s.textContent" % (tip, tip))
    finally:
        srv.close()


class GuidanceLateSrc(GuidanceOffSrc):
    """commands.list answers at once, but the first config.get fails (the core came up between the two calls)."""

    def __init__(self):
        super().__init__()
        self.config_fails = 1

    async def call(self, body):
        if type(body).TYPE == "config.get" and self.config_fails:
            self.config_fails -= 1
            self.calls.append(body)
            raise ConnectionError("core restarting")
        return await super().call(body)


@needs_chrome
def test_guidance_is_asked_again_when_only_config_get_failed():
    src = GuidanceLateSrc()
    srv = Served(src)
    tip = "document.querySelector('.xc-guide')"
    try:
        with Browser() as b:
            b.goto(srv.url())
            end = time.monotonic() + 15
            while len(src.of("config.get")) < 2 and time.monotonic() < end:
                time.sleep(0.05)
            assert len(src.of("config.get")) >= 2 and len(src.of("commands.list")) >= 2
            b.wait("%s.hidden && !%s.textContent" % (tip, tip))
    finally:
        srv.close()


@needs_chrome
def test_retries_keep_the_open_menus_selection():
    """While the core is down the page asks again on a timer; an answer must not move the menu's selected row."""
    src = ModelSrc(failed_lists=4)
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("document.querySelectorAll('.xc-msg').length > 0")
            b.type(".xc-input", "/")
            b.wait("document.querySelectorAll('.xc-menu-row').length > 2")
            b.press("ArrowDown", "ArrowDown", 40)
            row = "[...document.querySelectorAll('.xc-menu-row')].findIndex((r) => r.classList.contains('on'))"
            assert b.eval(row) == 1
            seen = len(src.of("commands.list"))
            end = time.monotonic() + 15
            while len(src.of("commands.list")) < seen + 1 and time.monotonic() < end:
                time.sleep(0.05)
            b.eval("new Promise((r) => setTimeout(r, 200))")
            assert len(src.of("commands.list")) > seen and b.eval(row) == 1
    finally:
        srv.close()


@needs_chrome
def test_an_older_core_without_commands_list_is_asked_once():
    """An older core says it has no commands.list: the page stops asking and uses its own and the client's names."""
    src = ModelSrc(older=True)
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("document.querySelectorAll('.xc-msg').length > 0")
            end = time.monotonic() + 10
            while not src.of("commands.list") and time.monotonic() < end:
                time.sleep(0.05)
            b.eval("new Promise((r) => setTimeout(r, 300))")
            enter(b, "/help")
            strip_has(b, "/help runs in the terminal client")
            enter(b, "/tmp is full")
            assert sent_by(src, 1) == ["/tmp is full"]
            b.eval("new Promise((r) => setTimeout(r, 1500))")   # past the first retry's delay
            assert len(src.of("commands.list")) == 1
    finally:
        srv.close()


@needs_chrome
def test_a_failed_calls_line_reads_the_attempts_chat_hands_over():
    """#855: a failure before any output is recorded as "all routes for 'main' failed or are cooling down", and its
    cause (here a ReadError) is only in the attempts. chat.js hands both to the guide, so the line is the transport
    one, not the general /trace line."""
    from test_layout_web import ThreadSrc
    srv = Served(ThreadSrc(pairs=0))
    tip = "document.querySelector('.xc-guide')"
    try:
        with Browser() as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-sess.on')")
            b.eval("import('/static/bus.js').then(bus=>bus.onState(st=>{window.synced=st.synced;}))")
            b.wait("window.synced >= 1")
            srv.call(srv.centre.record, {"seq": 1, "type": "model.call", "session": "s1", "body": {
                "call_id": "c1", "route": "", "error": "all routes for 'main' failed or are cooling down; next route reopens in 60s",
                "attempts": [{"route": "p:m", "outcome": "error", "error": "ReadError: ", "ms": 0}]}})
            b.wait("%s && !%s.hidden && %s.textContent.length > 0" % (tip, tip, tip))
            assert b.eval(tip + ".textContent").startswith("the model could not be reached: send it again, or /trace")
    finally:
        srv.close()
