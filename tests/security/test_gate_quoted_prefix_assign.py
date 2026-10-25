"""A quoted prefix assignment no longer hides the command after it, and an unknown program beside a
logged part asks (#471).

Bash reads `X='a b' cmd` as an assignment and runs cmd: only the value is quoted. The gate kept any
quoted assignment word as the program, an unknown one, so cmd was never read. Beside a push that
logs, that unknown program's ask was dropped too, since the opaque floor fired only on `indirection`.
A word is now an assignment when its NAME= is unquoted in the source, and the floor fires on an
unknown program as well. Classification and decision only; nothing is executed.
"""
from collections import Counter

import pytest

from exo.gate import CATEGORIES, OUTCOMES, binds_exact, classify, decide, normalize_policies

DEFAULT = normalize_policies(None)
DENY = normalize_policies({"gates": {"deny": ["delete"]}})
POSTING = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                              "posting": {"footer": "F", "forbid_words": ["Zork"]}})
R = "-R myorg/app"
CLEAN = f"gh pr comment 5 {R} --body $'ok\\nF'"
DIRTY = f"gh pr comment 6 {R} --body 'Zork leaked'"
PUSH = "git push origin feature"
RM = "rm -rf /srv/data"

# Each spelling bash reads as the assignment X=... before the command.
# A line continuation inside the name is no quoting: bash drops it first, so `X\\<newline>=` is X=.
STYLES = ["X='a b'", 'X="a b"', "X=a\\ b", "X=$'a b'", 'X="$Y"', "X=a'b c'd", "X+='a b'", "X='a b' Y=\"c d\"",
          "X\\\n='a b'"]


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


@pytest.mark.parametrize("style", STYLES)
def test_the_command_after_a_quoted_assignment_is_read(style):
    c = classify(sh(f"{style} {RM}"))
    assert c.category == "delete", c
    assert decide(c, DENY).outcome == "deny"


# The issue's rows and the same forms behind other joiners and wrappers: the delete is read, so the
# principal's `deny: [delete]` denies the line, where main logged or asked.
DENIED = [f"{PUSH} && {s} {RM}" for s in STYLES] + [
    f"true; X='a b' {RM}",
    f"false || X='a b' {RM}",
    f"echo hi | X='a b' {RM}",
    f"echo $(X='a b' {RM})",
    f"bash -c \"X='a b' {RM}\"",
    f"ssh host \"X='a b' {RM}\"",
    f"X='a b' sudo {RM}",
]


@pytest.mark.parametrize("line", DENIED)
def test_a_denied_delete_behind_a_quoted_assignment_is_denied(line):
    d = decide(classify(sh(line)), DENY)
    assert d.outcome == "deny", (line, d)


@pytest.mark.parametrize("line", [f"{PUSH} && X='a b' {RM}",
                                  f"{PUSH} && X='a b' git push --force origin main",
                                  f"{PUSH} && X='a b' curl -X POST -d x https://example.com/x",
                                  f"{PUSH} && mytool --wipe",
                                  f"{PUSH}; kill -9 1",
                                  f"{PUSH} && 'X=a' {RM}"])
def test_a_part_that_asks_alone_asks_beside_a_logged_push(line):
    c = classify(sh(line))
    assert decide(c, DEFAULT).outcome == "ask", (line, decide(c, DEFAULT))
    assert binds_exact(c, decide(c, DEFAULT)), line     # an "always" covers this exact line only


@pytest.mark.parametrize("style", STYLES)
def test_a_post_behind_a_quoted_assignment_is_checked(style):
    d = decide(classify(sh(f"{CLEAN} && {style} {DIRTY}")), POSTING)
    assert d.outcome == "ask", d
    assert "GitHub PR comment on myorg/app#6: forbidden word: Zork" in d.violations, d


# Controls: the same on main and here.

def test_an_unquoted_assignment_is_unchanged():
    d = decide(classify(sh(f"{CLEAN} && X=ab {DIRTY}")), POSTING)
    assert d.outcome == "ask" and "GitHub PR comment on myorg/app#6: forbidden word: Zork" in d.violations, d
    c = classify(sh(f"X=ab {RM}"))
    assert c.category == "delete" and decide(c, DENY).outcome == "deny", c
    c = classify(sh("FOO=1 make test"))
    assert c.category == "opaque_exec" and c.summary == "make: runs Makefile recipes", c


