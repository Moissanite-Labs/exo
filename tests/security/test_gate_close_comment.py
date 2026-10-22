"""A close posts its comment with it, so the posting rules check that comment (#326).

`gh issue close --comment` and `gh pr close -c` attach a comment to the close. The gate filed
both as `github_close` and read no text, so the footer, `forbid`, `forbid_words`, the scrub and
the exact-text binding never saw the comment: the same text that asks as a `gh pr comment`
logged as a close. Classification and decision only; nothing is executed.
"""
import pytest

from exo.gate import UNSEEN_BODY, approval_binding, body_unseen, classify, decide, normalize_policies

POLICY = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                             "posting": {"footer": "F", "forbid_words": ["Zork"]}})
EXPLICIT = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                               "posting": {"footer": "F", "forbid_words": ["Zork"],
                                           "applies_to": ["github_post", "github_close"]}})
R = "-R myorg/app"


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


# The issue's rows: the comment asks as a close exactly as it does as a comment post.
ASK = [
    f"gh pr comment 5 {R} --body 'Zork leaked'",          # control
    f"gh issue close 5 {R} --comment 'Zork leaked'",
    f"gh pr close 5 {R} -c 'Zork leaked'",
    f"gh pr close 5 {R} --comment 'Zork leaked' --delete-branch",
    f"gh issue close 5 {R} --comment='Zork leaked'",
]

# Nothing posted but the close itself, or a comment that passes the rules: these log.
LOG = [
    f"gh issue close 5 {R}",
    f"gh pr close 5 {R}",
    f"gh issue close 5 {R} --comment 'Done.\nF'",
    f"gh pr close 5 {R} --comment 'Done.\nF' -d",
    "gh repo archive myorg/app",
]

# A comment the gate cannot read as gh does asks as unseen, under the #208 rules.
UNSEEN = [
    f"gh pr close 5 {R} --comment \"$X\"",
    f"gh pr close 5 {R} --comment \"$(cat notes.md)\"",
    f"gh issue close 5 {R} --comment 'clean\nF' --comment 'Zork leaked'",  # gh takes the last
    f"gh pr close 5 {R} -dc 'Zork leaked'",                                # a short-option cluster
]


@pytest.mark.parametrize("line", ASK)
def test_a_close_comment_is_checked(line):
    c = classify(sh(line))
    assert c.text == "Zork leaked", c
    for pol in (POLICY, EXPLICIT):
        d = decide(c, pol)
        assert d.outcome == "ask", (line, d)
        assert "missing footer: F" in d.violations and "forbidden word: Zork" in d.violations, (line, d)


@pytest.mark.parametrize("line", LOG)
def test_a_close_without_a_checked_comment_logs(line):
    c = classify(sh(line))
    for pol in (POLICY, EXPLICIT):
        assert decide(c, pol).outcome == "log", (line, decide(c, pol))


@pytest.mark.parametrize("line", UNSEEN)
def test_an_unreadable_close_comment_asks(line):
    c = classify(sh(line))
    assert c.category == "github_close" and body_unseen(c), c
    d = decide(c, POLICY)
    assert d.outcome == "ask" and UNSEEN_BODY in d.violations, (line, d)


def test_a_close_comment_is_the_calls_text():
    assert classify(sh(f"gh issue close 5 {R} --comment 'Zork leaked'")).category == "github_close"
    assert classify(sh(f"gh issue close 5 {R} --comment 'Zork leaked'")).text == "Zork leaked"
    assert classify(sh(f"gh pr close 5 {R} -c 'Zork leaked'")).text == "Zork leaked"
    assert classify(sh(f"gh issue close 5 {R}")).text == ""


def test_an_approval_binds_the_comment():
    one = classify(sh(f"gh issue close 5 {R} --comment 'one'"))
    two = classify(sh(f"gh issue close 5 {R} --comment 'two'"))
    bare = classify(sh(f"gh issue close 5 {R}"))
    assert approval_binding(one) != approval_binding(two)
    assert approval_binding(one) != approval_binding(bare)
