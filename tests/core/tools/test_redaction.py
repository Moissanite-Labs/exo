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


def test_streamed_output_never_carries_a_key_split_across_reads(monkeypatch, tmp_path):
    """tool.output chunks are redacted across read edges: a key written in two parts never reaches the stream, a
    tail that only looks like the start of a key is held back and sent at the end, and the stream matches the result."""
    monkeypatch.setenv("SOME_API_KEY", SECRET)
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    seen = []
    cmd = f"printf 'start\\n'; sleep 0.3; printf 'token {SECRET[:10]}'; sleep 0.3; printf '{SECRET[10:]} then {SECRET[:6]}'"
    r = asyncio.run(default_registry().call(ToolCall(id="c1", name="bash", arguments={"command": cmd}),
                                            ToolContext(cwd=str(tmp_path), emit=seen.append)))
    assert SECRET not in "".join(seen) and not any(SECRET[:10] in c for c in seen)
    assert "".join(seen) == f"start\ntoken [redacted] then {SECRET[:6]}"
    assert r.text() == "".join(seen) + "\n[exit 0]"
    from exo.core.tools.builtin.shell import _key_start  # ordinary text is not held back, only a possible key start
    assert _key_start("start\n", [SECRET]) == 0 and _key_start(f"token {SECRET[:10]}", [OTHER, SECRET]) == 10


def test_streamed_output_redacts_a_key_across_a_64k_read_edge(monkeypatch, tmp_path):
    """No timing needed: a large output splits at the 64 KiB read size, here inside the key."""
    monkeypatch.setenv("SOME_API_KEY", SECRET)
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    (tmp_path / "big.log").write_text("x" * (65536 - 10) + SECRET + "\n" + "y" * 100_000)
    seen = []
    r = asyncio.run(default_registry().call(ToolCall(id="c1", name="bash", arguments={"command": "cat big.log"}),
                                            ToolContext(cwd=str(tmp_path), emit=seen.append)))
    joined = "".join(seen)
    assert SECRET not in joined and joined == "x" * (65536 - 10) + "[redacted]\n" + "y" * 100_000
    assert SECRET not in r.text() and r.text().endswith("[exit 0]")
