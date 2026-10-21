"""A reopen's comment and a merge's subject and body are posted text, so the posting rules check them (#397).

`gh issue reopen --comment` and `gh pr reopen -c` post a comment on the thread, but the gate filed the
call as `github_post` and read no text. `gh pr merge --body` was read but `vcs_merge` ran no posting
checks, and `--subject` (the long form of the merge commit's subject) was not read at all. The same text
that asks as a comment post logged in all three. Classification and decision only; nothing is executed.
"""
import pytest

from exo.gate import UNSEEN_BODY, approval_binding, body_unseen, classify, decide, normalize_policies

POLICY = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                             "posting": {"footer": "F", "forbid_words": ["Zork"]}})
EXPLICIT = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                               "posting": {"footer": "F", "forbid_words": ["Zork"],
                                           "applies_to": ["github_post", "vcs_merge"]}})
R = "-R myorg/app"


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


ASK = [
    f"gh pr comment 5 {R} --body 'Zork leaked'",          # control
    f"gh issue reopen 5 {R} --comment 'Zork leaked'",
    f"gh pr reopen 5 {R} -c 'Zork leaked'",
    f"gh pr merge 5 {R} --squash --body 'Zork leaked'",
    f"gh pr merge 5 {R} --squash --subject 'Zork leaked'",
    f"gh pr merge 5 {R} --squash -t 'Zork leaked'",
]

LOG = [
    f"gh issue reopen 5 {R}",
    f"gh pr reopen 5 {R} --comment 'Back open.\nF'",
    f"gh pr merge 5 {R} --merge",
    f"gh pr merge 5 {R} --squash --subject 'Merge the thing'",
    f"gh pr merge 5 {R} --squash --body 'Done.\nF'",
]

UNSEEN = [
    f"gh issue reopen 5 {R} --comment \"$X\"",
    f"gh pr reopen 5 {R} --comment 'clean\nF' --comment 'Zork leaked'",   # gh takes the last
    f"gh pr merge 5 {R} --squash --body \"$X\"",
    f"gh pr merge 5 {R} --squash --body-file notes.md",                  # a file the gate did not read
    f"gh pr merge 5 {R} --squash --subject 'clean' --subject 'Zork leaked'",
]


@pytest.mark.parametrize("line", ASK)
def test_a_reopen_or_merge_text_is_checked(line):
    c = classify(sh(line))
    assert "Zork leaked" in (c.text, c.title), c
    for pol in (POLICY, EXPLICIT):
        d = decide(c, pol)
        assert d.outcome == "ask", (line, d)
        assert "forbidden word: Zork" in d.violations, (line, d)


@pytest.mark.parametrize("line", LOG)
def test_a_reopen_or_merge_without_checked_text_logs(line):
    c = classify(sh(line))
    for pol in (POLICY, EXPLICIT):
        assert decide(c, pol).outcome == "log", (line, decide(c, pol))


@pytest.mark.parametrize("line", UNSEEN)
def test_an_unreadable_reopen_or_merge_text_asks(line):
    c = classify(sh(line))
    assert body_unseen(c), c
    d = decide(c, POLICY)
    assert d.outcome == "ask" and UNSEEN_BODY in d.violations, (line, d)


def test_a_merge_subject_is_its_title_and_body_its_text():
    c = classify(sh(f"gh pr merge 5 {R} --squash --subject 'S' --body 'B'"))
    assert (c.category, c.title, c.text) == ("vcs_merge", "S", "B"), c
    c = classify(sh(f"gh issue reopen 5 {R} --comment 'Zork leaked'"))
    assert (c.category, c.text) == ("github_post", "Zork leaked"), c


def test_an_approval_binds_the_merge_subject():
    one = classify(sh(f"gh pr merge 5 {R} --squash --subject 'one'"))
    two = classify(sh(f"gh pr merge 5 {R} --squash --subject 'two'"))
    bare = classify(sh(f"gh pr merge 5 {R} --merge"))
    assert approval_binding(one) != approval_binding(two)
    assert approval_binding(one) != approval_binding(bare)
