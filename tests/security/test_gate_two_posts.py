"""Two posts on one line are each checked by the posting rules (#396).

`decide` ran `check_post` and `check_scrub` on the combined classification's text, which a
compound line takes from its dominant part. A post that passed, put first, carried any second
post on the line past the footer, the forbidden words and the scrub (D-033). The checks now
run on every part whose category is under `posting.applies_to` and that carries text or an
unseen body; another part's violations are named after that part. Classification and decision
only; nothing is executed.
"""
import pytest

from exo.gate import UNSEEN_BODY, approval_binding, classify, decide, normalize_policies

POLICY = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                             "posting": {"footer": "F", "forbid_words": ["Zork"]}})
EXPLICIT = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                               "posting": {"footer": "F", "forbid_words": ["Zork"],
                                           "applies_to": ["github_post", "github_close"]}})
SCRUB = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                            "posting": {"footer": "F",
                                        "scrub": {"names": ["sekritname"],
                                                  "exposures": ["internal", "public"]}}})
R = "-R myorg/app"
CLEAN = f"gh pr comment 5 {R} --body $'ok\\nF'"
DIRTY = f"gh pr comment 6 {R} --body 'Zork leaked'"


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


# The issue's rows: a passing first post no longer shields a forbidden second one, in either
# order, and a close's comment on the same line is checked too (#326).
ASK = [
    f"{CLEAN} && {DIRTY}",
    f"{DIRTY} && {CLEAN}",
    f"{CLEAN} && gh issue close 5 {R} -c 'Zork leaked'",
    f"gh pr comment 5 {R} --body 'Zork one' && gh pr comment 6 {R} --body 'Zork two'",
]

# Both posts pass, or the second command posts nothing: these log.
LOG = [
    f"{CLEAN} && gh pr comment 6 {R} --body $'ok too\\nF'",
    f"{CLEAN} && ls",
    f"{CLEAN} && gh issue close 5 {R}",
]

# A second post whose body the gate cannot read asks as unseen, named after that part.
UNSEEN = [
    f"{CLEAN} && gh pr comment 6 {R} --body \"$X\"",
    f"{CLEAN} && gh pr comment 6 {R} --body-file notes.md",
    f"{CLEAN} && gh pr comment 6 {R} --body \"$(cat notes.md)\"",
]


@pytest.mark.parametrize("line", ASK)
def test_a_second_post_is_checked(line):
    c = classify(sh(line))
    for pol in (POLICY, EXPLICIT):
        d = decide(c, pol)
        assert d.outcome == "ask", (line, d)
        assert any(v.endswith("forbidden word: Zork") for v in d.violations), (line, d)


@pytest.mark.parametrize("line", LOG)
def test_two_clean_posts_log(line):
    c = classify(sh(line))
    for pol in (POLICY, EXPLICIT):
        assert decide(c, pol).outcome == "log", (line, decide(c, pol))


@pytest.mark.parametrize("line", UNSEEN)
def test_an_unreadable_second_post_asks(line):
    d = decide(classify(sh(line)), POLICY)
    assert d.outcome == "ask", (line, d)
    assert any(v.endswith(UNSEEN_BODY) for v in d.violations), (line, d)


def test_violations_name_the_part_that_carries_them():
    d = decide(classify(sh(f"{CLEAN} && {DIRTY}")), POLICY)
    assert d.violations == ["GitHub PR comment on myorg/app#6: missing footer: F",
                            "GitHub PR comment on myorg/app#6: forbidden word: Zork"], d
    # The dominant part's own violations stay bare, as a single post's do.
    d = decide(classify(sh(f"{DIRTY} && {CLEAN}")), POLICY)
    assert d.violations == ["missing footer: F", "forbidden word: Zork"], d


def test_the_scrub_reads_the_second_post():
    line = f"{CLEAN} && gh pr comment 6 {R} --body $'sekritname here\\nF'"
    d = decide(classify(sh(line)), SCRUB)
    assert d.outcome == "ask", d
    assert d.violations == ["GitHub PR comment on myorg/app#6: internal name: sekritname"], d
    line = f"gh pr comment 5 {R} --body $'sekritname here\\nF' && gh pr comment 6 {R} --body $'ok\\nF'"
    d = decide(classify(sh(line)), SCRUB)
    assert d.violations == ["internal name: sekritname"], d


def test_an_approval_binds_the_second_body():
    # The binding hashes the whole command, so a grant for one line cannot cover the same
    # line with the second post's body changed.
    one = classify(sh(f"{CLEAN} && {DIRTY}"))
    two = classify(sh(f"{CLEAN} && gh pr comment 6 {R} --body 'a changed body'"))
    assert approval_binding(one) != approval_binding(two)
    assert approval_binding(one) == approval_binding(classify(sh(f"{CLEAN} && {DIRTY}")))
