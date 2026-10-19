"""A category line or rule that denies any simple command on a line denies the line (#191, D-075 amendment).

`classify_shell` gives a compound line the category of its most severe part, and `decide` looked up only that
category in `categories` and matched rules only against it. D-075 made a `deny:` entry match every part; a
`categories: {X: deny}` line or a rule with `decision: deny` was still lost when another part outranked X.
Classification and decision only; nothing is executed.
"""
import pytest

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
