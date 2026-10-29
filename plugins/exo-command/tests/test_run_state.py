"""Run-state guidance: the pure mapping in run_state.js, and its persistent strip line in Chrome."""
from __future__ import annotations

import itertools
import json
import shutil
import subprocess

import pytest

from exo_command.server import STATIC

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="Node missing")

LOST = "Connection lost. Activity may still be running; sending is paused."
UNAVAILABLE = "Connection unavailable."
CHECKING = "Checking activity before sending."
STALE = "Activity is unknown. Retry activity before sending."
APPROVAL = "Review the approval request to continue. You can deny it or stop the run."
BUSY = "Send a message to steer this run at its next tool round, or stop it."
IDLE = "Ready for your next message."

PRELUDE = "const {runGuidance}=await import('./run_state.mjs');\n"


def node_guidance(tmp_path, script, **consts):
    """Run `script` against the real run_state.js in Node."""
    (tmp_path / "run_state.mjs").write_text((STATIC / "run_state.js").read_text())
    head = "".join(f"const {k}={json.dumps(v)};\n" for k, v in consts.items())
    (tmp_path / "test.mjs").write_text(PRELUDE + head + script + '\nconsole.log("ok");\n')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@needs_node
def test_guidance_precedence_and_text(tmp_path):
    node_guidance(tmp_path, r"""
import assert from "node:assert/strict";
const g = runGuidance;
// Precedence: disconnected > stale (loading, then not) > approval > busy > idle.
assert.equal(g({connected: false}), LOST);
assert.equal(g({connected: false, webUsable: false, coreState: "connecting"}), LOST);   // web down is a hard loss
assert.equal(g({connected: false, coreState: "unavailable"}), LOST);
assert.equal(g({connected: false, coreState: "connecting"}), UNAVAILABLE);              // reconnect underway: neutral
assert.equal(g({connected: false, stale: true, loading: true, approval: true, busy: true}), LOST);
assert.equal(g({stale: true, loading: true}), CHECKING);
assert.equal(g({stale: true, loading: true, approval: true, busy: true}), CHECKING);
assert.equal(g({stale: true}), STALE);
assert.equal(g({stale: true, approval: true, busy: true}), STALE);
assert.equal(g({approval: true}), IDLE);
assert.equal(g({approval: true, busy: true}), APPROVAL);
assert.equal(g({busy: true}), BUSY);
assert.equal(g({}), IDLE);
assert.equal(g(), IDLE);
""", LOST=LOST, UNAVAILABLE=UNAVAILABLE, CHECKING=CHECKING, STALE=STALE, APPROVAL=APPROVAL, BUSY=BUSY, IDLE=IDLE)


@needs_node
def test_guidance_every_flag_combination(tmp_path):
    """All input combinations resolve to exactly one of the seven lines, in precedence order."""
    cases = []
    for connected, web, state, stale, loading, approval, busy in itertools.product(
            (True, False), (True, False), ("connected", "connecting", "unavailable"), (True, False), (True, False), (True, False), (True, False)):
        inp = {"connected": connected, "webUsable": web, "coreState": state, "stale": stale,
               "loading": loading, "approval": approval, "busy": busy}
        if not connected:
            want = UNAVAILABLE if web and state == "connecting" else LOST
        elif stale:
            want = CHECKING if loading else STALE
        elif busy and approval:
            want = APPROVAL
        elif busy:
            want = BUSY
        else:
            want = IDLE
        cases.append({"in": inp, "want": want})
    node_guidance(tmp_path, r"""
import assert from "node:assert/strict";
const seen = new Set();
for (const c of CASES) {
  const got = runGuidance(c.in);
  assert.equal(got, c.want, JSON.stringify(c.in));
  seen.add(got);
}
assert.equal(seen.size, 7);   // every precedence class is reachable
""", CASES=cases)


# ---------------------------------------------------------------- the real page in headless Chrome
from browser import Browser, Served  # noqa: E402
from test_layout_web import ThreadSrc, needs_chrome  # noqa: E402

