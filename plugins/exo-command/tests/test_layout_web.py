"""D-046 in the web client: the inverse layout (box on top, newest first), the layout switch and its saved choice, and a
chat that opens at its newest item in both layouts. The behaviour tests drive headless Chrome and skip without it."""
from __future__ import annotations

import asyncio
import json
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


def test_the_layout_styles_use_the_site_tokens_only():
    block = text("style.css").split("layout experiment (D-046)")[1]
    assert "xc-inverse" in block and not __import__("re").search(r"#[0-9a-fA-F]{3,6}\b", block)


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
            "inH:innerHeight,rects:Object.fromEntries(['.xc-chat-main','.xc-composer','.xc-stage','.xc-input'].map(s=>{const r=document.querySelector(s).getBoundingClientRect();return [s,{top:r.top,bottom:r.bottom,left:r.left,right:r.right}]})),first:l.firstElementChild.textContent,last:l.lastElementChild.textContent,"
            "msgs:document.querySelectorAll('.xc-msg').length}})()")


def assert_layout_geometry(g, layout):
    """Composer order is relative to the column, independent of host frame origin."""
    main, composer, stage, field = (g["rects"][s] for s in (".xc-chat-main", ".xc-composer", ".xc-stage", ".xc-input"))
    for rect in (composer, stage):
        assert main["top"] <= rect["top"] < rect["bottom"] <= main["bottom"] + 1
        assert main["left"] <= rect["left"] < rect["right"] <= main["right"] + 1
    assert composer["top"] <= field["top"] < field["bottom"] <= composer["bottom"]
    if layout == "inverse":
        assert composer["bottom"] <= stage["top"] + 1
    else:
        assert stage["bottom"] <= composer["top"] + 1


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
    assert_layout_geometry(g, "classic")
    assert g["last"].startswith("answer 39") and g["first"].endswith("question 0")


@needs_chrome
def test_inverse_opens_with_the_box_on_top_and_the_newest_item_under_it(page):
    srv, b = page
    g = open_chat(srv, b, "inverse")
    assert g["top"] == 0 and g["page"] == 0
    assert_layout_geometry(g, "inverse")
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


@needs_chrome
@pytest.mark.parametrize("layout", ["classic", "inverse"])
def test_status_instruments_and_notices_fit_after_window_and_pane_resize(page, layout):
    srv, b = page
    open_chat(srv, b, layout)
    # Mount demanding status contents in the real themed chat. The longer estimate
    # also covers the experimental display without depending on its unmerged JS.
    b.eval('''(() => {
      const strip = document.querySelector('.xc-strip');
      const cell = (label, value, cls = '') => {
        const node = document.createElement('span'); node.className = cls;
        const key = document.createElement('span'); key.className = 'xc-i'; key.textContent = label;
        const val = document.createElement('span'); val.className = 'xc-v'; val.textContent = value;
        node.append(key, val); return node;
      };
      const model = cell('model', 'provider/' + 'long-model-name-'.repeat(8), 'xc-model');
      const fallback = document.createElement('span'); fallback.className = 'xc-via';
      fallback.textContent = '← fallback'; model.append(fallback);
      const state = cell('state', 'working', 'xc-state on');
      const wire = document.createElement('span'); wire.className = 'xc-wire';
      const notice = document.createElement('span'); notice.className = 'xc-flash';
      notice.textContent = 'Repeated call detected: exec_command; check the last result before repeating. ' + 'unbroken'.repeat(35);
      strip.replaceChildren(state, model,
        cell('memory', 'Estimated next call: about 15k. Limit unknown. Next model: gpt-6.1-sol.', 'xc-opt'),
        cell('last call', '14k input tokens'), cell('cache', '72%', 'xc-opt'),
        cell('findings', '2', 'xc-opt'), cell('cost', 'unknown'), wire, notice);
    })()''')
    for width, pane in [(2000, None), (1460, None), (1460, 320), (900, None), (360, None), (2000, None)]:
        b.call("Emulation.setDeviceMetricsOverride", width=width, height=1000, deviceScaleFactor=1, mobile=False)
        b.eval(f"document.querySelector('.xc-chat').style.gridTemplateColumns = {json.dumps('224px 320px 340px' if pane else '')}")
        got = b.eval('''(() => {
          const strip = document.querySelector('.xc-strip'), box = strip.getBoundingClientRect();
          const visible = el => el.getClientRects().length && getComputedStyle(el).display !== 'none';
          const bad = [...strip.querySelectorAll('.xc-i, .xc-v, .xc-via, .xc-flash')].filter(visible).filter(el => {
            const range = document.createRange(); range.selectNodeContents(el);
            return [...range.getClientRects()].some(r => r.left < box.left - 1 || r.right > box.right + 1 ||
              r.top < box.top - 1 || r.bottom > box.bottom + 1) || el.scrollWidth > el.clientWidth + 1;
          }).map(el => el.textContent);
          const notice = strip.querySelector('.xc-flash').getBoundingClientRect();
          return {bad, width: box.width, scroll: strip.scrollWidth,
            page: document.documentElement.scrollWidth, viewport: innerWidth,
            model: strip.querySelector('.xc-model .xc-v').getBoundingClientRect().width,
            noticeWidth: notice.width, wire: getComputedStyle(strip.querySelector('.xc-wire')).display};
        })()''')
        assert not got["bad"], (layout, width, pane, got)
        assert got["scroll"] <= got["width"] + 1, got
        assert got["page"] <= got["viewport"] + 1, got
        assert got["model"] > 20 and abs(got["noticeWidth"] - got["width"]) < 2, got
        assert got["wire"] != "none", got


@needs_chrome
@pytest.mark.parametrize("layout", ["inverse", "classic"])
@pytest.mark.parametrize("width,height,offset", [(1440, 900, 0), (1024, 900, 80)])
def test_long_conversation_geometry_follows_the_host_frame(layout, width, height, offset):
    srv = Served(ThreadSrc())
    try:
        with Browser(width=width, height=height) as b:
            open_chat(srv, b, layout)
            if offset:
                b.eval(f"(()=>{{const frame=document.querySelector('.xc-chat');frame.style.transform='translateY({offset}px)';frame.style.height='calc(100dvh - {89 + offset}px)'}})()")
                b.eval("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
            if offset and layout == "classic":
                # Resize changes the scroll range; restore the same newest-message view for this geometry probe.
                b.eval("(()=>{const l=document.querySelector('.xc-log');l.scrollTop=l.scrollHeight})()")
            g = b.eval(GEOMETRY)
            assert_layout_geometry(g, layout)
            assert g["page"] == 0 and g["h"] > g["c"]
            assert 0 <= g["inputY"] < g["inH"]
            if layout == "inverse":
                assert g["top"] == 0 and g["first"].startswith("answer 39")
                if offset:
                    assert g["inputY"] > 120  # valid shifted frame contradicts the former absolute threshold
            else:
                assert abs(g["top"] - (g["h"] - g["c"])) < 2
                assert g["last"].startswith("answer 39")
    finally:
        srv.close()
