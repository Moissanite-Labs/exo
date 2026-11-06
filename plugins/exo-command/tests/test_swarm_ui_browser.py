"""The Swarm view's group controls, driven in REAL headless Chrome against the REAL served app assets.

What runs for real: the page (index.html, app.js, dom.js, swarm.js, swarm.css served by `exo_command.server.WebServer`
on a loopback port), the browser, `exo_command.api.dispatch` -> `swarm_api` (route, validation, reply shape, error
mapping) and the page's own `api()` fetch (token header). What is synthetic: only the source behind the centre. `SwarmSrc`
answers the core's `command.run name=group` calls from memory, records every one of them, and can hold a reply on a
`threading.Event`, so no live core, provider, socket, config or private data is involved. Assertions are on what the
source RECEIVED (the exact `args` string `swarm_api` built) and on what the page shows, derived from swarm.js:

  * target prerequisite: Pause, Resume and Stop are disabled until the Control target select is not "Choose a target".
  * "Whole group" (value @group) sends no member key (`{"id": ...}`), a member sends `{"id": ..., "member": "<profile>"}`.
  * Stop first shows an inline "Confirm stop" / "Cancel" and sends nothing. Confirm sends once, a double click sends once.
  * keyboard: Enter and Space on a focused button activate it, Tab reaches the controls in order.
  * a definite refusal (403) shows "was refused", leaves the controls on and is not retried. An unclear failure (502)
    locks the group's controls, is not retried, and unlocks only after a successful Check status.
  * a status or inspect reply that arrives after another group was selected is dropped.

Limits: one synthetic source with two groups of two members. No real core, so "accepted" is the stand-in's echo and not
evidence the members paused or stopped (that is test_swarm_controls_socket.py). Layout/visual look is not asserted.
Every wait is bounded by 10 s. Needs Chrome and `websockets` (skipped otherwise). Not covered: Send, transcript paging,
the Refresh buttons, touch input, screen readers.
"""
import asyncio
import json
import threading
import time

import pytest
from browser import Browser, Served, find_chrome
from test_layout_web import ThreadSrc

from exo_command.source import SourceError

needs_chrome = pytest.mark.skipif(not find_chrome() or not __import__("importlib").util.find_spec("websockets"),
                                  reason="Chrome or websockets missing")

BOUND = 10
CONTROL_OPS = ("pause", "resume", "stop")
FLAG = {"pause": "paused", "resume": "resumed", "stop": "stopped"}
STAMP = "2026-01-01T00:00:00Z"


def group(gid, *profiles):
    return {"id": gid, "owner": "kai", "mode": "socratic", "conductor": None, "created": STAMP,
            "members": [{"profile": p, "session": "s_" + p} for p in profiles]}


GROUPS = {"g_one": group("g_one", "alpha", "beta"), "g_two": group("g_two", "gamma", "delta")}


class SwarmSrc(ThreadSrc):
    """A stand-in core for `command.run name=group`. Records (op, payload) for every call, thread-safely."""

    def __init__(self):
        super().__init__(pairs=0)
        self.log = []                       # [(op, payload)] in arrival order
        self.control = "ok"                 # ok | refuse | fail, for pause/resume/stop
        self.holds = {}                     # (op, group id) -> threading.Event the reply waits for
        self.states = {"alpha": "running", "beta": "running", "gamma": "idle", "delta": "idle"}

    def hold(self, op, gid):
        self.holds[(op, gid)] = ev = threading.Event()
        return ev

    def ops(self, *names):
        return [(o, p) for o, p in list(self.log) if o in names]

    async def call(self, body):
        if type(body).TYPE != "command.run" or body.name != "group":
            return await super().call(body)
        op, _, rest = body.args.partition(" ")
        payload = json.loads(rest) if rest else {}
        self.log.append((op, payload))
        gid = payload.get("id")
        gate = self.holds.get((op, gid))
        if gate is not None:
            end = time.monotonic() + BOUND
            while not gate.is_set() and time.monotonic() < end:
                await asyncio.sleep(0.01)
        if op == "list":
            return {"output": {"groups": list(GROUPS.values())}}
        if op == "inspect":
            return {"output": GROUPS[gid]}
        if op == "observe":
            return {"output": {"messages": [], "next_after": 0}}
        if op == "status":
            return {"output": {"id": gid, "owner": "kai", "local_runtime": True, "reconciliation_required": False,
                               "receipt": None, "members": [{"profile": m["profile"], "state": self.states[m["profile"]]}
                                                            for m in GROUPS[gid]["members"]]}}
        if op in CONTROL_OPS:
            if self.control == "refuse":
                raise SourceError("forbidden", "private core text that must never reach the page")
            if self.control == "fail":
                raise RuntimeError("private transport text that must never reach the page")
            return {"output": {"id": gid, FLAG[op]: True, "member": payload.get("member")}}
        raise AssertionError("unexpected group op " + op)


