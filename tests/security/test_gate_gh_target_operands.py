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

from exo.gate import UNSEEN_BODY, _each_part, body_unseen, classify, decide, fill_body, normalize_policies, summarize

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
    assert d.exposure == "public", (line, d)


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
    assert (c.target, d.outcome, d.exposure) == ("pub/repo#7", "ask", "internal"), (c, d)
    assert "It may land on myorg/app#7 instead." in summarize(c, d), summarize(c, d)
    c, d = run(f"gh pr checkout --branch {ORG} 5 -R pub/repo")
    assert "It may land on myorg/app#9 or pub/repo#9 instead." in summarize(c, d), summarize(c, d)
    c, d = run("gh repo delete --yes myorg/app")
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
    # The other target decides (internal beats private), and the reserved part on the main target joins it.
    pol = normalize_policies({"gates": {"scopes": {"private": ["myorg/app"]}, "reserved": ["vcs_merge"]}})
    c, d = run("gh pr merge 5 -R myorg/app -b https://github.com/pub/repo/pull/3", pol)
    assert (c.target, c.alt_targets) == ("myorg/app#5", ["pub/repo#3"]), c
    assert d.outcome == "ask" and set(d.reserved) == {("vcs_merge", "myorg/app#5"), ("vcs_merge", "pub/repo#3")}, d


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
    c, _ = run(f"gh pr checkout --branch {ORG} 5 -R pub/repo")
    assert c.target == "pub/repo#5" and "myorg/app#9" in c.alt_targets, c
    c, _ = run(f"gh pr checkout {ORG}")
    assert (c.target, c.alt_targets) == ("myorg/app#9", []), c
    c, _ = run(f"gh pr checkout --detach {ORG}")
    assert c.target == "myorg/app#9" and "" in c.alt_targets, c
    c, d = run("gh repo delete myorg/app")
    assert (c.target, c.alt_targets, d.exposure) == ("myorg/app", [], "internal"), (c, d)
    c, d = run("gh repo delete --yes myorg/app")
    assert c.target == "myorg/app" and "" in c.alt_targets, c
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


# On an ssh or mosh hop the host stands for an empty target, as on main. An empty other target gets the host too,
# so a rule on the host still sees the reading that had no repo (round 3, item 1).
@pytest.mark.parametrize("line", [
    "ssh buildhost gh repo view --web myorg/app",
    "ssh buildhost 'gh repo fork --clone myorg/app'",
    "mosh buildhost -- gh repo view --web myorg/app",
])
def test_an_ssh_host_stands_for_every_empty_target(line):
    pol = normalize_policies({"gates": {"rules": [{"match": {"target": "buildhost"}, "decision": "ask"}]}})
    c, d = run(line, pol)
    assert "" not in c.alt_targets and "buildhost" in [c.target] + c.alt_targets, c
    assert d.outcome == "ask", (line, d)


def test_an_ssh_host_is_not_an_other_target_of_itself():
    c, _ = run("ssh buildhost gh pr view 5")
    assert (c.target, c.alt_targets) == ("#5", []), c
    c, _ = run("ssh buildhost gh pr view")
    assert (c.target, c.alt_targets) == ("buildhost", []), c


# The copy of the action on an empty other target is a part of the line, and it gets the host too. So a deny, a rule
# or a reserved entry on the host's category sees it also when another part of the line outranks the gh part. On
# main the gh part's own target was the host (round 4, item 1).
def _host_rule(category, host):
    return {"rules": [{"match": {"category": category, "target": host}, "decision": "deny"}]}


HOST_POST = "ssh buildhost gh pr comment -wR myorg/app -b hi"


@pytest.mark.parametrize("gates, line", [
    ({"deny": [{"category": "github_post", "target": "buildhost"}]}, f"gh release delete v1 -y && {HOST_POST}"),
    (_host_rule("github_post", "buildhost"), f"gh release delete v1 -y && {HOST_POST}"),
    (_host_rule("read", "buildhost"), "bash -c 'ssh buildhost -- gh api repos/pub/repo/issues?per_page=1'"),
    (_host_rule("read", "build"), "ssh -i ~/.ssh/k build gh repo view --web myorg/app"),
    (_host_rule("read", "buildhost"), "gh release delete v1 -y && mosh buildhost -- gh repo view --web myorg/app"),
    (_host_rule("read", "build"), "gh release delete v1 -y && ssh jump ssh build gh repo view --web myorg/app"),
    (_host_rule("read", "build"), "ssh jump ssh -i ~/.ssh/k build gh repo view --web myorg/app"),
])
def test_an_ssh_host_stands_for_an_empty_other_target_below_the_top(gates, line):
    c, d = run(line, normalize_policies({"gates": gates}))
    assert d.outcome == "deny", (line, d)


@pytest.mark.parametrize("reserved, line, hit", [
    (["target:build*"], "ssh -i ~/.ssh/k build gh repo view --web myorg/app", ("read", "build")),
    (["target:build"], "gh release delete v1 -y && ssh jump ssh build gh repo view --web myorg/app", ("read", "build")),
    (["vcs_merge", "github_close"],
     "gh secret set T -b x || ssh -F ~/.ssh/config buildhost gh repo archive -dR=pub/repo pub/repo",
     ("github_close", "buildhost")),
])
def test_a_reserved_entry_records_the_host_of_an_empty_other_target(reserved, line, hit):
    c, d = run(line, normalize_policies({"gates": {"reserved": reserved}}))
    assert hit in d.reserved, (line, d)
    assert not any(p.target == "" and p.category == hit[0] for p in c.parts if p.alt_copy), c


# gh api takes its endpoint from its one operand, never from an option's value: a header, a jq query, a template, a
# host, a cache time, a preview name or an input file can hold a path that names another repo (round 3, item 2).
API_POST = "repos/pub/repo/issues/5/comments -f body=x"


@pytest.mark.parametrize("opts", [
    '-H "X-Note: repos/myorg/app/"', "--header 'X-Note: repos/myorg/app/'", "--header=X:repos/myorg/app/x",
    "-HX:repos/myorg/app/x", "-iH X:repos/myorg/app/x", "-q repos/myorg/app", "--jq repos/myorg/app",
    "-t repos/myorg/app/x", "--template repos/myorg/app/x", "-p repos/myorg/app", "--preview repos/myorg/app",
    "--cache repos/myorg/app", "--hostname repos/myorg/app", "-X POST -H X:repos/myorg/app/",
    "--method POST --jq .x/repos/myorg/app",
])
def test_gh_api_takes_its_endpoint_from_its_operand(opts):
    line = f"gh api {opts} {API_POST}"
    c, d = run(line)
    assert (c.category, c.target) == ("github_post", "pub/repo"), c
    assert c.summary.endswith(" repos/pub/repo/issues/5/comments"), c
    assert (d.outcome, d.exposure, d.third_party) == ("ask", "public", True), (line, d)
    pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}, "deny": ["target:pub/*"]}})
    assert decide(c, pol).outcome == "deny", line


