"""A gh command's repo comes from `-R` and its operands, never from an option value (#399).

`_gh_target` walked every word after the subcommand, and the first one that began with a GitHub URL replaced
the `-R` repo, even as the value of `--body`, `--title` or `--duplicate-of`. A post to a public repo whose body
opened with a link to an internal repo was scoped internal: it logged, and the footer, scrub and third-party
checks for public targets never ran. A number in an option value (`-b 7`, `--milestone 3`) became the target's
number the same way. Now operands are read with gh's own table of options that take a value, and every other
reading the gate cannot rule out is decided too, the strictest standing, so no line decides lower than before.
Classification and decision only; nothing is executed.
"""
from dataclasses import replace

import pytest

from exo.gate import UNSEEN_BODY, body_unseen, classify, decide, normalize_policies, summarize

POST = {"footer": "F", "scrub": {"names": ["SECRETX"]}}
POLICY = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}}, "posting": POST})
ORG = "https://github.com/myorg/app/pull/9"
ORG_ISSUE = "https://github.com/myorg/app/issues/1"
PUB = "https://github.com/pub/repo/pull/5"


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def run(line, policy=POLICY):
    c = classify(sh(line))
    return c, decide(c, policy)


# The issue's rows: each lands on pub/repo#5, a public repo, so each asks as public with a third party.
ROWS = [
    "gh pr comment 5 -R pub/repo -b $'Note SECRETX\\nF'",                                   # control
    f"gh pr comment 5 -R pub/repo -b $'{ORG} Note SECRETX\\nF'",
    f"gh issue comment 5 -R pub/repo --body $'{ORG_ISSUE} SECRETX\\nF'",
    "gh issue close 5 -R pub/repo",                                                        # control
    f"gh issue close 5 -R pub/repo --duplicate-of {ORG_ISSUE}",
]


@pytest.mark.parametrize("line", ROWS)
def test_the_issue_rows_land_on_the_public_repo(line):
    c, d = run(line)
    assert c.target == "pub/repo#5", c
    assert (d.outcome, d.exposure, d.third_party) == ("ask", "public", True), (line, d)
    if "SECRETX" in line:
        assert "internal name: SECRETX" in d.violations, (line, d)


# A URL or a number in an option value is not the target, in any form gh reads an option in.
OPTION_VALUES = [
    (f"gh pr comment 5 -R pub/repo --title {ORG}", "pub/repo#5"),
    (f"gh pr comment 5 -R pub/repo --body={ORG}", "pub/repo#5"),
    (f"gh pr edit 5 -R pub/repo --base {ORG}", "pub/repo#5"),
    (f"gh pr create -R pub/repo --head {ORG} -t t -b $'x\\nF'", "pub/repo"),
    (f"gh pr create -R pub/repo -H {ORG} -t t -b $'x\\nF'", "pub/repo"),
    (f"gh issue edit 5 -R pub/repo --add-label {ORG_ISSUE}", "pub/repo#5"),
    (f"gh issue close 5 -R pub/repo --comment $'{ORG_ISSUE}\\nF'", "pub/repo#5"),
    (f"gh pr close 5 -R pub/repo -c $'{ORG}\\nF'", "pub/repo#5"),
    (f"gh release create v1 -R pub/repo --notes $'{ORG}\\nF'", "pub/repo"),
]


@pytest.mark.parametrize("line,target", OPTION_VALUES)
def test_an_option_value_is_not_the_target(line, target):
    c, d = run(line)
    assert c.target == target, c
    assert d.exposure == "public" and d.third_party, (line, d)


# gh keeps a URL operand's repo (pr/shared/finder.go, issue/shared/lookup.go in gh 2.89).
def test_a_url_operand_still_sets_the_target():
    c, d = run(f"gh pr comment {PUB} -b $'x\\nF'")
    assert (c.target, c.link) == ("pub/repo#5", PUB), c
    assert (d.outcome, d.exposure) == ("ask", "public"), d
    c, d = run(f"gh pr comment {ORG} -b $'x\\nF'")
    assert (c.target, c.link, c.alt_targets) == ("myorg/app#9", ORG, []), c
    assert (d.outcome, d.exposure, d.third_party) == ("log", "internal", False), d
    c, _ = run(f"gh issue comment {ORG_ISSUE} --body $'x\\nF'")
    assert c.target == "myorg/app#1", c