# ------------------------------------------------------------------ page helpers (everything goes through the real DOM)
J = json.dumps
CTL = "document.querySelector('.xc-sw-ctl')"
BTN = "((t)=>[...document.querySelectorAll('.xc-sw-ctl button')].find(b=>b.textContent===t))"


def open_group(b, gid, who=None):
    b.wait("document.querySelectorAll('.xc-sw-item').length === 2")
    b.eval(f"[...document.querySelectorAll('.xc-sw-item')].find(i=>i.querySelector('.xc-sw-id').textContent==={J(gid)}).click()")
    b.wait(f"document.querySelector('.xc-sw-gname')?.textContent==={J(gid)} && !!{CTL}.querySelector('select')")


def pick_target(b, value):
    """Choose in the Control target select the way the change event does (value then a bubbling change)."""
    b.eval(f"(()=>{{const s={CTL}.querySelector('select[aria-label=\"Control target\"]');s.value={J(value)};"
           "s.dispatchEvent(new Event('change',{bubbles:true}))})()")
    b.wait(f"{CTL}.querySelector('select[aria-label=\"Control target\"]').value==={J(value)}")


def disabled(b, text):
    return b.eval(f"{BTN}({J(text)}).disabled")


def click(b, text):
    b.eval(f"{BTN}({J(text)}).click()")


def out(b):
    return b.eval(f"{CTL}.querySelector('.xc-sw-ctlout').textContent")


def hint(b):
    return b.eval(f"[...{CTL}.querySelectorAll('.xc-sw-hint')].map(h=>h.textContent).pop()")


def confirm_text(b):
    return b.eval(f"{CTL}.querySelector('.xc-sw-confirm').textContent")


def key(b, name, code, vk, text=""):
    """A real key press through the browser's input pipeline (keyDown with `text` is what activates a button)."""
    b.call("Input.dispatchKeyEvent", type="keyDown", key=name, code=code, windowsVirtualKeyCode=vk, text=text)
    b.call("Input.dispatchKeyEvent", type="keyUp", key=name, code=code, windowsVirtualKeyCode=vk)


def focus(b, text):
    b.eval(f"{BTN}({J(text)}).focus()")
    assert b.eval("document.activeElement.textContent") == text


def settle(src, n, seconds=0.6):
    """Prove nothing more arrives: wait a short bounded time, then the control count must still be n."""
    time.sleep(seconds)
    assert len(src.ops(*CONTROL_OPS)) == n, src.ops(*CONTROL_OPS)


def page(src):
    srv = Served(src)
    b = Browser()
    b.goto(srv.url("swarm"))
    return srv, b


def teardown(srv, b):
    for ev in list(getattr(srv.source, "holds", {}).values()):
        ev.set()
    try:
        b.close()
    finally:
        srv.close()


