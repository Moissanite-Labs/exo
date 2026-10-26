"""#454: a write redirection on a sudo or doas command sets the line's category, as on any other command.

Bash opens the file of `sudo echo hi > /tmp/f` as the calling user, before sudo runs. On main 8bd175a0 the line was
`read`: sudo made the command's own `read` part costly, so it outranked the write part, which stayed reversible.
`categories: {local_write: ask}` then allowed the line, while `echo hi > /tmp/f` asked. Only a deny or reserved entry
reached the write part (#191). Now, where that read leads the line, the first sudo write takes its cost and leads
instead, so the line is a costly `local_write`. A part that outranks the read, such as an unknown program, still
leads. The line is also decided with the displaced part leading, as on main, and the stricter outcome stands, so a
rule on a gh read's repo or on the host of an ssh hop still reaches it.

Classification and decision only; nothing is executed.
"""
import json

import pytest

from exo import gate
from exo.gate import binds_exact, classify, decide, normalize_policies, summarize


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(command):
    return classify({"tool": "bash", "args": {"command": command}})


def pol(**gates):
    return normalize_policies({"gates": gates})


# The issue's rows: each was `read` on main, and allowed under `categories: {local_write: ask}`.
ISSUE_ROWS = ["sudo echo hi > /tmp/f", "sudo -u root echo hi > /tmp/f", "sudo echo hi >> /etc/hosts",
              "doas echo hi > /tmp/f"]
# sudo and doas with options, behind other prefixes, and every write redirection the gate reads.
WRAPPERS = ["sudo", "sudo -u root", "sudo -E", "sudo --", "sudo -u root --", "sudo -s", "sudo -n -H", "doas",
            "doas -u root", "env sudo", "timeout 5 sudo", "nice sudo", "sudo env", "sudo nice -n 5", "command sudo"]
REDIRS = ["> /tmp/f", ">> /tmp/f", ">| /tmp/f", "&> /tmp/f", "&>> /tmp/f", "2> /tmp/f", "2>> /tmp/f", ">& /tmp/f",
          "<> /tmp/f", "1> /tmp/f"]
LINES = ISSUE_ROWS + [f"{w} echo hi {r}" for w in WRAPPERS for r in REDIRS] + ["> /tmp/f sudo echo hi"]


@pytest.mark.parametrize("line", LINES)
def test_the_write_sets_the_category_and_sudo_keeps_it_costly(line):
    c = sh(line)
    assert (c.category, c.reversibility) == ("local_write", "costly"), (line, c)
    assert "sudo" in c.signals


@pytest.mark.parametrize("line", ISSUE_ROWS)
@pytest.mark.parametrize("level", ["ask", "log", "deny"])
def test_a_category_line_on_local_write_decides_the_line(line, level):
    d = decide(sh(line), pol(categories={"local_write": level}))
    assert (d.outcome, d.rule) == (level, "category"), d


@pytest.mark.parametrize("line", ISSUE_ROWS)
def test_an_escalation_on_local_write_asks(line):
    d = decide(sh(line), pol(escalate={"categories": ["local_write"]}))
    assert (d.outcome, d.rule) == ("ask", "escalate"), d


@pytest.mark.parametrize("line", ISSUE_ROWS)
def test_the_line_keeps_its_costly_matrix_cell(line):
    # The write does not lower the line: a costly ask cell still asks, while the bare write stays reversible.
    ask_costly = pol(matrix={"reversible": {"private": "allow"}, "costly": {"private": "ask"}})
    d = decide(sh(line), ask_costly)
    assert (d.outcome, d.rule) == ("ask", "matrix"), d
    assert decide(sh("echo hi > /tmp/f"), ask_costly).outcome == "allow"


@pytest.mark.parametrize("line", ISSUE_ROWS)
def test_a_deny_still_denies(line):
    # The parts stay as they were: sudo's costly read, and the write as on any command. A deny keyed on either still
    # reaches it (#191).
    c = sh(line)
    assert [(p.category, p.reversibility) for p in c.parts] == [("read", "costly"), ("local_write", "reversible")]
    assert decide(c, pol(deny=["local_write"])).outcome == "deny"
    for match in ({"category": "read", "reversibility": "costly"},
                  {"category": "local_write", "reversibility": "reversible"}):
        assert decide(c, pol(rules=[{"match": match, "decision": "deny", "reason": "no"}])).outcome == "deny", match


