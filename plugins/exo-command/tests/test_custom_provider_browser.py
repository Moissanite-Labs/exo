"""Disposable browser, fake core and synthetic credentials; no external provider calls."""
import importlib.util

import pytest

from browser import Browser, Served, find_chrome
from exo import providers_setup as ps
from exo.config import Config
from exo.protocol import schema as s
from test_layout_web import ThreadSrc

needs_browser = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"),
                                   reason="Chrome or websockets missing")


@needs_browser
@pytest.mark.parametrize("width", [390, 900])
def test_custom_provider_form_persists_and_handles_errors(monkeypatch, tmp_path, width):
    home = tmp_path / "home"
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.setenv("EXO_KEYS_FILE", str(tmp_path / "keys.env"))
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    monkeypatch.setenv("EXO_NO_SECRET_SERVICE", "1")
    monkeypatch.setattr(ps, "default_store", lambda: "file")

    class Source(ThreadSrc):
        async def call(self, command):
            if isinstance(command, s.ConfigGet):
                return {"value": Config.load(home).get(command.key)}
            if isinstance(command, s.ConfigSet):
                cfg = Config.load(home)
                cfg.set(command.key, command.value)
                cfg.save()
                return {"saved": True}
            return await super().call(command)

    srv = Served(Source(pairs=0))
    try:
        with Browser(width=width, height=900) as browser:
            browser.goto(srv.url("providers"))
            browser.wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='Add custom provider')")
            assert browser.eval("document.body.textContent.includes('Google Gemini (AI Studio)')")
            browser.eval("[...document.querySelectorAll('button')].find(b=>b.textContent==='Add custom provider').click()")
            assert browser.eval("[...document.querySelectorAll('.xc-custom-form .xc-field')].every(x=>{const r=x.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth+1})")
            browser.type('[aria-label="Provider name"]', "demo")
            browser.type('[aria-label="Base URL"]', "https://example.test/v1?key=bad")
            browser.type('[aria-label="Model ID"]', "model-one")
            browser.type('[aria-label="Custom provider API key"]', "synthetic-browser-key-0123456789")
            browser.eval("document.querySelector('.xc-custom-form').requestSubmit()")
            browser.wait("document.querySelector('.xc-custom-form [role=status]').textContent.includes('without credentials')")
            assert browser.eval("document.querySelector('[aria-label=\"Custom provider API key\"]').value") == ""
            assert not (home / "config.json").exists()
            browser.eval("[...document.querySelectorAll('button')].find(b=>b.textContent==='Cancel').click()")
            assert not browser.eval("!!document.querySelector('.xc-custom-form')")
            browser.eval("[...document.querySelectorAll('button')].find(b=>b.textContent==='Add custom provider').click()")
            browser.type('[aria-label="Provider name"]', "demo")
            browser.type('[aria-label="Base URL"]', "https://example.test/v1")
            browser.type('[aria-label="Model ID"]', "model-one")
            browser.type('[aria-label="Custom provider API key"]', "synthetic-browser-key-0123456789")
            browser.eval("document.querySelector('.xc-custom-form').requestSubmit()")
            browser.wait("document.body.textContent.includes('Restart the core')")
            assert "synthetic-browser-key" not in (home / "config.json").read_text()
            assert "synthetic-browser-key" in (tmp_path / "keys.env").read_text()
            browser.reload()
            browser.wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='Edit demo')")
            browser.eval("[...document.querySelectorAll('button')].find(b=>b.textContent==='Edit demo').click()")
            assert browser.eval("document.querySelector('[aria-label=\"Model ID\"]').value") == "model-one"
            browser.eval("document.querySelector('[aria-label=\"Model ID\"]').value='model-two'")
            browser.eval("document.querySelector('.xc-custom-form').requestSubmit()")
            browser.wait("document.body.textContent.includes('model-two')")
            assert Config.load(home).get("models.providers.custom-demo.setup_model") == "model-two"
    finally:
        srv.close()
