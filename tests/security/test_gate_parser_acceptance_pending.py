"""Executable acceptance for open parser gaps; command strings are never executed.

These are desired outcomes from the linked issues, not claims that the parser is
fixed. A strict xfail names a gap reproduced on main 64bfee2c. When a fix starts
passing, XPASS fails the suite until the owner removes that gap marker.
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


PUSH = "git push --force origin main"
DELETE = "rm -rf /srv/data"
DENY_PUSH = {"deny": ["force_push"]}
DENY_DELETE = {"deny": ["delete"]}


def allow(pattern: str) -> dict:
    return {"match": {"command": pattern}, "decision": "allow"}


def nest(depth: int) -> str:
    return "echo $(" * depth + DELETE + ")" * depth


CASES = [
    Case(458, "here_string_cannot_authorize_delete", "rm -rf build <<< 'npm test'",
         {"categories": {"delete": "deny"}, "rules": [allow("npm test")]}, "deny"),
    Case(458, "comment_cannot_authorize_push", PUSH + " # --dry-run",
         {"categories": {"force_push": "deny"}, "rules": [allow("--dry-run")]}, "deny"),
    Case(458, "compound_here_string_cannot_authorize_push", "ls; " + PUSH + " <<< 'npm test'",
         {"categories": {"force_push": "deny"}, "rules": [allow("^ls"), allow("npm test")]}, "deny"),
    Case(469, "backtick_continuation_delete", "echo `r\\\nm -rf /srv/data`", DENY_DELETE, "deny"),
    Case(469, "backtick_continuation_push", "echo `git pu\\\nsh --force origin main`", DENY_PUSH, "deny"),
    Case(474, "process_substitution_delimiter", "cat << <(echo hi)\nbody\n<(echo hi)\n" + DELETE,
         DENY_DELETE, "deny"),
    Case(474, "quoted_delimiter_in_ps4", "export PS4='$(echo $(cat <<\"E\"\nbody\nE\n); " + DELETE + ")'",
         DENY_DELETE, "deny"),
    Case(477, "deny_inside_unread_nest", nest(17), DENY_DELETE, "deny"),
    Case(477, "deny_inside_deeper_unread_nest", nest(40), DENY_DELETE, "deny"),
    Case(517, "continued_heredoc_delimiter", "cat <<EF\nE\\\nF\n" + PUSH + "\nEF", DENY_PUSH, "deny"),
    Case(517, "tab_stripped_continued_delimiter", "cat <<-EF\n\tE\\\n\tF\n" + PUSH + "\nEF", DENY_PUSH, "deny"),
    Case(518, "backtick_escaped_hash", "echo `echo \\#; " + PUSH + "`", DENY_PUSH, "deny"),
    Case(518, "backtick_escaped_quote", "echo `echo \\'; " + PUSH + "; echo \\'`", DENY_PUSH, "deny"),
    Case(518, "backtick_escaped_double_quote", 'echo `echo \\"; ' + PUSH + '; echo \\"`', DENY_PUSH, "deny"),
    Case(570, "arithmetic_path_array_assignment", ": $((PATH[0]=1)); ls", {}, "ask"),
    Case(570, "arithmetic_command_hash_assignment", ": $((BASH_CMDS[ls]=1)); ls", {}, "ask"),
    Case(570, "arithmetic_alias_assignment", ": $((BASH_ALIASES[ls]=1)); ls", {}, "ask"),
    Case(570, "assigned_arithmetic_array", "x=$((PATH[0]=1)); ls", {}, "ask"),
    Case(570, "echoed_arithmetic_array", "echo $((BASH_CMDS[ls]=1)); ls", {}, "ask"),
    Case(570, "legacy_arithmetic_array_assignment", ": $[PATH[0]=1]; ls", {}, "ask"),
    Case(571, "tilde_unset_name", "OLDPWD=PATH; unset ~-; ls", {}, "ask"),
    Case(571, "tilde_read_name", "OLDPWD=PATH; read ~- <<< /tmp; ls", {}, "ask"),
    Case(571, "tilde_printf_name", "OLDPWD=PATH; printf -v ~- /tmp; ls", {}, "ask"),
    Case(571, "tilde_nameref_name", "OLDPWD=PATH; declare -n r=~-; r=/tmp; ls", {}, "ask"),
]

# Reproduced on the reviewed base; never infer xfail from the running code.
KNOWN_GAPS = {
    "here_string_cannot_authorize_delete", "comment_cannot_authorize_push",
    "compound_here_string_cannot_authorize_push", "backtick_continuation_delete",
    "backtick_continuation_push",
    "continued_heredoc_delimiter", "tab_stripped_continued_delimiter", "backtick_escaped_hash",
    "backtick_escaped_quote", "backtick_escaped_double_quote", "tilde_unset_name", "tilde_read_name",
    "tilde_printf_name", "tilde_nameref_name",
}


@pytest.fixture(autouse=True)
def isolated_gate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_PRINCIPAL_STORE", "EXO_KEYS_FILE", "EXO_VENV", "EXO_PLUGIN_DIRS"):
        monkeypatch.delenv(name, raising=False)


def decision(command: str, gates: dict):
    classification = gate.classify({"tool": "bash", "args": {"command": command}})
    return gate.decide(classification, gate.normalize_policies({"gates": gates}))


@pytest.mark.parametrize("case", [
    pytest.param(case, id=f"{case.issue}-{case.name}", marks=(
        pytest.mark.xfail(strict=True, reason=f"Open #{case.issue}: reproduced on main 64bfee2c")
        if case.name in KNOWN_GAPS else ())) for case in CASES
])
def test_open_parser_issue_acceptance(case):
    result = decision(case.command, case.gates)
    assert result.outcome == case.expected, result


@pytest.mark.parametrize("command,policy,expected", [
    (PUSH, DENY_PUSH, "deny"),
    (DELETE, DENY_DELETE, "deny"),
    ("echo `" + DELETE + "`", DENY_DELETE, "deny"),
    ("echo $(r\\\nm -rf /srv/data)", DENY_DELETE, "deny"),
    ("cat <<EOF\nbody\nEOF\n" + DELETE, DENY_DELETE, "deny"),
    (nest(5), DENY_DELETE, "deny"),
    (": $((PATH=1)); ls", {}, "ask"),
    ("unset $V; ls", {}, "ask"),
    ("read $V <<< /tmp; ls", {}, "ask"),
    ("printf -v $V /tmp; ls", {}, "ask"),
])
def test_existing_parser_guards_remain_enforced(command, policy, expected):
    assert decision(command, policy).outcome == expected