@pytest.mark.parametrize("program", ["echo hi", "git status", "cat x", "touch x", "cp a b", "python3 -V",
                                     "crontab -l", "curl https://x.example", "rm x", "git push origin main"])
def test_the_category_is_the_one_without_sudo(program):
    for wrapper in ("sudo", "doas"):
        assert sh(f"{wrapper} {program} > /tmp/f").category == sh(f"{program} > /tmp/f").category, program


@pytest.mark.parametrize("line", ["sudo echo hi > /tmp/f && ls", "ls; sudo echo hi > /tmp/f",
                                  "echo $(sudo echo hi > /tmp/f)", "(sudo echo hi > /tmp/f)",
                                  "if true; then sudo echo hi > /tmp/f; fi", "cd /tmp && sudo echo hi > f",
                                  "ssh host 'sudo echo hi > /tmp/f'",
                                  # #451 lowered these to the `read` of the bare line; they ask again
                                  "for x do sudo echo hi > /tmp/f; done", "for x do doas echo hi > /tmp/f; done"])
def test_compound_lines_take_the_write_too(line):
    c = sh(line)
    assert (c.category, c.reversibility) == ("local_write", "costly"), (line, c)
    assert decide(c, pol(categories={"local_write": "ask"})).outcome == "ask"


@pytest.mark.parametrize("line,category", [
    ("sudo echo hi > /tmp/f; foo", "opaque_exec"),     # an unknown program
    ("foo; sudo echo hi > /tmp/f", "opaque_exec"),
    ("while true; do sudo echo hi > /tmp/f; break; done", "opaque_exec"),
    ("sudo echo hi > /tmp/f; crontab f", "opaque_exec"),
    ("systemctl enable x; sudo echo hi > /tmp/f", "schedule"),
    ("sudo echo hi > /tmp/f; git push origin main", "vcs_push"),
    ("sudo echo hi > /tmp/f && rm -rf build", "delete"),
])
def test_a_part_that_led_the_line_still_leads(line, category):
    # The write leads with sudo's cost only where the command's own read led the line. A part that outranks that
    # read keeps the lead, also one that would tie with a costly write (an unknown program), so its ask stays.
    c = sh(line)
    assert c.category == category, (line, c)
    assert decide(c, pol()).outcome == decide(sh(line.replace("sudo ", "")), pol()).outcome, line


@pytest.mark.parametrize("line", ["bash -c 'sudo echo hi > /tmp/f'", "sh -c 'doas echo hi >> /tmp/f'"])
def test_a_shell_line_keeps_its_opaque_ask_and_reads_the_write(line):
    c = sh(line)
    assert c.category == "opaque_exec"
    assert ("local_write", "costly") in [(p.category, p.reversibility) for p in c.parts], (line, c)


@pytest.mark.parametrize("line,category", [
    ("sudo echo hi > /dev/null", "read"),              # no write, as for `echo hi > /dev/null`
    ("doas echo hi &> /dev/null", "read"),
    ("sudo echo hi 2> /dev/stderr", "read"),
    ("sudo echo hi", "read"),
    ("sudo echo hi > $F", "opaque_exec"),              # a target from a variable stays opaque
    ("sudo echo x > ~/.exo/policies/gates.yaml", "store_write"),
    ("doas echo x >> ~/.exo/policies/gates.yaml", "store_write"),
])
def test_other_targets_are_unchanged(line, category):
    c = sh(line)
    assert (c.category, c.reversibility) == (category, "costly"), (line, c)


def test_dev_null_matches_the_line_without_sudo():
    for line in ("echo hi > /dev/null", "echo hi &> /dev/null"):
        assert sh(f"sudo {line}").category == sh(line).category == "read"
        assert decide(sh(f"sudo {line}"), pol(categories={"local_write": "ask"})).outcome == "allow"


def test_the_store_floor_still_asks():
    d = decide(sh("sudo echo x > ~/.exo/policies/gates.yaml"), pol(categories={"local_write": "allow"}))
    assert (d.outcome, d.rule) == ("ask", "floor"), d


