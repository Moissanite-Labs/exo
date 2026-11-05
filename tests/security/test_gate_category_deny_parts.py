"""A category line or rule that denies any simple command on a line denies the line (#191, D-075 amendment).

`classify_shell` gives a compound line the category of its most severe part, and `decide` looked up only that
category in `categories` and matched rules only against it. D-075 made a `deny:` entry match every part; a
`categories: {X: deny}` line or a rule with `decision: deny` was still lost when another part outranked X.
Classification and decision only; nothing is executed.
"""
import pytest

from exo import gate
from exo.gate import classify, decide, normalize_policies


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def category_deny(cat, **more):
    return normalize_policies({"gates": {"categories": {cat: "deny", **more}}})


def rule_deny(cat):
    return normalize_policies({"gates": {"rules": [{"match": {"category": cat}, "decision": "deny",
                                                     "reason": f"no {cat}"}]}})


# The issue's rows (main: allow and log under the category form), the #189 subscript row and a plain sequence.
ROWS = [
    "echo $(sudo ls) > /tmp/f",
    "echo $(git push origin main) > /tmp/f",
    "printf -v 'a[$(sudo ls)]' %s x > /tmp/f",
    "sudo ls; echo hi > /tmp/f",
]


@pytest.mark.parametrize("line", ROWS)
def test_category_deny_on_an_outranked_part_denies_the_line(line):
    c = classify(sh(line))
    assert c.category != "local_write", c                       # another part sets the line's category
    for pol in (category_deny("local_write"), category_deny("local_write", opaque_exec="allow")):
        d = decide(c, pol)
        assert (d.outcome, d.rule) == ("deny", "category"), (line, d)
        assert d.reason == "denied by policy (category local_write: write /tmp/f)", d.reason   # names the part


@pytest.mark.parametrize("line", ROWS)
def test_rule_deny_on_an_outranked_part_denies_the_line(line):
    d = decide(classify(sh(line)), rule_deny("local_write"))
    assert (d.outcome, d.rule) == ("deny", "rule") and d.reason.startswith("denied by policy (no local_write: "), d


@pytest.mark.parametrize("line", ROWS)
def test_deny_list_form_is_unchanged(line):
    d = decide(classify(sh(line)), normalize_policies({"gates": {"deny": ["local_write"]}}))
    assert (d.outcome, d.rule, d.reason) == ("deny", "deny", "denied by policy (local_write)")


def test_the_line_s_own_category_keeps_its_reason():
    d = decide(classify(sh("echo hi > /tmp/f")), category_deny("local_write"))
    assert (d.outcome, d.rule, d.reason) == ("deny", "category", "category local_write")


def test_a_hidden_push_behind_a_delete_is_denied():
    c = classify(sh("git push origin main && rm -rf build"))
    assert c.category == "delete"
    assert decide(c, category_deny("vcs_push")).outcome == "deny"
    assert decide(c, rule_deny("vcs_push")).outcome == "deny"


def test_a_part_is_decided_as_it_would_be_alone():
    # The first rule that matches the part settles it, as it would for that command alone: an allow rule on the
    # write's target lowers the category line for that part, so the part does not deny the line.
    pol = normalize_policies({"gates": {"categories": {"local_write": "deny"},
                                        "rules": [{"match": {"target": "/tmp/*"}, "decision": "allow", "reason": "tmp"}]}})
    assert decide(classify(sh("echo hi > /tmp/f")), pol).outcome == "allow"
    assert decide(classify(sh("echo $(git push origin main) > /tmp/f")), pol).outcome == "log"
    assert decide(classify(sh("echo $(git push origin main) > /var/f")), pol).outcome == "deny"


def test_a_command_deny_rule_sees_the_decoded_line_for_a_part():
    # #136: `g''it` reads as `git` in the literal-word view, for a part as for the line.
    pol = normalize_policies({"gates": {"rules": [{"match": {"category": "vcs_push", "command": r"git push"},
                                                   "decision": "deny", "reason": "no pushes"}]}})
    c = classify(sh("g''it push origin main && rm -rf build"))
    assert c.category == "delete"
    d = decide(c, pol)
    assert (d.outcome, d.rule) == ("deny", "rule") and "no pushes" in d.reason, d


def test_lower_category_lines_still_see_only_the_dominant_part():
    # Only a deny reaches the parts; allow, log and ask lines keep deciding by the dominant part (D-075).
    c = classify(sh("echo $(git push origin main) > /tmp/f"))
    assert decide(c, normalize_policies({"gates": {"categories": {"local_write": "allow"}}})).outcome == "log"
    assert decide(c, normalize_policies({})).outcome == "log"


def test_opaque_exec_deny_reaches_a_wrapper_part():
    # GATE.md listed this as a #191 limit: a wrapped push took the line's category, so a deny on opaque_exec asked.
    c = classify(sh("setsid git push origin main"))
    assert c.category == "vcs_push"
    assert decide(c, category_deny("opaque_exec")).outcome == "deny"
    assert decide(c, rule_deny("opaque_exec")).outcome == "deny"


