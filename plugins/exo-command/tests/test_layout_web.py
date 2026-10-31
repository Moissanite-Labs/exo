"""D-046 in the web client: the inverse layout (box on top, newest first), the layout switch and its saved choice, and a
chat that opens at its newest item in both layouts. The behaviour tests drive headless Chrome and skip without it."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from browser import Browser, Served, find_chrome

STATIC = Path(__file__).resolve().parents[1] / "exo_command" / "static"
NODE = shutil.which("node")


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- static checks
def test_the_layout_module_names_the_default_and_how_to_flip_it():
    src = text("layout.js")
    assert 'export const DEFAULT_LAYOUT = "inverse"' in src
    assert 'change DEFAULT_LAYOUT to "classic"' in src


def test_the_chat_view_uses_the_layout_module_and_has_a_switch_and_a_command():
    src = text("chat.js")
    assert 'from "./layout.js"' in src and "xc-layout" in src and "/layout" in src
    assert "columnOrder(layout)" in src and "ordered(items, layout)" in src


def _missing_css_tokens(stylesheets, runtime=()):
    # A small lexical check for our stylesheets, not a CSS parser. Ignore comments and
    # quoted content so examples and generated text cannot declare or reference tokens.
    clean = "\n".join(re.sub(r'/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', "", css, flags=re.S)
                      for css in stylesheets)
    declared = set(re.findall(r"(--[\w-]+)\s*:", clean)) | set(runtime)
    # A fallback makes an optional token valid; nested var() fallbacks are checked too.
    required = {name for name, delimiter in re.findall(r"var\(\s*(--[\w-]+)\s*([,)])", clean)
                if delimiter == ")"}
    return required - declared


def test_the_layout_styles_use_the_site_tokens_only():
    block = text("style.css").split("layout experiment (D-046)")[1]
    assert "xc-inverse" in block and not re.search(r"#[0-9a-fA-F]{3,6}\b", block)
    # Board column counts come from these renderers, rather than a site-wide CSS token.
    for producer in ("command.js", "tasks.js"):
        assert re.search(r"\bstyle\s*:\s*[\"']--n:", text(producer)), producer
    missing = _missing_css_tokens([text(path.name) for path in STATIC.glob("*.css")], runtime={"--n"})
    assert not missing, "Undefined CSS tokens: " + ", ".join(sorted(missing))


@pytest.mark.parametrize("token", ["--surface-2", "--mono", "--no-such-token"])
def test_style_token_contract_rejects_undefined_picker_tokens(monkeypatch, token):
    original = text
    monkeypatch.setitem(globals(), "text", lambda name: original(name) + (
        "\n.xc-model-row { color: var(" + token + "); }" if name == "style.css" else ""))
    with pytest.raises(AssertionError, match=re.escape(token)):
        test_the_layout_styles_use_the_site_tokens_only()


@pytest.mark.parametrize("producer", ["command.js", "tasks.js"])
def test_style_token_contract_requires_the_runtime_column_producer(monkeypatch, producer):
    original = text
    monkeypatch.setitem(globals(), "text", lambda name: "" if name == producer else original(name))
    with pytest.raises(AssertionError, match=re.escape(producer)):
        test_the_layout_styles_use_the_site_tokens_only()


def test_style_token_check_handles_comments_strings_fallbacks_and_shared_declarations():
    assert not _missing_css_tokens([
        ':root { --fg: black; } /* var(--comment-only) */',
        '.row { color: var(--fg); padding: var(--optional, 20px); content: "var(--quoted)"; }',
    ])
    assert _missing_css_tokens([
        '/* --fake: black; */ .row { color: var(--fake); background: var(--optional, var(--missing)); }',
    ]) == {"--fake", "--missing"}


def test_the_chat_box_has_a_definite_height_so_the_log_scrolls_inside_it():
    css = text("style.css")
    line = next(ln for ln in css.splitlines() if ln.startswith(".xc-chat {"))
    assert "flex: 0 0 auto" in line  # flex: 1 let a long thread grow the whole page, so a chat opened at its top


# ---------------------------------------------------------------- the pure functions, in node
def node(tmp_path, body):
    (tmp_path / "layout.mjs").write_text(text("layout.js"))
    (tmp_path / "t.mjs").write_text('import * as l from "./layout.mjs";\n' + body)
    out = subprocess.run([NODE, str(tmp_path / "t.mjs")], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_js_ordering_home_scroll_and_saved_choice(tmp_path):
    got = node(tmp_path, '''
const mem = () => { const m = {}; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = v; } }; };
const s = mem();
const first = l.getLayout(s);
l.setLayout("classic", s); const second = l.getLayout(s);
const bad = l.setLayout("sideways", s); const third = l.getLayout(s);
const junk = mem(); junk.setItem("xc-layout", "weird");
console.log(JSON.stringify({
  def: l.DEFAULT_LAYOUT, first, second, bad, third, junk: l.getLayout(junk), broken: l.getLayout({ getItem() { throw new Error("no"); } }),
  inv: l.ordered([1, 2, 3], "inverse"), cls: l.ordered([1, 2, 3], "classic"),
  homeInv: l.homeScroll("inverse", 5000, 700), homeCls: l.homeScroll("classic", 5000, 700), short: l.homeScroll("classic", 300, 700),
  nearInv: [l.nearHome("inverse", 40, 5000, 700), l.nearHome("inverse", 900, 5000, 700)],
  nearCls: [l.nearHome("classic", 4250, 5000, 700), l.nearHome("classic", 10, 5000, 700)],
  orderInv: l.columnOrder("inverse"), orderCls: l.columnOrder("classic"),
}));''')
    assert got["def"] == "inverse" and got["first"] == "inverse" and got["second"] == "classic"
    assert got["bad"] is False and got["third"] == "classic"
    assert got["junk"] == "inverse" and got["broken"] == "inverse"
    assert got["inv"] == [3, 2, 1] and got["cls"] == [1, 2, 3]
    assert got["homeInv"] == 0 and got["homeCls"] == 4300 and got["short"] == 0
    assert got["nearInv"] == [True, False] and got["nearCls"] == [True, False]
    assert got["orderInv"][:4] == ["banner", "status", "guide", "composer"] and got["orderInv"].index("stage") > got["orderInv"].index("composer")
    assert got["orderCls"].index("status") > got["orderCls"].index("stage")
    assert got["orderCls"][-1] == "menu" and got["orderCls"].index("stage") < got["orderCls"].index("composer")
    assert sorted(got["orderInv"]) == sorted(got["orderCls"])


# ---------------------------------------------------------------- the real page in headless Chrome
needs_chrome = pytest.mark.skipif(not find_chrome() or not __import__("importlib").util.find_spec("websockets"),
                                  reason="headless Chrome or the websockets package is missing")


def parts(t):
    return [{"type": "text", "text": t}]


class ThreadSrc:
    centre = None

    def __init__(self, pairs=40):
        self.pairs = pairs

    async def call(self, body):
        t = type(body).TYPE
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        if t == "session.list":
            return {"sessions": [{"id": "s1", "title": "demo", "agent": "atlas", "model": "m", "status": "idle",
                                  "updated": "2026-10-12T10:00:00Z"}]}
        if t == "session.get":
            msgs = []
            for i in range(self.pairs):
                msgs.append({"id": f"u{i}", "role": "user", "content": parts(f"question {i}")})
                msgs.append({"id": f"a{i}", "role": "assistant", "content": parts(f"answer {i} " + "lorem ipsum " * 20)})
            return {"session": {"id": "s1", "model": "m", "agent": "atlas", "cwd": ""}, "messages": msgs, "seq": 0}
        if t == "config.get":
            return {"key": body.key, "value": None}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


GEOMETRY = ("(()=>{const l=document.querySelector('.xc-log');const i=document.querySelector('.xc-input');"
            "return {top:l.scrollTop,h:l.scrollHeight,c:l.clientHeight,page:window.scrollY,inputY:i.getBoundingClientRect().top,"
            "inH:innerHeight,first:l.firstElementChild.textContent,last:l.lastElementChild.textContent,"
            "msgs:document.querySelectorAll('.xc-msg').length}})()")


@pytest.fixture
def page():
    srv = Served(ThreadSrc())
    with Browser() as b:
        yield srv, b
    srv.close()


def open_chat(srv, b, layout):
    b.goto(srv.url())
    b.eval(f'localStorage.setItem("xc-layout", "{layout}")')
    b.goto(srv.url())
    b.wait("document.querySelectorAll('.xc-msg').length > 20")
    b.wait("(()=>{const l=document.querySelector('.xc-log');return l.scrollHeight > l.clientHeight})()")
    b.eval("new Promise((r) => setTimeout(r, 700))")   # the fade-in and the frame callback have run
    return b.eval(GEOMETRY)


@needs_chrome
def test_classic_opens_at_the_newest_item_at_the_bottom(page):
    srv, b = page
    g = open_chat(srv, b, "classic")
    assert g["h"] > g["c"] and abs(g["top"] - (g["h"] - g["c"])) < 2     # scrolled to the end of the log
    assert g["page"] == 0 and 0 < g["inputY"] < g["inH"]                  # the page itself does not scroll; the box is on screen
    assert g["last"].startswith("answer 39") and g["first"].endswith("question 0")


@needs_chrome
def test_inverse_opens_with_the_box_on_top_and_the_newest_item_under_it(page):
    srv, b = page
    g = open_chat(srv, b, "inverse")
    assert g["top"] == 0 and g["page"] == 0
    assert 0 <= g["inputY"] - b.eval("document.querySelector('.xc-chat-main').getBoundingClientRect().top") < 120  # top of the conversation, below pane controls
    assert g["first"].startswith("answer 39") and g["last"].endswith("question 0")
    assert b.eval("document.querySelector('.xc-chat-main').classList.contains('xc-inverse')")
    order = b.eval("[...document.querySelector('.xc-chat-main').children].map(e=>e.className.split(' ')[0])")
    assert order.index("xc-composer") < order.index("xc-stage")


@needs_chrome
def test_the_button_switches_layout_and_the_choice_is_saved(page):
    srv, b = page
    open_chat(srv, b, "inverse")
    b.eval("document.querySelector('.xc-layout').click()")
    b.wait("localStorage.getItem('xc-layout') === 'classic'")
    g = b.eval(GEOMETRY)
    assert g["last"].startswith("answer 39") and g["inputY"] > 400
    b.goto(srv.url())                                                     # a reload keeps the choice
    b.wait("document.querySelectorAll('.xc-msg').length > 20")
    assert b.eval("localStorage.getItem('xc-layout')") == "classic"
    assert not b.eval("document.querySelector('.xc-chat-main').classList.contains('xc-inverse')")


@needs_chrome
def test_slash_layout_switches_too(page):
    srv, b = page
    open_chat(srv, b, "inverse")
    b.eval("(()=>{const i=document.querySelector('.xc-input');i.focus();i.value='/layout classic';i.dispatchEvent(new Event('input'));})()")
    b.press("Enter", "Enter", 13)
    b.wait("localStorage.getItem('xc-layout') === 'classic'")
    assert b.eval("document.querySelector('.xc-input').value") == ""


@needs_chrome
def test_a_new_streaming_message_lands_directly_under_the_box_in_inverse(page):
    srv, b = page
    open_chat(srv, b, "inverse")
    ev = {"seq": 1, "type": "message.delta", "body": {"message_id": "live1", "text": "streaming now"}, "session": "s1",
          "ts": "2026-10-12T10:00:01Z"}
    srv.call(srv.centre.record, ev)
    b.wait("document.querySelector('.xc-log').firstElementChild.textContent.includes('streaming now')")
    g = b.eval(GEOMETRY)
    assert g["top"] == 0 and g["first"].startswith("streaming now")


@needs_chrome
def test_a_new_message_in_classic_lands_at_the_bottom_and_keeps_the_view_there(page):
    srv, b = page
    open_chat(srv, b, "classic")
    ev = {"seq": 1, "type": "message.delta", "body": {"message_id": "live1", "text": "streaming now"}, "session": "s1",
          "ts": "2026-10-12T10:00:01Z"}
    srv.call(srv.centre.record, ev)
    b.wait("document.querySelector('.xc-log').lastElementChild.textContent.includes('streaming now')")
    g = b.eval(GEOMETRY)
    assert abs(g["top"] - (g["h"] - g["c"])) < 2