@pytest.mark.parametrize("line", ["echo hi > /tmp/f", "env echo hi > /tmp/f", "timeout 5 echo hi > /tmp/f",
                                  "nice echo hi > /tmp/f"])
def test_the_controls_are_unchanged(line):
    c = sh(line)
    assert (c.category, c.reversibility) == ("local_write", "reversible"), (line, c)
    assert decide(c, pol(categories={"local_write": "ask"})).outcome == "ask"


def test_the_line_takes_the_written_path_as_target():
    # As without sudo, so a rule keyed on the path now reaches the line. Main left the target empty.
    c = sh("sudo echo hi >> /etc/hosts")
    assert c.target == sh("echo hi >> /etc/hosts").target == "/etc/hosts"
    d = decide(c, pol(rules=[{"match": {"target": "/etc/*"}, "decision": "ask", "reason": "etc"}]))
    assert (d.outcome, d.rule) == ("ask", "rule"), d


def test_an_ssh_hop_keeps_its_host_part():
    # The written path takes the place of the host as the line's target, as for `ssh host 'echo hi > /tmp/f'`. The
    # hop's own part still names the host, so a deny or reserved entry on the host still reaches the line.
    c = sh("ssh host 'sudo echo hi > /tmp/f'")
    assert (c.category, c.reversibility, c.exposure, c.target) == ("local_write", "costly", "internal", "/tmp/f")
    assert c.target == sh("ssh host 'echo hi > /tmp/f'").target
    assert decide(c, pol(deny=["target:host"])).outcome == "deny"
    d = decide(c, pol(reserved=["target:host"]))
    assert d.outcome == "ask" and {("shell", "host"), ("read", "host")} <= set(d.reserved), d


@pytest.mark.parametrize("line", ["ssh host 'sudo echo hi > /tmp/f'", "true; ssh host 'sudo echo hi > /tmp/f'"])
def test_a_reserved_entry_names_the_displaced_part_as_on_main(line):
    # The hop gives the host to the displaced part, so a reserved entry names it as main did, also when the ssh
    # command is not alone on the line, and a standing grant keyed on the write does not cover it.
    d = decide(sh(line), pol(reserved=["tool:bash"]))
    assert d.outcome == "ask" and ("read", "host") in d.reserved, d


# Review of #492 at 9614ba08: the copy of the write took the lead's own target too, so a rule on the repo a gh read
# names, or on the ssh host, no longer reached the line. Main asked for each of these under the rule.
GH_ROWS = ["sudo gh pr view 1 -R o/r > /tmp/f", "sudo gh api repos/o/r/contents/x > /tmp/f",
           "sudo -u root gh pr view 1 -R o/r > /tmp/f", "sudo -E gh pr view 1 -R o/r > /tmp/f",
           "env sudo gh pr view 1 -R o/r > /tmp/f", "doas gh pr view 1 -R o/r > /tmp/f",
           "(sudo gh pr view 1 -R o/r > /tmp/f)", "{ sudo gh pr view 1 -R o/r > /tmp/f; }",
           "cd /x && sudo gh pr view 1 -R o/r > /tmp/f"]


@pytest.mark.parametrize("line", GH_ROWS)
@pytest.mark.parametrize("level", ["ask", "log"])
def test_a_rule_on_the_displaced_part_still_reaches_the_line(line, level):
    c = sh(line)
    assert (c.category, c.target) == ("local_write", "/tmp/f"), (line, c)
    d = decide(c, pol(rules=[{"match": {"target": "o/r*"}, "decision": level, "reason": "repo"}]))
    assert (d.outcome, d.rule, d.reason) == (level, "rule", "repo"), d
    assert decide(sh("sudo gh pr view 1 -R o/r"), pol(rules=[{"match": {"target": "o/r*"}, "decision": level,
                                                               "reason": "repo"}])).outcome == level


