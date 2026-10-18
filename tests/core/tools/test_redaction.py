"""Tool results never carry key values the core knows (env, keys file, values it looked up)."""
import asyncio
import random

import pytest

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


class _Proc:
    """A process whose stdout hands out fixed reads, so each read edge is placed exactly."""

    def __init__(self, chunks):
        self.stdout = self
        self._chunks = list(chunks)

    def read1(self, n):
        return self._chunks.pop(0) if self._chunks else b""


NESTED = "postgres://svc:" + SECRET + "@db.internal:5432/prod"  # a key that holds another key
OVERLAP = SECRET[-6:] + "QQQQ-tail-xyz"  # a key that starts with the end of another key


@pytest.mark.parametrize("output", [f"url {NESTED}\nnext\n", f"x {SECRET}QQQQ-tail-xyz done\n"])
def test_stream_equals_whole_output_redaction_at_every_read_edge(monkeypatch, output):
    """Wherever a read ends, the stream matches redacting the whole output: the outer key of a nested pair is not
    sent half redacted, and no part of a key goes out because a longer tail starts another key."""
    from exo.core.tools.builtin.shell import _Capped, _drain
    monkeypatch.setenv("SOME_API_KEY", SECRET)
    monkeypatch.setenv("DB_PASSWORD", NESTED)
    monkeypatch.setenv("OTHER_TOKEN", OVERLAP)
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    whole = keys.redact_secrets(output)
    assert SECRET not in whole and "db.internal" not in whole
    data = output.encode()
    for k in range(1, len(data)):
        seen = []
        _drain(_Proc([data[:k], data[k:]]), _Capped(10_000), seen.append)
        assert "".join(seen) == whole, k
        assert not any(SECRET[:8] in c for c in seen), k


def test_a_three_key_overlap_chain_does_not_reach_the_stream(monkeypatch):
    """A key seen in the first read is later taken apart by a longer key, and a third key overlaps it. Every read
    edge gives whole-output redaction, so no part of the third key goes out."""
    from exo.core.tools.builtin.shell import _Capped, _drain
    for name, value in {"A_TOKEN": "bbaaaabbba", "B_TOKEN": "abaabbbaa", "C_TOKEN": "baaabaab"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    output = "baaabaabbbaaaabbba"
    assert keys.redact_secrets(output) == "[redacted][redacted]"
    data = output.encode()
    for k in range(1, len(data)):
        seen = []
        _drain(_Proc([data[:k], data[k:]]), _Capped(10_000), seen.append)
        assert "".join(seen) == "[redacted][redacted]", k


@pytest.mark.parametrize("alphabet", ["ab", "abc", "acde"])  # with "acde" a key can match inside "[redacted]"
def test_stream_equals_whole_output_redaction_for_random_keys(monkeypatch, alphabet):
    """A differential check: short random keys over a small alphabet nest, overlap and chain far more often than
    real ones. Each output, built from key pieces, is read in two parts at every byte and one byte at a time, and
    the stream must equal redacting the whole output. The held text stays under twice the keys' total length."""
    from exo.core.tools.builtin.shell import _Capped, _drain, _safe_cut
    rnd, vals = random.Random(f"stream-{alphabet}"), []
    monkeypatch.setattr(keys, "known_secret_values", lambda: vals)
    for _ in range(1000):
        keyset = {"".join(rnd.choices(alphabet, k=rnd.randint(2, 7))) for _ in range(rnd.randint(1, 4))}
        vals[:] = sorted(keyset, key=len, reverse=True)  # the order known_secret_values gives
        parts = []
        while sum(map(len, parts)) < 24:
            v = rnd.choice(vals)
            a = rnd.randint(0, len(v))
            parts.append(rnd.choice([v, v[a:], v[:a], rnd.choice(alphabet)]))
        output = "".join(parts)[:rnd.randint(2, 24)]
        whole, data = keys.redact_secrets(output, vals), output.encode()
        for reads in [[data[:k], data[k:]] for k in range(1, len(data))] + [[bytes([b]) for b in data]]:
            seen = []
            _drain(_Proc(reads), _Capped(10_000), seen.append)
            assert "".join(seen) == whole, (vals, output, reads)
        for k in range(1, len(output) + 1):
            assert k - _safe_cut(output[:k], vals) < 2 * sum(map(len, vals)), (vals, output[:k])