def test_gh_api_keeps_the_word_it_took_before():
    # The path in an option's value was the target before, and it still counts, so no line decides lower.
    c, d = run("gh api -q repos/pub/repo repos/myorg/app/issues/1/comments -f body=x")
    assert (c.target, c.alt_targets) == ("myorg/app", ["pub/repo"]), c
    assert (d.exposure, d.third_party) == ("public", True), d
    c, _ = run("gh api --input repos/myorg/app/f repos/pub/repo/issues/5/comments")
    assert (c.target, c.alt_targets) == ("pub/repo", ["myorg/app"]), c


@pytest.mark.parametrize("line,target", [
    ("gh api repos/pub/repo/issues/5/comments -f body=x", "pub/repo"),
    ("gh api -X POST -- repos/pub/repo/issues/5/comments", "pub/repo"),
    ("gh api --paginate --slurp repos/myorg/app/issues", "myorg/app"),
    ("gh api graphql -f query=x", ""),
    ("gh api /user", "/user"),
    ("gh api 'repos/pub/repo/issues?state=open'", "pub/repo"),
])
def test_gh_api_endpoints_as_before(line, target):
    c, _ = run(line)
    assert c.target == target, c


# pflag drops one `=` after a short option, also at the end of a cluster (`-R=owner/name`, `-wR=owner/name`), and
# gh reads `-R` as `[HOST/]OWNER/REPO` or a repo URL (ghrepo.FromFullName in gh 2.89). Round 3, item 4.
DENY_PUB = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}, "deny": ["target:pub/*"]}})
GIT_AT = "git" + "@github.com"      # an ssh remote's user and host, split so the privacy check reads no address


@pytest.mark.parametrize("line,target", [
    ("gh pr comment 5 -R=pub/repo -b x", "pub/repo#5"),
    ("gh pr comment 5 -wR=pub/repo -b x", "pub/repo#5"),
    ("gh issue close 5 -R=pub/repo", "pub/repo#5"),
    ("gh pr -R=pub/repo comment 5 -b x", "pub/repo#5"),
    ("gh pr comment 5 -R github.com/pub/repo -b x", "pub/repo#5"),
    ("gh pr comment 5 -R=github.com/pub/repo -b x", "pub/repo#5"),
    ("gh pr comment 5 --repo=github.com/pub/repo -b x", "pub/repo#5"),
    ("gh pr comment 5 -R www.github.com/pub/repo -b x", "pub/repo#5"),
    ("gh pr comment 5 -R https://github.com/pub/repo -b x", "pub/repo#5"),
    ("gh pr comment 5 -R https://github.com/pub/repo.git -b x", "pub/repo#5"),
    (f"gh pr comment 5 -R {GIT_AT}:pub/repo.git -b x", "pub/repo#5"),
    (f"gh pr comment 5 -R ssh://{GIT_AT}/pub/repo.git -b x", "pub/repo#5"),
    ("gh pr merge 5 -dR=github.com/pub/repo", "pub/repo#5"),
    ("gh repo delete github.com/pub/repo --yes", "pub/repo"),
])
def test_r_as_gh_reads_it(line, target):
    c, d = run(line, DENY_PUB)
    assert c.target == target, c
    assert d.outcome == "deny", (line, d)


def test_r_on_another_host_keeps_the_value_as_written():
    # A scope names repos on github.com. On another instance the gate cannot tell, so the value as written counts too.
    c, d = run("gh pr comment 5 -R ghe.corp/myorg/app -b $'x\\nF'")
    assert c.target == "myorg/app#5" and "ghe.corp/myorg/app#5" in c.alt_targets, c
    assert d.exposure == "public", d
    c, _ = run("gh pr comment 5 -R https://ghe.corp/myorg/app -b x")
    assert c.target == "myorg/app#5" and "https://ghe.corp/myorg/app#5" in c.alt_targets, c
    # gh keeps the last -R, and the reading before #399 the first, so only this reading names the last as written.
    c, _ = run("gh pr comment 5 -R pub/repo -R ghe.corp/myorg/app -b x")
    assert c.target == "myorg/app#5" and "ghe.corp/myorg/app#5" in c.alt_targets, c


def test_a_repo_operand_on_github_com_keeps_the_reading_before():
    # The gate read no repo from `github.com/pub/repo` before, so the line was decided on the current repo. It
    # still is, beside pub/repo, so a scope on pub/* does not lower it.
    c, d = run("gh repo delete github.com/pub/repo --yes", normalize_policies({"gates": {"scopes": {"internal": ["pub/*"]}}}))
    assert (c.target, c.alt_targets, d.exposure) == ("pub/repo", [""], "public"), (c, d)


@pytest.mark.parametrize("value", ["a/b/c/d", "pub/repo/", "/pub/repo", "https://github.com/pub/repo/pull/5", "=x/y"])
def test_r_that_gh_refuses_stays_as_written(value):
    c, _ = run(f"gh pr comment 5 -R {value} -b x")
    assert c.target == f"{value}#5", c


# The table lists more of gh's options that take a value (gh 2.89 --help), so everyday lines read one target and no
# empty one: `gh pr ready --undo URL` asked as "the current repo" before (round 3, item 5).
@pytest.mark.parametrize("line,target", [
    (f"gh pr ready --undo {ORG}", "myorg/app#9"),
    (f"gh pr reopen -c $'x\\nF' {ORG}", "myorg/app#9"),
    (f"gh pr merge --squash --delete-branch {ORG}", "myorg/app#9"),
    (f"gh pr merge -t subject -b body {ORG}", "myorg/app#9"),
    (f"gh pr merge --author-email a@b.c --match-head-commit abc {ORG}", "myorg/app#9"),
    (f"gh pr lock --reason spam {ORG}", "myorg/app#9"),
    (f"gh pr unlock {ORG}", "myorg/app#9"),
    (f"gh issue reopen --comment x {ORG_ISSUE}", "myorg/app#1"),
    (f"gh issue develop --checkout --name b {ORG_ISSUE}", "myorg/app#1"),
    (f"gh issue develop --branch-repo x/y -b main {ORG_ISSUE}", "myorg/app#1"),
    (f"gh issue lock -r spam {ORG_ISSUE}", "myorg/app#1"),
    (f"gh issue unlock {ORG_ISSUE}", "myorg/app#1"),
    (f"gh issue delete --yes {ORG_ISSUE}", "myorg/app#1"),
    (f"gh issue transfer {ORG_ISSUE} myorg/other", "myorg/app#1"),
    ("gh release upload v1 f.tgz --clobber -R myorg/app", "myorg/app"),
    ("gh release delete v1 -y --cleanup-tag -R myorg/app", "myorg/app"),
])
def test_everyday_lines_read_one_target(line, target):
    c, _ = run(line)
    assert (c.target, c.alt_targets) == (target, []), c


