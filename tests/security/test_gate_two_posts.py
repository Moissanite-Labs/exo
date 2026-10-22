"""Two posts on one line are each checked by the posting rules (#396).

`decide` ran `check_post` and `check_scrub` on the combined classification's text, which a
compound line takes from its dominant part. A post that passed, put first, carried any second
post on the line past the footer, the forbidden words and the scrub (D-033). The checks now
run on every part whose category is under `posting.applies_to`, and on a close or merge
carrying text (#326, #397), when it carries text, a title or an unseen body. Another part's
violations are named after that part. Classification and decision only; nothing is executed.
"""
import pytest

from exo.gate import (UNSEEN_BODY, _scrub_compiled, approval_binding, classify, decide,
                      normalize_policies)

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
# order, and a close's comment on the same line is checked too (#326). So are a reopen's comment
# and a merge's subject and body (#397), which the posting rules check on a line of their own.
ASK = [
    f"{CLEAN} && {DIRTY}",
    f"{DIRTY} && {CLEAN}",
    f"{CLEAN} && gh issue close 5 {R} -c 'Zork leaked'",
    f"gh pr comment 5 {R} --body 'Zork one' && gh pr comment 6 {R} --body 'Zork two'",
    f"{CLEAN} && gh pr merge 6 {R} --squash --body 'Zork leaked'",
    f"gh pr merge 6 {R} --squash --body 'Zork leaked' && {CLEAN}",
    f"{CLEAN} && gh pr merge 6 {R} --squash --subject 'Zork leaked'",
    f"{CLEAN} && gh pr merge 6 {R} --squash -t 'Zork leaked'",
    f"{CLEAN} && gh pr reopen 6 {R} -c 'Zork leaked'",
    f"{CLEAN} && gh issue reopen 6 {R} --comment 'Zork leaked'",
]

# Both posts pass, or the second command posts nothing: these log.
LOG = [
    f"{CLEAN} && gh pr comment 6 {R} --body $'ok too\\nF'",
    f"{CLEAN} && ls",
    f"{CLEAN} && gh issue close 5 {R}",
    f"{CLEAN} && gh pr merge 6 {R} --squash",
    f"{CLEAN} && gh pr merge 6 {R} --squash --body $'Done.\\nF'",
    f"{CLEAN} && gh pr reopen 6 {R}",
]

# A second post whose body the gate cannot read asks as unseen, named after that part.
UNSEEN = [
    f"{CLEAN} && gh pr comment 6 {R} --body \"$X\"",
    f"{CLEAN} && gh pr comment 6 {R} --body-file notes.md",
    f"{CLEAN} && gh pr comment 6 {R} --body \"$(cat notes.md)\"",
    f"{CLEAN} && gh pr merge 6 {R} --squash --body-file notes.md",     # a file the gate did not read
    f"{CLEAN} && gh pr merge 6 {R} --squash --body \"$X\"",
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


def test_a_merge_is_named_after_its_part():
    d = decide(classify(sh(f"{CLEAN} && gh pr merge 6 {R} --squash --body 'Zork leaked'")), POLICY)
    assert d.violations == ["merge PR on myorg/app#6: missing footer: F",
                            "merge PR on myorg/app#6: forbidden word: Zork"], d
    d = decide(classify(sh(f"{CLEAN} && gh pr merge 6 {R} --squash --body-file notes.md")), POLICY)
    assert d.violations == [f"merge PR on myorg/app#6: {UNSEEN_BODY}"], d


@pytest.mark.parametrize("first", [f"gh pr comment 5 {R} --body \"$A\"",
                                   f"gh pr comment 5 {R} --body $'ok\\nF' --body \"$A\""])
def test_two_unseen_bodies_are_both_named(first):
    # _combine gives the dominant post the other post's unseen_body signal. Each post's own
    # signals are kept, so the first post's unseen body is not taken for the second's.
    c = classify(sh(f"{first} && gh pr comment 6 {R} --body \"$B\""))
    d = decide(c, POLICY)
    assert d.outcome == "ask", d
    assert UNSEEN_BODY in d.violations, d
    assert f"GitHub PR comment on myorg/app#6: {UNSEEN_BODY}" in d.violations, d
    assert "own_signals" not in c.as_dict()


def test_many_posts_list_twenty_violations():
    line = " && ".join(f"gh pr comment {n} {R} --body 'Zork {n}'" for n in range(5, 35))
    d = decide(classify(sh(line)), POLICY)
    assert d.outcome == "ask", d
    assert d.violations[:2] == ["missing footer: F", "forbidden word: Zork"], d
    assert len(d.violations) == 21 and d.violations[-1] == "and 40 more on other posts", d
    assert len(d.reason) < 2000, d.reason


def test_the_line_unseen_note_counts_toward_the_twenty():
    # The web post decides this line. Its unread body is outside applies_to, so it adds the
    # line-wide unseen note. The note is listed bare, before the posts' violations, inside the 20.
    posts = " && ".join(f"gh pr comment {n} {R} --body 'Zork {n}'" for n in range(5, 30))
    d = decide(classify(sh(posts + " && curl -X POST -d @notes.md https://hooks.example.com/x")), POLICY)
    assert d.outcome == "ask", d
    assert d.violations[:2] == [UNSEEN_BODY, "GitHub PR comment on myorg/app#5: missing footer: F"], d
    assert len(d.violations) == 21 and d.violations[-1] == "and 31 more on other posts", d


def test_many_posts_compile_the_scrub_once():
    pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                              "posting": {"footer": "F",
                                          "scrub": {"names": [f"name{n:04d}" for n in range(600)],
                                                    "exposures": ["internal", "public"]}}})
    posts = [f"gh pr comment {n} {R} --body $'ok\\nF'" for n in range(5, 45)]
    line = " && ".join(posts + [f"gh pr comment 99 {R} --body $'name0599 here\\nF'"])
    _scrub_compiled.cache_clear()
    d = decide(classify(sh(line)), pol)
    assert d.violations == ["GitHub PR comment on myorg/app#99: internal name: name0599"], d
    assert _scrub_compiled.cache_info().misses == 1