@pytest.mark.parametrize("line,target,level", [
    ("ssh host 'sudo gh api repos/o/r/contents/x > /tmp/f'", "o/r*", "ask"),
    ("ssh prod 'sudo echo hi > /srv/out'", "prod", "ask"),
    ("ssh -p 22 prod 'sudo -u root echo hi > /srv/out'", "prod", "ask"),
    ("ssh prod 'doas echo hi >> /srv/out'", "prod", "ask"),
    ("ssh prod 'sudo echo hi > /srv/out'", "prod", "log"),
])
def test_a_rule_on_the_repo_or_the_host_reaches_an_ssh_line(line, target, level):
    # The hop gives the host to the part the write displaced, as it gave it to that part when it led on main.
    allow = {r: {e: "allow" for e in ("private", "internal", "public")} for r in ("reversible", "costly", "irreversible")}
    d = decide(sh(line), pol(matrix=allow, rules=[{"match": {"target": target}, "decision": level, "reason": "t"}]))
    assert (d.outcome, d.rule) == (level, "rule"), (line, d)


COSTLY_ASK = {"reversible": {"private": "allow", "internal": "allow"}, "costly": {"private": "ask", "internal": "ask"}}


@pytest.mark.parametrize("policy,outcome", [
    ({"categories": {"read": "ask"}}, "ask"),
    ({"escalate": {"categories": ["read"]}}, "ask"),
    ({"rules": [{"match": {"category": "read", "reversibility": "costly"}, "decision": "ask", "reason": "r"}]}, "ask"),
    ({"matrix": COSTLY_ASK, "categories": {"local_write": "allow"}}, "ask"),
    ({"matrix": COSTLY_ASK, "rules": [{"match": {"target": "/tmp/*"}, "decision": "allow", "reason": "t"}]}, "ask"),
])
def test_a_policy_on_the_old_reading_still_reaches_the_line(policy, outcome):
    # Each of these reached `sudo echo hi > /tmp/f` on main through its costly read, and still does.
    assert decide(sh("sudo echo hi > /tmp/f"), pol(**policy)).outcome == outcome


def test_the_card_names_the_program_and_the_path():
    c = sh("sudo chmod 777 /etc/shadow > /tmp/f")
    d = decide(c, pol(matrix={"costly": {"private": "ask"}}))
    line = summarize(c, d)
    assert d.outcome == "ask" and "chmod" in line and "/tmp/f" in line, line
    assert "chmod" in c.as_dict()["summary"] and "/tmp/f" in c.as_dict()["summary"]


TMP_ALLOW = pol(matrix=COSTLY_ASK, rules=[{"match": {"target": "/tmp/*"}, "decision": "allow", "reason": "tmp"}])


@pytest.mark.parametrize("line", ["sudo echo hi > /srv/b; sudo echo hi > /tmp/a", "sudo echo hi > /srv/b > /tmp/a",
                                  "doas echo hi > /srv/b; doas echo hi > /tmp/a"])
def test_the_first_sudo_write_leads(line):
    c = sh(line)
    assert (c.category, c.target) == ("local_write", "/srv/b"), (line, c)
    assert decide(c, pol(rules=[{"match": {"target": "/srv/*"}, "decision": "ask", "reason": "s"}])).outcome == "ask"
    assert decide(c, TMP_ALLOW).outcome == "ask"


@pytest.mark.parametrize("line", ["{ sudo echo hi; } > /tmp/f", "(sudo echo hi) > /tmp/f",
                                  "sudo echo hi | cat > /tmp/f", "echo hi > /tmp/f; sudo ls"])
def test_a_write_that_is_not_on_the_sudo_command_does_not_lead(line):
    c = sh(line)
    assert (c.category, c.reversibility, c.target) == ("read", "costly", ""), (line, c)
    assert decide(c, TMP_ALLOW).outcome == "ask"


def test_the_parts_stay_when_the_copy_does_not_lead():
    # The ssh hop's read leads, as on main: it is internal, so no copy of the write is made.
    c = sh("ssh host 'sudo ls'; sudo echo hi > /tmp/f")
    assert (c.category, c.target) == ("read", "host")
    assert [(p.category, p.reversibility, p.exposure, p.target) for p in c.parts] == [
        ("shell", "reversible", "internal", "host"), ("read", "costly", "private", ""),
        ("local_write", "reversible", "private", "/tmp/f")]


