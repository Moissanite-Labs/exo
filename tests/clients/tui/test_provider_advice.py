"""Provider advice in the terminal client: a keyless default gets start or login advice instead of "add a key", and
a passing /providers test clears the core's cooldown at once. The providers_setup engine is faked."""

from __future__ import annotations

import time

import pytest
from test_providers import demo_rows, fakes, open_panel, run  # noqa: F401  (fakes is a fixture)
from test_tui import until

from exo import providers_setup as ps
from exo.core.models.router import Breaker
from exo.protocol import schema as s

ADD_A_KEY = "No model provider is set up yet. Type /providers to add a key."


def keyless_default(name):
    rows = [{**r, "default": False} for r in demo_rows() if r["provider"] not in ("anthropic", "ollama")]
    rows.append({**demo_rows()[0], "provider": "anthropic", "status": "no key", "key": "missing", "source": None,
                 "ready": False, "verified": False, "default": False})
    if name == "ollama":
        rows.append({**demo_rows()[4], "status": "not running", "reachable": False, "ready": False, "default": True,
                     "base_url": "http://127.0.0.1:11434/v1"})
    else:
        rows.append({"provider": name, "status": "not installed", "needs": "cli", "key": "n/a", "key_env": [],
                     "transport": name, "local": False, "installed": False, "ready": False, "verified": False,
                     "last_test": None, "models": 3, "roles": [], "serves": ["main"], "default": True})
    return rows


def test_setup_advice_follows_what_the_default_needs():
    from exo_tui.providers import setup_advice

    assert setup_advice(keyless_default("ollama")) == ("ollama is not running at 127.0.0.1:11434: "
                                                       "start it, then test it in /providers.")
    assert setup_advice(keyless_default("claude-cli")) == ("claude-cli is not installed: install it and log in, "
                                                           "then test it in /providers.")
    muse = [{**r, "status": "not logged in"} if r.get("default") else r for r in keyless_default("muse-cli")]
    assert setup_advice(muse) == "muse-cli is not logged in: log in, then test it in /providers."
    keyed = [{**r, "ready": False} for r in demo_rows()]  # anthropic is the default and needs a key
    assert setup_advice(keyed) == ADD_A_KEY
    assert setup_advice([]) == ADD_A_KEY


@pytest.mark.parametrize("name,advice", [
    ("ollama", "ollama is not running at 127.0.0.1:11434: start it, then test it in /providers."),
    ("claude-cli", "claude-cli is not installed: install it and log in, then test it in /providers."),
])
def test_first_run_with_a_keyless_default_does_not_ask_for_a_key(tmp_path, monkeypatch, name, advice):
    monkeypatch.setattr(ps, "rows", lambda *a, **k: keyless_default(name))
    monkeypatch.setattr(ps, "any_ready", lambda *a, **k: False)

    async def sc(app, pilot, client, eng):
        await pilot.pause(0.3)
        text = app.transcript_text()
        assert advice in text and "add a key" not in text

    run(tmp_path, sc, provider_check=True)


def test_passing_test_clears_the_core_cooldown_at_once(tmp_path, fakes, monkeypatch):  # noqa: F811
    """The audit: the server came back and /providers t passed, yet the chat stayed blocked until the cooldown ran
    out. A failed test leaves the core alone; a passing one tells it to reload that provider."""
    results = [{"ok": False, "latency_ms": None, "error_class": "not_running", "detail": "", "models": None},
               {"ok": True, "latency_ms": 40, "error_class": "", "detail": "HTTP 200", "models": 1}]
    monkeypatch.setattr(ps, "test_provider", lambda provider, **k: results.pop(0))

    def prepare(eng):
        eng.router.record_failure("ollama", "llama3.2", None, None, True)
        eng.router.breakers["ollama:llama3.2"] = Breaker(level=1, until=time.time() + 600)

    def reloads(app):
        return [b.args for b in app.sent if isinstance(b, s.CommandRun) and b.name == "providers"]

    async def sc(app, pilot, client, eng):
        scr = await open_panel(app, pilot)
        scr.idx = [r["provider"] for r in scr.table].index("ollama")
        await pilot.press("t")
        await until(lambda: "ollama failed" in scr.plain and not scr.work, pilot)
        assert reloads(app) == [] and "ollama" in eng.router.cooldowns
        await pilot.press("t")
        await until(lambda: "ollama ok" in scr.plain and "ollama" not in eng.router.cooldowns, pilot)
        assert "ollama:llama3.2" not in eng.router.breakers
        assert reloads(app) == ["reload ollama"]

    run(tmp_path, sc, prepare=prepare)