# #303 (#207, #240) reads the program a coproc name runs and the shell text fed to `ssh host`. The line then takes the
# category of what runs, and the old dominant reading stays a part, so a category line or rule that denies it still
# denies the line.
@pytest.mark.parametrize("line", [
    "coproc rm >/dev/null { -rf x; }",
    "coproc 2>/dev/null rm { -rf x; }",
    "coproc rm <<<x while true; do :; done",
])
def test_a_coproc_that_runs_its_name_is_denied_under_an_opaque_exec_deny(line):
    c = classify(sh(line))
    assert c.category == "delete", c
    assert decide(c, category_deny("opaque_exec")).outcome == "deny"
    assert decide(c, rule_deny("opaque_exec")).outcome == "deny"


HOST_RULE = normalize_policies({"gates": {"rules": [{"match": {"target": "prod-db"}, "decision": "deny",
                                                      "reason": "no prod-db"}]}})


@pytest.mark.parametrize("line", [
    "echo 'rm -rf /data' | ssh prod-db",
    "ssh prod-db <<'EOF'\nrm -rf /data\nEOF",
    "ssh prod-db <<< 'rm -rf /data'",
    "ssh prod-db -T <<< 'rm -rf /data'",
    "ssh prod-db '' <<< 'rm -rf /data'",
    "ssh prod-db -- <<< 'ls'",
    "generate | ssh prod-db",
])
def test_a_shell_fed_to_ssh_is_denied_under_a_shell_or_host_deny(line):
    c = classify(sh(line))
    assert c.category != "shell", c                         # what runs on the host sets the line's category
    assert decide(c, category_deny("shell")).outcome == "deny"
    d = decide(c, HOST_RULE)
    assert d.outcome == "deny" and "no prod-db" in d.reason, d


# A leading assignment quoted whole in a -S text hid the command after it (#591): env splits the text into words
# and reads them as its own argv, so a word quoted whole with the form NAME=value is an assignment and the next
# word starts the command. main read the text as one shell line, whose first word was an unknown program, so the
# command behind it was never read. main's decisions: allow under the allow line below, log for the push row.
ENV_S = [
    "env -S \"'X=a b' rm -rf /srv/data\"",
    "sudo env -S \"'X=a b' rm -rf /srv/data\"",
    "env -S '\"X=a b\" rm -rf /srv/data'",
    "env -S \"-i 'X=a b' rm -rf /srv/data\"",
    "env --split-string=\"'X=a b' rm -rf /srv/data\"",
    "env -S \"-i --null 'X=a b' rm -rf /srv/data\"",
    "sudo env -S \"X=a 'Y=2' rm -rf /srv/data\"",
    "env -S \"'X=a b' 'Y=2 c' Z=3 rm -rf /srv/data\"",
]


@pytest.mark.parametrize("line", ENV_S)
def test_an_assignment_quoted_whole_in_a_split_text_no_longer_hides_the_command(line):
    c = classify(sh(line))
    assert any(p.category == "delete" for p in gate._each_part(c)), c
    assert decide(c, normalize_policies({"gates": {"deny": ["delete"]}})).outcome == "deny", c


@pytest.mark.parametrize("line", [
    "env -S \"'X=a b' rm -rf /srv/data\"",
    "git push origin feature && env -S \"'X=a b' git push --force origin main\"",
])
def test_the_command_behind_a_split_assignment_decides_when_the_shell_around_is_allowed(line):
    d = decide(classify(sh(line)), normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}}))
    assert d.outcome == "ask", (line, d)


@pytest.mark.parametrize("line", [
    "gh pr comment 5 -R myorg/app --body $'ok\\nF' && env -S \"'X=a b' gh pr comment 6 -R myorg/app --body 'Zork leaked'\"",
    "gh pr comment 5 -R myorg/app --body $'ok\\nF' && gh pr comment 6 -R myorg/app --body 'Zork leaked'",
])
def test_a_post_behind_a_split_assignment_decides_as_the_spelling_without_the_split(line):
    # posting.forbid_words is the key the policy reads (a `gates:` one is dropped): the hidden post trips it as the
    # spelling without the split does.
    pol = normalize_policies({"posting": {"forbid_words": ["Zork"]}})
    c = classify(sh(line))
    assert any("myorg/app#6" in p.summary for p in gate._each_part(c)), c
    d = decide(c, pol)
    assert d.outcome == "ask" and any("forbidden word: Zork" in v for v in d.violations), (line, d)


@pytest.mark.parametrize("line, expected", [
    # a command, or an unquoted assignment, first: read as the shell line reads it, as on main
    ("env -S \"nohup rm -rf /srv/data\"", "deny"),
    ("env -S \"X=a rm -rf /srv/data\"", "deny"),
    ("env -S \"'X=a b' && rm -rf /srv/data\"", "deny"),
    # an unknown program first, or one behind an assignment: its arguments run nothing
    ("env -S \"frobnicate rm -rf /srv/data\"", "ask"),
    ("env -S \"X=1 frobnicate rm -rf /srv/data\"", "ask"),
    # after `--` env still reads a NAME=value as an assignment (`env -- prog= true` runs true), so rm runs: the
    # follow-up to #634 reads it, where #634 took the quoted word for the command and asked
    ("env -S \"-- 'X=a b' rm -rf /srv/data\"", "deny"),
])
def test_the_split_reading_keeps_its_old_decisions(line, expected):
    c = classify(sh(line))
    assert any(p.category == "delete" for p in gate._each_part(c)) == (expected == "deny"), c
    assert decide(c, normalize_policies({"gates": {"deny": ["delete"]}})).outcome == expected, (line, c)
