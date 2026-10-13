"""D-034: the /providers panel. The providers_setup engine is replaced by fakes; nothing touches a key store."""

from __future__ import annotations

import asyncio

import pytest
from test_tui import FakeServerClient, make_engine, send, until

from exo import providers_setup as ps
from exo.protocol import schema as s
from exo_tui.app import COMMANDS, ExoApp
from exo_tui.providers import KeyScreen, ProvidersScreen, refuse_key_in_composer
from exo_tui.widgets import Composer, Palette

FAKE_KEY = "sk-fake-TUI-0123456789abcdef"


def demo_rows():
    def row(name, status, **kw):
        base = {"provider": name, "status": status, "needs": "key", "key": "missing", "source": None,
                "key_env": [name.upper() + "_API_KEY"], "local": False, "reachable": None, "verified": False,
                "last_test": None, "models": None, "roles": [], "serves": [], "default": False, "ready": False}
        base.update(kw)
        return base

    return [
        row("anthropic", "verified", key="found", source="keychain", verified=True, ready=True, models=12,
            default=True, last_test={"ok": True, "latency_ms": 412}),
        row("openai", "key found", key="found", source="env", ready=True, models=40),
        row("openrouter", "no key"),
        row("groq", "failed: auth", key="found", source="file", ready=True,
            last_test={"ok": False, "error_class": "auth"}),
        row("ollama", "running", needs="server", key="n/a", key_env=[], local=True, reachable=True, ready=True,
            models=3),
        row("lmstudio", "not running", needs="server", key="n/a", key_env=[], local=True, reachable=False),
    ]


@pytest.fixture
def fakes(monkeypatch):
    calls = {"set_key": [], "test": [], "remove": []}
    monkeypatch.setattr(ps, "rows", lambda *a, **k: demo_rows())
    monkeypatch.setattr(ps, "any_ready", lambda *a, **k: True)

    def set_key(provider, value, **k):
        calls["set_key"].append((provider, value))
        return {"provider": provider, "var": "X", "stored_in": "keychain", "shadowed_by_env": False}

    def test_provider(provider, **k):
        calls["test"].append(provider)
        return {"ok": True, "latency_ms": 99, "error_class": "", "detail": "HTTP 200", "models": 5}

    def remove_key(provider, **k):
        calls["remove"].append(provider)
        return {"removed_from": ["keychain"], "still_in_env": []}

    monkeypatch.setattr(ps, "set_key", set_key)
    monkeypatch.setattr(ps, "test_provider", test_provider)
    monkeypatch.setattr(ps, "remove_key", remove_key)
    return calls


def run(tmp_path, scenario, **kw):
    eng = make_engine(tmp_path)
    client = FakeServerClient(eng)

    async def main():
        app = ExoApp(client, seat="tester", home=str(tmp_path / "exo-state"), **kw)
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await scenario(app, pilot, client, eng)

    asyncio.run(main())


async def open_panel(app, pilot):
    await send(app, pilot, "/providers")
    await until(lambda: isinstance(app.screen, ProvidersScreen) and app.screen.table, pilot)
    await pilot.pause(0.05)
    return app.screen


def leaked(app, extra=""):
    """Every place the fake key must not appear."""
    where = [app.transcript_text(), repr(app.sent), app.query_one(Composer).text, extra,
             app.export_screenshot()]
    return [w for w in where if FAKE_KEY in w]


def test_command_is_listed():
    assert COMMANDS["providers"][0] == "model"


def test_panel_renders_rows(tmp_path, fakes):
    async def sc(app, pilot, client, eng):
        scr = await open_panel(app, pilot)
        text = scr.plain
        assert "PROVIDERS" in text
        for name in ("anthropic", "openai", "openrouter", "groq", "ollama", "lmstudio"):
            assert name in text
        assert "verified 412ms" in text and "failed: auth" in text and "no key" in text
        assert "not running" in text and "running" in text and "key found" in text
        assert "ANTHROPIC_API_KEY · keychain" in text and "OPENAI_API_KEY · env" in text
        assert "12 models" in text and "★ default" in text
        await pilot.press("escape")
        await until(lambda: not isinstance(app.screen, ProvidersScreen), pilot)

    run(tmp_path, sc)


