"""Desired open store/target outcomes, classified only; no command is executed.

Strict xfails are reproduced gaps on main 64bfee2c, not implementation claims.
An implementation must remove its marker when the desired outcome starts passing.
"""
from __future__ import annotations

from typing import NamedTuple

import pytest

from exo import gate


class Case(NamedTuple):
    issue: int
    name: str
    command: str
    gates: dict
    expected: str
    category: str | None = None


DENY_STORE = {"deny": ["store_write"]}
DENY_KEYS = {"deny": ["target:*/.exo/keys*"]}
PLUGIN = "~/.exo/plugins/foo/x.py"

CASES = [
    Case(479, "for_second_pass_store_cwd", "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
         DENY_STORE, "deny", "store_write"),
    Case(479, "while_second_pass_store_cwd", "while :; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
         DENY_STORE, "deny", "store_write"),
    Case(479, "until_second_pass_store_cwd", "until false; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
         DENY_STORE, "deny", "store_write"),
    Case(481, "tar_relative_key_target", "tar -C ~/.exo/policies -czf - ../keys/seat", DENY_KEYS, "deny"),
    Case(481, "cd_relative_key_target", "cd ~/.exo/policies; cat ../keys/seat", DENY_KEYS, "deny"),
    Case(482, "sed_read_only_store", "sed -n 1,10p " + PLUGIN, {}, "allow", "read"),
    Case(482, "tree_read_only_store", "tree ~/.exo/plugins", {}, "allow", "read"),
    Case(482, "sort_read_only_store", "sort " + PLUGIN, {}, "allow", "read"),
    Case(482, "uniq_read_only_store", "uniq " + PLUGIN, {}, "allow", "read"),
    Case(482, "xxd_read_only_store", "xxd " + PLUGIN, {}, "allow", "read"),
    Case(482, "rsync_dry_run_store", "rsync -an ~/.exo/plugins/foo/ /tmp/foo/", {}, "allow", "read"),
    Case(482, "cat_output_outside_store", "cat " + PLUGIN + " > /tmp/x.py", {}, "allow"),
    Case(482, "grep_output_outside_store", "grep -rn x ~/.exo/plugins > /tmp/out.txt", {}, "allow"),
    Case(483, "recursive_home_landing", "cp -r /tmp/evil/.exo ~/", DENY_STORE, "deny", "store_write"),
    Case(483, "copy_home_itself", "cp -rT /tmp/evil ~/.exo", DENY_STORE, "deny", "store_write"),
    Case(483, "copy_dot_contents", "cp -r /tmp/evil/. ~/.exo/", DENY_STORE, "deny", "store_write"),
    Case(483, "copy_policy_folder", "cp -r /tmp/evil/policies ~/.exo/", DENY_STORE, "deny", "store_write"),
    Case(483, "copy_update_folder", "cp -r /tmp/evil/update ~/.exo/", DENY_STORE, "deny", "store_write"),
    Case(483, "tar_into_home", "tar -xf evil.tar -C ~/.exo", DENY_STORE, "deny", "store_write"),
    Case(483, "tar_into_home_parent", "tar -xf evil.tar -C ~", DENY_STORE, "deny", "store_write"),
    Case(483, "unzip_into_home", "unzip -o evil.zip -d ~/.exo", DENY_STORE, "deny", "store_write"),
    Case(484, "ssh_log_with_read_remote", "ssh -E ~/.exo/state/log host ls", DENY_STORE, "deny", "store_write"),
    Case(484, "ssh_log_with_compound_read_remote", "ssh -E ~/.exo/state/log host 'cat x; sort x'",
         DENY_STORE, "deny", "store_write"),
    Case(484, "ssh_known_hosts_with_read_remote", "ssh -o UserKnownHostsFile=~/.exo/state/kh host ls",
         DENY_STORE, "deny", "store_write"),
    Case(484, "ssh_socket_with_read_remote", "ssh -S ~/.exo/state/ctl host ls", DENY_STORE, "deny", "store_write"),
]

KNOWN_GAPS = {
    # #479 (for/while/until second pass) is fixed: its three cases pass and carry no marker.
    "tar_relative_key_target", "cd_relative_key_target", "sed_read_only_store",
    "sort_read_only_store", "uniq_read_only_store", "xxd_read_only_store", "rsync_dry_run_store",
    "cat_output_outside_store", "grep_output_outside_store", "recursive_home_landing", "copy_home_itself",
    "copy_dot_contents", "copy_policy_folder", "copy_update_folder", "tar_into_home",
    "tar_into_home_parent", "unzip_into_home", "ssh_log_with_read_remote",
    "ssh_log_with_compound_read_remote", "ssh_known_hosts_with_read_remote", "ssh_socket_with_read_remote",
}


@pytest.fixture(autouse=True)
def isolated_gate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_PRINCIPAL_STORE", "EXO_KEYS_FILE", "EXO_VENV", "EXO_PLUGIN_DIRS"):
        monkeypatch.delenv(name, raising=False)


def inspect(command: str, gates: dict):
    classification = gate.classify({"tool": "bash", "args": {"command": command}})
    return classification, gate.decide(classification, gate.normalize_policies({"gates": gates}))


@pytest.mark.parametrize("case", [
    pytest.param(case, id=f"{case.issue}-{case.name}", marks=(
        pytest.mark.xfail(strict=True, reason=f"Open #{case.issue}: reproduced on main 64bfee2c")
        if case.name in KNOWN_GAPS else ())) for case in CASES
])
def test_open_store_issue_acceptance(case):
    classification, result = inspect(case.command, case.gates)
    assert result.outcome == case.expected, result
    if case.category:
        assert classification.category == case.category, classification


@pytest.mark.parametrize("command", [
    "cd ~/.exo/policies; cp /tmp/evil gates.yaml",
    "cp /tmp/x ~/.exo/policies/default.yaml",
    "rsync -a /tmp/evil/ ~/.exo/",
    "sed -i 's/x/y/' " + PLUGIN,
    "tree -o ~/.exo/state/out ~/.exo/plugins",
    "sort -o ~/.exo/state/out " + PLUGIN,
    "uniq /tmp/in ~/.exo/state/out",
    "xxd -r /tmp/in ~/.exo/state/out",
    "ssh -E ~/.exo/state/log host",
    "ssh -E ~/.exo/state/log host 'sort x'",
])
def test_existing_store_write_guards_stay_denied(command):
    classification, result = inspect(command, DENY_STORE)
    assert classification.category == "store_write", classification
    assert result.outcome == "deny", result


def test_plain_remote_read_stays_a_read():
    classification, result = inspect("ssh host ls", {})
    assert classification.category == "read", classification
    assert result.outcome in ("allow", "log"), result


def test_plain_store_read_stays_a_read():
    classification, result = inspect("cat " + PLUGIN, {})
    assert classification.category == "read", classification
    assert result.outcome == "allow", result


@pytest.mark.parametrize("command", [
    "tar -C ~/.exo/policies -czf - ../keys/seat",
    "cd ~/.exo/policies; cat ../keys/seat",
])
def test_relative_key_read_keeps_the_credentials_deny(command):
    _, result = inspect(command, {"deny": ["credentials"]})
    assert result.outcome == "deny", result
