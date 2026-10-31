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


def _random_case(rnd, alphabet):
    """A few short random keys, longest first, and an output built from key pieces and marker pieces."""
    keyset = {"".join(rnd.choices(alphabet, k=rnd.randint(2, 7))) for _ in range(rnd.randint(1, 4))}
    vals = sorted(keyset, key=len, reverse=True)  # the order known_secret_values gives
    parts = []
    while sum(map(len, parts)) < 24:
        v, a, b = rnd.choice(vals), rnd.randint(0, 10), rnd.randint(0, 10)
        parts.append(rnd.choice([v, v[a % (len(v) + 1):], v[:a % (len(v) + 1)], rnd.choice(alphabet),
                                 keys.REDACTED[min(a, b):max(a, b)]]))
    return vals, "".join(parts)[:rnd.randint(2, 24)]


# "[]acdert" and "ted]x" are the letters of the marker, so keys can match inside a marker or across one.
ALPHABETS = ["ab", "abc", "acde", "ab]", "a[b", "ab[]", "ted]x", "[rex", "[]acdert"]


@pytest.mark.parametrize("alphabet", ALPHABETS)
def test_stream_equals_whole_output_redaction_for_random_keys(monkeypatch, alphabet):
    """A differential check: short random keys over a small alphabet nest, overlap and chain far more often than
    real ones, and keys over the marker's letters meet "[redacted]" in the output. Each output is read in two parts
    at every byte, one byte at a time and in a few random splits. The stream must equal redacting the whole output,
    and each piece must come back unchanged from the engine's second pass, which keeps the markers. The held text
    stays under twice the keys' total length."""
    from exo.core.tools.builtin.shell import _Capped, _drain, _safe_cut
    rnd, vals = random.Random(f"stream-{alphabet}"), []
    monkeypatch.setattr(keys, "known_secret_values", lambda: vals)
    for _ in range(600):
        vals[:], output = _random_case(rnd, alphabet)
        whole, data = keys.redact_secrets(output, vals), output.encode()
        splits = [[data[:k], data[k:]] for k in range(1, len(data))] + [[bytes([b]) for b in data]]
        for _ in range(4):
            edges = sorted(rnd.sample(range(1, len(data)), min(len(data) - 1, rnd.randint(2, 4))))
            splits.append([data[a:b] for a, b in zip([0, *edges], [*edges, len(data)])])
        for reads in splits:
            seen = []
            _drain(_Proc(reads), _Capped(10_000), seen.append)
            assert "".join(seen) == whole, (vals, output, reads)
            assert all(keys.redact_secrets(c, vals, keep_markers=True) == c for c in seen), (vals, output, seen)
        for k in range(1, len(output) + 1):
            assert k - _safe_cut(output[:k], vals) < 2 * sum(map(len, vals)), (vals, output[:k])


def _key_start_at_every_place(text, vals):
    """``shell._key_start`` as first written: every tail shorter than a value, tested against every value."""
    return max((len(text) - i for v in vals for i in range(max(len(text) - len(v) + 1, 0), len(text))
                if v.startswith(text[i:])), default=0)


def test_key_start_skips_only_places_that_cannot_start_a_key(monkeypatch):
    """``_key_start`` tests only the places that hold a key's first character and could give a longer tail. Many
    keys that share first characters, small reads and tails full of those characters must give what testing every
    place gives, and the stream must still equal redacting the whole output."""
    from exo.core.tools.builtin.shell import _Capped, _drain, _key_start
    rnd, vals = random.Random("key-start"), []
    monkeypatch.setattr(keys, "known_secret_values", lambda: vals)
    for _ in range(300):
        found = {rnd.choice("ab") + "".join(rnd.choices("abc", k=rnd.randint(1, 12))) for _ in range(rnd.randint(1, 30))}
        vals[:] = sorted(found, key=len, reverse=True)
        output = "".join(rnd.choice([rnd.choice(vals)[:rnd.randint(1, 13)], *"abcx\n"]) for _ in range(30))
        assert _key_start("", vals) == 0
        for k in range(1, len(output) + 1):
            assert _key_start(output[:k], vals) == _key_start_at_every_place(output[:k], vals), (vals, output[:k])
        whole, data = keys.redact_secrets(output, vals), output.encode()
        for size in (1, 3, 8, 80):
            seen = []
            _drain(_Proc([data[i:i + size] for i in range(0, len(data), size)]), _Capped(10_000), seen.append)
            assert "".join(seen) == whole, (vals, output, size)


