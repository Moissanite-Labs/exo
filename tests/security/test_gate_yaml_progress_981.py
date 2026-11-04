"""Malformed flow values terminate and reach callers as PolicyError (#981)."""
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _capped_parse(text, manifest_path=None):
    # Linux has a hard address-space limit. macOS rejects RLIMIT_AS; sample the
    # child's RSS and kill it above the cap. Neither path invokes a shell.
    code = """import json, sys
try:
    from exo.gate import parse_yaml, PolicyError
    from exo.core.plugins import PluginError, read_manifest
    try:
        if len(sys.argv) > 1:
            read_manifest(sys.argv[1])
            print(json.dumps(['manifest_ok']))
        else:
            print(json.dumps(['value', parse_yaml(sys.stdin.read())]))
    except PolicyError:
        print(json.dumps(['policy_error']))
    except PluginError as e:
        print(json.dumps(['plugin_error', str(e)]))
except MemoryError:
    sys.exit(3)
"""
    def cap():
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))

    argv = [sys.executable, "-c", code] + ([str(manifest_path)] if manifest_path is not None else [])
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, preexec_fn=cap if sys.platform.startswith("linux") else None)
    try:
        proc.stdin.write(text)
        proc.stdin.close()
        deadline = time.monotonic() + 5
        while proc.poll() is None:
            if time.monotonic() > deadline:
                pytest.fail("parser exceeded time cap")
            if sys.platform == "darwin":
                rss = subprocess.run(["ps", "-o", "rss=", "-p", str(proc.pid)], capture_output=True, text=True)
                if rss.stdout.strip() and int(rss.stdout.strip()) > 512 * 1024:
                    pytest.fail("parser exceeded sampled RSS cap")
            time.sleep(0.01)
        out, err = proc.stdout.read(), proc.stderr.read()
        assert proc.returncode == 0, err
        return json.loads(out)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


@pytest.mark.parametrize("text", ["x: [}", "- [}", "x: [a, }", "x: {a: [}", "x: {a: ]",
                                  'x: "\\q"', "x: " + "[" * 1500 + "]" * 1500])
def test_malformed_yaml_is_bounded_and_is_a_policy_error(text):
    assert _capped_parse(text) == ["policy_error"]


@pytest.mark.parametrize(("text", "expected"), [
    ("x: [a, b]", {"x": ["a", "b"]}),
    ("x: {a: 1}", {"x": {"a": 1}}),
    ("x: {a: }", {"x": {"a": None}}),
    ("x: {a: ], b: 1}", {"x": {"a": None, "], b": 1}}),
    ("x: [[], {a: [1, true]}, 'a,b']", {"x": [[], {"a": [1, True]}, "a,b"]}),
])
def test_valid_flow_values_are_preserved(text, expected):
    assert _capped_parse(text) == ["value", expected]


def test_seeded_flow_mutations_finish_within_caps():
    rng = random.Random(981)
    source = "x: [a, {b: [1, 2]}, c]"
    for _ in range(24):
        i = rng.randrange(len(source))
        text = source[:i] + rng.choice("[]{}:, '") + source[i + 1:]
        assert _capped_parse(text)[0] in ("value", "policy_error")


def test_malformed_plugin_manifest_is_a_plugin_error(tmp_path: Path):
    (tmp_path / "plugin.yaml").write_text("name: sample\nx: [}")
    result = _capped_parse("", tmp_path)
    assert result[0] == "plugin_error" and "unclosed or mismatched flow collection" in result[1]
