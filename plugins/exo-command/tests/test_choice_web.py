"""D-066 in the web client: a pick on a choice panel reaches the agent. The panel's ui.respond "acted" {choice} is a
learning signal only, so the label also goes to the thread as the user's reply (a new turn, or a steer while a turn
runs), exactly once, and the choice closes. The page tests drive headless Chrome and skip without it."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from browser import Browser, Served, find_chrome
from exo_command import api
from exo_command.config import load_settings
from exo_command.service import CommandCentre

STATIC = Path(__file__).resolve().parents[1] / "exo_command" / "static"
OPTIONS = [{"key": "a", "label": "Ship it"}, {"key": "b", "label": "Wait a day"}]


def panel_event(seq=1, sid="s1", pid="p1"):
    return {"seq": seq, "type": "ui.panel", "session": sid, "ts": "2026-10-13T10:00:00Z",
            "body": {"panel_id": pid, "action": "open", "kind": "choice", "title": "Release",
                     "data": {"prompt": "Release now?", "options": OPTIONS}}}


class ChoiceSrc:
    """One idle thread; records every command the page sends."""

    centre = None

    def __init__(self):
        self.calls = []

    async def call(self, body):
        t = type(body).TYPE
        self.calls.append(body)
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        if t == "session.list":
            return {"sessions": [{"id": "s1", "title": "demo", "agent": "atlas", "model": "m", "status": "idle",
                                  "updated": "2026-10-13T10:00:00Z"}]}
        if t == "session.get":
            return {"session": {"id": "s1", "model": "m", "agent": "atlas", "cwd": ""}, "messages": [], "seq": 0}
        if t == "config.get":
            return {"key": body.key, "value": None}
        if t == "turn.send":
            return {"message_id": f"m{len(self.calls)}"}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None

    def of(self, kind):
        return [b for b in self.calls if type(b).TYPE == kind]


def test_static_wiring():
    chat, panels, bus = ((STATIC / n).read_text(encoding="utf-8") for n in ("chat.js", "panels.js", "bus.js"))
    assert "act({ choice: o.key }, o.label)" in panels
    assert "async function pickChoice(p, value, label)" in chat and "picked.has(p.panel_id)" in chat
    assert "export function forget(id)" in bus


def test_an_acted_choice_closes_on_the_server_but_other_panels_stay():
    async def go():
        c = CommandCentre(load_settings({}), ChoiceSrc())
        c.record(panel_event())
        c.record({**panel_event(2, pid="p2"), "body": {**panel_event()["body"], "panel_id": "p2", "kind": "table"}})
        for pid in ("p1", "p2"):
            assert (await api.dispatch(c, "POST", "/api/ui/respond", {},
                                       {"panel_id": pid, "action": "acted", "value": {"choice": "a"}}))[0] == 200
        return set(c.panels)

    assert asyncio.run(go()) == {"p2"}


needs_chrome = pytest.mark.skipif(not find_chrome() or not __import__("importlib").util.find_spec("websockets"),
                                  reason="headless Chrome or the websockets package is missing")
CARD = "document.querySelector('.xc-sidepanel[data-kind=\"choice\"]')"


@needs_chrome
@pytest.mark.parametrize("busy", [False, True])
def test_a_pick_sends_acted_and_the_label_once_and_closes(busy):
    src = ChoiceSrc()
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("document.querySelector('.xc-input') !== null")
            b.eval("new Promise((r) => setTimeout(r, 400))")
            if busy:   # a turn is running: the pick goes in as a steer
                srv.call(srv.centre.record, {"seq": 1, "type": "turn.started", "session": "s1",
                                             "ts": "2026-10-13T10:00:00Z", "body": {"turn_id": "t1"}})
                b.wait("document.querySelector('.xc-composer').classList.contains('xc-steer')")
            srv.call(srv.centre.record, panel_event(seq=2))
            b.wait(f"{CARD} !== null")
            # two quick clicks on the same option, then one on the other: only the first counts
            b.eval(f"(()=>{{const k=[...{CARD}.querySelectorAll('.xc-key')];k[1].click();k[1].click();k[0].click()}})()")
            b.wait(f"{CARD} === null")
            b.eval("new Promise((r) => setTimeout(r, 500))")
            acted = [x for x in src.of("ui.respond") if x.action == "acted"]
            assert [(x.panel_id, x.value) for x in acted] == [("p1", {"choice": "b"})]
            sent = src.of("turn.send")
            assert [(x.session, x.text) for x in sent] == [("s1", "Wait a day")]
            assert sent[0].mode == ("steer" if busy else "send")
            assert "p1" not in srv.centre.panels   # a reload does not bring it back
            srv.call(srv.centre.record, {**panel_event(seq=3, pid="p9"), "body": {**panel_event()["body"],
                                                                                  "panel_id": "p9", "kind": "table"}})
            b.wait("document.querySelector('.xc-sidepanel[data-panel=\"p9\"]') !== null")
            assert b.eval(f"{CARD} === null")      # another panel's event does not redraw the answered choice
    finally:
        srv.close()
