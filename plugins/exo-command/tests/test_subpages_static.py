"""Static checks on the sub-page look (D-061): the shared egg stays in step with the site, every sub-page has a page
head and a designed empty state, and status pills carry their state in form, not colour alone."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "exo_command" / "static"
SITE_EGG = Path(__file__).resolve().parents[3] / "site" / "brand" / "egg.js"


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_egg_is_the_site_copy():
    if SITE_EGG.exists():   # the plugin can be shipped without the site
        assert text("egg.js") == SITE_EGG.read_text(encoding="utf-8")


def test_egg_is_used_once():
    # An attribution comment is not a second instance of the ambient brand renderer.
    imported = re.compile(r'''(?:from\s*|import\s*\(?\s*)["']\./egg\.js["']''')
    users = [f.name for f in STATIC.glob("*.js") if f.name != "egg.js" and imported.search(f.read_text(encoding="utf-8"))]
    assert users == ["tasks.js"]


def test_every_sub_page_has_a_head_and_an_empty_state():
    for name in ("tasks.js", "schedule.js", "command.js", "providers.js"):
        js = text(name)
        assert "pageHead({" in js and "eyebrow:" in js and "lede:" in js, name
    for name in ("tasks.js", "schedule.js", "providers.js"):
        assert "emptyState({" in text(name), name


def test_every_pill_state_has_a_led_form():
    css = text("style.css")
    for state in ("run", "queue", "done", "fail", "stop", "on", "off", "ok", "known", "pause", "warn", "alert"):
        assert re.search(r'\.xc-led\[data-s="%s"\]' % state, css), state


def test_no_null_children():
    """replaceChildren draws a null as the text "null": the sub-pages filter their lists first."""
    for name in ("tasks.js", "schedule.js", "command.js"):
        for m in re.finditer(r"replaceChildren\(([^;]*)\);", text(name), re.S):
            assert ": null," not in m.group(1) or ".filter(Boolean)" in m.group(1), (name, m.group(0)[:80])


def test_reduced_motion_stops_everything():
    css = text("style.css")
    assert "@media (prefers-reduced-motion: reduce)" in css and "animation: none !important" in css


def test_tasks_rows_carry_open_steer_and_stop_controls():
    """#97: a task with a child session can be opened in Chat; a running one can be steered or stopped."""
    js = (STATIC / "tasks.js").read_text(encoding="utf-8")
    assert '"/api/tasks/" + encodeURIComponent(t.task_id) + "/" + verb' in js
    assert "child_session" in js and '"Steer"' in js and '"Stop"' in js and "xc-open" in js
    assert 'sessionStorage.getItem("xc-open")' in (STATIC / "chat.js").read_text(encoding="utf-8")
