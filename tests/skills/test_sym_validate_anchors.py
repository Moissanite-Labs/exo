"""sym-validate.py: a trailing newline is never part of an id, ref, hash or timestamp.

Python's `$` also matches before a final newline, so `.match()` with `...$` accepted
"builder\\n" as an ID. The grammar patterns now anchor with `\\Z`.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "skills/sym-protocol/scripts/sym-validate.py"
PACKET = {"v": 2, "id": "a1", "tk": "t6k45", "prj": "p1", "f": "builder", "t": "orchestrator",
          "a": "assert", "s": "c7", "st": "inferred", "cf": 73, "ev": ["e3:v3"]}


def _module():
    spec = importlib.util.spec_from_file_location("sym_validate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("pattern,good", [
    ("ID_RE", "builder"),
    ("REF_RE", "e3:v3"),
    ("REF_VERSIONED_RE", "c7:v2"),
    ("HASH_RE", "sha256:" + "0" * 64),
    ("APPROVAL_REF_RE", "approval:a881"),
    ("ISO_Z_RE", "2026-09-17T02:00:00Z"),
])
def test_grammar_pattern_rejects_a_trailing_newline(pattern, good):
    pat = getattr(_module(), pattern)
    assert pat.match(good)
    assert not pat.match(good + "\n")


def _validate(tmp_path, packet):
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return subprocess.run([sys.executable, "-I", str(SCRIPT), str(path)], capture_output=True, text=True)


def test_control_packet_is_valid(tmp_path):
    r = _validate(tmp_path, PACKET)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("field", ["id", "tk", "prj", "f", "t", "s", "ev"])
def test_packet_field_with_a_trailing_newline_is_err_syn(tmp_path, field):
    packet = json.loads(json.dumps(PACKET))
    if field == "ev":
        packet["ev"][0] += "\n"
    else:
        packet[field] += "\n"
    r = _validate(tmp_path, packet)
    assert r.returncode == 1, r.stdout
    assert f"ERR:SYN: {field}" in r.stdout, r.stdout