@pytest.mark.parametrize("line", ["gh issue close -r 7 5 -R pub/repo", "gh pr lock -r 7 5 -R pub/repo",
                                  "gh pr merge -A 7 5 -R pub/repo", "gh issue develop -n 7 5 -R pub/repo"])
def test_a_number_in_a_new_rows_option_value_is_not_the_number(line):
    c, _ = run(line)
    assert c.target == "pub/repo#5", c


# A line the unseen check found unseen without these rows stays unseen: the table read it so before #399, and a
# line never decides lower than it did. The reopen and merge rows came with #397 and are main's own.
@pytest.mark.parametrize("line", [
    "gh issue lock 5 -R myorg/app -rb x", "gh pr lock 5 -R myorg/app -rb x",
    "gh issue close 5 -R myorg/app -rc x", "gh issue develop 5 -R myorg/app -nt x",
    "gh issue close 5 -R myorg/app -c a --duplicate-of -c b",
])
def test_a_line_unseen_before_the_new_rows_stays_unseen(line):
    c, d = run(line)
    assert body_unseen(c) and UNSEEN_BODY in d.violations, (line, d)


# A word from an expansion where an operand stands may be a URL for any repo, so `-R` does not rule one out: the
# line is also decided on a repo it does not name, the empty target (round 3, item 6).
@pytest.mark.parametrize("line", [
    'gh pr merge "$U" -R myorg/app', "gh pr comment $U -R myorg/app -b x", 'gh issue close "${N}" -R myorg/app',
    'gh pr merge -R myorg/app -- "$U"', 'gh pr view "$(cat u)" -R myorg/app', 'gh api "repos/$O/app/issues" -f body=x',
])
def test_a_dynamic_operand_may_name_any_repo(line):
    c, d = run(line, normalize_policies({"gates": {"scopes": {"private": ["myorg/*"]}}}))
    assert "" in c.alt_targets, c
    assert d.exposure != "private" or c.category == "read", (line, d)


def test_a_dynamic_option_value_names_no_other_repo():
    c, _ = run('gh pr merge 5 -R myorg/app -t "$T" -b "$B"')
    assert (c.target, c.alt_targets) == ("myorg/app#5", []), c


# For a command outside the table gh may take some options' values and not others. The words both readings call
# operands, with the first reading's repo, are one such mix, and it may be the one gh acts on (round 3, item 7).
def test_a_mix_of_the_two_readings_counts_too():
    line = "gh pr checkout -b 7 9 -f -R pub/repo"   # gh: -b takes 7 and -f is a flag, so this checks out pub/repo#9
    c, d = run(line, normalize_policies({"gates": {"deny": ["target:pub/repo#9"]}}))
    assert c.target == "pub/repo#7" and "pub/repo#9" in c.alt_targets and d.outcome == "deny", (c, d)


# A URL operand reads close to how gh reads it (gh 2.89 url.Parse, `ghrepo.NewWithHost`): the scheme and the host in
# any case, without a user, a port or a leading `www.`, and the path decoded. gh repo drops a repo URL's `.git`
# (round 4, item 3).
DENY_REPO = normalize_policies({"gates": {"categories": {"vcs_merge": "log", "github_close": "log", "read": "log"},
                                          "deny": ["target:pub/repo", "target:pub/repo#5"]}})


@pytest.mark.parametrize("line,target", [
    ("gh pr merge https://www.github.com/pub/repo/pull/5 -R myorg/app", "pub/repo#5"),
    ("gh pr merge https://GitHub.com/pub/repo/pull/5 -R myorg/app", "pub/repo#5"),
    ("gh pr merge HTTPS://github.com/pub/repo/pull/5 -R myorg/app", "pub/repo#5"),
    ("gh pr merge https://github.com:443/pub/repo/pull/5 -R myorg/app", "pub/repo#5"),
    (f"gh pr merge https://tok{GIT_AT[3:]}/pub/repo/pull/5 -R myorg/app", "pub/repo#5"),
    ("gh pr merge https://github.com/%70ub/repo/pull/5 -R myorg/app", "pub/repo#5"),
    ("gh issue close https://WWW.GITHUB.COM/pub/repo/issues/5 -R myorg/app", "pub/repo#5"),
    ("gh repo view https://github.com/pub/repo.git", "pub/repo"),
    ("gh repo view https://www.github.com/pub/repo.git", "pub/repo"),
])
def test_a_url_operand_reads_as_gh_reads_it(line, target):
    c, d = run(line, DENY_REPO)
    assert c.target == target and "tok" not in c.link, c
    assert d.outcome == "deny", (line, d)


def test_a_url_operand_keeps_the_reading_before():
    # The repo URL's name as written was the target before, so a deny on it still fires.
    c, d = run("gh repo view https://github.com/pub/repo.git",
               normalize_policies({"gates": {"deny": ["target:pub/repo.git"]}}))
    assert (c.target, c.alt_targets, d.outcome) == ("pub/repo", ["pub/repo.git"], "deny"), (c, d)


# Go 1.26's url.Parse refuses a port that is not ASCII digits, a second colon, and an escape below %80 other than
# %25 in the host (round 7). It refuses a control character before the first `#`, in a query too (round 9).
@pytest.mark.parametrize("url", ["https://github.com:x/pub/repo/pull/5", "ftp://github.com/pub/repo/pull/5",
                                 "https://github.com:80:80/pub/repo/pull/5", "https://g%69thub.com/pub/repo/pull/5",
                                 "https://github.com:\u0665/pub/repo/pull/5",
                                 "$'https://api.github.com/pub/repo/pull/5?\\t'",
                                 "$'https://api.github.com/pub/repo/pull/5?a\\x7f#b'",
                                 "$'https://api.github.com/pub/repo/pull/5\\t#a'"])
def test_a_url_gh_refuses_names_no_repo(url):
    c, _ = run(f"gh pr merge {url} -R myorg/app")
    assert (c.target, c.alt_targets) == ("myorg/app", []), c


# Go refuses a `[` in a host outside a bracketed IPv6 literal, and anything after its closing `]` but a port. Read
# without that check, these hosts end in `.github.com` or parse as one, and the URL would name pub/x#9.
@pytest.mark.parametrize("url", ["https://a[b.github.com/pub/x/pull/9", "https://[::1][/pub/x/pull/9",
                                 "https://a]b.github.com[/pub/x/pull/9", "https://[x[.github.com]/pub/x/pull/9"])
