"""gh finds a subcommand past the options before it, and so does the gate (#399 review).

gh (cobra) skips the options between a group and its subcommand: `gh pr -R pub/repo comment 5 -b text` comments
on pub/repo#5, and so does `gh pr -b text comment 5 -R pub/repo`, since the subcommand parses every option on the
line (checked with gh 2.89). The gate took the third word for the subcommand, so these lines were `read` and ran
under any policy, a public post with a scrub hit too. Now the gate finds the subcommand as cobra does: an option
word without `=` that starts with `--`, or has two characters, takes the next word as its value, `--help` and
every other option word stand alone, and `--` ends the search. A word from an expansion where the subcommand or
an option may stand is code the gate cannot read. The line as the gate read it before is decided too, and the
stricter decision stands, so no line decides lower than it did. Classification and decision only; nothing is
executed.
"""
from dataclasses import replace

import pytest

from exo.gate import UNSEEN_BODY, binds_exact, body_unseen, classify, decide, is_outward, normalize_policies

POST = {"footer": "F", "scrub": {"names": ["SECRETX"]}}
POLICY = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}}, "posting": POST})


def run(line, policy=POLICY):
    c = classify({"tool": "bash", "args": {"command": line}})
    return c, decide(c, policy)


def same(line, canonical):
    """The line reads and decides as the same line with its options after the subcommand."""
    c, d = run(line)
    k, e = run(canonical)
    assert (c.category, c.target, c.text, c.title, c.alt_targets) == (k.category, k.target, k.text, k.title,
                                                                       k.alt_targets), (line, c, k)
    assert (d.outcome, d.exposure, d.third_party, d.violations) == (e.outcome, e.exposure, e.third_party,
                                                                    e.violations), (line, d, e)
    return c, d


# The review's lines: each read as `read` and ran under any policy.
@pytest.mark.parametrize("line,canonical", [
    ("gh pr -R pub/repo comment 5 -b $'Note SECRETX\\nF'", "gh pr comment -R pub/repo 5 -b $'Note SECRETX\\nF'"),
    ("gh pr --repo=pub/repo comment 5 -b $'Note SECRETX\\nF'", "gh pr comment --repo=pub/repo 5 -b $'Note SECRETX\\nF'"),
    ("gh pr --repo pub/repo comment 5 -b $'Note SECRETX\\nF'", "gh pr comment --repo pub/repo 5 -b $'Note SECRETX\\nF'"),
    ("gh pr -Rpub/repo comment 5 -b $'Note SECRETX\\nF'", "gh pr comment -Rpub/repo 5 -b $'Note SECRETX\\nF'"),
    ("gh issue -R pub/repo close 5", "gh issue close -R pub/repo 5"),
    ("gh pr -R pub/repo merge 5", "gh pr merge -R pub/repo 5"),
])
def test_repo_option_before_the_subcommand(line, canonical):
    c, d = same(line, canonical)
    assert c.category != "read" and c.target == "pub/repo#5", c
    if "SECRETX" in line:
        assert (d.outcome, d.exposure) == ("ask", "public") and "internal name: SECRETX" in d.violations, d


# Any option of the subcommand may come first, when gh can skip its value: the subcommand parses it.
@pytest.mark.parametrize("line,canonical", [
    ("gh pr -b $'Note SECRETX\\nF' comment 5 -R pub/repo", "gh pr comment -b $'Note SECRETX\\nF' 5 -R pub/repo"),
    ("gh pr --body $'Note SECRETX\\nF' comment 5 -R pub/repo", "gh pr comment --body $'Note SECRETX\\nF' 5 -R pub/repo"),
    ("gh pr -bSECRETX comment 5 -R pub/repo", "gh pr comment -bSECRETX 5 -R pub/repo"),
    ("gh pr --body=SECRETX comment 5 -R pub/repo", "gh pr comment --body=SECRETX 5 -R pub/repo"),
    ("gh pr -b x -R pub/repo comment 5", "gh pr comment -b x -R pub/repo 5"),
    ("gh issue -c SECRETX close 5 -R pub/repo", "gh issue close -c SECRETX 5 -R pub/repo"),
    ("gh release -n SECRETX create v1 -R pub/repo", "gh release create -n SECRETX v1 -R pub/repo"),
])
def test_a_subcommand_option_before_the_subcommand(line, canonical):
    c, d = same(line, canonical)
    assert c.category != "read" and c.target.startswith("pub/repo") and c.text, c
    assert d.outcome == "ask", d