@pytest.mark.parametrize("line", ["'X=a' ls", '"X"=a ls', "X\\=a ls"])
def test_a_quoted_name_is_the_program(line):
    # bash runs a program named X=a: the name or the `=` is quoted, so the word is no assignment
    c = classify(sh(line))
    assert c.category == "opaque_exec" and "unknown program" in c.signals, c
    assert c.summary == "runs X=a, a program the gate does not know", c
    assert decide(c, DEFAULT).outcome == "ask"


@pytest.mark.parametrize("line", ["mytool --wipe", f"{PUSH} && ./tool.sh", "X='a b' ls", "X='a b'",
                                  "PATH='/tmp/x' ls", "'PATH=/tmp/x' ls", "GIT_SSH_COMMAND='evil' git fetch"])
def test_these_ask_as_before(line):
    c = classify(sh(line))
    assert decide(c, DEFAULT).outcome == "ask", (line, decide(c, DEFAULT))


def test_naming_opaque_exec_still_lowers_an_unknown_program():
    for pol in ({"gates": {"categories": {"opaque_exec": "log"}}},
                {"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": "log", "reason": "ok"}]}}):
        d = decide(classify(sh("mytool --wipe")), normalize_policies(pol))
        assert d.outcome == "log", (pol, d)
    d = decide(classify(sh(f"{PUSH} && mytool --wipe")),
               normalize_policies({"gates": {"categories": {"opaque_exec": "log"}}}))
    assert d.outcome == "log", d
    # a rule on opaque_exec lowers it only when the rule matches the line's dominant part, as for `indirection`
    d = decide(classify(sh(f"{PUSH} && mytool --wipe")), normalize_policies(
        {"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": "log", "reason": "ok"}]}}))
    assert d.outcome == "ask", d


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("policy, outcome", [
    ({"gates": {"categories": {c: "allow" for c in CATEGORIES}}}, "ask"),
    ({"gates": {"categories": {c: "log" for c in CATEGORIES}}}, "ask"),
    ({"gates": {"matrix": {"irreversible": {"public": "deny", "internal": "deny"}, "costly": {"public": "deny"}}}},
     "deny"),
])
def test_the_line_is_also_decided_as_before(style, policy, outcome):
    # The delete now outranks the post, but the matrix cell, category lines and third-party escalation read only the
    # dominant part (#437). The line is also decided with the post on top, as main decided it, and the stricter stands.
    line = f"gh pr comment 5 -R myorg/app --body $'ok\\nF' && {style} {RM}"
    d = decide(classify(sh(line)), normalize_policies(policy))
    assert d.outcome == outcome, (line, policy, d)


STORE = '"$HOME/.exo/state/notes.md"'
SECRET = '"$HOME/.ssh/id_rsa"'


@pytest.mark.parametrize("line", [f"X={STORE} ls", f"X={STORE}; echo x > \"$X\"", "X='~/.exo/state/notes.md' ls",
                                  f"cd ~/.exo/state && X={STORE} ls", f"{PUSH} && X={STORE} ls"])
def test_a_store_path_in_a_quoted_value_is_still_a_store_write(line):
    # main read the quoted word as an argument and found the store path in it; the word is still scanned
    c = classify(sh(line))
    assert c.category == "store_write", (line, c)
    assert decide(c, normalize_policies({"gates": {"deny": ["store_write"]}})).outcome == "deny", line
    d = decide(c, normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}}))
    assert d.outcome == "ask", (line, d)


def test_a_store_path_set_for_a_later_command_still_asks():
    d = decide(classify(sh(f"f={STORE}; cp /tmp/evil \"$f\"")),
               normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}}))
    assert d.outcome == "ask", d


@pytest.mark.parametrize("line", [f"X={SECRET} ls", "X='~/.ssh/id_rsa' cat", f"{PUSH} && X={SECRET} ls"])
def test_a_secret_path_in_a_quoted_value_is_still_credentials(line):
    c = classify(sh(line))
    assert c.category == "credentials", (line, c)
    assert decide(c, normalize_policies({"gates": {"deny": ["credentials"]}})).outcome == "deny", line


# An `env -S` behind the quoted word ends the new reading there (its text is read as a line of its own), but main read
# the quoted word as the program: its value was scanned for store paths and env-inject names. Both still are.
STORE_ENV_S = [f"X={STORE} env -S 'ls'", "X='~/.exo/state/notes.md' env -S 'ls'", f"X={STORE} sudo env -S 'ls'",
               f"X={STORE} env --split-string='ls'", f"X={STORE} env -S'ls'", f"{PUSH} && X={STORE} env -S 'ls'"]