def test_a_host_with_a_stray_bracket_names_no_repo(url):
    c, _ = run(f"gh pr merge {url} -R myorg/app")
    assert c.target == "myorg/app" and not any(t.startswith("pub/x") for t in c.alt_targets), c


# Round 6, blocking item. gh sends a pull request or issue lookup for a URL on any host that ends in `.github.com` to
# github.com, with the github.com token: go-gh 2.13's NormalizeHostname reads such a host as github.com, and its
# GraphQL client calls https://api.github.com/graphql for it. The gate read only github.com and www.github.com, so
# these lines landed on pub/repo#9 and logged as internal, with no scrub and no third-party check. The review's
# rows, with `internal: [myorg/*]` and the footer F.
REVIEW = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}}, "posting": {"footer": "F"}})


@pytest.mark.parametrize("line", [
    "gh pr comment https://www.github.com/pub/repo/pull/9 -R myorg/app -b $'Note\\nF'",
    "gh pr comment https://api.github.com/pub/repo/pull/9 -R myorg/app -b $'Note\\nF'",
    "gh issue comment https://gist.github.com/pub/repo/issues/9 -R myorg/app -b $'Note\\nF'",
    # Round 7: url.Parse checks only that a port is digits, with no range check, and u.Hostname() drops it. So gh
    # reads github.com here, and the gate read no URL at all, since Python refuses a port past 65535.
    "gh pr comment https://github.com:99999/pub/repo/pull/9 -R myorg/app -b $'Note\\nF'",
    "gh pr comment https://api.github.com:65536/pub/repo/pull/9 -R myorg/app -b $'Note\\nF'",
    "gh issue comment https://github.com:99999/pub/repo/issues/9 -R myorg/app -b $'Note\\nF'",
    # Round 9: url.Parse cuts the fragment off before it looks for a control character, and keeps a fragment with
    # one. So gh reads these URLs, and the gate read none of them.
    "gh pr comment $'https://api.github.com/pub/repo/pull/9#\\t' -R myorg/app -b $'Note\\nF'",
    "gh pr comment $'https://api.github.com/pub/repo/pull/9#a\\nb' -R myorg/app -b $'Note\\nF'",
    "gh pr comment $'https://api.github.com/pub/repo/pull/9#\\x7f' -R myorg/app -b $'Note\\nF'",
    "gh pr comment $'https://github.com:99999/pub/repo/pull/9#\\t' -R myorg/app -b $'Note\\nF'",
    "gh pr comment $'https://github.com:99999/pub/repo/pull/9#a\\nb' -R myorg/app -b $'Note\\nF'",
    "gh issue comment $'https://github.com:99999/pub/repo/issues/9#\\x7f' -R myorg/app -b $'Note\\nF'",
])
def test_a_url_on_a_github_com_host_names_the_repo(line):
    c, d = run(line, REVIEW)
    assert (c.target, c.alt_targets) == ("pub/repo#9", ["myorg/app#9", "myorg/app"]), c
    assert (d.outcome, d.exposure, d.third_party) == ("ask", "public", True), (line, d)


@pytest.mark.parametrize("url", ["https://api.github.com/pub/x/pull/9", "https://github.com:99999/pub/x/pull/9",
                                 # Round 9: a control character in the fragment
                                 "$'https://api.github.com/pub/x/pull/9#\\t'",
                                 "$'https://api.github.com/pub/x/pull/9#a\\nb'",
                                 "$'https://api.github.com/pub/x/pull/9#\\x7f'",
                                 "$'https://github.com:99999/pub/x/pull/9#\\t'",
                                 "$'https://github.com:99999/pub/x/pull/9#a\\nb'",
                                 "$'https://github.com:99999/pub/x/pull/9#\\x7f'"])
def test_an_allow_rule_on_the_r_repo_does_not_cover_the_url_s_repo(url):
    pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}, "rules": [
        {"match": {"category": "github_post", "target": "myorg/*"}, "decision": "allow"}]}, "posting": {"footer": "F"}})
    c, d = run(f"gh pr review {url} -R myorg/app --approve -b $'hi\\nF'", pol)
    assert c.target == "pub/x#9", c
    assert d.outcome == "ask", d


@pytest.mark.parametrize("line", ["gh repo archive https://api.github.com/pub/x --yes",
                                  "gh repo archive api.github.com/pub/x --yes",
                                  f"gh repo archive git{GIT_AT[3:4]}api.github.com:pub/x.git --yes",
                                  "gh pr comment 5 -R api.github.com/pub/x -b x",
                                  # Round 7: a port past 65535, and a host gh lowers to github.com
                                  "gh repo archive https://github.com:99999/pub/x --yes",
                                  "gh issue close https://github.com:99999/pub/x/issues/9 -R myorg/app",
                                  "gh pr review https://github.com:99999/pub/x/pull/9 -R myorg/app --approve",
                                  "gh repo archive https://g%C4%B0thub.com/pub/x --yes",
                                  "gh repo archive g\u0130thub.com/pub/x --yes",
                                  "gh pr comment 5 -R https://g%C4%B0thub.com/pub/x -b x",
                                  # Round 9: a control character in the fragment
                                  "gh repo archive $'https://api.github.com/pub/x#\\t' --yes",
                                  "gh repo delete $'https://api.github.com/pub/x#a\\nb' --yes",
                                  "gh repo archive $'https://github.com:99999/pub/x#\\x7f' --yes",
                                  "gh pr comment 5 -R $'https://api.github.com/pub/x#\\t' -b x",
                                  "gh pr comment 5 -R $'https://github.com:99999/pub/x#a\\nb' -b x",
                                  f"gh pr comment 5 -R $'git{GIT_AT[3:4]}api.github.com:pub/x#\\x7f' -b x",
                                  "gh issue close $'https://api.github.com/pub/x/issues/9#\\x01' -R myorg/app",
                                  "gh pr review $'https://github.com:99999/pub/x/pull/9#\\t' -R myorg/app --approve",
                                  "gh pr review $'https://api.github.com/pub/x/pull/9#\\x7f' -R myorg/app --approve"])
def test_a_repo_on_a_github_com_host_meets_a_deny(line):
    c, d = run(line, normalize_policies({"gates": {"deny": ["target:pub/*"]}}))
    assert c.target.startswith("pub/x"), c
    assert d.outcome == "deny", (line, d)