# `--help` stands alone and `-` is skipped, as in cobra. With `--help` gh prints help; the gate reads the line as
# it reads `gh pr comment --help`.
@pytest.mark.parametrize("line,target", [
    ("gh pr --help comment 5 -R pub/repo -b x", "pub/repo#5"),
    ("gh pr - comment -R pub/repo -b x", "pub/repo"),
])
def test_words_gh_skips_alone(line, target):
    c, _ = run(line)
    assert (c.category, c.target) == ("github_post", target), c


# Where gh finds no subcommand it prints the group's help, and the gate keeps its reading.
@pytest.mark.parametrize("line", ["gh pr --help", "gh pr -R pub/repo", "gh pr -R pub/repo --", "gh pr -h"])
def test_no_subcommand(line):
    c, d = run(line)
    assert c.category == "read" and d.outcome == "allow", (line, c, d)


# A word from an expansion where the subcommand or an option may stand: the gate cannot tell what gh runs.
@pytest.mark.parametrize("line", ['gh pr "$V" 5 -b x', "gh pr $OPTS comment 5 -b x", 'gh issue -R pub/repo "$V" 5'])
def test_an_expansion_where_the_subcommand_may_be(line):
    c, d = run(line)
    assert c.category == "opaque_exec" and d.outcome == "ask", (line, c, d)


def test_an_expansion_as_an_options_value_is_only_that():
    c, d = run('gh pr -R "$R" comment 5 -b x')
    assert c.category == "github_post" and d.outcome == "ask", (c, d)


def test_the_line_as_written_still_counts():
    # The target the gate read before (-R anywhere) stays a part, so a deny on it still fires.
    pol = normalize_policies({"gates": {"deny": ["target:pub/*"]}})
    for line in ('gh pr "$V" 5 -R pub/repo', "gh pr -R pub/repo comment 5 -b x"):
        _, d = run(line, pol)
        assert d.outcome == "deny", (line, d)
    # gh auth and gh secret hold credentials whatever the subcommand.
    c, d = run('gh auth "$V"')
    assert c.category == "credentials" and d.outcome == "ask", (c, d)


# In a compound line the gh command found past its options may outrank another part, which a scope then lowers. The
# line as the gate read it before is decided too, and the stricter stands, so no line decides lower than it did.
@pytest.mark.parametrize("line,before,wins", [
    ("git push origin f && gh issue -R myorg/app edit 5 -X-c", "git push origin f", True),
    ("git push origin f && gh discussion --repo myorg/secret comment --reason=pub/repo 5 -", "git push origin f", True),
    ("rm -rf build; gh repo --repo myorg/secret delete", "rm -rf build", False),
])
def test_the_line_as_read_before_is_a_floor(line, before, wins):
    pol = normalize_policies({"gates": {"scopes": {"private": ["myorg/*"]}}})
    c, d = run(line, pol)
    _, alone = run(before, pol)
    assert c.floor is not None and c.floor.summary != c.summary, c
    assert (d.outcome, d.exposure) == (alone.outcome, alone.exposure), (d, alone)
    assert d.reason.endswith(f"(for {c.floor.summary})") == wins, d
    assert binds_exact(c, d) and "floor" not in c.as_dict(), c


def test_code_that_runs_such_a_line_gets_a_floor_too():
    code = 'import subprocess\nsubprocess.run("git push origin f && gh issue -R myorg/app edit 5", shell=True)'
    c = classify({"tool": "execute_code", "args": {"code": code}})
    assert c.floor is not None and c.floor.command == code, c


def test_a_line_with_no_moved_subcommand_has_no_floor():
    for line in ("gh pr comment 5 -R pub/repo -b x", "git push origin f && gh issue edit -R myorg/app 5", "gh pr --help"):
        assert classify({"tool": "bash", "args": {"command": line}}).floor is None, line


