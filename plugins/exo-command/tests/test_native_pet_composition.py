"""The native restoration consumer and companion share the selected-conversation lifecycle."""

import importlib.util

import pytest
from browser import Browser, Served, find_chrome
from exo_command.source import SourceError
from test_pet_web import PetSource


@pytest.mark.skipif(
    not find_chrome() or not importlib.util.find_spec("websockets"),
    reason="Chrome/websockets missing",
)
def test_confirmed_missing_native_thread_clears_companion_selection():
    class Missing(PetSource):
        async def call(self, body):
            if type(body).TYPE == "session.get" and body.session == "missing":
                raise SourceError("not_found", "Synthetic missing conversation")
            return await super().call(body)

    src = Missing()
    srv = Served(src)
    try:
        with Browser() as browser:
            browser.call(
                "Page.addScriptToEvaluateOnNewDocument",
                source="window.__exoDesktopPrefs={thread:'missing',scroll:.4,petMode:'mini'};window.savedPrefs=[];window.webkit={messageHandlers:{exoDesktop:{postMessage:p=>savedPrefs.push(p)}}};",
            )
            browser.goto(srv.url())
            browser.wait("savedPrefs.some(p=>p.thread===null && p.scroll===null)")
            browser.wait(
                "document.querySelector('.xc-pet-glyph')?.getAttribute('aria-label').includes('No conversation selected')"
            )
            assert not browser.eval("!!document.querySelector('.xc-sess.on')")
            assert not any(kind == "turn.send" for kind, _ in src.calls)
    finally:
        srv.close()