# Every host gh sends to github.com, in the forms gh reads: the case, a user, a port and a leading `www.` go first.
@pytest.mark.parametrize("host", ["api.github.com", "gist.github.com", "uploads.github.com", "codeload.github.com",
                                  "raw.github.com", "www.github.com", "API.GitHub.COM", "WWW.api.github.com",
                                  "u:p" + "@api.github.com", "api.github.com:443", "a.b.github.com",
                                  # Round 7: url.Parse decodes the host, and Go's strings.ToLower makes U+0130 `i`
                                  # (Python makes it `i` and a combining dot). A port has no range check.
                                  "g\u0130thub.com", "g%C4%B0thub.com", "api.g%c4%b0thub.com", "www.g\u0130thub.com",
                                  "github.com:99999", "github.com:", "a]b.github.com"])
def test_each_github_com_host_names_the_repo(host):
    c, d = run(f"gh pr comment https://{host}/pub/repo/pull/9 -R myorg/app -b $'Note\\nF'", REVIEW)
    assert c.target == "pub/repo#9" and "myorg/app#9" in c.alt_targets, c
    assert (d.outcome, d.exposure) == ("ask", "public"), d


# A host gh treats as another instance: gh calls that host's API (ghinstance.IsEnterprise, a ghe.com tenancy,
# garage.github.com), or the host only looks like GitHub's. The URL's repo there is another target, as a `-R` on
# another instance is, never pub/repo on github.com. So a deny on pub/* does not fire, and the line asks as public.
@pytest.mark.parametrize("host", ["ghe.corp", "x.ghe.com", "garage.github.com", "github.com.", "api.github.com.",
                                  "github.com.evil.io", "evilgithub.com", "github.localhost", "api.github.localhost"])
def test_a_url_on_another_instance_is_another_target(host):
    pol = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}, "deny": ["target:pub/*"]},
                              "posting": {"footer": "F"}})
    c, d = run(f"gh pr comment https://{host}/pub/repo/pull/9 -R myorg/app -b $'Note\\nF'", pol)
    other = f"{host.lower()}/pub/repo#9"
    assert (c.target, c.alt_targets) == ("myorg/app", [other]), c
    assert (d.outcome, d.exposure) == ("ask", "public"), d


def test_a_repo_operand_on_another_instance_is_another_target():
    for line, other in [("gh repo delete ghe.corp/myorg/app --yes", "ghe.corp/myorg/app"),
                        ("gh repo delete https://ghe.corp/myorg/app --yes", "ghe.corp/myorg/app"),
                        ("gh issue close https://x.ghe.com/pub/repo/issues/3 -R myorg/app", "x.ghe.com/pub/repo#3"),
                        ("gh issue close https://x.ghe.com/pub/repo/pull/3 -R myorg/app", "x.ghe.com/pub/repo#3"),
                        ("gh pr close https://ghe.corp/pub/repo/pull/3 -R myorg/app", "ghe.corp/pub/repo#3"),
                        ("gh pr comment https://[::1]:8/pub/x/pull/9 -R myorg/app -b x", "::1/pub/x#9")]:
        c, d = run(line)
        assert other in c.alt_targets, (line, c)
        assert d.exposure == "public", (line, d)


# Round 7: gh label clone reads its source with ghrepo.FromFullName, so a source on another instance is a repo the
# scopes may not mean. The line no longer reads as private only.
@pytest.mark.parametrize("source", ["ghe.corp/pub/x", "https://ghe.corp/pub/x", f"git{GIT_AT[3:4]}ghe.corp:pub/x.git"])
def test_a_label_clone_source_on_another_instance_is_another_target(source):
    pol = normalize_policies({"gates": {"scopes": {"private": ["myorg/*"]}}})
    c, d = run(f"gh label clone {source} -R myorg/app", pol)
    assert (c.target, c.alt_targets) == ("myorg/app", ["ghe.corp/pub/x"]), c
    assert d.exposure != "private", d


# Round 8: gh reads `-R` and gh repo's first word with ghrepo.FromFullName. For a URL, go-gh's RepoInfoFromURL drops
# a leading `www.` and lowers the host, then ghrepo.NewWithHost does both again. So `WWW.garage.github.com` and
# `www.www.garage.github.com` are garage.github.com there, another instance. The gate read them with one strip, as
# pub/x on github.com. A pull request URL's host gets one strip (finder.go's ParseURL), so there they stay github.com.
@pytest.mark.parametrize("word", ["https://WWW.garage.github.com/pub/x", "https://www.www.garage.github.com/pub/x",
                                  "https://www.WWW.garage.github.com/pub/x", f"git{GIT_AT[3:4]}WWW.garage.github.com:pub/x.git"])
def test_a_repo_word_drops_www_twice_as_gh_does(word):
    c, d = run(f"gh repo delete {word} --yes", DENY_PUB)
    assert (c.target, c.alt_targets) == ("", ["garage.github.com/pub/x"]), c
    assert (d.outcome, d.exposure) == ("ask", "public"), d
    c, _ = run(f"gh pr comment 5 -R {word} -b x", DENY_PUB)
    assert c.target == "pub/x#5" and f"{word}#5" in c.alt_targets, c


# Round 9: gh repo fork reads an `http:/`, `https:/` or `git@` value, and gh repo clone any value with a `:`, with
# ghrepo.FromURL, which drops a leading `www.` once (fork.go and clone.go in gh 2.89). So these hosts are
# www.garage.github.com there, which gh sends to github.com, and gh forks or clones github.com's pub/x. Round 8 read
# them as garage.github.com, and a deny on pub/* no longer fired. gh repo clone keeps a path's first two parts.
@pytest.mark.parametrize("line", ["gh repo fork https://www.www.garage.github.com/pub/x",
                                  "gh repo fork https://WWW.garage.github.com/pub/x",
                                  f"gh repo fork git{GIT_AT[3:4]}www.www.garage.github.com:pub/x",
                                  f"gh repo fork git{GIT_AT[3:4]}WWW.garage.github.com:pub/x.git",
                                  "gh repo clone https://www.www.garage.github.com/pub/x",
                                  "gh repo clone https://WWW.garage.github.com/pub/x.git",
                                  f"gh repo clone git{GIT_AT[3:4]}www.www.garage.github.com:pub/x",
                                  f"gh repo clone ssh://git{GIT_AT[3:4]}WWW.garage.github.com/pub/x.git",
                                  "gh repo clone git+ssh://www.www.garage.github.com/pub/x",
                                  "gh repo clone ftp://WWW.garage.github.com/pub/x",
                                  "gh repo clone www.www.garage.github.com:pub/x",
                                  f"gh repo clone git{GIT_AT[3:4]}api.github.com:pub/x/tree/main",
                                  "gh repo clone api.github.com:pub/x"])
def test_fork_and_clone_drop_a_url_s_www_once(line):
    c, d = run(line, DENY_PUB)
    assert c.target == "pub/x", (line, c)
    assert d.outcome == "deny", (line, d)


