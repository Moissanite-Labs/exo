"""A non-deny rule keyed on `command` decides only the command it matches (#394).

Every part of a compound line carried the whole line as its `command`, so an allow rule written for
one command (`^git status`) matched the line's combined classification and decided the whole line,
and in `_override_deny` it matched every other part too, cancelling a `categories:` deny on one of
them. Each part now carries its own text, the simple command as written, and non-deny matching uses
it. Deny and reserved entries keep matching the whole line and its decoded views (D-075, #136).

Two limits the first version missed, caught by a blocking review (#411): a per-part allow could
still cancel the category deny on the part it names, and an allow naming the dominant part could
decide the whole line past another part's matrix ask. A non-deny rule now decides the line only
when it names every command on it, and a category deny is sticky against non-deny rules in the
#191 pass. Classification and decision only; nothing is executed.
"""
import pytest

from exo.gate import _each_part, classify, decide, normalize_policies

F = "git status; git push --force origin main"
GH = "gh pr comment 3 --body hi && rm -rf build"
ALLOW_STATUS = {"match": {"command": "^git status"}, "decision": "allow"}
ALLOW_RM = {"match": {"command": "rm -rf build"}, "decision": "allow"}
A11_PUSH = "git push --force origin main && rm -rf build"
A11_PUSH2 = "rm -rf build && git push --force origin main"
A11_PUSH3 = "git push --force origin main; kubectl delete ns prod"
A11_PUSH4 = 'export GIT_SSH_COMMAND="$(cat /tmp/cmd)"; git push --force origin main'
A11_ALLOW_RM = {"match": {"command": "^rm -rf build$"}, "decision": "allow"}
A11_ALLOW_PUSH = {"match": {"command": "^git push --force origin main$"}, "decision": "allow"}




def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def run(line, gates):
    return decide(classify(sh(line)), normalize_policies({"gates": gates}))


DENY = [
    # a categories: deny on the other part stands (the allow rule no longer cancels it, #191 path)
    (F, {"categories": {"force_push": "deny"}, "rules": [ALLOW_STATUS]}),
    (GH, {"categories": {"github_post": "deny"}, "rules": [ALLOW_RM]}),
    # a categories: deny stands even when the allow rule names the denied part itself (#411)
    (A11_PUSH, {"categories": {"delete": "deny"}, "rules": [A11_ALLOW_RM]}),
    (A11_PUSH2, {"categories": {"delete": "deny"}, "rules": [A11_ALLOW_PUSH]}),
    (GH, {"categories": {"delete": "deny"}, "rules": [ALLOW_RM]}),
    # a deny entry or a deny rule reaches the part as before (D-075)
    (F, {"deny": ["force_push"], "rules": [ALLOW_STATUS]}),
    (GH, {"rules": [{"match": {"category": "github_post"}, "decision": "deny"}, ALLOW_RM]}),
    # a deny rule keyed on command still sees the whole line, before or after an allow rule
    (F, {"rules": [{"match": {"command": "git push"}, "decision": "deny"}]}),
    (F, {"rules": [ALLOW_STATUS, {"match": {"command": "git push"}, "decision": "deny"}]}),
]

ASK = [
    # the push alone asks; riding behind an allowed command changes nothing
    (F, {"rules": [ALLOW_STATUS]}),
    (F, {"rules": [{"match": {"command": "^git status"}, "decision": "log"}]}),
    ("ls; rm -rf build", {"rules": [{"match": {"command": "^ls"}, "decision": "allow"}]}),
    ("bash -c 'git status; git push --force origin main'", {"rules": [ALLOW_STATUS]}),
    (F, {"reserved": ["force_push"], "rules": [ALLOW_STATUS]}),
    # an allow naming one part never decides the line when another part is stricter (#411):
    # the named part's matrix ask stands
    (F, {"rules": [{"match": {"command": "^git push"}, "decision": "allow"}]}),
    (A11_PUSH2, {"rules": [A11_ALLOW_PUSH]}),
    (A11_PUSH3, {"rules": [A11_ALLOW_PUSH]}),
    (A11_PUSH4, {"rules": [A11_ALLOW_PUSH]}),
]

ALLOW = [
    ("git status", {"rules": [ALLOW_STATUS]}),                       # the command alone, as before
    ("git status; git status", {"rules": [ALLOW_STATUS]}),          # nothing on the line but the named command
]


@pytest.mark.parametrize("line,gates", DENY)
def test_a_part_the_policy_denies_still_denies_the_line(line, gates):
    d = run(line, gates)
    assert d.outcome == "deny", (line, d)


@pytest.mark.parametrize("line,gates", ASK)
def test_an_allow_rule_for_another_command_does_not_decide_the_line(line, gates):
    d = run(line, gates)
    assert d.outcome == "ask", (line, d)


@pytest.mark.parametrize("line,gates", ALLOW)
def test_an_allow_rule_still_decides_the_command_it_names(line, gates):
    d = run(line, gates)
    assert d.outcome == "allow", (line, d)


def test_each_part_carries_its_own_text_and_the_combined_its_dominant_part():
    c = classify(sh(F))
    assert c._own_command == "git push --force origin main", c
    assert {p._own_command for p in _each_part(c)} == {"git status", "git push --force origin main"}
    assert c.command == F  # deny and reserved entries still see the whole line
