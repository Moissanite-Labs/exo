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


# The reserved half: each of these passes only when `decide` checks reserved entries against every part.
# The reserved action is the less severe part, so the delete sets the card and the category.

def test_reserved_action_behind_a_more_severe_part_asks():
    c = classify(sh("gh issue close 9 -R acme-org/app && rm -rf build"))
    d = decide(c, RESERVE_CLOSE)
    assert d.outcome == "ask" and d.rule == "reserved", d
    assert d.reserved == [("github_close", "acme-org/app#9")]


def test_reserved_part_is_not_lowered_by_a_category_override_on_the_top_part():
    pol = normalize_policies({"gates": {"reserved": ["github_close"], "categories": {"delete": "allow"}}})
    c = classify(sh("rm -rf build && gh issue close 9 -R acme-org/app"))
    assert decide(c, pol).outcome == "ask"
    assert decide(classify(sh("rm -rf build")), pol).outcome == "allow"


def test_plain_category_reserved_entry_matches_a_less_severe_part():
    pol = normalize_policies({"gates": {"reserved": ["vcs_push"]}})
    d = decide(classify(sh("git push origin feature && rm -rf build")), pol)
    assert d.rule == "reserved" and d.reserved == [("vcs_push", "origin")], d


def test_lines_without_the_denied_action_are_unchanged():
    for command in ("rm -rf build", "ls && cat README.md", "git status && git log -1"):
        d = decide(classify(sh(command)), DENY_PUSH)
        assert d.outcome != "deny", (command, d)


def test_parts_are_kept_on_the_classification_but_not_in_the_ledger_view():
    c = classify(sh("git push origin main && rm -rf build"))
    assert {p.category for p in c.parts} >= {"vcs_push"}
    assert "parts" not in c.as_dict()


# Parts carry the tool of the call they came in on, not an internal tag.

def test_tool_execute_code_entry_does_not_match_inline_code_run_from_bash():
    pol = normalize_policies({"gates": {"deny": ["tool:execute_code"]}})
    assert decide(classify({"tool": "execute_code", "args": {"code": "print(1)"}}), pol).outcome == "deny"
    for command in ("python -c 'print(1)'", "node -e 'console.log(1)'", "python3 <<EOF\nprint(1)\nEOF"):
        d = decide(classify(sh(command)), pol)
        assert (d.outcome, d.rule) == ("ask", "opaque"), (command, d)


def test_tool_keyed_mapping_entry_matches_a_part():
    pol = normalize_policies({"gates": {"deny": [{"match": {"tool": "bash", "category": "vcs_push"}}]}})
    assert decide(classify(sh("git push origin main")), pol).outcome == "deny"
    assert decide(classify(sh("git push origin main && rm -rf build")), pol).outcome == "deny"


def test_ssh_exposure_reaches_the_inner_parts():
    pol = normalize_policies({"gates": {"deny": [{"category": "delete", "exposure": "internal"}]}})
    assert decide(classify(sh("rm -rf /data")), pol).outcome != "deny"
    assert decide(classify(sh("ssh build-host 'cat ~/.netrc; rm -rf /data'")), pol).outcome == "deny"


def test_ssh_hop_is_a_part_so_a_target_entry_matches_the_host():
    pol = normalize_policies({"gates": {"deny": ["target:prod-db"]}})
    for command in ("ssh prod-db ls", "ssh prod-db 'git push origin main'", "ssh prod-db 'rm -rf /data'"):
        d = decide(classify(sh(command)), pol)
        assert (d.outcome, d.rule) == ("deny", "deny"), (command, d)
    assert decide(classify(sh("ssh build-host 'rm -rf /data'")), pol).outcome != "deny"


def test_flat_mapping_entry_with_a_reason_or_name_still_matches():
    pol = normalize_policies({"gates": {"deny": [{"category": "delete", "reason": "no deletes"}],
                                        "reserved": [{"category": "vcs_push", "name": "pushes"}]}})
    d = decide(classify(sh("rm -rf build")), pol)
    assert (d.outcome, d.reason) == ("deny", "denied by policy (no deletes)"), d
    d = decide(classify(sh("git push origin feature")), pol)
    assert d.rule == "reserved" and "pushes" in d.reason, d
