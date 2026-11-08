"""Static checks on flow mode: one body class, a labelled toggle, Cmd/Ctrl+Shift+F and Esc, a saved choice, and CSS
that leaves only the transcript, the composer, the working animation, approval cards and Stop on screen."""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "exo_command" / "static"


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def flow_css():
    return "\n".join(line for line in text("style.css").splitlines() if "xc-flow" in line)


def test_state_is_one_body_class_saved_in_local_storage():
    layout, app = text("layout.js"), text("app.js")
    assert 'export const FLOW_KEY = "xc-flow"' in layout
    assert 'export const FLOW_CLASS = "xc-flow"' in layout
    assert "localStorage" in layout
    assert re.search(r"export function getFlow[\s\S]*?try \{[\s\S]*?catch", layout)
    assert re.search(r"export function setFlow[\s\S]*?try \{[\s\S]*?catch", layout)
    assert "document.body.classList.toggle(FLOW_CLASS, flow)" in app
    assert "getFlow()" in app and "setFlow(on)" in app
    assert "setFlowMode(flow, false)" in app   # first paint never writes storage


def test_toggle_button_is_labelled_and_pressed_state_follows():
    app = text("app.js")
    assert '"aria-label": "Flow mode"' in app
    assert '"aria-pressed": "false"' in app
    assert 'flowBtn.setAttribute("aria-pressed", flow ? "true" : "false")' in app
    assert "flowBtn" in app.split("nav.replaceChildren(")[1].split(");")[0]


def test_keyboard_toggle_and_escape_exit():
    app, layout = text("app.js"), text("layout.js")
    assert 'document.addEventListener("keydown"' in app and "}, true);" in app   # capture, so it runs before cards
    assert "isFlowToggle(e)" in app and "flow && isFlowExit(e)" in app
    assert 'String(e.key).toLowerCase() === "f"' in layout and "e.shiftKey" in layout and "e.metaKey" in layout
    assert 'e.key !== "Escape"' in layout and "e.defaultPrevented" in layout
    for blocker in ('[role="dialog"]', '[aria-modal="true"]', ".xc-menu:not([hidden])", ".xc-model-picker:not([hidden])"):
        assert blocker in layout
    assert '.closest(".xc-approvals")' in layout   # a card uses Esc to cancel


def test_css_hides_chrome_but_keeps_the_working_pieces():
    css = flow_css()
    scope = 'body.xc-flow[data-xc-view="chat"]'
    assert scope in css
    for hidden in (".xc-chat-left", ".xc-side", ".xc-setup", ".xc-guide"):
        assert scope + " " + hidden in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert "100dvh" in css
    assert scope + " .xc-nav > :not(.xc-flow-toggle) { display: none; }" in css
    # kept: the working state, the wire, notices, approvals, the composer and Stop are never display:none
    assert ":not(.xc-state.on):not(.xc-wire):not(.xc-flash)" in css
    for kept in (".xc-approvals", ".xc-composer", ".xc-btnrow", ".xc-log", ".xc-chat-main {"):
        assert not re.search(re.escape(scope + " " + kept) + r"[^{]*\{[^}]*display: none", css)
    chat = text("chat.js")
    assert 'class: "xc-approvals"' in chat and "stopBtn" in chat


def test_flow_rules_never_leak_to_other_pages():
    css = text("style.css")
    for line in css.splitlines():
        if line.startswith("body.xc-flow"):
            assert '[data-xc-view="chat"]' in line
    assert "document.body.dataset.xcView = current" in text("app.js")
    assert 'body:not([data-xc-view="chat"]) .xc-flow-toggle { display: none; }' in css


def test_no_em_dashes_in_flow_code():
    assert "\u2014" not in flow_css()
    assert "\u2014" not in text("layout.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_helpers_behave_in_node(tmp_path):
    script = textwrap.dedent(f"""
        import {{ getFlow, setFlow, isFlowToggle, isFlowExit }} from "file://{STATIC / 'layout.js'}";
        const mem = {{ d: {{}}, getItem(k) {{ return k in this.d ? this.d[k] : null; }}, setItem(k, v) {{ this.d[k] = v; }} }};
        const bad = {{ getItem() {{ throw new Error("x"); }}, setItem() {{ throw new Error("x"); }} }};
        const ok = [];
        ok.push(getFlow(mem) === false);
        setFlow(true, mem); ok.push(mem.d["xc-flow"] === "1" && getFlow(mem) === true);
        setFlow(false, mem); ok.push(getFlow(mem) === false);
        ok.push(getFlow(bad) === false && setFlow(true, bad) === true);
        ok.push(isFlowToggle({{ key: "F", shiftKey: true, metaKey: true }}));
        ok.push(isFlowToggle({{ key: "f", shiftKey: true, ctrlKey: true }}));
        ok.push(!isFlowToggle({{ key: "f", shiftKey: false, ctrlKey: true }}));
        ok.push(!isFlowToggle({{ key: "f", shiftKey: true }}));
        const doc = (open) => ({{ querySelector: () => (open ? {{}} : null) }});
        const esc = (extra) => Object.assign({{ key: "Escape", target: {{ closest: () => null }} }}, extra);
        ok.push(isFlowExit(esc({{}}), doc(false)));
        ok.push(!isFlowExit(esc({{}}), doc(true)));
        ok.push(!isFlowExit(esc({{ defaultPrevented: true }}), doc(false)));
        ok.push(!isFlowExit(esc({{ target: {{ closest: () => ({{}}) }} }}), doc(false)));
        ok.push(!isFlowExit({{ key: "a" }}, doc(false)));
        console.log(JSON.stringify(ok));
    """)
    f = tmp_path / "t.mjs"
    f.write_text(script, encoding="utf-8")
    out = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[" + ",".join(["true"] * 13) + "]"