# A value gh repo fork reads with ghrepo.FromFullName (another scheme) drops it twice, and garage.github.com itself
# stays another instance for both, so the deny on pub/* does not fire.
@pytest.mark.parametrize("line", ["gh repo fork https://garage.github.com/pub/x",
                                  "gh repo fork ssh://www.www.garage.github.com/pub/x",
                                  "gh repo fork git+https://WWW.garage.github.com/pub/x",
                                  "gh repo clone https://garage.github.com/pub/x",
                                  "gh repo clone garage.github.com:pub/x"])
def test_fork_and_clone_keep_garage_github_com_another_instance(line):
    c, d = run(line, DENY_PUB)
    assert (c.target, c.alt_targets) == ("", ["garage.github.com/pub/x"]), (line, c)
    assert d.outcome != "deny", (line, d)


@pytest.mark.parametrize("line,target", [("gh repo delete https://www.www.github.com/pub/x --yes", "pub/x"),
                                         ("gh repo delete https://WWW.github.com/pub/x --yes", "pub/x"),
                                         ("gh pr comment https://WWW.garage.github.com/pub/x/pull/5 -b x", "pub/x#5")])
def test_a_host_that_stays_github_com_after_its_www_strips_names_the_repo(line, target):
    c, d = run(line, DENY_PUB)
    assert c.target == target, c
    assert d.outcome == "deny", (line, d)


# Round 8: gh gist takes a gist's URL or id, never a repo (GistIDFromURL takes the id from the path), so a gist URL
# names no repo. Round 6 read `https://gist.github.com/pub/abc` as the repo pub/abc, since its host ends in
# `.github.com`. A URL that main read (`https://github.com/pub/abc`) keeps that reading.
@pytest.mark.parametrize("line", ["gh gist delete https://gist.github.com/pub/abc --yes",
                                  "gh gist view https://gist.github.com/pub/abc",
                                  "gh gist edit https://gist.github.com/pub/abc -a f.txt",
                                  "gh gist rename https://gist.github.com/pub/abc a.txt b.txt",
                                  "gh gist clone https://gist.github.com/pub/abc"])
def test_a_gist_url_names_no_repo(line):
    c, d = run(line, DENY_PUB)
    assert (c.target, c.alt_targets) == ("", []), (line, c)
    assert d.outcome != "deny", (line, d)


def test_a_gist_url_keeps_the_reading_before():
    c, d = run("gh gist view https://github.com/pub/abc", DENY_PUB)
    assert (c.target, c.alt_targets, d.outcome) == ("", ["pub/abc"], "deny"), (c, d)


# Round 7: gh reads a repo from an operand only in a few places: a pull request URL in gh pr (ParseURL), an issue or
# pull request URL in gh issue (the issue lookup), and the first word of gh repo and of gh label clone
# (ghrepo.FromFullName). Another URL on another host names no target, also as an option's value that the reading
# with every option a flag takes for an operand. These lines were allow and private on main and log and internal
# at round 6, each with a target such as `hooks.example.com/a/b`.
@pytest.mark.parametrize("line", ["gh variable set X -R myorg/app --body https://hooks.example.com/a/b",
                                  "gh repo edit myorg/app --homepage https://myorg.github.io/app/docs",
                                  "gh repo edit myorg/app --description https://docs.corp.io/a/b",
                                  "gh label create bug -R myorg/app --description https://example.com/a/b",
                                  "gh pr close https://x.ghe.com/pub/repo/issues/3 -R myorg/app",
                                  "gh pr comment https://ghe.corp/pub/repo -R myorg/app -b x",
                                  "gh label clone pub/x -R myorg/app"])
def test_a_url_where_gh_reads_no_repo_is_no_target(line):
    c, d = run(line, normalize_policies({"gates": {"scopes": {"private": ["myorg/*"]}}}))
    assert (c.target, c.alt_targets) == ("myorg/app", []), (line, c)
    assert (d.outcome, d.exposure) == ("allow", "private"), (line, d)


# Round 5, item 4: one test for each safety mutant the review found alive.
USERINFO = "u:p" + "@github.com"     # split, as GIT_AT is, so the privacy check reads no address


@pytest.mark.parametrize("line", [
    f"gh pr comment 5 -R https://{USERINFO}/pub/repo -b x",       # a user and a password before the host
    f"gh pr comment 5 -R ssh://{GIT_AT}:22/pub/repo -b x",         # a port after it
    "gh pr comment 5 -R https://github.com/pub/repo/ -b x",        # a slash after the name
    "gh pr comment 5 -R git://github.com/pub/repo -b x",           # the git scheme
])
def test_r_as_a_url_gh_reads_names_its_repo(line):
    c, d = run(line, DENY_PUB)
    assert c.target == "pub/repo#5", c
    assert d.outcome == "deny", (line, d)


def test_the_reading_before_keeps_the_url_as_written():
    # The URL reads as gh reads it, and the reading before #399 keeps _GH_URL as it was. _GH_URL stops at a `%`, so
    # main read this line on `pub/re`, and a rule on that still fires.
    c, d = run("gh pr comment https://github.com/pub/re%70o/pull/5 -b x",
               normalize_policies({"gates": {"rules": [{"match": {"target": "pub/re"}, "decision": "deny"}]}}))
    assert (c.target, c.alt_targets, d.outcome) == ("pub/repo#5", ["pub/re"], "deny"), (c, d)
    # _GH_URL reads no capitals, so main read this one on the current repo, which no scope makes internal.
    c, d = run("gh pr comment HTTPS://GitHub.com/pub/repo/pull/5 -b x",
               normalize_policies({"gates": {"scopes": {"internal": ["pub/*"]}}}))
    assert (c.target, c.alt_targets, d.exposure) == ("pub/repo#5", [""], "public"), (c, d)


@pytest.mark.parametrize("field", ["-f body=x", "-F body=x", "--field body=x", "--raw-field body=x"])
def test_gh_api_reads_a_field_before_its_endpoint(field):
    # The field option takes the next word, so `body=x` is not the endpoint and the word after it is.
    c, d = run(f"gh api {field} repos/pub/repo/issues/5/comments")
    assert (c.category, c.target, c.alt_targets, c.text) == ("github_post", "pub/repo", [], "x"), c
    assert d.exposure == "public", d


@pytest.mark.parametrize("line", ["gh api -f body=see/x repos/pub/repo/issues/5/comments",
                                  "gh api -HAccept:application/json repos/pub/repo/issues -f body=x"])
