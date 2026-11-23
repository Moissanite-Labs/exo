"""Web client load performance: a windowed transcript (newest 200 items, "Show earlier messages"), cached Markdown,
modulepreload links for the app's module graph, and egg.js loaded on demand. Static checks read the files; the
transcript test drives headless Chrome with a 1,000-item session and skips without it."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from browser import Browser, Served
from test_layout_web import ThreadSrc, needs_chrome

STATIC = Path(__file__).resolve().parents[1] / "exo_command" / "static"
IMPORT = re.compile(r'^import\b[^;]*?\bfrom\s+"\./([^"]+)"|^import\s+"\./([^"]+)"', re.M)


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def imports(name):
    return [a or b for a, b in IMPORT.findall(text(name))]


def preloads():
    return re.findall(r'<link rel="modulepreload" href="(/static/[^"]+)">', text("index.html"))


# ---------------------------------------------------------------- the transcript window
def test_the_transcript_window_is_200_with_a_show_earlier_control():
    src = text("chat.js")
    assert re.search(r"^const WINDOW = 200;", src, re.M)
    assert "Show earlier messages (" in src and "xc-earlier" in src
    assert "WINDOW" in src.split("function showEarlier")[1].split("function drawLog")[0]   # each click adds one window
    body = src.split("function showEarlier")[1].split("function drawLog")[0]
    # and keeps the reader's place: one anchor correction, no browser anchoring on top, no focus scroll
    assert "getBoundingClientRect().top - was" in body and 'overflowAnchor = "none"' in body and "preventScroll: true" in body


def test_both_layouts_put_the_control_at_the_old_end():
    body = text("chat.js").split("function logNodes")[1].split("function showEarlier")[0]
    assert 'layout === "inverse"' in body and "nodes.push(btn)" in body and "nodes.unshift(btn)" in body


def test_events_for_items_outside_the_window_still_find_them_and_do_not_redraw_the_log():
    src = text("chat.js")
    assert "function find(id) { return items.find(" in src                 # find() searches items[], not the DOM
    redraw = src.split("function redraw(it)")[1].split("function hiddenCount")[0]
    assert "items.indexOf(it) < hiddenCount()" in redraw and "drawLog()" in redraw


def test_completed_markdown_is_cached_per_item_and_streaming_text_is_not():
    src = text("chat.js")
    assert "it.mdEl" in src and "it.mdText !== it.text" in src
    assert "it.streaming ? it.text : cachedMarkdown(it)" in src            # the live-text path is unchanged
    assert "it.textEl.textContent" in src


# ---------------------------------------------------------------- modulepreload
def test_modulepreload_links_are_same_origin_files_that_exist():
    links = preloads()
    assert links and len(links) == len(set(links))
    for href in links:
        assert (STATIC / href.removeprefix("/static/")).is_file(), href
    html = text("index.html")
    assert "<script>" not in html and not re.search(r"<link[^>]+href=\"(?!/static/)", html)   # no inline script, same origin only


def test_modulepreload_links_match_the_imports_of_app_js():
    direct = imports("app.js")
    second = [m for d in direct for m in imports(d)]
    want = {f"/static/{m}" for m in direct + second}
    assert want <= set(preloads())
    reachable, todo = set(), list(direct)
    while todo:
        m = todo.pop()
        if m not in reachable:
            reachable.add(m)
            todo += imports(m)
    assert set(preloads()) <= {f"/static/{m}" for m in reachable}          # nothing preloaded that app.js never loads


# ---------------------------------------------------------------- egg.js
def test_egg_is_no_longer_statically_imported_and_loads_on_demand():
    for p in STATIC.glob("*.js"):
        assert not re.search(r'^import\s+"\./egg\.js"|from\s+"\./egg\.js"', p.read_text(encoding="utf-8"), re.M), p.name
    assert "egg.js" not in preloads() and "/static/egg.js" not in preloads()
    tasks = text("tasks.js")
    assert 'import("./egg.js")' in tasks and ".catch(" in tasks.split('import("./egg.js")')[1].split("return eggEl")[0]


# ---------------------------------------------------------------- the command view
def test_the_command_view_subscribes_to_the_stream_and_polls_only_as_the_fallback():
    src = text("command.js")
    assert 'stream("/api/stream")' in src and "es.onmessage = later" in src
    assert "readyState === 2" in src and "poll_tick" in src


# ---------------------------------------------------------------- the real page: 1,000 items
COUNT = "document.querySelectorAll('.xc-log .xc-msg').length"
EARLIER = "document.querySelector('.xc-log .xc-earlier')"


@pytest.fixture
def page():
    srv = Served(ThreadSrc(pairs=500))
    with Browser() as b:
        yield srv, b
    srv.close()


@needs_chrome
@pytest.mark.parametrize("layout", ["classic", "inverse"])
def test_a_thousand_item_transcript_renders_at_most_200_and_show_earlier_adds_more(page, layout):
    srv, b = page
    b.goto(srv.url())
    b.eval(f'localStorage.setItem("xc-layout", "{layout}")')
    b.reload()
    b.wait(f"{COUNT} > 20")
    b.settle()
    assert b.eval(COUNT) <= 200
    assert b.eval(f"{EARLIER}.textContent") == "Show earlier messages (800)"
    assert b.eval("document.querySelectorAll('.xc-log .xc-earlier').length") == 1
    log = "document.querySelector('.xc-log')"
    # the button sits at the old end: the top of the log in classic, the bottom in inverse
    assert b.eval(f"{log}.{'firstElementChild' if layout == 'classic' else 'lastElementChild'}.classList.contains('xc-earlier')")
    # reading position: the message nearest the button keeps its place on screen across the click
    edge = f"{log}.querySelector('.xc-msg')" if layout == "classic" else f"[...{log}.querySelectorAll('.xc-msg')].pop()"
    b.eval(f"{EARLIER}.scrollIntoView({{block: 'center'}})")
    b.settle()
    before = b.eval(f"(()=>{{const e={edge};e.setAttribute('data-probe','1');return e.getBoundingClientRect().top}})()")
    b.eval(f"{EARLIER}.click()")
    b.wait(f"{COUNT} > 200")
    b.settle()
    assert 200 < b.eval(COUNT) <= 400
    assert b.eval(f"{EARLIER}.textContent") == "Show earlier messages (600)"
    after = b.eval("document.querySelector('[data-probe]').getBoundingClientRect().top")
    assert abs(after - before) < 3
