"""What a first user meets in the web client: a context note from other threads is not shown as a message they typed
(D-028, as in the terminal client), and a second `exo web` on a busy port says what is there and how to get back in,
not a raw bind error. The page tests drive headless Chrome and skip without it."""
from __future__ import annotations

import asyncio
import errno
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from browser import Browser, Served, find_chrome
from exo_command import __main__ as web_main

NOTE = ("[Meanwhile in your other threads, since this thread last looked. Background from your own memory, not the "
        "user's words in this thread.] In thread \"plans\" (1a2b3c4d), 4 min ago:\n  user: the other plan")


def parts(t):
    return [{"type": "text", "text": t}]


# ---------------------------------------------------------------- a second exo web on a busy port
@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    return tmp_path


class QuietSrc:
    centre = None

    async def call(self, body):
        return {"seq": 0, "events": [], "sessions": []}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


def test_a_second_exo_web_says_one_is_running_and_how_to_get_back_in(home, capsys):
    srv = Served(QuietSrc())
    try:
        assert web_main.main(["--port", str(srv.port), "--no-open"]) == 1
    finally:
        srv.close()
    err = capsys.readouterr().err
    assert "Errno" not in err and "address already in use" not in err
    assert "Exo's web client is already running at http://127.0.0.1:{}/".format(srv.port) in err
    assert "link it printed" in err and "#token=" in err and "Ctrl+C" in err
    assert "exo web --port {}".format(srv.port + 1) in err


def test_another_program_on_the_port_is_named_and_other_bind_errors_pass_through(home, capsys, monkeypatch):
    class Other(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><title>Something else</title></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    other = HTTPServer(("127.0.0.1", 0), Other)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    port = other.server_address[1]
    try:
        assert web_main.main(["--port", str(port), "--no-open"]) == 1
    finally:
        other.shutdown()
        other.server_close()
    err = capsys.readouterr().err
    assert "Errno" not in err and "already running" not in err
    assert "127.0.0.1:{} is in use by another program".format(port) in err
    assert "exo web --port {}".format(port + 1) in err

    async def denied(self, host, port):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(web_main.WebServer, "run", denied)
    assert web_main.main(["--port", "80", "--no-open"]) == 1
    err = capsys.readouterr().err
    assert "Permission denied" in err and "already running" not in err and "another program" not in err


# ---------------------------------------------------------------- context notes stay out of the transcript
needs_chrome = pytest.mark.skipif(not find_chrome() or not __import__("importlib").util.find_spec("websockets"),
                                  reason="headless Chrome or the websockets package is missing")


class NoteSrc(QuietSrc):
    async def call(self, body):
        t = type(body).TYPE
        if t == "session.list":
            return {"sessions": [{"id": "s1", "title": "demo", "agent": "atlas", "model": "m", "status": "idle",
                                  "updated": "2026-10-12T10:00:00Z"}]}
        if t == "session.get":
            msgs = [{"id": "u1", "role": "user", "content": parts("what is next?"), "meta": {"author": "principal"}},
                    {"id": "n1", "role": "user", "content": parts(NOTE), "meta": {"note": "threads", "local": True}},
                    {"id": "a1", "role": "assistant", "content": parts("the next step"), "meta": {}}]
            return {"session": {"id": "s1", "model": "m", "agent": "atlas", "cwd": ""}, "messages": msgs, "seq": 0}
        if t == "config.get":
            return {"key": body.key, "value": None}
        return {"seq": 0, "events": []}


USERS = "[...document.querySelectorAll('.xc-msg.xc-user')].map(e => e.textContent)"


@pytest.fixture
def page():
    srv = Served(NoteSrc())
    with Browser() as b:
        b.goto(srv.url())
        b.wait("document.querySelectorAll('.xc-asst').length === 1")
        yield srv, b
    srv.close()


@needs_chrome
def test_a_stored_context_note_is_not_shown_as_a_user_message(page):
    srv, b = page
    assert b.eval(USERS) == ["›what is next?"]
    assert "Meanwhile" not in b.eval("document.querySelector('.xc-log').textContent")


@needs_chrome
def test_a_live_context_note_is_skipped_and_a_live_user_message_still_shows(page):
    srv, b = page
    note = {"id": "n2", "role": "user", "content": parts(NOTE), "meta": {"note": "threads", "local": True}}
    mine = {"id": "u2", "role": "user", "content": parts("and after that?"), "meta": {"author": "principal"}}
    for seq, msg in ((1, note), (2, mine)):
        srv.call(srv.centre.record, {"seq": seq, "type": "message.completed", "session": "s1",
                                     "body": {"message": msg}, "ts": "2026-10-12T10:00:01Z"})
    b.wait("document.querySelector('.xc-log').textContent.includes('and after that?')")
    assert sorted(b.eval(USERS)) == ["›and after that?", "›what is next?"]
    assert "Meanwhile" not in b.eval("document.querySelector('.xc-log').textContent")