# `-R` and a URL operand that name two repos: gh posts on the URL's, and the gate decides on both and keeps the
# stricter, so the line is never less exposed than either.
@pytest.mark.parametrize("line,target", [
    (f"gh pr comment {ORG} -R pub/repo -b $'x\\nF'", "myorg/app#9"),
    (f"gh pr comment {PUB} -R myorg/app -b $'x\\nF'", "pub/repo#5"),
    (f"gh issue comment {ORG_ISSUE} --repo=pub/repo --body $'x\\nF'", "myorg/app#1"),
    (f"gh issue close {ORG_ISSUE} -R pub/repo", "myorg/app#1"),
])
def test_r_and_a_url_operand_that_disagree_take_the_more_exposed(line, target):
    c, d = run(line)
    assert c.target == target, c
    assert (d.outcome, d.exposure, d.third_party) == ("ask", "public", True), (line, d)


def test_the_reason_names_the_other_target_when_it_decides():
    _, d = run(f"gh pr comment {ORG} -R pub/repo -b $'x\\nF'")
    assert "if it lands on pub/repo#9" in d.reason, d


# The ask names every other target, also when none decides the line: the principal sees where gh may act.
def test_the_ask_names_the_other_targets():
    pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}, "categories": {"vcs_merge": "ask"}}})
    c, d = run("gh pr merge --auto https://github.com/pub/repo/pull/7 -R myorg/app", pol)
    assert (c.target, d.outcome, d.exposure) == ("myorg/app", "ask", "internal"), (c, d)
    assert "It may land on pub/repo#7 or myorg/app#7 instead." in summarize(c, d), summarize(c, d)
    c, d = run(f"gh pr ready --foo {ORG}")
    assert c.alt_targets == [""], c
    assert "It may land on the current repo instead." in summarize(c, d), summarize(c, d)
    assert d.reason.endswith("(if it lands on the current repo)"), d
    c, d = run("gh pr comment 5 -R pub/repo -b x")
    assert not c.alt_targets and "may land" not in summarize(c, d), summarize(c, d)


def test_a_deny_on_either_repo_denies_the_disagreement():
    for entry in ("target:pub/*", "target:myorg/*"):
        pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}, "deny": [entry]}, "posting": POST})
        _, d = run(f"gh pr comment {ORG} -R pub/repo -b $'x\\nF'", pol)
        assert d.outcome == "deny", (entry, d)


# decide merges the readings axis by axis: the strictest outcome, the widest exposure, a third party if any reading
# has one, and every violation and reserved part. Each axis may come from another reading.
def test_the_widest_exposure_and_any_third_party_stand_beside_a_deny_from_another_reading():
    pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}, "deny": ["target:myorg/*"]}})
    c, d = run("gh pr comment 5 -R myorg/app -F https://github.com/pub/repo/issues/3", pol)
    assert (c.target, c.alt_targets) == ("myorg/app#5", ["pub/repo#3"]), c
    # The deny comes from myorg/app#5, the public exposure and the third party from pub/repo#3.
    assert (d.outcome, d.exposure, d.third_party) == ("deny", "public", True), d


def test_reserved_parts_come_from_every_reading():
    pol = normalize_policies({"gates": {"scopes": {"private": ["myorg/app"]}, "reserved": ["vcs_merge"]}})
    c, d = run(f"gh pr merge 5 -b {ORG}", pol)
    assert (c.target, c.alt_targets) == ("myorg/app#9", ["#5"]), c
    assert d.outcome == "ask" and set(d.reserved) == {("vcs_merge", "myorg/app#9"), ("vcs_merge", "#5")}, d


def test_violations_come_from_every_reading():
    pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                              "posting": {"footer": "F", "footer_exposures": ["internal"],
                                          "scrub": {"names": ["SECRETX"], "exposures": ["public"]}}})
    c, d = run(f"gh pr comment 5 -R pub/repo -b $'{ORG} Note SECRETX'", pol)
    assert (c.target, c.alt_targets) == ("pub/repo#5", ["myorg/app#9"]), c
    # The scrub hit is the public reading's, the missing footer the internal one's.
    assert {"internal name: SECRETX", "missing footer: F"} <= set(d.violations), d


def test_a_tie_in_outcome_goes_to_the_more_exposed_reading():
    pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}, "categories": {"github_post": "ask"}}})
    c, d = run(f"gh pr comment {ORG} -R pub/repo -b x", pol)
    assert (c.target, c.alt_targets) == ("myorg/app#9", ["pub/repo#9"]), c
    assert (decide(replace(c, alt_targets=[]), pol).outcome, d.outcome, d.exposure) == ("ask", "ask", "public"), d
    assert d.reason.startswith("category github_post") and d.reason.endswith("(if it lands on pub/repo#9)"), d


