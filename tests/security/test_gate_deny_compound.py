"""A reserved or deny entry matches any simple command on a line, not only the most severe one (D-075).

`classify_shell` reduces a compound line to its most severe part. Before, `decide` checked the principal's
`deny` and `reserved` lists against that one part only, so `git push origin main && rm -rf build` was an
`ask` for the delete and the denied push ran once the delete was approved.
"""
import pytest

from exo.gate import classify, decide, normalize_policies

DENY_PUSH = normalize_policies({"gates": {"deny": ["vcs_push", "force_push"]}})
RESERVE_CLOSE = normalize_policies({"gates": {"reserved": ["github_close"]}})


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def test_denied_push_alone_is_denied():
    d = decide(classify(sh("git push origin main")), DENY_PUSH)
    assert (d.outcome, d.rule) == ("deny", "deny")


@pytest.mark.parametrize("command", [
    "git push origin main && rm -rf build",                      # hidden behind a delete
    "rm -rf build && git push origin main",
    "git push origin main; cat ~/.ssh/id_rsa",                   # hidden behind a credential read
    "git push origin main && gh pr comment 3 --body hi",         # hidden behind a post
    "ls && git push origin main || echo failed",
    "bash -c 'git push origin main && rm -rf build'",            # inside a shell the gate can read
    "python -c \"import subprocess; subprocess.run(['git', 'push', 'origin', 'main']); print(1)\" && rm -rf build",
])
def test_denied_action_on_a_compound_line_denies_the_line(command):
    c = classify(sh(command))
    d = decide(c, DENY_PUSH)
    assert (d.outcome, d.rule) == ("deny", "deny"), (command, d)


def test_reserved_action_on_a_compound_line_asks():
    c = classify(sh("gh issue close 9 -R acme-org/app && ls"))
    d = decide(c, RESERVE_CLOSE)
    assert d.outcome == "ask" and d.rule == "reserved", d


def test_lines_without_the_denied_action_are_unchanged():
    for command in ("rm -rf build", "ls && cat README.md", "git status && git log -1"):
        d = decide(classify(sh(command)), DENY_PUSH)
        assert d.outcome != "deny", (command, d)


def test_parts_are_kept_on_the_classification_but_not_in_the_ledger_view():
    c = classify(sh("git push origin main && rm -rf build"))
    assert {p.category for p in c.parts} >= {"vcs_push"}
    assert "parts" not in c.as_dict()