# Re-check of #492 at b358eb59: the copy a nested line (a $( ), backticks, <( ), an ssh hop) leads with is a costly
# local_write part of the line around it. There it took the lead from a part it ties with, which led on main as the
# first of the tie, so these lines were allowed or logged under the default policy. _combine ranks them as on main.
@pytest.mark.parametrize("line", ["echo $(sudo echo hi > /tmp/f) $(foo)", "cat <(sudo echo hi > /tmp/f) <(foo)",
                                  "echo $(sudo echo hi > /tmp/f) `foo`", "x=$(sudo echo hi > /tmp/f) y=$(foo)",
                                  "echo $(doas echo hi > /tmp/f) $(./run)", "echo $(sudo echo hi > /tmp/f) $(sudo foo)",
                                  "echo $(sudo echo hi > /tmp/f) > >(foo)", "ssh h1 'sudo echo hi > /tmp/f'; ssh h2 foo"])
def test_an_unknown_program_beside_a_nested_sudo_write_still_leads(line):
    c = sh(line)
    assert c.category == "opaque_exec", (line, c)
    assert decide(c, pol()).outcome == "ask"


@pytest.mark.parametrize("line", ["ssh -p 2? prod 'sudo echo hi > /tmp/f' < x.sh",
                                  "echo ls | ssh -p 2* prod 'sudo gh pr view 1 -R o/r > /tmp/f'"])
def test_ssh_running_its_stdin_still_leads_beside_a_sudo_write(line):
    # A word ssh may expand among its options leaves the remote command uncountable, so ssh's stdin is read as
    # commands on the host too (#240). That part leads, as on main, so a deny on it with the host still denies.
    c = sh(line)
    assert (c.category, c.target) == ("opaque_exec", "prod"), (line, c)
    rule = {"match": {"category": "opaque_exec", "target": "prod"}, "decision": "deny", "reason": "host"}
    assert decide(c, pol(rules=[rule])).outcome == "deny"


ALL_ALLOW = {r: {e: "allow" for e in ("private", "internal", "public")} for r in ("reversible", "costly", "irreversible")}


@pytest.mark.parametrize("line,target", [
    ("sudo gh pr view 9 -R a/b; x=$(sudo echo hi > /tmp/f)", "a/b*"),
    ("sudo gh pr view 9 -R a/b; echo $(sudo gh pr view 1 -R o/r > /tmp/f)", "a/b*"),
    ("x=$(sudo gh pr view 9 -R a/b) y=$(sudo echo hi > /tmp/f)", "a/b*"),
    ("doas gh repo view a/b; cat <(doas echo hi >> /srv/x)", "a/b*"),
    ("ssh prod 'sudo ls'; ssh h1 'sudo echo hi > /tmp/f'", "prod"),
    ("ssh prod 'sudo gh pr view 9 -R a/b; x=$(sudo echo hi > /tmp/f)'", "a/b*"),
])
@pytest.mark.parametrize("level", ["ask", "log"])
def test_a_nested_copy_displaces_the_part_main_led_with(line, target, level):
    # The nested copy still leads, and the line is also decided with the part main led with, so a rule on it reaches.
    c = sh(line)
    assert c.category == "local_write", (line, c)
    d = decide(c, pol(matrix=ALL_ALLOW, rules=[{"match": {"target": target}, "decision": level, "reason": "t"}]))
    assert (d.outcome, d.rule) == (level, "rule"), (line, d)


def test_a_category_line_on_the_part_main_led_with_reaches_a_nested_copy():
    c = sh("sudo chmod 777 /x; echo $(sudo echo hi > /tmp/f)")
    assert c.category == "local_write"
    assert decide(c, pol(categories={"shell": "ask"})).outcome == "ask"
    assert decide(c, pol(categories={"local_write": "ask"})).outcome == "ask"


@pytest.mark.parametrize("line,exact", [
    ("sudo gh pr view 1 -R o/r > /tmp/f", True), ("ssh prod 'sudo echo hi > /srv/out'", True),
    ("sudo gh pr view 9 -R a/b; x=$(sudo echo hi > /tmp/f)", True), ("sudo echo hi > /tmp/f", False),
    ("sudo chmod 777 /x > /tmp/f", False), ("echo hi > /tmp/f", False)])