def test_commands_without_subcommands_are_untouched():
    c, _ = run("gh api -X POST repos/pub/repo/issues/5/comments -f body=x")
    assert (c.category, c.target) == ("github_post", "pub/repo"), c
    c, _ = run("gh browse -R pub/repo 5")
    assert c.category == "read", c


# cobra skips an empty word where it looks for the subcommand (stripFlags), so `gh pr "" comment` comments
# (checked with gh 2.89: `gh pr '' comment --help` prints the help of pr comment). Round 3, item 3.
@pytest.mark.parametrize("line,canonical", [
    ("gh pr \"\" comment -R pub/repo -b $'Note SECRETX\\nF'", "gh pr comment \"\" -R pub/repo -b $'Note SECRETX\\nF'"),
    ("gh issue '' '' close 5 -R pub/repo", "gh issue close '' '' 5 -R pub/repo"),
    ("gh pr -R pub/repo '' merge 5", "gh pr merge -R pub/repo '' 5"),
])
def test_an_empty_word_before_the_subcommand(line, canonical):
    c, d = same(line, canonical)
    assert c.category != "read" and c.target.startswith("pub/repo") and c.floor is not None, c


# gh's aliases for its subcommands (gh 2.89, the ALIASES of each command's --help): each reads and decides as the
# subcommand it names. Main read them as reads, so each also keeps that reading as its floor.
@pytest.mark.parametrize("line,canonical", [
    ("gh pr new -R pub/repo -t t -b $'x SECRETX\\nF'", "gh pr create -R pub/repo -t t -b $'x SECRETX\\nF'"),
    ("gh issue new -R pub/repo -t t -b $'x\\nF'", "gh issue create -R pub/repo -t t -b $'x\\nF'"),
    ("gh repo new myorg/app2 --public", "gh repo create myorg/app2 --public"),
    ("gh repo new myorg/app2 --private", "gh repo create myorg/app2 --private"),
    ("gh release new v1 -R pub/repo -n $'x\\nF'", "gh release create v1 -R pub/repo -n $'x\\nF'"),
    ("gh gist new f.txt", "gh gist create f.txt"),
    ("gh gist new --public f.txt", "gh gist create --public f.txt"),
    ("gh variable remove X -R pub/repo", "gh variable delete X -R pub/repo"),
    ("gh pr -R pub/repo new -t t -b x", "gh pr create -R pub/repo -t t -b x"),
])
def test_an_alias_is_the_subcommand_it_names(line, canonical):
    c, d = same(line, canonical)
    assert c.category != "read" and c.floor is not None and c.floor.category == "read", c
    assert binds_exact(c, d), c


@pytest.mark.parametrize("line,summary", [
    ("gh pr ls", "gh pr list"), ("gh pr co 5", "gh pr checkout"), ("gh issue ls -R pub/repo", "gh issue list"),
    ("gh secret remove X", "gh secret delete"), ("gh secret ls", "gh secret list"),
])
def test_an_alias_that_reads_or_holds_credentials(line, summary):
    c, d = run(line)
    assert c.summary == summary and c.category in ("read", "credentials"), c


# In a compound line the earlier reading's most severe part may be a post where the line's is a close. The line stays
# an outward act, as on main, so a caller that asks is_outward gets no lower answer.
def test_a_line_whose_earlier_reading_is_outward_stays_outward():
    c, d = run("gh issue -R myorg/app close 5 && gh pr comment 3 -R myorg/app -b ok")
    assert (c.category, c.floor.category) == ("github_close", "github_post"), c
    assert is_outward(c, d) and binds_exact(c, d), (c, d)


# `--` ends cobra's search for the subcommand: past it every word is an argument of the group, so gh finds no
# subcommand and prints help. The gate keeps the line as written (round 3, item 7).
@pytest.mark.parametrize("line", ["gh pr -- x comment 5", "gh pr -- x comment 5 -R pub/repo -b SECRETX"])
def test_the_end_of_options_ends_the_search(line):
    c, d = run(line)
    assert (c.category, c.floor, d.outcome) == ("read", None, "allow"), (line, c, d)