GUIDANCE = ("(()=>{const g=document.querySelector('.xc-guidance');"
"const s=document.querySelector('.xc-strip');"
"return {text:g&&g.textContent, role:g&&g.getAttribute('role'), live:g&&g.getAttribute('aria-live'),"
"last:s&&s.lastElementChild===g, count:s?s.querySelectorAll('.xc-guidance').length:0}})()")


class Track:
    """Element wrapper counting textContent sets (what aria-live announces)."""

    def __init__(self, browser):
        self.browser = browser
        # textContent lives on Node.prototype; redefining it on Element.prototype shadows
        # it for elements only, which is exactly the set chat.js writes to.
        browser.eval("(()=>{const d=Object.getOwnPropertyDescriptor(Node.prototype,'textContent');"
                     "window.__sets=0;"
                     "Object.defineProperty(Element.prototype,'textContent',{get(){return d.get.call(this);},"
                     "set(v){if(this.classList&&this.classList.contains('xc-guidance'))window.__sets++;return d.set.call(this,v);}})"
                     "})()")

    def sets(self):
        return self.browser.eval("window.__sets")


@needs_chrome
def test_guidance_line_persistent_and_updates_only_on_state_change():
    srv = Served(ThreadSrc())
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("!!document.querySelector('.xc-guidance')")
            b.wait("document.querySelector('.xc-guidance').textContent.length > 0")
            got = b.eval(GUIDANCE)
            assert got["role"] == "status" and got["live"] == "polite"
            assert got["last"] and got["count"] == 1                      # one persistent element, last in the strip
            assert got["text"] == IDLE
            t = Track(b)
            b.eval("window.__guidance=document.querySelector('.xc-guidance');window.__removed=0;new MutationObserver(rs=>{for(const r of rs)for(const n of r.removedNodes)if(n===window.__guidance)window.__removed++;}).observe(document.querySelector('.xc-strip'),{childList:true})")
            for i in range(1, 4):
                srv.call(srv.centre.record, {"seq": i, "type": "usage.updated", "session": "s1",
                    "body": {"session_total": {"cost_usd": i}}, "ts": "2026-10-12T10:00:01Z"})
                b.wait(f"document.querySelector('.xc-strip').textContent.includes('${i}.00')")
            assert b.eval(GUIDANCE)["text"] == IDLE and t.sets() == 0
            assert b.eval("window.__removed") == 0
            assert b.eval("window.__guidance===document.querySelector('.xc-guidance')")
            srv.call(srv.centre.set_core_state, "unavailable")            # real state change -> one new announcement
            b.wait(f"document.querySelector('.xc-guidance').textContent === {json.dumps(LOST)}")
            assert t.sets() == 1
            assert b.eval(GUIDANCE)["count"] == 1                         # still the same single element
    finally:
        srv.close()


@needs_chrome
@pytest.mark.parametrize("width", [390, 768, 1440])
def test_guidance_is_visible_at_narrow_and_wide_widths(width):
    srv = Served(ThreadSrc())
    try:
        with Browser(width=width) as b:
            b.goto(srv.url())
            b.wait("document.querySelector('.xc-guidance')?.textContent.length > 0")
            for message in (IDLE, LOST, APPROVAL, BUSY):
                b.eval("document.querySelector('.xc-guidance').textContent=" + json.dumps(message))
                geometry = b.eval("""(()=>{
                    const g=document.querySelector('.xc-guidance');
                    const r=g.getBoundingClientRect(), p=g.parentElement.getBoundingClientRect();
                    return {left:r.left, right:r.right, bottom:r.bottom,
                            parentLeft:p.left, parentRight:p.right, parentBottom:p.bottom,
                            height:g.clientHeight, contentHeight:g.scrollHeight};
                })()""")
                assert geometry["left"] >= geometry["parentLeft"] - 1
                assert geometry["right"] <= geometry["parentRight"] + 1
                assert geometry["bottom"] <= geometry["parentBottom"] + 1
                assert geometry["contentHeight"] <= geometry["height"]
    finally:
        srv.close()