def test_a_displaced_part_with_a_target_binds_the_approval_to_the_line(line, exact):
    # A standing grant is keyed on the line's category and target. One on the written path must not answer a rule on
    # the repo or host the displaced part names, so such a line binds its approval to the exact command.
    assert binds_exact(sh(line)) is exact, line


# Re-check of #492 at a0da2f31: `ssh -o RemoteCommand=` raised the nested copy's exposure in place, but not that of
# the part it displaced. _combine ranked the copy as that part, at the stale private axes, so it tied with an earlier
# costly read, which took its place, or an unknown program led. Main asked for each of these under the rule.
OPAQUE_ALLOW = pol(categories={"opaque_exec": "allow"},
                   rules=[{"match": {"target": "a/b*"}, "decision": "ask", "reason": "repo rule"}])
REMOTE = ["ssh -o RemoteCommand='{}' h", "ssh -oRemoteCommand='{}' h", "scp -o RemoteCommand='{}' a h:b",
          "sftp -o RemoteCommand='{}' h", "ssh -o 'RemoteCommand={}' h", "ssh h '{}'"]


@pytest.mark.parametrize("before", ["sudo ls", "foo", "sudo cat /etc/hosts", "./run"])
@pytest.mark.parametrize("remote", REMOTE)
@pytest.mark.parametrize("inner", ["sudo gh pr view 9 -R a/b > /tmp/f", "x=$(sudo gh pr view 9 -R a/b > /tmp/f)",
                                   "doas gh pr view 9 -R a/b >> /tmp/f"])
def test_a_remote_command_raises_the_displaced_part_too(before, remote, inner):
    line = f"{before}; {remote.format(inner)}"
    c = sh(line)
    assert (c.category, c.exposure) == ("local_write", "internal"), (line, c)
    assert (c.displaced.target, c.displaced.exposure) == ("a/b#9", "internal"), (line, c.displaced)
    assert any(q is c.displaced for q in c.parts), line          # the part decide() and the deny checks see
    d = decide(c, OPAQUE_ALLOW)
    assert (d.outcome, d.rule, d.reason) == ("ask", "rule", "repo rule"), (line, d)


def test_a_copy_ranks_by_its_own_axes():
    # A raise in place on the copy alone (none is left in the gate) must not let a part that ties with the stale
    # displaced part take its place: the copy ranks as a read with its own axes.
    nested = sh("sudo gh pr view 9 -R a/b > /tmp/f")
    nested.exposure = "internal"
    c = gate._combine([sh("sudo ls"), nested], "x")
    assert c.displaced.target == "a/b#9", c.displaced
    c = gate._combine([sh("foo"), nested], "x")
    assert (c.category, c.displaced.target) == ("local_write", "a/b#9"), c


@pytest.mark.parametrize("line,summary", [
    ("sudo gh pr view 9 -R a/b; x=$(sudo echo hi > /tmp/f)", "gh pr view; write /tmp/f"),
    ("sudo gh pr view 9 -R a/b > /tmp/f", "gh pr view, write /tmp/f"),
    ("x=$(sudo gh pr view 9 -R a/b) y=$(sudo echo hi > /tmp/f)", "gh pr view; write /tmp/f"),
])
def test_the_card_names_the_part_whose_rule_asked(line, summary):
    # The summary names the part the copy displaces, also one a nested copy took the place of, and the card links to
    # it when its reading asks, as main's card did. The write is named under that part's program only on its command.
    c = sh(line)
    d = decide(c, pol(rules=[{"match": {"target": "a/b*"}, "decision": "ask", "reason": "repo rule"}]))
    card = summarize(c, d)
    assert d.outcome == "ask" and card.startswith(f"{summary} (") and "Link: https://github.com/a/b/issues/9." in card
    assert c.as_dict()["summary"] == summary
    assert "Link: /tmp/f." in summarize(c, decide(c, pol(categories={"local_write": "ask"})))


