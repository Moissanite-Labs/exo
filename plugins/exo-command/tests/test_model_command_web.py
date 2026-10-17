"""#146 in the real page: which slash lines the composer holds, and the /model picker's keys (headless Chrome)."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import time

import pytest

from browser import Browser, Served, find_chrome

needs_chrome = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"),
                                  reason="headless Chrome or the websockets package is missing")
CORE = ["context", "compact", "trace", "findings", "grants", "gate", "peers", "peer-release", "peer-deny", "exo"]


class ModelSrc:
    centre = None

    def __init__(self, failed_lists=0):
        self.calls = []
        self.failed_lists = failed_lists   # how many commands.list calls fail first (a core restarting)

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
            if self.failed_lists:
                self.failed_lists -= 1
                raise ConnectionError("core restarting")
            return {"commands": [{"name": n, "description": "", "args": ""} for n in CORE]}
        if t == "models.list":
            return {"models": [{"route": "fake:x", "role": "main"}], "available": [{"route": "fake:y"}]}
        if t == "turn.send":
            return {"message_id": "m%d" % len(self.calls)}
        if t == "config.get":
            return {"key": body.key, "value": None}
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
             "/data is corrupted", "/Users is big"]   # as in the TUI: no near name, or not a lower-case name
    for i, line in enumerate(lines):
        enter(b, line)
        end = time.monotonic() + 10
        while len(src.sent()) <= i and time.monotonic() < end:
            time.sleep(0.05)
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
def test_core_names_missing_from_a_good_answer_are_asked_again():
    src = ModelSrc(failed_lists=1)   # the route answers 200, but without the core's names
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("document.querySelectorAll('.xc-msg').length > 0")
            enter(b, "/gate status")   # a core-only name, outside the shared table: unknown until the names load
            strip_has(b, "commands are still loading")
            end = time.monotonic() + 15
            while len(src.of("commands.list")) < 2 and time.monotonic() < end:
                time.sleep(0.05)
            assert len(src.of("commands.list")) == 2   # asked again after the answer without core names
            b.eval("new Promise((r) => setTimeout(r, 300))")
            enter(b, "/gate status")
            strip_has(b, "/gate runs in exo tui")
            assert b.eval("document.querySelector('.xc-input').value") == "/gate status" and src.sent() == []
    finally:
        srv.close()