# ------------------------------------------------------------------ tests
@needs_chrome
def test_controls_need_an_explicit_target_and_whole_group_sends_no_member():
    src = SwarmSrc()
    srv, b = page(src)
    try:
        open_group(b, "g_one")
        # Nothing is chosen: the select shows its placeholder and the three controls are off. Check status is not gated.
        assert b.eval(f"{CTL}.querySelector('select[aria-label=\"Control target\"]').value") == ""
        assert b.eval(f"[...{CTL}.querySelectorAll('select[aria-label=\"Control target\"] option')].map(o=>o.value+'='+o.textContent)") == \
            ["=Choose a target", "@group=Whole group", "alpha=alpha", "beta=beta"]
        assert [disabled(b, t) for t in ("Pause", "Resume", "Stop")] == [True, True, True]
        assert disabled(b, "Check status") is False
        assert "Choose a target" in hint(b)
        for t in ("Pause", "Resume", "Stop"):
            click(b, t)                                    # a click on a disabled button does nothing
        assert b.eval(f"{CTL}.querySelector('.xc-sw-confirm').children.length") == 0
        settle(src, 0)

        # Whole group: no member key at all (not null, not an empty string).
        pick_target(b, "@group")
        assert [disabled(b, t) for t in ("Pause", "Resume", "Stop")] == [False, False, False]
        click(b, "Pause")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('was accepted')")
        assert src.ops(*CONTROL_OPS) == [("pause", {"id": "g_one"})]
        assert "pause request for the whole group g_one was accepted" in out(b)
        assert "does not mean members have changed state" in out(b)
        assert "Check status" in out(b)

        # A member: exactly {"member": "<profile>"} and the member's own label in the result.
        pick_target(b, "beta")
        click(b, "Resume")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('member beta of group g_one was accepted')")
        assert src.ops(*CONTROL_OPS)[-1] == ("resume", {"id": "g_one", "member": "beta"})
        assert "resume request for member beta of group g_one was accepted" in out(b)

        # Going back to the placeholder turns every control off again, and nothing else was sent.
        pick_target(b, "")
        assert [disabled(b, t) for t in ("Pause", "Resume", "Stop")] == [True, True, True]
        settle(src, 2)

        # The raw wire args are exactly the fixed command strings: no extra fields and no op built from input.
        assert [(o, list(p)) for o, p in src.ops(*CONTROL_OPS)] == [("pause", ["id"]), ("resume", ["id", "member"])]
    finally:
        teardown(srv, b)


@needs_chrome
def test_stop_asks_inline_first_sends_nothing_until_confirmed_then_exactly_once():
    src = SwarmSrc()
    srv, b = page(src)
    try:
        open_group(b, "g_one")
        pick_target(b, "alpha")

        # Click Stop: the question is shown inline, Stop itself goes off, nothing is sent.
        click(b, "Stop")
        b.wait(f"{CTL}.querySelector('.xc-sw-confirm').children.length > 0")
        assert confirm_text(b) == "Stop member alpha of group g_one? This cannot be undone here.Confirm stopCancel"
        assert disabled(b, "Stop") is True
        assert b.eval(f"{CTL}.querySelector('.xc-sw-confirm').getAttribute('role')") == "group"
        settle(src, 0)

        # Cancel: the question goes away, still nothing sent, Stop is usable again.
        click(b, "Cancel")
        b.wait(f"{CTL}.querySelector('.xc-sw-confirm').children.length === 0")
        assert disabled(b, "Stop") is False
        settle(src, 0)

        # Changing the target while asked also drops the question (it could otherwise confirm the wrong target).
        click(b, "Stop")
        b.wait(f"{CTL}.querySelector('.xc-sw-confirm').children.length > 0")
        pick_target(b, "beta")
        assert b.eval(f"{CTL}.querySelector('.xc-sw-confirm').children.length") == 0
        settle(src, 0)

        # Ask again and confirm with a double click in one task: the request goes out once.
        click(b, "Stop")
        b.wait(f"{CTL}.querySelector('.xc-sw-confirm').children.length > 0")
        assert "Stop member beta of group g_one?" in confirm_text(b)
        b.eval(f"(()=>{{const c={BTN}('Confirm stop');c.click();c.click()}})()")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('was accepted')")
        assert src.ops(*CONTROL_OPS) == [("stop", {"id": "g_one", "member": "beta"})]
        assert b.eval(f"{CTL}.querySelector('.xc-sw-confirm').children.length") == 0
        settle(src, 1)

        # The whole group needs its own confirmation, and its stop carries no member.
        pick_target(b, "@group")
        click(b, "Stop")
        b.wait(f"{CTL}.querySelector('.xc-sw-confirm').children.length > 0")
        assert confirm_text(b).startswith("Stop the whole group g_one?")
        settle(src, 1)
        click(b, "Confirm stop")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('whole group g_one was accepted')")
        assert src.ops(*CONTROL_OPS)[-1] == ("stop", {"id": "g_one"})
        settle(src, 2)
    finally:
        teardown(srv, b)