def test_gh_api_s_word_from_before_skips_an_option_and_a_field(line):
    # Before #399 the endpoint was the first word with a `/`, no `=` and no leading `-`: here the operand too.
    c, _ = run(line)
    assert (c.target, c.alt_targets) == ("pub/repo", []), c


def test_an_ssh_hop_gives_its_host_to_the_copies_on_other_targets_only():
    # `echo hi` has no target on the remote host, as on main. Only the copy of the gh read on its empty other target
    # takes the host.
    c, _ = run("ssh buildhost 'gh repo view --web myorg/app; echo hi'")
    assert [(p.category, p.target) for p in c.parts if p.alt_copy] == [("read", "buildhost")], c
    assert ("read", "") in [(p.category, p.target) for p in c.parts if not p.alt_copy], c


def test_two_posts_that_share_a_body_file_keep_the_second_unseen():
    # A caller reads the line's own post's body file (fill_body). The text reaches that post's copies on its other
    # targets, never another post on the line, which stays unseen as on main.
    c = classify(sh(f"gh pr comment {PUB} -R myorg/app -F /tmp/b.md && gh issue comment 6 -R myorg/app -F /tmp/b.md"))
    assert c.alt_targets and c.text_file == "/tmp/b.md", c
    fill_body(c, "A note.\nF")
    assert all(p.text for p in c.parts if p.alt_copy), c
    assert body_unseen(c), c


def test_the_card_redacts_a_user_and_password_in_another_target():
    secret = "u:tok" + "@ghe.example.com"
    c, d = run(f"gh pr comment 5 -R https://{secret}/pub/repo -b x")
    assert f"https://{secret}/pub/repo#5" in c.alt_targets, c
    card = summarize(c, d)
    assert "It may land on https://***@ghe.example.com/pub/repo#5 instead." in card and "tok" not in card, card


# #406 names a post's violations, and #191 a denied part, after that post. A post's copy on another target lists
# none of the post's own violations again, and a denied copy is named after the URL as written, with its user and
# password hidden. Neither shows in the reason, the violations, the card or the ledger views.
@pytest.mark.parametrize("policy, named", [
    ({"posting": POST}, "missing footer: F"),
    ({"gates": {"rules": [{"match": {"target": "*ghe.example.com*"}, "decision": "deny", "reason": "no ghe"}]}},
     "GitHub PR comment on https://***@ghe.example.com/pub/repo#5"),
])
def test_a_post_on_another_target_is_named_without_its_user_and_password(policy, named):
    secret = "u:tok" + "@ghe.example.com"
    c, d = run(f"gh pr comment 5 -R https://{secret}/pub/repo -b x", normalize_policies(policy))
    assert named in d.reason + "; ".join(d.violations), d
    assert "posting" not in policy or d.violations == ["missing footer: F"], d.violations
    for text in (d.reason, *d.violations, summarize(c, d), str(d.as_dict()), str(c.as_dict())):
        assert "tok" not in text, text


# Round 10: each copy of a post repeated the post's violations, so a post with many of them pushed another post's
# violation past the twenty listed, where main listed it. A copy lists only what neither its post nor another copy of
# it lists, right after its post, and a line with copies is not cut, so the line lists what main lists.
@pytest.mark.parametrize("line, names", [
    ("gh pr comment https://api.github.com/pub/repo/pull/9 -R myorg/app -b 'N0X N1X N2X N3X N4X N5X'", 6),
    ("gh pr comment 5 -R https://ghe.example.com/pub/repo -b 'N0X N1X N2X N3X N4X N5X N6X N7X N8X'", 9),
])
def test_copies_of_a_post_do_not_push_another_post_out_of_the_list(line, names):
    pol = normalize_policies({"posting": {"footer": "F", "scrub": {"names": [f"N{i}X" for i in range(names)]
                                                                            + ["ZZOTHER"]}}})
    c, d = run(f"{line} && gh pr comment 7 -R pub/other -b 'ZZOTHER F'", pol)
    assert c.alt_targets and d.outcome == "ask", (c, d)
    assert "GitHub PR comment on pub/other#7: internal name: ZZOTHER" in d.violations, d.violations
    assert len(d.violations) == names + 2 and not any(v.startswith("and ") for v in d.violations), d.violations


def test_a_copys_own_violations_are_listed_and_nothing_is_cut():
    # A copy that finds more than its post (here its text is given ten more names) lists that right after its post,
    # and a line with copies lists every violation, so the other post's is there too.
    pol = normalize_policies({"posting": {"footer": "F", "scrub": {
        "names": [f"N{i}X" for i in range(9)] + [f"M{i}X" for i in range(10)] + ["ZZOTHER"]}}})
    c = classify(sh("gh pr comment 5 -R https://ghe.example.com/pub/repo -b '" + " ".join(f"N{i}X" for i in range(9))
                    + "' && gh pr comment 7 -R pub/other -b 'ZZOTHER F'"))
    copy = next(p for p in _each_part(c) if p.alt_copy)
    copy.text += " " + " ".join(f"M{i}X" for i in range(10))
    v = decide(c, pol).violations
    assert "GitHub PR comment on pub/other#7: internal name: ZZOTHER" in v and len(v) == 21, v
    assert all(any(f"M{i}X" in x for x in v) for i in range(10)) and not any(x.startswith("and ") for x in v), v


# Round 10, second pass: under a scope a post's copy can find what its post does not (main read the post on the copy's
# target, a public one). The line lists that, and nothing is cut, whatever target main read each post on.
NAMES13 = " ".join(f"N{i}X" for i in range(1, 12)) + " ZZOTHER YYTHIRD"
SCOPED = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}}, "posting": {"footer": "F", "scrub": {
    "names": [f"N{i}X" for i in range(12)] + ["ZZOTHER", "YYTHIRD"]}}})


@pytest.mark.parametrize("post, text", [
    ("gh pr comment 5 -R https://ghe.example.com/myorg/repo -b 'N0X F'", "internal name: N0X"),
    ("gh pr comment https://api.github.com/pub/repo/pull/9 -R myorg/app -b 'N0X'", "internal name: N0X"),
    ("gh pr comment \"$U\" -R myorg/app -b 'N0X F'", "internal name: N0X"),
])
def test_a_line_with_copies_lists_what_main_lists_under_a_scope(post, text):
    c, d = run(f"gh pr comment 1 -R pub/a -b 'F' && {post} && gh pr comment 2 -R pub/r -b '{NAMES13}'"
               f" && gh pr comment 3 -R pub/s -b '{NAMES13}'", SCOPED)
    v = d.violations
    assert any(x.endswith(text) for x in v), v
    for n in NAMES13.split():
        for post_name in ("pub/r#2", "pub/s#3"):
            assert f"GitHub PR comment on {post_name}: internal name: {n}" in v, (n, post_name, v)
    assert not any(x.startswith("and ") for x in v), v