def test_a_bracket_key_does_not_let_another_key_reach_the_stream(monkeypatch):
    """The review's repro. The first key ends in "]" and the second starts with it. Redacting the whole output
    matched the second key across the first key's marker, while the stream cut only for matches in the output
    itself. So the third key went out in two pieces that join to the whole key. Both now match in the output."""
    from exo.core.tools.builtin.shell import _Capped, _drain
    env = {"A_PASSWORD": "admin-pass]", "B_TOKEN": "]abcsk-900", "C_API_KEY": "sk-9000-s"}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    reads = [b"admin-pass]abcsk-9000-sk-9000-s", b"\nmore\n"]
    whole = keys.redact_secrets(b"".join(reads).decode())
    assert whole == "[redacted]abc[redacted]k-9000-s\nmore\n"
    seen = []
    _drain(_Proc(reads), _Capped(10_000), seen.append)
    assert "".join(seen) == whole and not any(v in c for v in env.values() for c in seen)
    data = b"".join(reads)
    for k in range(1, len(data)):
        seen = []
        _drain(_Proc([data[:k], data[k:]]), _Capped(10_000), seen.append)
        assert "".join(seen) == whole, k


def _old_redact(text, values, marker="[redacted]"):
    """redact_secrets before matches moved to the output itself: one value after another over the text so far."""
    for v in values:
        if v in text:
            text = text.replace(v, marker)
    return text


@pytest.mark.parametrize("alphabet", ["ab", "abc", "acde", "redact", "xyz", *ALPHABETS[3:]])
def test_redaction_is_the_old_replace_with_a_marker_no_key_can_match(alphabet):
    """Redaction gives what the old value-by-value replace gives with a marker no key can match, for any keys. So
    where no key can match inside or across a "[redacted]" marker, its output is the same as before. A key without
    "[" or "]" can only do that by being part of the word "redacted"."""
    rnd, same = random.Random(f"old-{alphabet}"), 0
    for _ in range(3000):
        vals, text = _random_case(rnd, alphabet)
        new = keys.redact_secrets(text, vals)
        assert new == _old_redact(text, vals, "\0").replace("\0", keys.REDACTED), (vals, text)
        if not any("[" in v or "]" in v or v in "redacted" for v in vals):
            assert new == _old_redact(text, vals), (vals, text)
            same += 1
    assert same >= 50  # every alphabet gives cases where the old output must come back


def test_tool_output_events_equal_the_redacted_result(monkeypatch, tmp_path):
    """The engine redacts each piece once more on its way to clients. That pass keeps the markers already in the
    piece, so a key that holds "]" does not match across one there, and the events still equal the result."""
    from exo.core.engine import CoreEngine
    from exo.protocol import schema as s
    monkeypatch.setenv("FIRST_TOKEN", "pass-word-1")
    monkeypatch.setenv("SECOND_TOKEN", "d]tail-9999")
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    eng = CoreEngine(tmp_path / "home", {"fake": {"rules": []}, "roles": {"main": ["fake:x"]}}, cwd=str(tmp_path),
                     env={})
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")

    async def go():
        try:
            r = await eng.run_tool(ToolCall("c1", "bash", {"command": "printf 'pass-word-1tail-9999 end'"}), {})
            await asyncio.sleep(0.1)  # the last piece is published on the loop
            return r
        finally:
            await asyncio.wait_for(eng.close(), 5)

    r = asyncio.run(go())
    pieces = [e.body.text for e in eng.hub.since(0) if isinstance(e.body, s.ToolOutput)]
    assert r.text() == "[redacted]tail-9999 end\n[exit 0]"
    assert "".join(pieces) == "[redacted]tail-9999 end"