@needs_chrome
def test_keyboard_activation_and_focus_order_smoke():
    src = SwarmSrc()
    srv, b = page(src)
    try:
        open_group(b, "g_one")
        pick_target(b, "alpha")

        # Tab order inside the panel: Check status, Control target, Pause, Resume, Stop (DOM order, all real buttons).
        focus(b, "Check status")
        order = []
        for _ in range(4):
            key(b, "Tab", "Tab", 9)
            order.append(b.eval("document.activeElement.tagName + ':' + (document.activeElement.getAttribute('aria-label') || document.activeElement.textContent)"))
        assert order == ["SELECT:Control target", "BUTTON:Pause", "BUTTON:Resume", "BUTTON:Stop"]

        # Enter on a focused Pause sends it once. Space on Resume sends it once.
        focus(b, "Pause")
        key(b, "Enter", "Enter", 13, "\r")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('pause request')  && document.querySelector('.xc-sw-ctlout').textContent.includes('accepted')")
        assert src.ops(*CONTROL_OPS) == [("pause", {"id": "g_one", "member": "alpha"})]
        focus(b, "Resume")
        key(b, " ", "Space", 32, " ")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('resume request') && document.querySelector('.xc-sw-ctlout').textContent.includes('accepted')")
        assert [o for o, _ in src.ops(*CONTROL_OPS)] == ["pause", "resume"]

        # Enter on Stop only asks. Enter on Cancel, reached by keyboard, closes it. Still no stop.
        focus(b, "Stop")
        key(b, "Enter", "Enter", 13, "\r")
        b.wait(f"{CTL}.querySelector('.xc-sw-confirm').children.length > 0")
        settle(src, 2)
        focus(b, "Cancel")
        key(b, "Enter", "Enter", 13, "\r")
        b.wait(f"{CTL}.querySelector('.xc-sw-confirm').children.length === 0")
        settle(src, 2)

        # Enter on Stop, then Enter on "Confirm stop", sends the one stop.
        focus(b, "Stop")
        key(b, "Enter", "Enter", 13, "\r")
        b.wait(f"{CTL}.querySelector('.xc-sw-confirm').children.length > 0")
        focus(b, "Confirm stop")
        key(b, "Enter", "Enter", 13, "\r")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('stop request')  && document.querySelector('.xc-sw-ctlout').textContent.includes('accepted')")
        assert src.ops(*CONTROL_OPS)[-1] == ("stop", {"id": "g_one", "member": "alpha"})
        settle(src, 3)
    finally:
        teardown(srv, b)


@needs_chrome
def test_error_results_are_shown_and_never_retried():
    src = SwarmSrc()
    srv, b = page(src)
    try:
        open_group(b, "g_one")
        pick_target(b, "@group")

        # 1. A definite refusal (the core said 403): shown, static text only, controls stay on, nothing retried.
        src.control = "refuse"
        click(b, "Pause")
        b.wait("document.querySelector('.xc-sw-ctlout')?.classList.contains('is-bad')")
        text = out(b)
        assert "pause request for the whole group g_one was refused: " in text
        assert "Only the principal may use swarm groups." in text
        assert "private core text" not in text
        assert [disabled(b, t) for t in ("Pause", "Resume", "Stop")] == [False, False, False]
        settle(src, 1)

        # 2. An unclear failure (502): outcome unknown, the group's controls lock, nothing is retried.
        src.control = "fail"
        click(b, "Resume")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('Could not confirm')")
        text = out(b)
        assert "Could not confirm the resume request for the whole group g_one (HTTP 502)." in text
        assert "It may or may not have been applied. Nothing is retried. Use Check status" in text
        assert "private transport text" not in text
        assert [disabled(b, t) for t in ("Pause", "Resume", "Stop")] == [True, True, True]
        assert "unknown result" in hint(b)
        for t in ("Pause", "Resume", "Stop"):
            click(b, t)                                    # locked: a click does nothing
        assert b.eval(f"{CTL}.querySelector('.xc-sw-confirm').children.length") == 0
        settle(src, 2)                                     # one refused pause, one failed resume: no automatic repeat

        # The lock is the group's: the other group is not locked, and coming back to this one still shows it.
        src.control = "ok"
        open_group(b, "g_two")
        pick_target(b, "gamma")
        assert disabled(b, "Pause") is False
        open_group(b, "g_one")
        pick_target(b, "@group")
        assert disabled(b, "Pause") is True
        assert not [1 for o, _ in src.ops("status")]       # no status read happened by itself

        # 3. Only a successful Check status unlocks it, and it sends nothing else.
        click(b, "Check status")
        b.wait("!!document.querySelector('.xc-sw-states')")
        assert [disabled(b, t) for t in ("Pause", "Resume", "Stop")] == [False, False, False]
        assert b.eval("[...document.querySelectorAll('.xc-sw-mstate')].map(l=>l.textContent)") == ["alpharunning", "betarunning"]
        assert [o for o, _ in src.ops("status")] == ["status"]
        settle(src, 2)

        # A malformed accepted reply is also "unknown" and locks, never retried (the source answers the wrong action).
        orig = src.call

        async def wrong(body):
            if getattr(body, "name", "") == "group" and body.args.startswith("pause"):
                src.log.append(("pause", json.loads(body.args.partition(" ")[2])))
                return {"output": {"id": "g_one", "resumed": True, "member": None}}
            return await orig(body)
        src.call = wrong
        click(b, "Pause")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('Could not confirm')")
        assert "(HTTP 502)" in out(b)
        assert disabled(b, "Pause") is True
        settle(src, 3)
    finally:
        teardown(srv, b)


