"""Approval cards must not turn a latest-message reader into a scrolled-back reader (#1012)."""
import json

import pytest

from browser import Browser, Served, find_chrome
from test_workspace_chat import WorkspaceSource

pytestmark = pytest.mark.skipif(not find_chrome(), reason="Chrome is unavailable")


def requests():
    return [{"request_id": f"reading-{i}", "session": "s1", "summary": f"Review synthetic request {i}",
             "risk": "medium", "options": ["allow", "deny"]} for i in range(3)]


@pytest.mark.parametrize("layout", ["classic", "inverse"])
@pytest.mark.parametrize("scenario", ["initial", "events", "away"])
def test_approval_geometry_preserves_the_reader(layout, scenario):
    src = WorkspaceSource()
    if scenario == "initial":
        src.pending = requests()
    srv = Served(src)
    try:
        with Browser(width=1200, height=900) as b:
            b.goto(srv.url())
            b.eval(f"localStorage.setItem('xc-layout',{json.dumps(layout)})")
            b.reload()
            b.wait("document.querySelectorAll('.xc-msg').length>30")
            b.settle()
            if scenario == "away":
                b.eval("(()=>{const l=document.querySelector('.xc-log');"
                       "l.dispatchEvent(new Event('wheel'));l.scrollTop="
                       + ("400" if layout == "inverse" else "l.scrollHeight-l.clientHeight-400") + ";"
                       "l.dispatchEvent(new Event('scroll'));"
                       "window.readingIndex=[...l.children].findIndex(e=>e.getBoundingClientRect().bottom>l.getBoundingClientRect().top+10);"
                       "window.readingOffset=l.children[readingIndex].getBoundingClientRect().top-l.getBoundingClientRect().top;})()")

            def check():
                b.frames()
                if scenario == "away":
                    assert abs(b.eval("(()=>{const l=document.querySelector('.xc-log');return "
                                      "l.children[readingIndex].getBoundingClientRect().top-l.getBoundingClientRect().top-readingOffset;})()")) < 2
                    assert not b.eval("document.querySelector('.xc-latest').hidden")
                else:
                    distance = b.eval("(()=>{const l=document.querySelector('.xc-log');return "
                                      + ("l.scrollTop" if layout == "inverse" else "l.scrollHeight-l.clientHeight-l.scrollTop") + ";})()")
                    assert abs(distance) < 2
                    assert b.eval("document.querySelector('.xc-latest').hidden")

            check()
            if scenario != "initial":
                src.pending = requests()
                for i, req in enumerate(src.pending, 1):
                    srv.call(srv.centre.record, {"seq": i, "type": "approval.requested", "session": "s1", "body": req})
            b.wait("document.querySelectorAll('[data-approval]').length===3")
            check()
            b.eval("import('/static/bus.js').then(bus=>{bus.restart();return true;})")
            b.settle(syncs=2)
            check()
            src.pending = []
            for i, req in enumerate(requests(), 4):
                srv.call(srv.centre.record, {"seq": i, "type": "approval.resolved", "session": "s1",
                                           "body": {"request_id": req["request_id"], "decision": "deny"}})
            b.wait("document.querySelectorAll('[data-approval]').length===0")
            check()
            b.eval("import('/static/bus.js').then(bus=>{bus.restart();return true;})")
            b.settle(syncs=3)
            check()
            assert "approvals.answer" not in src.calls  # events exercise geometry, not authorization actions
    finally:
        srv.close()
