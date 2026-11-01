"""Pane controls use existing DOM nodes and retain the active conversation."""
import importlib.util

import pytest

from browser import Browser, Served, find_chrome
from test_model_command_web import ModelSrc

pytestmark = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec('websockets'), reason='headless Chrome unavailable')


def test_pane_controls_persist_keep_draft_and_reveal_details_at_narrow_width():
    web = Served(ModelSrc())
    try:
        with Browser() as browser:
            browser.goto(web.url())
            browser.wait('!!document.querySelector("[data-pane=conversation]")')
            browser.type('.xc-input', 'keep pane draft')
            browser.eval('globalThis.savedComposer=document.querySelector(".xc-input");globalThis.savedLog=document.querySelector(".xc-log")')
            browser.eval('document.querySelector("[data-pane-toggle=details]").focus()')
            browser.press(" ", "Space", 32)
            assert browser.eval('document.querySelector("[data-pane=details]").hidden')
            browser.eval('Array.from(document.querySelectorAll("button")).find(b=>b.getAttribute("aria-label")==="Move conversation left").click()')
            assert browser.eval('document.querySelector(".xc-pane-layout").firstElementChild.dataset.pane') == 'conversation'
            browser.eval('const r=document.querySelector("[data-pane-width=details]");r.value="400";r.dispatchEvent(new Event("input"))')
            assert browser.eval('document.querySelector("[data-pane=details]").style.width') == '400px'
            assert browser.eval('document.querySelector(".xc-input")===savedComposer && document.querySelector(".xc-log")===savedLog')
            assert browser.eval('savedComposer.value') == 'keep pane draft'
            event = {'seq': 101, 'type': 'message.delta', 'session': 's1',
                     'body': {'message_id': 'pane-live', 'text': 'pane stream retained'}}
            def record():
                web.centre.state.apply(event)
                web.centre.record(event)
            web.call(record)
            browser.wait('document.querySelector(".xc-log").textContent.includes("pane stream retained")')
            browser.call('Emulation.setDeviceMetricsOverride', width=600, height=700, deviceScaleFactor=1, mobile=False)
            browser.eval('document.querySelector("[data-pane-toggle=details]").click()')
            assert browser.eval('getComputedStyle(document.querySelector("[data-pane=details]")).display') != 'none'
            assert browser.eval('document.documentElement.scrollWidth <= innerWidth')
            assert browser.eval('document.querySelector(".xc-input").getBoundingClientRect().width > 100')
            browser.goto(web.url())
            browser.wait('document.querySelector("[data-pane=details]")?.style.width === "400px"')
            assert browser.eval('document.querySelector(".xc-pane-layout").firstElementChild.dataset.pane') == 'conversation'
            browser.eval('Array.from(document.querySelectorAll(".xc-pane-controls button")).find(b=>b.textContent==="Reset panes").click()')
            assert browser.eval('document.querySelector("[data-pane-width=details]").value') == '340'
    finally:
        web.close()