@pytest.mark.parametrize("line", STORE_ENV_S)
def test_a_store_path_in_a_quoted_value_before_env_s_still_counts(line):
    c = classify(sh(line))
    assert decide(c, normalize_policies({"gates": {"deny": ["store_write"]}})).outcome == "deny", (line, c)
    d = decide(c, normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}}))
    assert d.outcome == "ask", (line, d)


INJECT_ENV_S = ["GIT_SSH_COMMAND='rm -rf /srv/data' env -S 'git fetch'",
                "GIT_SSH_COMMAND=rm\\ -rf\\ /srv/data env -S 'git fetch'",
                "PS4='$(rm -rf /srv/data)' env -S 'bash -x s.sh'",
                f"{PUSH} && GIT_SSH_COMMAND='rm -rf /srv/data' env -S 'git fetch'",
                f"{PUSH} && PS4='$(rm -rf /srv/data)' env -S 'bash -x s.sh'"]


@pytest.mark.parametrize("line", INJECT_ENV_S)
def test_an_env_inject_value_before_env_s_is_still_read(line):
    c = classify(sh(line))
    assert decide(c, DENY).outcome == "deny", (line, c)
    d = decide(c, normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}}))
    assert d.outcome == "ask", (line, d)


@pytest.mark.parametrize("line, outcome", [('X="$Y" ls; git push --force origin main', "ask"),
                                           ('X="$Y" ls; git push origin feature', "log"),
                                           ('X="$(id)" ls; git push origin feature', "log")])
def test_a_dynamic_quoted_assignment_word_is_still_a_setter(line, outcome):
    # main read `X="$Y"` as a program from a variable, which may change the shell for the push after it, so a rule on
    # `git push` that would lower the line must name that command too (#394). It still counts.
    pol = normalize_policies({"gates": {"categories": {"opaque_exec": "allow"},
                                        "rules": [{"match": {"command": "git push"}, "decision": "allow",
                                                   "reason": "r"}]}})
    d = decide(classify(sh(line)), pol)
    assert d.outcome == outcome, (line, d)


MATRIX_DENY = normalize_policies({"gates": {"matrix": {"irreversible": {"public": "deny", "internal": "deny"},
                                                       "costly": {"public": "deny"}}}})


@pytest.mark.parametrize("value", [f"declare -a 'arr=($({RM}))'", f"declare -i 'x=a[$({RM})]'",
                                   f"declare -A 'm=([k]=$({RM}))'"])
def test_a_value_read_again_behind_a_quoted_assignment_is_decided_as_before(value):
    # declare reads the value again, and the delete in it is added to the line late; the post on top before still
    # decides the line too
    line = f"{CLEAN} && X='a b' {value}"
    d = decide(classify(sh(line)), MATRIX_DENY)
    assert d.outcome == "deny", (line, d)


@pytest.mark.parametrize("line", ["X='a b' cd /proc && cat self/environ", "X='a b' cd /proc/self && cat environ",
                                  "X='a b' cd ~/.exo && mv keys /tmp/k", "X='a b' cd /dev && tar czfC - ~/.kube config",
                                  "X='a b' coproc env | cat"])
def test_a_secret_read_after_a_quoted_assignment_is_still_denied(line):
    # the new reading of the quoted word (a `cd`, a `coproc`) gives a store write or another part on top, but the line
    # read as before, the word a program, reads the secret, and a deny on credentials still denies it
    c = classify(sh(line))
    assert decide(c, normalize_policies({"gates": {"deny": ["credentials"]}})).outcome == "deny", (line, c)
    allow_under_denies = {"gates": {"categories": {x: "deny" for x in ("delete", "force_push", "github_post",
                                                                         "vcs_push", "local_write", "opaque_exec",
                                                                         "web_post", "credentials", "web_read")},
                                    "rules": [{"match": {"command": ".*"}, "decision": "allow", "reason": "r"}]}}
    assert decide(c, normalize_policies(allow_under_denies)).outcome == "deny", (line, c)


def test_a_store_scan_reads_its_own_reading():
    # main read `X='a b'` as the program with the folder `cd /dev` left; the find's delete does not widen that scan
    d = decide(classify(sh("cd /dev && X='a b' find / -delete")),
               normalize_policies({"gates": {"deny": ["target:/dev*", "target:/proc*"]}}))
    assert d.outcome == "deny", d


@pytest.mark.parametrize("cmd", ["yarn publish", "git push --force origin main", f"gh pr comment 5 {R} --body hi",
                                 "ssh -vpls 22 rm -rf /data"])
