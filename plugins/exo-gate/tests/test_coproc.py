"""Coprocess opacity must not hide a visible destructive command."""

import pytest

from exo import gate


@pytest.mark.parametrize("command", [
    "coproc rm file",
    "coproc { rm file; }",
    "coproc worker { rm file; }",
    "coproc (rm file)",
    "coproc coproc rm file",
    "coproc sudo rm file",
    "coproc bash -c 'rm file'",
    "coproc bash <<'EOF'\nrm file\nEOF",
    "coproc rm 'file with spaces'",
])
def test_coproc_visible_delete_is_denied(command):
    c = gate.classify_shell(command)
    assert c.category == "delete", c
    assert gate.decide(c, {"gates": {"deny": ["delete"]}}).outcome == "deny"


@pytest.mark.parametrize("command", [
    "coproc",
    "coproc unknown-program",
    "coproc $PROGRAM file",
    "coproc echo hello",
    'coproc "rm file"',
    'coproc echo "x; rm file"',
    "coproc cat <<'EOF'\nrm file\nEOF",
    'coproc worker "{" rm file',
])
def test_coproc_retains_opacity_without_inventing_commands(command):
    c = gate.classify_shell(command)
    assert c.category == "opaque_exec", c
    assert gate.decide(c).outcome == "ask"


def test_coproc_depth_cap_retains_opacity():
    c = gate.classify_shell("coproc rm file", depth=gate._DEPTH)
    assert c.category == "opaque_exec"
    assert gate.decide(c).outcome == "ask"


def test_coproc_quoted_target_is_preserved():
    assert gate.classify_shell('coproc rm "file with spaces"').target == "file with spaces"


def test_coproc_redirection_and_store_floors():
    c = gate.classify_shell("coproc echo hello > output.txt")
    assert c.category == "opaque_exec"
    assert gate.decide(c).outcome == "ask"
    c = gate.classify_shell("coproc echo hello > ~/.exo/principals/me/policies/gates.yaml")
    assert c.category == "store_write"
    assert gate.decide(c).outcome == "ask"