# A line with a floor binds its approval to the exact command, also when neither reading is an outward act and the
# line has no other target: `gh pr -R myorg/app merge 5` under a private scope is a merge, and was a read.
def test_a_line_with_a_floor_binds_exactly():
    c, d = run("gh pr -R myorg/app merge 5", normalize_policies({"gates": {"scopes": {"private": ["myorg/*"]}}}))
    assert (c.category, c.alt_targets, c.floor.category, d.exposure) == ("vcs_merge", [], "read", "private"), (c, d)
    assert not is_outward(c, d) and binds_exact(c, d), (c, d)


# cobra passes over an empty word before the command too: `gh '' pr comment --help` prints the help of pr comment
# (gh 2.89). So `gh '' pr comment` comments, and the gate reads it so.
@pytest.mark.parametrize("line,canonical", [
    ("gh '' pr comment 5 -R pub/repo -b $'Note SECRETX\\nF'", "gh pr comment '' 5 -R pub/repo -b $'Note SECRETX\\nF'"),
    ("gh '' '' issue '' close 5 -R pub/repo", "gh issue close '' '' '' 5 -R pub/repo"),
    ("gh '' pr new -R pub/repo -t t -b x", "gh pr create '' -R pub/repo -t t -b x"),
])
def test_an_empty_word_before_the_command(line, canonical):
    c, d = same(line, canonical)
    assert c.category != "read" and c.target.startswith("pub/repo") and c.floor is not None, c


def test_an_empty_word_before_a_command_without_subcommands():
    # gh api then takes the empty word for an operand beside the endpoint, and refuses two. The path still counts.
    c, _ = run("gh '' api -X POST repos/pub/repo/issues/5/comments -f body=x")
    assert c.category == "github_post" and "pub/repo" in [c.target] + c.alt_targets, c
    c, d = run("gh '' auth token")
    assert c.category == "credentials" and d.outcome == "ask", (c, d)



# #397's checks of a reopen's comment and a merge's subject hold in each reading: with the subcommand past an option,
# and on each other target. A repeated --subject is unseen there too.
ZORK = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                           "posting": {"footer": "F", "forbid_words": ["Zork"]}})


@pytest.mark.parametrize("line,canonical", [
    ("gh issue -R pub/repo reopen 5 --comment 'Zork leaked'", "gh issue reopen -R pub/repo 5 --comment 'Zork leaked'"),
    ("gh pr -c 'Zork leaked' reopen 5 -R pub/repo", "gh pr reopen -c 'Zork leaked' 5 -R pub/repo"),
    ("gh pr -R myorg/app merge 5 --subject 'Zork leaked'", "gh pr merge -R myorg/app 5 --subject 'Zork leaked'"),
    ("gh pr --subject 'Zork leaked' merge 5 -R myorg/app", "gh pr merge --subject 'Zork leaked' 5 -R myorg/app"),
])
def test_a_reopen_comment_and_a_merge_subject_are_checked_past_an_option(line, canonical):
    c, d = run(line, ZORK)
    k, e = run(canonical, ZORK)
    assert c.floor is not None and (c.category, c.target, c.text, c.title) == (k.category, k.target, k.text, k.title)
    assert "Zork leaked" in (c.text, c.title), c
    assert d.outcome == e.outcome == "ask" and "forbidden word: Zork" in d.violations, (d, e)


def test_a_merge_subject_repeated_is_unseen_on_each_target():
    c, d = run("gh pr merge https://github.com/pub/repo/pull/5 -R myorg/app --subject ok --subject 'Zork leaked'", ZORK)
    assert c.target == "pub/repo#5" and c.alt_targets == ["myorg/app#5"] and body_unseen(c), c
    for t in [c.target] + c.alt_targets:
        assert body_unseen(replace(c, target=t, alt_targets=[])), t
    assert d.outcome == "ask" and UNSEEN_BODY in d.violations, d


# Round 5, item 6: with an expansion where the subcommand may stand, the line as written is read past the empty
# words before the command, as gh passes over them, and the card names the command.
def test_an_empty_word_before_a_credentials_command_with_an_expansion():
    c, _ = run('gh "" secret "$V"')
    assert c.category == "credentials", c
    c, _ = run('gh "" "" auth "$V" x')
    assert c.category == "credentials", c
    c, _ = run('gh "" pr "$V" 5')
    assert c.category == "opaque_exec" and c.summary.startswith("gh pr with a word from an expansion"), c