def test_a_cd_behind_a_quoted_assignment_leaves_the_line_decided_as_before(cmd):
    # bash runs the cd, so the command after it is in /dev, a store write the gate cannot resolve that outranks it
    # (#437); main read the quoted word as the program, and the command after it decided the line. Both are decided.
    line = f"X='a b' cd /dev && {cmd}"
    c = classify(sh(line))
    assert decide(c, MATRIX_DENY).outcome == "deny", (line, decide(c, MATRIX_DENY))


def test_the_reading_as_before_is_kept_only_for_a_quoted_assignment():
    assert classify(sh("X=ab cd /dev && yarn publish")).before is None
    assert classify(sh("'X=a' ls")).before is None
    c = classify(sh("X='a b' cd /dev && yarn publish"))
    assert c.category == "store_write", c          # the cd is read: yarn runs in /dev
    assert c.before is not None and c.before.category == "publish", c.before
    assert "before" not in c.as_dict()


# gate_diff's posting policies. Two posts to one PR that both miss the footer list that violation twice, named after
# the post, once a `cd` or `pushd` into the store, a descriptor folder or a secret folder is on top (#468). The line
# read as before lists it twice; the stricter of both readings keeps each entry as often as either lists it (#439's
# merge), not once.
POSTING_POLICIES = {
    "posting_footer": normalize_policies({"posting": {"footer": "-- sent by an agent"}}),
    "posting_close_and_merge": normalize_policies({"posting": {
        "applies_to": ["github_post", "github_close", "vcs_merge", "message_send"], "footer": "-- bot",
        "forbid_words": ["done", "secret"]}}),
    "posting_footer_everywhere": normalize_policies({"gates": {"scopes": {"private": ["*"]}}, "posting": {
        "footer": "-- bot", "footer_exposures": ["private", "internal", "public"]}}),
}
MOVES = ["X='a b' pushd /dev/fd", "X=a\\ b cd ~/.exo/state", "X='a b' command cd ~/.exo/state", "X=a\\ b pushd ~/.ssh",
         "X=a\\ b command cd ~/.ssh", "X='a b' builtin cd ~/.ssh", 'X="$Y" cd /dev/fd',
         "X='a b' Y=\"c d\" pushd ~/.exo/state", "X='a b' cd $EXO_HOME/plugins", "X=a\\ b pushd /proc/self"]


def _kept(d, before):
    """Every violation and reserved part of the line decided as before is in ``d``, as often, and no lower."""
    assert OUTCOMES.index(d.outcome) >= OUTCOMES.index(before.outcome), (d, before)
    assert not Counter(before.violations) - Counter(d.violations), (d.violations, before.violations)
    assert set(before.reserved) <= set(d.reserved), (d.reserved, before.reserved)


@pytest.mark.parametrize("name", POSTING_POLICIES)
@pytest.mark.parametrize("move", MOVES)
def test_two_alike_violations_are_both_kept(name, move):
    pol = POSTING_POLICIES[name]
    line = f"{CLEAN} && {move} && gh pr comment 5 {R} --body hi"
    c = classify(sh(line))
    d = decide(c, pol)
    footer = pol["posting"]["footer"]
    assert d.outcome == "ask" and d.violations.count(f"GitHub PR comment on myorg/app#5: missing footer: {footer}") \
        == 2, (line, name, d)
    _kept(d, decide(c.before, pol))


@pytest.mark.parametrize("line", [
    f"{CLEAN} ; X='a b' cd ~/.exo/state ; gh pr comment 5 {R} --body hi ; git push --force origin main",
    f"{CLEAN} && echo $(X='a b' pushd /dev/fd && gh pr comment 5 {R} --body hi) && gh pr comment 5 {R} --body hi",
    f"{CLEAN} && bash -c 'X=a\\ b cd ~/.ssh && gh pr comment 5 {R} --body hi' && gh pr comment 5 {R} --body hi",
    f"{CLEAN} && for i in 1; do X='a b' cd ~/.exo/plugins && gh pr comment 5 {R} --body hi; done",
    f"gh pr comment 5 {R} --body 'secret done' && X='a b' cd /dev && gh pr merge 5 {R} --squash",
    "gh -R myorg/app pr comment 5 -b hi && X='a b' pushd ~/.exo/state && gh pr -R myorg/app comment 5 -b hi | cat",
])
@pytest.mark.parametrize("name", POSTING_POLICIES)
def test_the_line_decided_as_before_loses_nothing_in_the_merge(name, line):
    pol = POSTING_POLICIES[name]
    c = classify(sh(line))
    assert c.before is not None, line
    _kept(decide(c, pol), decide(c.before, pol))