def test_gh_keeps_the_last_repo_option():
    c, d = run("gh pr comment 5 -R myorg/app -R pub/repo -b $'x\\nF'")
    assert c.target == "pub/repo#5", c
    assert (d.outcome, d.exposure) == ("ask", "public"), d


# A number in an option value is not the target's number.
@pytest.mark.parametrize("line,target", [
    ("gh pr comment -R pub/repo -b 7 5", "pub/repo#5"),
    ("gh issue comment --body '#9' 5 -R pub/repo", "pub/repo#5"),
    ("gh pr create -R pub/repo -m 3 -t t -b $'x\\nF'", "pub/repo"),
    ("gh pr create -R pub/repo --milestone 3 -t t -b $'x\\nF'", "pub/repo"),
    ("gh issue close 5 -R pub/repo --duplicate-of 9", "pub/repo#5"),
    ("gh issue close -R pub/repo --reason 'not planned' 5", "pub/repo#5"),
])
def test_a_numeric_option_value_is_not_the_number(line, target):
    c, _ = run(line)
    assert c.target == target, c


# Options end at `--`; every word after it is an operand, and the words before it are read as options.
@pytest.mark.parametrize("line,target", [
    ("gh pr comment -R pub/repo --body 7 -- 5", "pub/repo#5"),
    (f"gh pr comment -R pub/repo -b {ORG} -- 5", "pub/repo#5"),
    (f"gh pr comment -R pub/repo -b -- {ORG}", "myorg/app#9"),     # `--` is -b's value, so the URL is an operand
])
def test_the_end_of_options(line, target):
    c, d = run(line)
    assert c.target == target, c
    assert d.exposure == "public", (line, d)


# Short options cluster as in gh: the first letter that takes a value takes the rest of the word or the next one.
@pytest.mark.parametrize("line", [
    f"gh pr close 5 -R pub/repo -dc {ORG}",
    f"gh issue comment 5 -wb {ORG_ISSUE} -R pub/repo",
    f"gh pr comment 5 -Rpub/repo -b {ORG}",
    f"gh pr comment 5 --repo=pub/repo -b {ORG}",
])
def test_clusters_and_attached_values(line):
    c, d = run(line)
    assert c.target == "pub/repo#5", c
    assert d.exposure == "public", (line, d)


# A close whose `--duplicate-of` value is an option word: gh refuses the line, and the gate keeps it unseen.
def test_a_duplicate_of_option_word_stays_unseen():
    c, d = run("gh issue close 5 -R myorg/app -c a --duplicate-of -c b")
    assert body_unseen(c) and UNSEEN_BODY in d.violations and d.outcome == "ask", d


# A command the table does not list: a URL the gate cannot tell from an option value never replaces `-R`, and
# with no `-R` it names the target while the reading without it still counts.
def test_a_command_outside_the_table():
    c, _ = run(f"gh pr merge --subject {ORG} 5 -R pub/repo")
    assert c.target == "pub/repo#5" and "myorg/app#9" in c.alt_targets, c
    c, d = run(f"gh pr ready {ORG}")
    assert (c.target, c.alt_targets, d.outcome) == ("myorg/app#9", [], "log"), (c, d)
    c, d = run(f"gh pr ready --foo {ORG}")
    assert c.target == "myorg/app#9" and "" in c.alt_targets, c
    assert (d.outcome, d.exposure) == ("ask", "public"), d


# No line decides lower than before: the reading before #399 still counts, as a floor.
def test_the_earlier_reading_stays_a_floor():
    line = "gh pr comment 5 -R myorg/app -b https://github.com/pub/repo/pull/3"
    c, d = run(line)
    assert c.target == "myorg/app#5" and "pub/repo#3" in c.alt_targets, c
    assert (d.exposure, d.third_party) == ("public", True), d
    pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}, "deny": ["target:pub/*"]}})
    assert decide(c, pol).outcome == "deny"
    # Also when the gh call is not the line's most severe part: the other reading is a part, so a deny sees it.
    compound = classify(sh(f"rm -rf build; {line}"))
    assert compound.category == "delete" and decide(compound, pol).outcome == "deny", compound


def test_the_ledger_view_names_other_targets_only_when_there_are_some():
    assert "alt_targets" not in classify(sh("gh pr comment 5 -R pub/repo -b x")).as_dict()
    row = classify(sh(f"gh pr comment {ORG} -R pub/repo -b x")).as_dict()
    assert row["alt_targets"] == ["pub/repo#9"] and "parts" not in row, row