@pytest.mark.parametrize("line,summary", [
    ("sudo gh pr view 1 -R o/r; sudo echo hi > /tmp/f", "gh pr view; write /tmp/f"),
    ("sudo gh pr view 1 -R o/r && doas echo hi >> /tmp/f", "gh pr view; write /tmp/f"),
    ("sudo gh pr view 1 -R o/r | sudo tee /x > /tmp/f", "gh pr view; write /tmp/f"),
    ("sudo chmod 777 /x; sudo echo hi > /tmp/f", "chmod; write /tmp/f"),
    ("sudo echo hi > /tmp/f; sudo chmod 777 /x", "echo, write /tmp/f"),
    ("(sudo gh pr view 1 -R o/r > /tmp/f)", "gh pr view, write /tmp/f"),
    ("env sudo chmod 777 /x > /tmp/f", "chmod, write /tmp/f"),
])
def test_the_summary_names_the_write_under_a_program_only_on_its_own_command(line, summary):
    # Review 5640972780: for a sudo read and, after a separator, a separate sudo write, the summary read
    # `gh pr view, write /tmp/f`, as if gh wrote the file. It names the two apart now. The card links to the gh read
    # when its reading asks, and a part with no link, such as chmod, leaves the written path as the link.
    c = sh(line)
    assert (c.category, c.target, c.summary) == ("local_write", "/tmp/f", summary), (line, c)
    d = decide(c, pol(categories={"read": "ask", "shell": "ask"}))
    card = summarize(c, d)
    assert d.displaced_asks and card.startswith(f"{summary} ("), card
    assert ("Link: https://github.com/o/r/issues/1." if "gh " in line else "Link: /tmp/f.") in card, card


@pytest.mark.parametrize("line", ["ssh -o RemoteCommand='sudo echo hi > /srv/out' prod",
                                  "ssh -oRemoteCommand='sudo echo hi > /srv/out' prod",
                                  "ssh -o 'RemoteCommand=sudo -u root echo hi > /srv/out' prod",
                                  "ssh -o RemoteCommand='doas echo hi >> /srv/out' prod"])
def test_a_remote_command_gives_no_host_to_the_copy_or_its_displaced_part(line):
    # Review 5640972780: the hop form gives its host to the displaced part, but ssh -o RemoteCommand= gives it to
    # neither part, so a rule that asks on the host does not reach the line. Before #454 it was logged the same way, as
    # a costly read with no target, and the line without sudo misses the rule there too (#493). ssh's own part still
    # names the host, so a rule that denies it, and a deny or reserved entry for it, still reach the line (D-075).
    c = sh(line)
    assert (c.category, c.target, c.displaced.target) == ("local_write", "/srv/out", ""), (line, c)
    host = pol(rules=[{"match": {"target": "prod"}, "decision": "ask", "reason": "host"}])
    d = decide(c, host)
    assert (d.outcome, d.rule, d.displaced_asks) == ("log", "matrix", False), (line, d)
    plain = sh(line.replace("sudo -u root ", "").replace("sudo ", "").replace("doas ", ""))
    assert (plain.target, decide(plain, host).rule) == ("/srv/out", "matrix"), plain
    hop = sh("ssh prod 'sudo echo hi > /srv/out'")
    assert decide(hop, host).outcome == "ask"
    deny = pol(rules=[{"match": {"target": "prod"}, "decision": "deny", "reason": "host"}])
    assert decide(c, deny).outcome == decide(c, pol(deny=["target:prod"])).outcome == "deny", line
    assert ("shell", "prod") in decide(c, pol(reserved=["target:prod"])).reserved, line


@pytest.mark.parametrize("line", [
    "sudo gh api -H 'Authorization: token ghp_0123456789abcdefABCDEF0123456789abcd' repos/o/r/contents/x > /tmp/f",
    "ssh h 'sudo gh api -H \"Authorization: token ghp_0123456789abcdefABCDEF0123456789abcd\" repos/o/r/x > /tmp/f'",
    "sudo ls; x=$(sudo gh api -H 'Authorization: token ghp_0123456789abcdefABCDEF0123456789abcd' repos/o/r > /tmp/f)"])
def test_the_ledger_view_leaves_out_the_displaced_part(line):
    # The displaced part holds the raw command and its own text, a token among them. The ledger row gets neither.
    c = sh(line)
    assert c.displaced is not None and "ghp_" in c.displaced.command, (line, c)
    row = json.dumps(c.as_dict())
    assert "displaced" not in row and "ghp_" not in row and line not in row, row
    assert "displaced_asks" not in json.dumps(decide(c, pol(categories={"read": "ask"})).as_dict())