@needs_chrome
def test_status_and_inspect_replies_for_a_previous_selection_are_dropped():
    src = SwarmSrc()
    srv, b = page(src)
    try:
        # A status read for g_one is in flight when g_two is selected: its late answer must not show under g_two.
        open_group(b, "g_one")
        late = src.hold("status", "g_one")
        click(b, "Check status")
        b.wait("document.querySelector('.xc-sw-ctl').textContent.includes('Checking status...')")
        deadline = time.monotonic() + BOUND
        while ("status", {"id": "g_one"}) not in src.log and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ("status", {"id": "g_one"}) in src.log
        b.eval("[...document.querySelectorAll('.xc-sw-item')].find(i=>i.querySelector('.xc-sw-id').textContent==='g_two').click()")
        b.wait(f"document.querySelector('.xc-sw-gname')?.textContent==='g_two' && !!{CTL}.querySelector('select')")
        late.set()
        b.wait("document.querySelector('.xc-sw-ctl').textContent.includes('Check status') && !document.querySelector('.xc-sw-ctl').textContent.includes('Checking')")
        time.sleep(0.4)
        assert b.eval("document.querySelector('.xc-sw-states')") is None
        assert b.eval(f"{CTL}.querySelector('.xc-sw-state').children.length") == 0
        assert b.eval("document.querySelector('.xc-sw-gname').textContent") == "g_two"
        assert b.eval(f"[...{CTL}.querySelectorAll('select[aria-label=\"Control target\"] option')].map(o=>o.textContent).join()") == "Choose a target,Whole group,gamma,delta"
        # g_two's own status read works and shows g_two's members only.
        click(b, "Check status")
        b.wait("!!document.querySelector('.xc-sw-states')")
        assert b.eval("[...document.querySelectorAll('.xc-sw-mname')].map(l=>l.textContent)") == ["gamma", "delta"]

        # An inspect reply for g_one that arrives after g_two was picked must not replace g_two.
        gate = src.hold("inspect", "g_one")
        b.eval("[...document.querySelectorAll('.xc-sw-item')].find(i=>i.querySelector('.xc-sw-id').textContent==='g_one').click()")
        b.wait("document.querySelector('.xc-sw-notes')?.textContent.includes('Loading group...')")
        b.eval("[...document.querySelectorAll('.xc-sw-item')].find(i=>i.querySelector('.xc-sw-id').textContent==='g_two').click()")
        b.wait(f"document.querySelector('.xc-sw-gname')?.textContent==='g_two' && !!{CTL}.querySelector('select')")
        gate.set()
        time.sleep(0.5)
        assert b.eval("document.querySelector('.xc-sw-gname').textContent") == "g_two"
        assert b.eval("[...document.querySelectorAll('.xc-sw-member')].map(l=>l.textContent)") == ["gamma", "delta"]
        pick_target(b, "delta")
        click(b, "Pause")
        b.wait("document.querySelector('.xc-sw-ctlout')?.textContent.includes('accepted')")
        assert src.ops(*CONTROL_OPS) == [("pause", {"id": "g_two", "member": "delta"})]
    finally:
        teardown(srv, b)