def test_add_key_through_masked_input(tmp_path, fakes):
    async def sc(app, pilot, client, eng):
        scr = await open_panel(app, pilot)
        await pilot.press("down", "down", "a")  # openrouter
        await until(lambda: isinstance(app.screen, KeyScreen), pilot)
        field = app.screen.query_one("Input")
        assert field.password is True
        field.value = FAKE_KEY
        await pilot.pause(0.05)
        assert FAKE_KEY not in app.export_screenshot()  # masked while typed
        await pilot.press("enter")
        await until(lambda: fakes["set_key"], pilot)
        assert fakes["set_key"] == [("openrouter", FAKE_KEY)]
        assert field.value == ""
        await until(lambda: any(isinstance(b, s.CommandRun) and b.name == "providers" for b in app.sent), pilot)
        await until(lambda: "saved in keychain" in scr.plain, pilot)
        reload = [b for b in app.sent if isinstance(b, s.CommandRun) and b.name == "providers"]
        assert [b.args for b in reload] == ["reload openrouter"]
        assert FAKE_KEY not in repr(reload[0])
        assert not leaked(app, scr.plain)
        assert FAKE_KEY not in " ".join(str(w) for w in app.query("Static"))

    run(tmp_path, sc)


def test_key_error_shows_class_only(tmp_path, fakes, monkeypatch):
    def boom(provider, value, **k):
        raise OSError(f"disk said {value}")  # a careless message must not reach the screen

    monkeypatch.setattr(ps, "set_key", boom)

    async def sc(app, pilot, client, eng):
        scr = await open_panel(app, pilot)
        await pilot.press("a")
        await until(lambda: isinstance(app.screen, KeyScreen), pilot)
        app.screen.query_one("Input").value = FAKE_KEY
        await pilot.press("enter")
        await until(lambda: "could not save the key" in scr.plain, pilot)
        assert "OSError" in scr.plain
        assert not leaked(app, scr.plain)

    run(tmp_path, sc)


def test_composer_key_is_refused(tmp_path, fakes):
    async def sc(app, pilot, client, eng):
        assert refuse_key_in_composer(f"/providers add openai {FAKE_KEY}")
        assert not refuse_key_in_composer("/providers add openai")
        await send(app, pilot, f"/providers add openai {FAKE_KEY}")
        await until(lambda: "Keys do not go in the composer" in app.transcript_text(), pilot)
        await pilot.pause(0.1)
        assert not isinstance(app.screen, ProvidersScreen)
        assert not fakes["set_key"]
        assert not leaked(app)
        assert app.query_one(Composer).text == ""
        assert not app.query_one(Composer).history.undo_stack or all(
            FAKE_KEY not in repr(e) for e in app.query_one(Composer).history.undo_stack)

    run(tmp_path, sc)


def test_add_with_name_opens_key_field(tmp_path, fakes):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/providers add openai")
        await until(lambda: isinstance(app.screen, KeyScreen), pilot)
        assert app.screen.provider == "openai"

    run(tmp_path, sc)


def test_test_and_remove_call_the_engine(tmp_path, fakes):
    async def sc(app, pilot, client, eng):
        scr = await open_panel(app, pilot)
        await pilot.press("down", "t")
        await until(lambda: fakes["test"], pilot)
        assert fakes["test"] == ["openai"]
        await until(lambda: "openai ok" in scr.plain, pilot)
        await pilot.press("r")
        await until(lambda: scr.confirm == "openai", pilot)
        assert not fakes["remove"]  # nothing happens before the confirm
        await pilot.press("y")
        await until(lambda: fakes["remove"], pilot)
        assert fakes["remove"] == ["openai"]
        await until(lambda: any(isinstance(b, s.CommandRun) and b.args == "reload openai" for b in app.sent), pilot)

    run(tmp_path, sc)


