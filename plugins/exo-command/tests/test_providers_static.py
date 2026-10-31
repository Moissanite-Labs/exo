"""Static checks on the web client's provider setup code: a key must never reach storage, logs or the URL."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "exo_command" / "static"


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_key_field_is_a_password_input_that_is_not_saved():
    js = text("providers.js")
    assert 'type: "password"' in js
    assert 'autocomplete: "new-password"' in js
    assert 'spellcheck: "false"' in js
    inputs = re.findall(r'h\("input",\s*\{(.*?)\}', js, re.S)
    assert inputs and all(not re.search(r'\bname\s*:', attrs) for attrs in inputs if 'type: "password"' in attrs)


def test_no_local_storage_anywhere():
    for f in STATIC.glob("*.js"):
        body = f.read_text(encoding="utf-8")
        if f.name == "layout.js":  # D-046: the one exception, the layout choice. It never sees keys or text.
            assert "setItem(KEY" in body.replace("(store || localStorage).setItem(KEY", "setItem(KEY") and 'const KEY = "xc-layout"' in body
            continue
        if f.name == "appearance.js":  # Only the enum display preference, never arbitrary text.
            assert 'const KEY = "xc-appearance"' in body
            assert 'const CHOICES = ["dark", "light", "system"]' in body
            assert ".setItem(KEY, choice)" in body and "CHOICES.includes(value)" in body
            continue
        if f.name == "desktop_prefs.js":  # Native display enums only; the bridge validates every outgoing key.
            assert "localStorage.setItem(storage, values[key] || fallback)" in body
            assert "Object.hasOwn(choices, key)" in body and "choices[key].includes(value)" in body
            assert "if (valid(key, value))" in body
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
    assert i < js.index("connecting = bus.start()")


def test_token_goes_with_every_request():
    """D-039: api() sends X-Exo-Token on every call, and stream() puts it on the EventSource URL."""
    js = text("dom.js")
    api = js[js.index("export async function api("):js.index("await fetch(path")]
    assert '"X-Exo-Token": token()' in api and "startsWith(" not in api
    stream = js[js.index("export function stream("):js.index("export async function api(")]
    assert "new EventSource(" in stream and '"token="' in stream


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed to execute the browser save handler")
@pytest.mark.parametrize("source,warning,kind", [
    ("file", "", "ok"),
    ("keychain", "The keychain key still wins; the saved file key is shadowed. Update with --store keychain.", "bad"),
])
def test_web_save_handler_displays_effective_source_and_warning(source, warning, kind):
    script = r'''
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
const func = src.slice(src.indexOf("async function save("), src.indexOf("async function test("));
const data = JSON.parse(fs.readFileSync(0, "utf8"));
const results = [], input = { value: "synthetic-browser-test-key" };
const context = { api: async () => data, show: (...args) => results.push(args), load: () => {}, alive: false };
vm.createContext(context);
vm.runInContext(func + "; this.save = save;", context);
context.save({provider: "openai"}, input).then(() => {
  process.stdout.write(JSON.stringify({results, input: input.value}));
}).catch(() => process.exit(1));
'''
    data = {"stored_in": "file", "effective_source": source, "shadowed_by_env": False,
            "warning": warning, "var": "OPENAI_API_KEY", "core_reloaded": True}
    proc = subprocess.run([shutil.which("node"), "-e", script, str(STATIC / "providers.js")],
                          input=json.dumps(data), capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    rendered = json.loads(proc.stdout)
    name, message, css = rendered["results"][0]
    assert name == "openai" and "Stored in file" in message and f"Effective source: {source}" in message
    assert warning in message and css == kind and rendered["input"] == ""
    assert "synthetic-browser-test-key" not in proc.stdout


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed to execute the browser status chip")
def test_web_chip_shows_a_provider_without_base_url_cannot_answer():
    """A key alone is not a route: the router gives a provider with no base_url no credential."""
    script = r'''
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
const func = src.slice(src.indexOf("function chip("), src.indexOf("export function mount("));
const context = { pill: (text, cls) => [text, cls] };
vm.createContext(context);
vm.runInContext(func + "; this.chip = chip;", context);
const row = { status: "no base_url", needs: "key", key: "found", ready: false, verified: false };
process.stdout.write(JSON.stringify([context.chip(row), context.chip({ ...row, status: "key found", ready: true }),
  context.chip({ ...row, status: "configured", needs: "server", ready: true })]));
'''
    proc = subprocess.run([shutil.which("node"), "-e", script, str(STATIC / "providers.js")],
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [["no base_url", "off"], ["key found", "known"], ["configured · untested", "known"]]


def test_model_list_failure_is_shown_per_class_with_a_retry_only_where_one_helps():
    """D-091: a refused core is not a broken core. The page shows the sentence the route sent and puts the retry
    button behind ``retryable``, so a refusal cannot be answered with a button that changes nothing."""
    js = text("providers.js")
    assert "modelsError" in js and "error_class" in js and "retryable" in js
    assert "modelsError.retryable ?" in js                       # the button is conditional on the class
    assert '"Try again"' in js and "loadModels" in js
    assert "console." not in js
    # the old silent swallow is gone: a failure used to become an empty list, which reads as "no models"
    assert 'catch(() => ({ models: [], available: [] }))' not in js