def test_a_tie_keeps_the_exposure_and_reason_of_both_readings():
    # Under a scope that makes the written path private, the reading with the host leading stays internal, as on main.
    c = sh("ssh prod 'sudo echo hi > /tmp/f'")
    d = decide(c, pol(scopes={"private": ["/tmp/*"]},
                      rules=[{"match": {"command": "^ssh "}, "decision": "ask", "reason": "ssh"}]))
    assert (d.outcome, d.exposure, d.reason) == ("ask", "internal", "ssh"), d
    # A host rule beside a costly internal ask cell: the card still names the host rule.
    d = decide(c, pol(matrix={"costly": {"internal": "ask"}},
                      rules=[{"match": {"target": "prod"}, "decision": "ask", "reason": "host rule"}]))
    assert (d.outcome, d.rule) == ("ask", "rule") and "host rule" in summarize(c, d), d


@pytest.mark.parametrize("policy,asks", [
    ({"categories": {"shell": "ask"}}, True), ({"categories": {"read": "ask", "shell": "ask"}}, True),
    ({"escalate": {"categories": ["shell"]}}, True),
    ({"rules": [{"match": {"category": "shell", "reversibility": "costly"}, "decision": "ask", "reason": "r"}]}, True),
    ({"categories": {"local_write": "ask"}}, False), ({}, False)])
def test_an_ask_from_the_displaced_reading_binds_the_approval_to_the_line(policy, asks):
    # Its part has no target, so only the decision tells: a grant keyed on the written path must not answer it.
    for line in ("sudo chmod u+s /tmp/x > /tmp/log", "sudo chmod 4755 /usr/bin/python3 > /tmp/f"):
        c = sh(line)
        assert c.displaced is not None and not c.displaced.target, (line, c.displaced)
        d = decide(c, pol(**policy))
        assert d.displaced_asks is asks and binds_exact(c, d) is asks, (line, policy, d)


def test_an_ask_from_the_displaced_part_of_any_reading_stays_known():
    # #454 with #399: decide keeps the stricter of a line's readings (_strictest), and with it an ask from the displaced
    # part of any of them, so the hook narrows who may answer for it. Here only the line as read before #399 (floor)
    # is a sudo write line.
    c = sh('sudo chmod u+s /tmp/x > /tmp/f; gh pr "$V" 5')
    assert c.displaced is None and c.floor is not None and c.floor.displaced.category == "shell", c
    assert decide(c, pol(categories={"shell": "ask"})).displaced_asks and not decide(c, pol()).displaced_asks
    ds = [gate.Decision("ask", "a", exposure="internal"), gate.Decision("ask", "b", exposure="private",
                                                                         displaced_asks=True)]
    assert gate._strictest(ds, ["", ""]).displaced_asks


@pytest.mark.parametrize("line,rule", [
    ("sudo gh pr view https://github.com/pub/repo/pull/5 -R myorg/app > /tmp/f", "myorg/*"),
    ("ssh h 'sudo gh pr view https://github.com/pub/repo/pull/5 -R myorg/app > /tmp/f'", "myorg/*"),
    ("ssh prod 'sudo gh pr view -c https://github.com/myorg/app/pull/9 > /tmp/f'", "prod"),
])
def test_the_displaced_part_keeps_the_other_targets_399_gives_it(line, rule):
    # #454 with #399: gh may act on a target other than the one it names (alt_targets). The displaced part keeps them,
    # the ssh hop gives its host to one that is the current repo, and the second reading is decided on each, as main
    # decides the gh read. The card names them when that reading asks.
    c = sh(line)
    assert c.displaced is not None and c.displaced.alt_targets, c
    d = decide(c, pol(rules=[{"match": {"target": rule}, "decision": "ask", "reason": "r"}]))
    assert (d.outcome, d.displaced_asks) == ("ask", True) and binds_exact(c, d), (line, d)
    assert "It may land on" in summarize(c, d), summarize(c, d)
