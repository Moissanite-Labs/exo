"""#162: every response of the web client carries one strict Content-Security-Policy, nosniff and no-referrer. The page
works under it with no violation (chat, a Markdown panel, the sub-pages), and markup that ever got into the page
could not run script. Raw HTTP for the headers; headless Chrome for the page, skipped without it."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from browser import Browser, Served, find_chrome
from exo_command import server
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre

TOKEN = "page-token-for-tests-0123456789abcdef"
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
          "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Src:
    centre = None

    async def call(self, body):
        t = type(body).TYPE
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        if t == "session.list":
            return {"sessions": [{"id": "s1", "title": "demo", "agent": "atlas", "model": "m", "status": "idle",
                                  "updated": "2026-10-17T10:00:00Z"}]}
        if t == "session.get":
            return {"session": {"id": "s1", "model": "m", "agent": "atlas", "cwd": ""}, "seq": 0, "messages": [
                {"id": "u1", "role": "user", "content": [{"type": "text", "text": "hello"}]},
                {"id": "a1", "role": "assistant", "content": [{"type": "text", "text": "**hi** <script>x()</script>"}]}]}
        if t == "tasks.list":
            return {"tasks": [{"task_id": "t1", "title": "run the tests", "status": "running", "updated_at": 1},
                              {"task_id": "t2", "title": "write the notes", "status": "queued", "updated_at": 1}]}
        if t == "config.get":
            return {"key": body.key, "value": None}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


async def head(port, method, path, headers=None):
    """The status and the response headers (lower-case names) of one request; reads only the head, so it works on
    the event streams too."""
    r, w = await asyncio.open_connection("127.0.0.1", port)
    hs = dict({"Host": "127.0.0.1:{}".format(port)}, **(headers or {}))
    w.write("{} {} HTTP/1.1\r\n{}Content-Length: 0\r\n\r\n".format(
        method, path, "".join("{}: {}\r\n".format(k, v) for k, v in hs.items())).encode())
    await w.drain()
    got = (await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)).decode("latin-1")
    w.close()
    lines = got.split("\r\n")
    out = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        if k:
            assert k.lower() not in out, "header sent twice: " + k
            out[k.lower()] = v.strip()
    return int(lines[0].split()[1]), out


AUTH = {"X-Exo-Token": TOKEN}
CASES = [
    ("page", "GET", "/", None, 200),
    ("static js", "GET", "/static/markdown.js", None, 200),
    ("static css", "GET", "/static/style.css", None, 200),
    ("json route", "GET", "/api/health", AUTH, 200),
    ("event stream", "GET", "/api/events?token=" + TOKEN, None, 200),
    ("seq stream", "GET", "/api/stream?token=" + TOKEN, None, 200),
    ("unknown route", "GET", "/api/nothing-here", AUTH, 404),
    ("missing static", "GET", "/static/nope.js", None, 404),
    ("no token", "GET", "/api/health", None, 403),
    ("bad host", "GET", "/", {"Host": "evil.example:80"}, 403),
    ("wrong method", "GET", "/api/approvals/r1", AUTH, 405),
    # The launch route of #166 where it exists (a token-less POST), else a refusal: the header either way.
    ("launch", "POST", "/api/launch", {"Content-Type": "application/json"}, None),
]


@pytest.mark.parametrize("name,method,path,hdrs,status", CASES, ids=[c[0] for c in CASES])
def test_every_response_carries_the_one_policy(name, method, path, hdrs, status):
    async def go():
        web = WebServer(CommandCentre(load_settings({}), Src()), token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        try:
            return await head(port, method, path, hdrs)
        finally:
            await web.close()
    got, hs = asyncio.run(go())
    if status is not None:
        assert got == status
    assert hs.get("content-security-policy") == POLICY
    assert hs.get("x-content-type-options") == "nosniff"
    assert hs.get("referrer-policy") == "no-referrer"
    assert hs.get("cache-control") in ("no-store", "no-cache")


def test_the_policy_has_no_inline_or_eval_escape_hatch():
    assert server.CSP == POLICY
    for weak in ("unsafe-inline", "unsafe-eval", "unsafe-hashes", "sha256-", "nonce-", "data:", "blob:", "*", "ws:"):
        assert weak not in server.CSP


# ---------------------------------------------------------------- the real page in headless Chrome
needs_chrome = pytest.mark.skipif(not find_chrome() or not __import__("importlib").util.find_spec("websockets"),
                                  reason="headless Chrome or the websockets package is missing")

# Runs before the page's own code in every document: keeps each violation the browser reports.
WATCH = ("window.__csp = []; document.addEventListener('securitypolicyviolation', (e) => window.__csp.push("
         "[e.effectiveDirective, e.blockedURI, e.sample].join(' ')), true);")


@pytest.fixture
def page():
    srv = Served(Src(), token=TOKEN)
    try:
        with Browser() as b:
            b.call("Page.addScriptToEvaluateOnNewDocument", source=WATCH)
            yield srv, b
    finally:
        srv.close()


def settle(b, ms=400):
    b.eval("new Promise((r) => setTimeout(r, {}))".format(ms))


@needs_chrome
def test_the_page_runs_under_the_policy_with_no_violation(page):
    srv, b = page
    b.goto(srv.url())
    b.wait("document.querySelectorAll('.xc-msg').length >= 2")
    # the page really is under the policy, and the sanity probe below proves a violation would be seen
    assert b.eval("fetch('/').then((r) => r.headers.get('content-security-policy'))") == POLICY
    md = "## Notes\n\n**bold** and *em* and `code` and [a link](https://example.com)\n\n- one\n- two\n\n" \
         "<img src=x onerror=alert(1)> <script>alert(2)</script>"
    srv.call(srv.centre.record, {"seq": 1, "type": "ui.panel", "session": "s1", "ts": "2026-10-17T10:00:01Z",
                                 "body": {"panel_id": "p1", "action": "open", "kind": "markdown", "title": "Panel",
                                          "data": {"markdown": md}}})
    b.wait("[...document.querySelectorAll('.xc-side .xc-md strong')].some((e) => e.textContent === 'bold')")
    assert b.eval("document.querySelector('.xc-side .xc-md').textContent.includes('<script>alert(2)</script>')")
    # the reply (plain text on main, Markdown once #149 lands) shows its tag as text too
    assert b.eval("document.querySelector('.xc-log').textContent.includes('<script>x()</script>')")
    assert b.eval("document.querySelectorAll('.xc-side img, .xc-side script, .xc-log img, .xc-log script').length") == 0
    # the command view reads the folded state: two task columns and one priced session draw its board and cost bars
    now = time.time()
    srv.call(srv.centre.state.apply_all, [
        {"seq": 2, "type": "task.updated", "session": "s1", "ts": now, "body": {"task_id": "t1", "status": "running"}},
        {"seq": 3, "type": "task.updated", "session": "s1", "ts": now, "body": {"task_id": "t2", "status": "queued"}},
        {"seq": 4, "type": "usage.updated", "session": "s1", "ts": now,
         "body": {"usage": {"input_tokens": 1200, "output_tokens": 300, "cost_usd": 0.5}}}])
    for view in ("tasks", "schedule", "command", "providers", "chat"):
        b.eval("location.hash = '#{}'".format(view))
        b.wait("document.querySelector('.xc-nav a[aria-current=page]') && "
               "document.querySelector('.xc-nav a[aria-current=page]').textContent.toLowerCase() === '{}'".format(view))
        if view == "tasks":   # inline sizes go through the CSSOM, so they still apply
            b.wait("!!document.querySelector('.xc-board')")
            assert b.eval("getComputedStyle(document.querySelector('.xc-board')).getPropertyValue('--n').trim()") == "2"
            assert b.eval("document.querySelector('.xc-tally-bar span').style.flexGrow") == "1"
        if view == "command":   # command.js has its own h(), so its sizes are checked too
            b.wait("!!document.querySelector('.xc-cmd .xc-board') && !!document.querySelector('.xc-cmd .xc-bar span')")
            assert b.eval("getComputedStyle(document.querySelector('.xc-cmd .xc-board'))"
                          ".getPropertyValue('--n').trim()") == "2"
            assert b.eval("document.querySelector('.xc-cmd .xc-bar span').style.width") == "100%"
        settle(b, 300)
    settle(b)
    assert b.eval("window.__csp") == []
    b.eval("document.body.setAttribute('style', 'color: red')")   # a probe the policy must refuse and report
    b.wait("window.__csp.length > 0")
    assert b.eval("window.__csp")[0].startswith("style-src")


# Markup that got into the page by any route: an inline handler, an inline script element, a javascript: URL and a
# string timer. Each pushes a word if it runs.
INJECT = """(async () => {
  window.__ran = [];
  document.body.insertAdjacentHTML('beforeend', '<img src="/static/missing.png" onerror="window.__ran.push(\\'handler\\')">');
  const s = document.createElement('script'); s.textContent = "window.__ran.push('script')"; document.body.appendChild(s);
  document.body.insertAdjacentHTML('beforeend', '<a id="xc-probe" href="javascript:window.__ran.push(\\'url\\')">x</a>');
  document.getElementById('xc-probe').click();
  setTimeout("window.__ran.push('timer')", 0);
  await new Promise((r) => setTimeout(r, 600));
  return window.__ran.slice().sort();
})()"""


@needs_chrome
@pytest.mark.parametrize("policy", [True, False], ids=["with-policy", "without-policy"])
def test_injected_markup_does_not_run_under_the_policy(page, monkeypatch, policy):
    srv, b = page
    if not policy:   # the control: the same page with no policy runs all four, so the test can see a failure
        monkeypatch.setattr(server, "SECURITY_HEADERS", "Referrer-Policy: no-referrer\r\n", raising=False)
    b.goto(srv.url())
    b.wait("document.querySelectorAll('.xc-msg').length >= 2")
    ran = b.eval(INJECT)
    if policy:
        assert ran == []
        seen = json.dumps(b.eval("window.__csp"))
        assert "script-src" in seen
    else:
        assert ran == ["handler", "script", "timer", "url"]