def test_two_copies_of_a_post_list_a_finding_once():
    # The post lands on pub/repo#9, internal here, and its two copies (myorg/app#9 and myorg/app) are public: each
    # finds SECRETX, and the second lists none of what the first did.
    pol = normalize_policies({"gates": {"scopes": {"internal": ["pub/*"]}},
                              "posting": {"footer": "F", "scrub": {"names": ["SECRETX"]}}})
    c, d = run("gh pr comment 1 -R pub/a -b F && gh pr comment https://GitHub.com:443/pub/repo/pull/9 -R myorg/app"
               " -b 'SECRETX F'", pol)
    assert sum(p.alt_copy for p in _each_part(c)) == 2, c
    assert sum("SECRETX" in v for v in d.violations) == 1, d.violations


# A text two posts of one name both meet is listed twice, as main lists it, also when decide merges the line's
# readings (another target, or the line as read before).
@pytest.mark.parametrize("post", [
    "gh -R pub/repo pr comment 5 -b {}",
    "gh pr comment 5 -R https://ghe.example.com/pub/repo -b {}",
])
def test_a_text_two_posts_of_one_name_meet_is_listed_twice(post):
    c, d = run("; ".join(post.format(b) for b in "abc"), normalize_policies({"posting": {"footer": "-- bot"}}))
    assert c.floor is not None or c.alt_targets, c
    assert d.violations.count("GitHub PR comment on pub/repo#5: missing footer: -- bot") == 2, d.violations


# gh api's method reads as pflag reads it (round 5, item 5): a cluster ends at `-X` (`-iX DELETE`, `-iXPUT`) and gh
# keeps the last of a repeated `-X` or `--method`. The gate read the first `-X` word as written, so these were GETs.
# Where the two readings differ the line also has the reading before (floor), and the stricter decision stands.
DENY_DELETE = normalize_policies({"gates": {"deny": [{"category": "delete"}]}})


@pytest.mark.parametrize("line,category,method", [
    ("gh api -iX DELETE repos/pub/repo", "delete", "DELETE"),
    ("gh api -iXDELETE repos/pub/repo", "delete", "DELETE"),
    ("gh api -iXPUT repos/pub/repo/pulls/5/merge", "github_post", "PUT"),
    ("gh api -X=DELETE repos/pub/repo", "delete", "DELETE"),
    ("gh api -X GET -X DELETE repos/pub/repo", "delete", "DELETE"),
    ("gh api --method GET --method=DELETE repos/pub/repo", "delete", "DELETE"),
    ("gh api -X GET --method DELETE repos/pub/repo", "delete", "DELETE"),
])
def test_gh_api_reads_its_method_as_gh_does(line, category, method):
    c, d = run(line, DENY_DELETE)
    assert (c.category, c.target) == (category, "pub/repo") and c.summary.startswith(f"gh api {method} "), c
    assert c.floor is not None, c
    if category == "delete":
        assert d.outcome == "deny", (line, d)


@pytest.mark.parametrize("line,before", [
    ("gh api -X DELETE -X GET repos/pub/repo", "delete"),
    ("gh api -X DELETE --method GET repos/pub/repo", "delete"),
    ("gh api --jq -X repos/pub/repo", "github_post"),           # `-X` is the jq query, read before as `-X repos/...`
])
def test_gh_api_s_method_as_read_before_still_counts(line, before):
    # gh sends a GET, but the gate read another method before, so that reading still decides, as on main.
    c, d = run(line, DENY_DELETE)
    assert (c.category, c.floor.category) == ("read", before), c
    assert decide(c, DENY_DELETE).outcome == decide(c.floor, DENY_DELETE).outcome, (line, d)
    assert d.exposure == "public", (line, d)


@pytest.mark.parametrize("line", ["gh api -X DELETE repos/pub/repo", "gh api --method=PUT repos/pub/repo/pulls/5/merge",
                                  "gh api repos/pub/repo/issues -f title=x", "gh api -i -X GET repos/pub/repo"])
def test_gh_api_s_method_read_one_way_has_no_floor(line):
    c, _ = run(line)
    assert c.floor is None, c


# Round 5, item 6: `-R` as a URL reads its path decoded, as Go's url.Parse gives it to ghrepo.FromURL. Round 7: url.Parse
# leaves a query and a fragment out of the path, decodes the host, and takes any port of digits.
@pytest.mark.parametrize("value", ["https://github.com/pub/re%70o", "https://github.com/pub%2Frepo",
                                   f"ssh://{GIT_AT}/pub/re%70o.git", "'https://github.com/pub/repo?tab=x'",
                                   "'https://github.com/pub/repo#x'", "https://g%C4%B0thub.com/pub/repo",
                                   "https://github.com:99999/pub/repo", f"ssh://{GIT_AT}:99999/pub/repo.git"])
def test_r_as_a_url_reads_its_path_decoded(value):
    c, d = run(f"gh pr comment 5 -R {value} -b x", DENY_PUB)
    assert c.target == "pub/repo#5", c
    assert d.outcome == "deny", d


def test_a_body_unseen_in_the_line_as_read_before_counts():
    # gh sends a GET, but the gate read a PATCH with a field from a file before: the line stays unseen, as on main.
    c, d = run("gh api --method PATCH repos/pub/repo/issues/5 -X GET -f body=@notes.md")
    assert c.category == "read" and c.floor.category == "github_post", c
    assert body_unseen(c) and body_unseen(c.floor), c
    assert d.outcome == "ask" and UNSEEN_BODY in d.violations, d


# #399 with #394: the line is decided on each other target through the whole of decide, the own text pass too. On
# myorg/app#5 the `^git status` rule matches the line but does not name the gh post, so it is passed over there,
# and the line asks for that target. A rule there that names the post allows the line, as on pub/repo#5.
@pytest.mark.parametrize("named,outcome", [("^git status", "ask"), ("gh pr comment", "allow")])
def test_each_other_target_gets_the_own_text_pass(named, outcome):
    pol = normalize_policies({"gates": {"escalate": {"third_party": False}, "rules": [
        {"match": {"command": "gh pr comment", "target": "pub/*"}, "decision": "allow"},
        {"match": {"command": named, "target": "myorg/*"}, "decision": "allow"}]}})
    c, d = run(f"git status; gh pr comment {PUB} -R myorg/app -b hi", pol)
    assert c.target == "pub/repo#5" and c.alt_targets == ["myorg/app#5"], c
    assert decide(replace(c, alt_targets=[]), pol).outcome == "allow"
    assert decide(replace(c, alt_targets=[], target="myorg/app#5"), pol).outcome == outcome
    assert d.outcome == outcome, d