@pytest.mark.parametrize("value", ["fake-[redacted]-tail", 'fake-"quote"-tail', "fake-\\slash-tail"])
def test_bound_command_redacts_direct_result_and_split_stream(monkeypatch, tmp_path, value):
    """A bound lookup remembers its value before drain starts, even when it is absent from the host env."""
    from exo.core import github_auth
    from exo.core.tools.builtin.shell import run_command
    monkeypatch.setattr(keys, "_loaded", set())
    monkeypatch.setattr(github_auth, "binding", lambda *_: {"mode": "token", "host": "github.com", "store": "keychain"})
    monkeypatch.setattr(keys, "keychain_get", lambda _: value)
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    # Files avoid putting the synthetic credential in command quoting or process arguments.
    (tmp_path / "part1").write_text(value[:7])
    (tmp_path / "part2").write_text(value[7:] + " done")
    seen = []
    code, output, status = run_command("cat part1; sleep 0.05; cat part2", str(tmp_path),
                                       github_auth=(str(tmp_path / "home"), "test"), emit=seen.append)
    assert (code, status) == (0, "ok")
    assert value in keys.known_secret_values()
    assert output == "[redacted] done" and "".join(seen) == output


@pytest.mark.parametrize("value", ["fake-[redacted]-tail", 'fake-"quote"-tail', "fake-\\slash-tail"])
def test_registry_strictly_redacts_literal_marker_and_escaped_credentials(monkeypatch, tmp_path, value):
    """A raw credential containing the marker must still be removed by the strict Registry pass."""
    from exo.core import github_auth
    monkeypatch.setattr(keys, "_loaded", set())
    monkeypatch.setattr(github_auth, "binding", lambda *_: {"mode": "token", "host": "github.com", "store": "keychain"})
    monkeypatch.setattr(keys, "keychain_get", lambda _: value)
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    (tmp_path / "output").write_text(value + " done")
    seen = []
    result = asyncio.run(default_registry().call(ToolCall("c1", "bash", {"command": "cat output"}),
        ToolContext(cwd=str(tmp_path), emit=seen.append, meta={"github_auth": (str(tmp_path / "home"), "test")})))
    assert result.text() == "[redacted] done\n[exit 0]"
    assert "".join(seen) == "[redacted] done"


@pytest.mark.parametrize("failed_operation", ["redact_secrets", "known_secret_values"])
def test_bound_registry_withholds_output_when_final_redaction_fails(monkeypatch, tmp_path, failed_operation):
    """No stream is needed to protect the returned bound result when its final scrub fails."""
    from exo.core import github_auth
    value = "fake-bound-" + "credential-753"
    monkeypatch.setattr(keys, "_loaded", set())
    monkeypatch.setattr(github_auth, "binding", lambda *_: {"mode": "token", "host": "github.com", "store": "keychain"})
    monkeypatch.setattr(keys, "keychain_get", lambda _: value)
    monkeypatch.setenv("EXO_KEYS_FILE", "/nonexistent")
    (tmp_path / "output").write_text(value + " sensitive output")

    def fail(*_args, **_kwargs):
        raise RuntimeError(value)  # neither the result nor the exception text may escape

    monkeypatch.setattr(keys, failed_operation, fail)
    result = asyncio.run(default_registry().call(ToolCall("c1", "bash", {"command": "cat output"}),
        ToolContext(cwd=str(tmp_path), meta={"github_auth": (str(tmp_path / "home"), "test")})))
    assert result.is_error and result.call_id == "c1"
    assert result.text() == "Tool output withheld: secret redaction failed."
    assert value not in result.text() and "sensitive output" not in result.text()
