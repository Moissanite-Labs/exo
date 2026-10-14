"""Tool results never carry key values the core knows (env, keys file, values it looked up)."""
import asyncio

from exo.core.models import keys
from exo.core.tools import ToolContext, default_registry
from exo.core.types import ToolCall

SECRET = "sk-test-0123456789abcdef"
OTHER = "tok_ZZZZ9999yyyyxxxx"


def _call(reg, name, **args):
    return asyncio.run(reg.call(ToolCall(id="c1", name=name, arguments=args), ToolContext(cwd=".")))


def test_env_keys_file_and_loaded_values_are_redacted(tmp_path, monkeypatch):
    kf = tmp_path / "keys.env"
    kf.write_text(f"FILE_API_KEY={OTHER}\n")
    kf.chmod(0o644)  # redaction reads it even when the lookup would refuse
    monkeypatch.setenv("EXO_KEYS_FILE", str(kf))
    monkeypatch.setenv("SOME_API_KEY", SECRET)
    text = f"a {SECRET} b {OTHER} c"
    out = keys.redact_secrets(text)
    assert SECRET not in out and OTHER not in out and out == "a [redacted] b [redacted] c"
    keys._loaded.clear()
    loaded = "keychain-held-value-12345"
    monkeypatch.setattr(keys, "keychain_get", lambda name: loaded)
    monkeypatch.delenv("NOT_SET_KEY", raising=False)
    assert keys.lookup_key("NOT_SET_KEY") == loaded
    assert keys.redact_secrets(f"x {loaded}") == "x [redacted]"
    keys._loaded.clear()


def test_registry_redacts_tool_output(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", SECRET)
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    reg = default_registry()
    r = _call(reg, "bash", command=f"echo {SECRET}")
    assert SECRET not in r.text() and "[redacted]" in r.text()


def test_short_values_are_left_alone(monkeypatch):
    monkeypatch.setenv("TINY_KEY", "abc")
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    assert keys.redact_secrets("abc def") == "abc def"