def test_remove_can_be_cancelled(tmp_path, fakes):
    async def sc(app, pilot, client, eng):
        scr = await open_panel(app, pilot)
        await pilot.press("r", "n")
        await pilot.pause(0.1)
        assert not fakes["remove"] and scr.confirm == ""

    run(tmp_path, sc)


def test_set_default_uses_the_model_palette(tmp_path, fakes, monkeypatch):
    seen = []

    async def sd(self, ref):
        seen.append(ref)

    monkeypatch.setattr(ExoApp, "set_default", sd)

    async def sc(app, pilot, client, eng):
        async def models(body):
            return {"models": [{"route": "openai:gpt-x", "role": "main"}, {"route": "fake:x", "role": "main"}],
                    "available": []}

        _scr = await open_panel(app, pilot)
        real = client.call

        async def call(body):
            return await models(body) if isinstance(body, s.ModelsList) else await real(body)

        client.call = call
        await pilot.press("down", "d")
        await until(lambda: isinstance(app.screen, Palette), pilot)
        assert [i[1] for i in app.screen.items] == ["openai:gpt-x"]
        await pilot.press("enter")
        await until(lambda: seen, pilot)
        assert seen == ["openai:gpt-x"]

    run(tmp_path, sc)


@pytest.mark.parametrize("ready,shown", [(False, True), (True, False)])
def test_first_run_note(tmp_path, monkeypatch, ready, shown):
    monkeypatch.setattr(ps, "any_ready", lambda *a, **k: ready)

    async def sc(app, pilot, client, eng):
        await pilot.pause(0.3)
        assert ("No model provider is set up yet. Type /providers to add a key." in app.transcript_text()) is shown

    run(tmp_path, sc, provider_check=True)


def test_first_run_check_failure_is_silent(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(ps, "any_ready", boom)

    async def sc(app, pilot, client, eng):
        await pilot.pause(0.3)
        assert "provider" not in app.transcript_text()

    run(tmp_path, sc, provider_check=True)


def test_first_run_check_is_off_unless_the_entry_point_asks(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "any_ready", lambda *a, **k: False)

    async def sc(app, pilot, client, eng):
        await pilot.pause(0.3)
        assert "No model provider" not in app.transcript_text()

    run(tmp_path, sc)


def test_first_run_without_provider_opens_panel_and_holds_the_intro(tmp_path, monkeypatch):
    state = {"ready": False}
    monkeypatch.setattr(ps, "rows", lambda *a, **k: demo_rows())
    monkeypatch.setattr(ps, "any_ready", lambda *a, **k: state["ready"])

    async def sc(app, pilot, client, eng):
        await until(lambda: isinstance(app.screen, ProvidersScreen), pilot)
        assert not [b for b in app.sent if isinstance(b, s.TurnSend)]  # no intro without a provider
        await pilot.press("escape")                                     # closed, still none: still no intro
        await until(lambda: not isinstance(app.screen, ProvidersScreen), pilot)
        await pilot.pause(0.2)
        assert not [b for b in app.sent if isinstance(b, s.TurnSend)]
        state["ready"] = True                                           # a key was added; panel closes again
        app.open_providers()
        await until(lambda: isinstance(app.screen, ProvidersScreen), pilot)
        await pilot.press("escape")
        await until(lambda: any(isinstance(b, s.TurnSend) for b in app.sent), pilot)
        assert sum(isinstance(b, s.TurnSend) for b in app.sent) == 1    # one intro, not two

    run(tmp_path, sc, provider_check=True, auto_intro=True)


def test_first_run_with_provider_goes_straight_to_the_intro(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "any_ready", lambda *a, **k: True)

    async def sc(app, pilot, client, eng):
        await until(lambda: any(isinstance(b, s.TurnSend) for b in app.sent), pilot)
        assert not isinstance(app.screen, ProvidersScreen)

    run(tmp_path, sc, provider_check=True, auto_intro=True)
