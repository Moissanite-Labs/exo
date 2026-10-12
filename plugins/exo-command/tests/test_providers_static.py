"""Static checks on the web client's provider setup code: a key must never reach storage, logs or the URL."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "exo_command" / "static"


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_key_field_is_a_password_input_that_is_not_saved():
    js = text("providers.js")
    assert 'type: "password"' in js
    assert 'autocomplete: "new-password"' in js
    assert 'spellcheck: "false"' in js
    assert not re.search(r"\bname:\s*\"", js)           # no name attribute for a password manager to key on


def test_no_local_storage_anywhere():
    for f in STATIC.glob("*.js"):
        body = f.read_text(encoding="utf-8")
        if f.name == "layout.js":  # D-046: the one exception, the layout choice. It never sees keys or text.
            assert "setItem(KEY" in body.replace("(store || localStorage).setItem(KEY", "setItem(KEY") and 'const KEY = "xc-layout"' in body
            continue
        assert "localStorage" not in body, f.name


def test_session_storage_only_for_the_token_in_provider_code():
    for name in ("providers.js", "dom.js"):
        for m in re.finditer(r"sessionStorage\.\w+\(([^)]*)\)", text(name)):
            assert m.group(1).strip().startswith('"xc-token"'), (name, m.group(0))
    for m in re.finditer(r"sessionStorage\.setItem\(([^,)]*)", text("app.js")):
        assert m.group(1).strip() in ('"xc-token"', '"xc-booted"')


def test_providers_js_never_logs():
    js = text("providers.js")
    assert "console." not in js


def test_key_field_is_cleared_before_the_request():
    js = text("providers.js")
    body = js[js.index("async function save"):js.index("async function test")]
    assert 'input.value = ""' in body
    assert body.index('input.value = ""') < body.index("await api(")
    assert "const body = { key: input.value }" in body
    assert "innerHTML" not in js and "textContent = " not in js
    assert "?key=" not in js and "location" not in js


def test_app_strips_the_token_from_the_url():
    js = text("app.js")
    assert 'sessionStorage.setItem("xc-token"' in js
    assert "history.replaceState" in js
    i = js.index("takeToken();")
    assert i < js.index("show((location.hash")           # before the first view reads the hash
    assert i < js.index("await bus.start()")


def test_token_goes_with_every_request():
    """D-039: api() sends X-Exo-Token on every call, and stream() puts it on the EventSource URL."""
    js = text("dom.js")
    api = js[js.index("export async function api("):js.index("await fetch(path")]
    assert '"X-Exo-Token": token()' in api and "startsWith(" not in api
    stream = js[js.index("export function stream("):js.index("export async function api(")]
    assert "new EventSource(" in stream and '"token="' in stream
